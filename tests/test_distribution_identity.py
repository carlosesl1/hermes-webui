"""Offline regressions for fork/source-image distribution identity."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from api import updates

ROOT = Path(__file__).resolve().parents[1]
FORK = 'https://github.com/carlosesl1/hermes-webui'


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


def test_manual_image_workflow_has_no_release_or_floating_tag():
    import yaml
    workflow = yaml.safe_load((ROOT / '.github/workflows/release.yml').read_text())
    trigger = workflow.get('on', workflow.get(True))  # PyYAML's YAML 1.1 "on"
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

