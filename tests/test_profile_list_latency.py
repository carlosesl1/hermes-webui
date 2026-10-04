"""Deterministic work-count and freshness guards; no timing thresholds."""
import os
import shutil
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from api import profiles
from tests.profile_latency_fixture import install_core_doubles, write_layout


@pytest.fixture
def layout(monkeypatch, tmp_path):
    homes = write_layout(tmp_path / 'home', profile_count=2, skills_per_profile=12)
    cli = install_core_doubles(monkeypatch, homes[0])
    monkeypatch.setattr(profiles, '_DEFAULT_HERMES_HOME', homes[0])
    monkeypatch.setattr(profiles, '_INITIAL_ISOLATED_PROFILE_OPT_IN', '')
    monkeypatch.setattr(profiles, '_INITIAL_HERMES_HOME', str(homes[0]))
    monkeypatch.setattr(profiles, '_active_profile', 'default')
    monkeypatch.setattr(profiles, '_SKILLS_STATS_CACHE', {})
    monkeypatch.setattr(profiles, '_LIST_PROFILES_CACHE', None)
    profiles.clear_request_profile()
    yield homes, cli
    profiles.clear_request_profile()


def test_cold_stats_uses_one_precompute_probe(layout):
    homes, _ = layout
    with patch.object(profiles, '_skill_tree_max_mtime_ns',
                      wraps=profiles._skill_tree_max_mtime_ns) as probe:
        assert profiles._get_profile_skills_stats(homes[0]) == (11, 12)
    assert probe.call_count == 1, 'cold miss must not walk the same tree twice before parsing'


def test_probe_stats_each_nested_directory_once(layout, monkeypatch):
    homes, _ = layout
    counts = Counter()
    original = Path.stat

    def stat(path, *args, **kwargs):
        counts[str(path)] += 1
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', stat)
    profiles._skill_tree_max_mtime_ns(homes[0] / 'skills', homes[0] / 'config.yaml')
    nested = homes[0] / 'skills' / 'category-0' / 'nested'
    assert counts[str(nested)] == 1, 'parent + child visits must not stat a directory twice'


def test_warm_stats_still_probe_but_never_reparse(layout):
    homes, _ = layout
    profiles._get_profile_skills_stats(homes[0])
    with patch.object(profiles, '_compute_profile_skills_stats', side_effect=AssertionError('parse')):
        with patch.object(profiles, '_skill_tree_max_mtime_ns',
                          wraps=profiles._skill_tree_max_mtime_ns) as probe:
            assert profiles._get_profile_skills_stats(homes[0]) == (11, 12)
    assert probe.call_count == 1


@pytest.mark.parametrize('change', ['edit', 'add', 'delete', 'config', 'symlink'])
def test_next_stats_call_observes_changes(layout, change, tmp_path):
    homes, _ = layout
    home = homes[0]
    skill = home / 'skills' / 'category-0' / 'nested' / 'skill-0'
    assert profiles._get_profile_skills_stats(home) == (11, 12)
    future = time.time_ns() + 5_000_000_000
    if change == 'edit':
        target = skill / 'SKILL.md'
        target.write_text('---\nname: renamed\n---\n', encoding='utf-8')
        expected = (12, 12)
    elif change == 'config':
        target = home / 'config.yaml'
        target.write_text('skills:\n  platform_disabled:\n    webui: []\n', encoding='utf-8')
        expected = (12, 12)
    elif change == 'delete':
        shutil.rmtree(skill)
        target = skill.parent
        expected = (11, 11)
    else:
        target = home / 'skills' / 'new'
        if change == 'symlink':
            external = tmp_path / 'external'
            external.mkdir()
            (external / 'SKILL.md').write_text('---\nname: linked\n---\n', encoding='utf-8')
            target.symlink_to(external, target_is_directory=True)
        else:
            target.mkdir()
            (target / 'SKILL.md').write_text('---\nname: new\n---\n', encoding='utf-8')
        expected = (12, 13)
    os.utime(target, ns=(future, future))
    assert profiles._get_profile_skills_stats(home) == expected


def test_rows_cache_contract_metadata_gateway_and_request_activity(layout):
    homes, _ = layout
    rows = profiles.list_profiles_api()
    assert [row['name'] for row in rows] == ['default', 'profile-1']
    assert rows[0]['is_default'] and rows[0]['model'] == 'model-0'
    assert rows[1]['provider'] == 'synthetic' and rows[1]['skill_count'] == 11
    profiles.set_request_profile('profile-1')
    rows[0]['model'] = 'must-not-poison-cache'
    with patch.object(profiles, '_build_profile_rows_fast', side_effect=AssertionError('rebuild')):
        hit = profiles.list_profiles_api()
    assert hit[0]['model'] == 'model-0'
    assert [r['name'] for r in hit if r['is_active']] == ['profile-1']
    assert profiles._LIST_PROFILES_CACHE_TTL == 4.0
    (homes[1] / 'profile.yaml').write_text('visible: false\n', encoding='utf-8')
    (homes[1] / 'gateway.synthetic').touch()
    profiles._invalidate_list_profiles_cache()
    fresh = profiles.list_profiles_api()[1]
    assert fresh['visible'] is False and fresh['gateway_running'] is True


