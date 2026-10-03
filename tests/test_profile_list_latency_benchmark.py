"""Opt in: PROFILE_LATENCY_BENCHMARK_JSON=/absolute/output.json ./scripts/test.sh tests/test_profile_list_latency_benchmark.py -q -s.

Synthetic WebUI-only benchmark. Core helpers/platform matching are explicit doubles,
not production timings. File creation is outside timing. No sleeps or latency injection.
"""
import cProfile
import importlib.util
import json
import os
import pstats
import statistics
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from api import profiles as current_profiles
from tests.profile_latency_fixture import install_core_doubles, write_layout


@pytest.mark.skipif(not os.getenv('PROFILE_LATENCY_BENCHMARK_JSON'), reason='opt-in benchmark')
def test_profile_list_repeat_benchmark(tmp_path, monkeypatch):
    # Optional read-only baseline source allows paired runs without reverting the
    # worktree. It is imported only in this explicit opt-in synthetic benchmark.
    source = os.getenv('PROFILE_LATENCY_BASELINE_SOURCE')
    profiles = current_profiles
    if source:
        spec = importlib.util.spec_from_file_location('profile_latency_baseline', source)
        profiles = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(profiles)
    homes = write_layout(tmp_path / 'synthetic', profile_count=4, skills_per_profile=120)
    install_core_doubles(monkeypatch, homes[0])
    monkeypatch.setattr(profiles, '_INITIAL_ISOLATED_PROFILE_OPT_IN', '')
    monkeypatch.setattr(profiles, '_INITIAL_HERMES_HOME', str(homes[0]))
    monkeypatch.setattr(profiles, '_SKILLS_STATS_CACHE', {})
    monkeypatch.setattr(profiles, '_LIST_PROFILES_CACHE', None)
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'default')
    result = {'fixture': {'profiles': 4, 'skills_per_profile': 120, 'nested_categories': 8,
                          'core_boundary': 'synthetic doubles; not installed core'}, 'modes': {}}
    for mode in ['cold_stats', 'row_refresh', 'list_cache_hit']:
        samples = []
        counts = []
        for _ in range(7):
            if mode == 'cold_stats':
                profiles._SKILLS_STATS_CACHE.clear()
            if mode != 'list_cache_hit':
                profiles._invalidate_list_profiles_cache()
            with patch.object(profiles, '_skill_tree_max_mtime_ns',
                              wraps=profiles._skill_tree_max_mtime_ns) as probe:
                start = time.perf_counter()
                rows = profiles.list_profiles_api()
                samples.append((time.perf_counter() - start) * 1000)
                counts.append(probe.call_count)
            assert len(rows) == 4 and all(r['total_skills'] == 120 for r in rows)
        # Attribute work separately: cProfile overhead is not wall-clock latency.
        if mode == 'cold_stats':
            profiles._SKILLS_STATS_CACHE.clear()
        if mode != 'list_cache_hit':
            profiles._invalidate_list_profiles_cache()
        profiler = cProfile.Profile()
        profiler.runcall(profiles.list_profiles_api)
        stats = pstats.Stats(profiler)
        top = sorted(stats.stats.items(), key=lambda item: item[1][3], reverse=True)[:20]
        result['modes'][mode] = {'samples_ms': samples, 'median_ms': statistics.median(samples),
            'probe_calls': counts, 'separate_profile_top_cumulative': [
                {'function': f'{Path(key[0]).name}:{key[1]}:{key[2]}',
                 'calls': value[1], 'self_s': value[2], 'cumulative_s': value[3]}
                for key, value in top]}
    target = Path(os.environ['PROFILE_LATENCY_BENCHMARK_JSON'])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v['median_ms'] for k, v in result['modes'].items()}))
