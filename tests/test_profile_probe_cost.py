"""Profile probe operation counts, including optional source-only core parity.

PROFILE_PROBE_CORE_SOURCE may point to agent/skill_utils.py. Only the named
stdlib-only helper definitions/constants are compiled; no core imports/boot.
"""
import ast
from collections import Counter
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, Optional, Tuple

import pytest
import yaml

from api import profiles
from tests.profile_latency_fixture import install_core_doubles, write_layout


def _install_org_helpers(monkeypatch):
    su = sys.modules['agent.skill_utils']
    su.ORG_MIRROR_DIR_NAME = '_org'
    su.ORG_ACTIVE_MARKER = '.active_org'

    def active(root):
        marker = root / '_org' / '.active_org'
        try:
            return (marker.read_text(encoding='utf-8-sig').strip() or None) if marker.exists() else None
        except OSError:
            return None

    def index(root, filename):
        active_org = active(root)
        matches = []
        for folder, dirs, files in os.walk(str(root), followlinks=True):
            if folder == str(root) and '_org' in dirs and active_org is None:
                dirs.remove('_org')
            elif folder == str(root / '_org'):
                dirs[:] = [d for d in dirs if d == active_org]
            dirs[:] = [d for d in dirs if d not in su.EXCLUDED_SKILL_DIRS
                       and not ('SKILL.md' in files and d in su.SKILL_SUPPORT_DIRS)]
            if filename in files:
                matches.append(os.path.join(folder, filename))
        yield from map(Path, sorted(matches))

    su.read_active_org_id = active
    su.iter_skill_index_files = index
    source = os.getenv('PROFILE_PROBE_CORE_SOURCE')
    if source:
        # Reviewed helper semantics from core e1ab5577. Explicit allowlist avoids
        # executing import-time installers, registry discovery, or real home access.
        names = {'EXCLUDED_SKILL_DIRS', 'SKILL_SUPPORT_DIRS', 'ORG_MIRROR_DIR_NAME',
                 'ORG_ACTIVE_MARKER', 'PLATFORM_MAP', 'read_active_org_id',
                 'iter_skill_index_files', 'parse_frontmatter',
                 'skill_matches_platform', 'skill_matches_platform_list'}
        tree = ast.parse(Path(source).read_text(encoding='utf-8'))
        nodes = [node for node in tree.body if
                 (isinstance(node, ast.FunctionDef) and node.name in names) or
                 (isinstance(node, ast.Assign) and any(
                     isinstance(t, ast.Name) and t.id in names for t in node.targets))]
        ns = dict(os=os, Path=Path, re=re, sys=sys, Optional=Optional,
                  Tuple=Tuple, Dict=Dict, Any=Any, yaml_load=yaml.safe_load)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), source, 'exec'), ns)
        assert names <= ns.keys()
        for name in names:
            monkeypatch.setattr(su, name, ns[name], raising=False)
    return su


@pytest.fixture
def tree(tmp_path, monkeypatch):
    home = write_layout(tmp_path / 'home', profile_count=1, skills_per_profile=2)[0]
    install_core_doubles(monkeypatch, home)
    su = _install_org_helpers(monkeypatch)
    org = home / 'skills' / '_org'
    for i in range(100):
        skill = org / f'org-{i}' / 'skill'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text(f'---\nname: org-skill-{i}\n---\n')
    monkeypatch.setattr(profiles, '_SKILLS_STATS_CACHE', {})
    monkeypatch.setattr(profiles, '_SKILLS_STATS_LOCKS', {})
    return home, su


@pytest.mark.parametrize('active_org,expected_dirs', [(None, 7), ('org-0', 10)])
def test_probe_prunes_exactly_like_core(tree, monkeypatch, active_org, expected_dirs):
    home, su = tree
    if active_org:
        (home / 'skills/_org/.active_org').write_text(active_org)
    counts = Counter()
    scan, resolve = os.scandir, Path.resolve

    def counted_scan(path):
        counts['scandir'] += 1
        return scan(path)

    def counted_resolve(path, *args, **kwargs):
        counts['resolve'] += 1
        return resolve(path, *args, **kwargs)

    monkeypatch.setattr(os, 'scandir', counted_scan)
    monkeypatch.setattr(Path, 'resolve', counted_resolve)
    indexed = list(su.iter_skill_index_files(home / 'skills', 'SKILL.md'))
    core_counts = dict(counts)
    counts.clear()
    profiles._skill_tree_max_mtime_ns(home / 'skills', home / 'config.yaml')
    print({'active': active_org, 'core': core_counts, 'probe': dict(counts)})
    assert len(indexed) == (3 if active_org else 2)
    assert core_counts == {'scandir': expected_dirs}
    assert dict(counts) == core_counts


def test_inactive_org_edits_do_not_invalidate_stats(tree):
    home, _ = tree
    skills = home / 'skills'
    before = profiles._skill_tree_max_mtime_ns(skills, home / 'config.yaml')
    ignored = skills / '_org/org-99/skill/SKILL.md'
    future = time.time_ns() + 50_000_000_000
    os.utime(ignored, ns=(future, future))
    assert profiles._skill_tree_max_mtime_ns(skills, home / 'config.yaml') == before


@pytest.mark.parametrize('symlink_root', [False, True])
def test_org_marker_switch_edit_delete_and_symlink(tree, symlink_root):
    home, _ = tree
    if symlink_root:
        target = home.parent / 'linked-skills'
        (home / 'skills').rename(target)
        (home / 'skills').symlink_to(target, target_is_directory=True)
    marker = home / 'skills/_org/.active_org'
    assert profiles._get_profile_skills_stats(home) == (1, 2)
    future = time.time_ns() + 100_000_000_000
    marker.write_text('\ufefforg-0\n', encoding='utf-8')
    os.utime(marker, ns=(future, future))
    assert profiles._get_profile_skills_stats(home) == (2, 3)
    skill = home / 'skills/_org/org-0/skill/SKILL.md'
    skill.write_text('---\nname: skill-0\n---\n')
    os.utime(skill, ns=(future + 1, future + 1))
    assert profiles._get_profile_skills_stats(home) == (1, 2)
    # Active org may be a symlink, as may the skills root itself.
    link = home / 'skills/_org/alias'
    link.symlink_to(home / 'skills/_org/org-1', target_is_directory=True)
    marker.write_text('alias')
    os.utime(marker, ns=(future + 2, future + 2))
    assert profiles._get_profile_skills_stats(home) == (2, 3)
    marker.unlink()
    os.utime(marker.parent, ns=(future + 3, future + 3))
    assert profiles._get_profile_skills_stats(home) == (1, 2)


def test_older_core_without_org_helpers_keeps_original_walk(tree, monkeypatch):
    home, su = tree
    monkeypatch.delattr(su, 'read_active_org_id')
    future = time.time_ns() + 50_000_000_000
    os.utime(home / 'skills/_org/org-99/skill/SKILL.md', ns=(future, future))
    assert profiles._skill_tree_max_mtime_ns(home / 'skills', home / 'config.yaml') == future