def test_fallback_preserves_renamed_default(layout, monkeypatch):
    homes, cli = layout
    monkeypatch.setattr(profiles, '_build_profile_rows_fast', lambda: None)
    cli.list_profiles = lambda: [SimpleNamespace(name='root-alias', path=homes[0],
        is_default=True, gateway_running=True, model='fallback', provider='synthetic', has_env=False)]
    monkeypatch.setattr(profiles, '_active_profile', 'root-alias')
    result = profiles.list_profiles_api()[0]
    assert result['name'] == 'root-alias' and result['is_default'] and result['is_active']
    assert result['gateway_running'] and result['model'] == 'fallback'


def test_isolated_rows_cannot_return_root_default(layout, monkeypatch):
    homes, cli = layout
    pinned = homes[0] / 'profiles' / 'default'
    pinned.mkdir()
    monkeypatch.setattr(profiles, '_INITIAL_HERMES_HOME', str(pinned))
    monkeypatch.setattr(profiles, '_INITIAL_ISOLATED_PROFILE_OPT_IN', 'true')
    cli.list_profiles = lambda: [SimpleNamespace(name='default', path=homes[0])]
    result = profiles.list_profiles_api()
    assert len(result) == 1 and result[0]['path'] == str(pinned)
    assert result[0]['total_skills'] == 0


def test_probe_prunes_support_and_excluded_trees(layout):
    homes, _ = layout
    home = homes[0]
    before = profiles._skill_tree_max_mtime_ns(home / 'skills', home / 'config.yaml')
    support = next((home / 'skills').glob('category-*/nested/skill-*/references/vendor/nested/SKILL.md'))
    future = time.time_ns() + 50_000_000_000
    os.utime(support, ns=(future, future))
    assert profiles._skill_tree_max_mtime_ns(home / 'skills', home / 'config.yaml') == before


def test_probe_includes_directory_that_cannot_be_walked(layout, monkeypatch):
    homes, _ = layout
    blocked = homes[0] / 'skills' / 'blocked'
    blocked.mkdir()
    future = time.time_ns() + 50_000_000_000
    os.utime(blocked, ns=(future, future))
    scandir = os.scandir

    def scan(path):
        if str(path) == str(blocked):
            raise PermissionError(13, 'synthetic denied', str(blocked))
        return scandir(path)

    monkeypatch.setattr(os, 'scandir', scan)
    assert profiles._skill_tree_max_mtime_ns(homes[0] / 'skills', homes[0] / 'config.yaml') == future


def test_write_during_compute_is_not_stamped_as_already_observed(layout):
    homes, _ = layout
    home = homes[0]
    compute = profiles._compute_profile_skills_stats

    def changing_compute(path):
        result = compute(path)
        target = path / 'config.yaml'
        target.write_text('skills: {disabled: []}\n', encoding='utf-8')
        future = time.time_ns() + 5_000_000_000
        os.utime(target, ns=(future, future))
        return result

    with patch.object(profiles, '_compute_profile_skills_stats', side_effect=changing_compute):
        assert profiles._get_profile_skills_stats(home) == (11, 12)
    assert profiles._get_profile_skills_stats(home) == (12, 12)


def test_compute_failure_releases_lock_and_does_not_cache(layout):
    homes, _ = layout
    with patch.object(profiles, '_compute_profile_skills_stats', side_effect=RuntimeError('synthetic')):
        with pytest.raises(RuntimeError, match='synthetic'):
            profiles._get_profile_skills_stats(homes[0])
    assert homes[0].resolve() not in profiles._SKILLS_STATS_CACHE
    assert profiles._get_profile_skills_stats(homes[0]) == (11, 12)


def test_warm_probes_are_not_serialized_by_compute_lock(layout, monkeypatch):
    homes, _ = layout
    home = homes[0].resolve()
    profiles._SKILLS_STATS_CACHE[home] = (11, 12, 1, time.time() + 300)
    barrier = threading.Barrier(4, timeout=3)

    def probe(*_args):
        # All warm readers must reach the probe before any can return; no
        # wall-clock speed assertion or synthetic latency benchmark.
        barrier.wait()
        return 1

    monkeypatch.setattr(profiles, '_skill_tree_max_mtime_ns', probe)
    with patch.object(profiles, '_compute_profile_skills_stats', side_effect=AssertionError('parse')):
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(profiles._get_profile_skills_stats, home) for _ in range(4)]
            assert [f.result(timeout=5) for f in futures] == [(11, 12)] * 4
