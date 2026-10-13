"""Offline regressions for fork/source-image distribution identity."""
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tarfile
from unittest.mock import MagicMock

import pytest

from api import updates

ROOT = Path(__file__).resolve().parents[1]
FORK = 'https://github.com/carlosesl1/hermes-webui'


def _workflow_trigger(workflow):
    # PyYAML follows YAML 1.1 and parses the GitHub Actions "on" key as True.
    return workflow.get('on', workflow.get(True))


@pytest.mark.parametrize('declaration', ['__source__', '__source__: str'])
@pytest.mark.parametrize('source', [None, FORK, 'https://github.com/another/fork'])
def test_no_git_release_check_uses_distribution_source(tmp_path, monkeypatch, source, declaration):
    monkeypatch.setattr(updates, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(updates, 'WEBUI_VERSION', 'v1.0')
    if source is not None:
        (tmp_path / 'api').mkdir()
        (tmp_path / 'api' / '_version.py').write_text(f'{declaration} = {source!r}\n')
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps([
        {'name': 'v1.1', 'commit': {'sha': 'new'}},
        {'name': 'v1.0', 'commit': {'sha': 'old'}},
    ]).encode()
    opener = MagicMock(return_value=response)
    monkeypatch.setattr(updates.urllib.request, 'urlopen', opener)
    result = updates._check_repo(tmp_path, 'webui')
    expected = source or FORK
    assert result['no_git'] and result['manual_update']
    assert result['repo_url'] == expected
    assert result['compare_url'] == expected + '/compare/old...new'
    assert opener.call_args.args[0].full_url == (
        'https://api.github.com/repos/' + expected.removeprefix('https://github.com/')
        + '/tags?per_page=100'
    )


@pytest.mark.parametrize('declaration', ['__source__', '__source__: str'])
@pytest.mark.parametrize('source', [
    '', 'https://evil.example/fork/repo', 'https://github.com/a/b?token=secret',
    'https://github.com/a/b/../other', 'https://user@github.com/a/b',
])
def test_invalid_explicit_source_never_queries_another_repo(tmp_path, monkeypatch, source, declaration):
    monkeypatch.setattr(updates, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(updates, 'WEBUI_VERSION', 'v1.0')
    (tmp_path / 'api').mkdir()
    (tmp_path / 'api' / '_version.py').write_text(f'{declaration} = {source!r}\n')
    opener = MagicMock(side_effect=AssertionError('network must not be called'))
    monkeypatch.setattr(updates.urllib.request, 'urlopen', opener)
    assert updates._check_repo(tmp_path, 'webui')['behind'] is None
    opener.assert_not_called()


def test_baked_metadata_roundtrip_without_git(tmp_path, monkeypatch):
    target = tmp_path / 'api' / '_version.py'
    target.parent.mkdir()
    revision = 'a' * 40
    env = dict(os.environ, HERMES_VERSION='sha-' + revision,
               HERMES_SOURCE=FORK, HERMES_REVISION=revision)
    subprocess.run([sys.executable, str(ROOT / 'scripts/write_distribution_metadata.py'),
                    str(target)], env=env, check=True)
    metadata = runpy.run_path(str(target))
    assert metadata['__source__'] == FORK
    assert metadata['__revision__'] == revision
    monkeypatch.setattr(updates, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(updates, '_describe_git_version', lambda *a, **kw: None)
    assert updates._detect_webui_version() == 'sha-' + revision
    assert updates._webui_distribution_source() == FORK


@pytest.mark.parametrize('version', ['unknown', 'sha-' + 'a' * 40, 'exp-v1.1'])
def test_no_release_identity_does_not_query_github(tmp_path, monkeypatch, version):
    monkeypatch.setattr(updates, 'WEBUI_VERSION', version)
    opener = MagicMock(side_effect=AssertionError('network must not be called'))
    monkeypatch.setattr(updates.urllib.request, 'urlopen', opener)
    assert updates._check_repo(tmp_path, 'webui')['behind'] is None
    opener.assert_not_called()


def test_invalid_revision_does_not_write_metadata(tmp_path):
    target = tmp_path / '_version.py'
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/write_distribution_metadata.py'), str(target)],
        env=dict(os.environ, HERMES_SOURCE=FORK, HERMES_REVISION='not-a-sha'),
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert not target.exists()


def test_exact_head_pull_request_workflows_cover_code_changes():
    import yaml

    workflows = {
        'tests': yaml.safe_load((ROOT / '.github/workflows/tests.yml').read_text()),
        'browser-smoke': yaml.safe_load((ROOT / '.github/workflows/browser-smoke.yml').read_text()),
        'conversation-lifecycle': yaml.safe_load(
            (ROOT / '.github/workflows/conversation-lifecycle.yml').read_text()
        ),
    }
    for workflow in workflows.values():
        pull_request = _workflow_trigger(workflow)['pull_request']
        assert pull_request['branches'] == ['master']

    for name in ('tests', 'browser-smoke'):
        pull_request = _workflow_trigger(workflows[name])['pull_request']
        assert 'paths' not in pull_request
        assert 'paths-ignore' not in pull_request
        detect = next(
            step for step in workflows[name]['jobs']['changes']['steps']
            if step.get('id') == 'detect'
        )
        assert detect['env']['HEAD_SHA'] == '${{ github.event.pull_request.head.sha }}'
        assert 'RANGE="$BASE_SHA...$HEAD_SHA"' in detect['run']

    assert workflows['tests']['jobs']['test']['if'] == '${{ always() }}'
    assert workflows['browser-smoke']['jobs']['browser-smoke']['if'] == '${{ always() }}'

    lifecycle_paths = set(_workflow_trigger(
        workflows['conversation-lifecycle']
    )['pull_request']['paths'])
    assert {'api/**.py', 'static/**'} <= lifecycle_paths


def test_source_archive_discovers_distribution_tests_without_git(tmp_path):
    if not (ROOT / '.git').exists():
        pytest.skip('source archive export requires a git checkout')

    archive_paths = [
        '.github/workflows',
        'api',
        'docker-compose.yml',
        'docker-compose.two-container.yml',
        'docker-compose.three-container.yml',
        'pytest.ini',
        'scripts',
        'tests/test_distribution_identity.py',
    ]
    archive = subprocess.run(
        ['git', 'archive', '--format=tar', 'HEAD', *archive_paths],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    extract_root = tmp_path / 'source'
    extract_root.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        target_root = extract_root.resolve()
        for member in tar.getmembers():
            target = (extract_root / member.name).resolve()
            assert target == target_root or target_root in target.parents
        tar.extractall(extract_root, filter='data')

    assert not (extract_root / '.git').exists()
    assert (extract_root / 'scripts/test.sh').exists()
    assert (extract_root / 'tests/test_distribution_identity.py').exists()

    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env['HERMES_DISABLE_LAZY_INSTALLS'] = '1'
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    collected = subprocess.run(
        [
            sys.executable,
            '-m',
            'pytest',
            '-q',
            '--collect-only',
            'tests/test_distribution_identity.py',
        ],
        cwd=extract_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert collected.returncode == 0, collected.stdout + collected.stderr
    assert 'test_baked_metadata_roundtrip_without_git' in collected.stdout


def test_manual_image_workflow_has_no_release_or_floating_tag():
    import yaml
    workflow = yaml.safe_load((ROOT / '.github/workflows/release.yml').read_text())
    trigger = _workflow_trigger(workflow)
    assert 'workflow_dispatch' in trigger
    steps = workflow['jobs']['release']['steps']
    release = next(s for s in steps if s.get('uses', '').startswith('softprops/'))
    assert release['if'] == "github.event_name == 'push'"
    meta = next(s for s in steps if s.get('id') == 'meta')['with']
    assert 'type=sha,format=long,prefix=sha-' in meta['tags']
    for line in meta['tags'].splitlines():
        if 'type=match' in line or 'value=latest' in line or 'value=experimental' in line:
            assert "github.event_name == 'push'" in line
    build = next(s for s in steps if s.get('uses', '').startswith('docker/build-push-action@'))
    assert 'HERMES_REVISION=${{ github.sha }}' in build['with']['build-args']
    assert 'HERMES_SOURCE=https://github.com/${{ github.repository }}' in build['with']['build-args']


@pytest.mark.parametrize('filename', [
    'docker-compose.yml', 'docker-compose.two-container.yml', 'docker-compose.three-container.yml',
])
def test_every_compose_topology_builds_the_reviewed_checkout(filename):
    import yaml
    service = yaml.safe_load((ROOT / filename).read_text())['services']['hermes-webui']
    assert service['build'] == '.'
    if filename != 'docker-compose.yml':
        assert service['image'] == 'hermes-webui:local'
        assert service['pull_policy'] == 'build'
