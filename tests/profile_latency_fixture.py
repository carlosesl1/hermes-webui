"""Synthetic-only profile layout and explicit core-boundary doubles.

The benchmark measures WebUI functions, not installed Hermes core internals.
No installed core or user profile data is imported/read by this fixture.
"""
import os
import re
import sys
import types
from pathlib import Path

import yaml

EXCLUDED = frozenset({'.git', '.venv', 'node_modules', 'site-packages'})
SUPPORT = frozenset({'references', 'scripts', 'assets', 'templates'})


def write_layout(base, profile_count=4, skills_per_profile=120):
    homes = [base] + [base / 'profiles' / f'profile-{i}' for i in range(1, profile_count)]
    for index, home in enumerate(homes):
        home.mkdir(parents=True, exist_ok=True)
        config = {'model': {'default': f'model-{index}', 'provider': 'synthetic'},
                  'skills': {'disabled': ['skill-0']},
                  'settings': {f'group-{i}': {'enabled': True, 'values': list(range(8))}
                               for i in range(80)}}
        (home / 'config.yaml').write_text(yaml.safe_dump(config), encoding='utf-8')
        (home / 'profile.yaml').write_text('visible: true\n', encoding='utf-8')
        for i in range(skills_per_profile):
            skill = home / 'skills' / f'category-{i % 8}' / 'nested' / f'skill-{i}'
            skill.mkdir(parents=True)
            (skill / 'SKILL.md').write_text(
                f'---\nname: skill-{i}\ndescription: Synthetic skill {i}\n'
                'metadata:\n  tags: [test, synthetic]\n---\n' + '# Guide\nExample content.\n' * 240,
                encoding='utf-8')
            support = skill / 'references' / 'vendor' / 'nested'
            support.mkdir(parents=True)
            (support / 'SKILL.md').write_text('not an indexed skill', encoding='utf-8')
    return homes


def install_core_doubles(monkeypatch, base):
    """Cheap helper ABI checked against core e1ab5577; cache model by stat identity."""
    skills = types.ModuleType('agent.skill_utils')
    skills.EXCLUDED_SKILL_DIRS = EXCLUDED
    skills.SKILL_SUPPORT_DIRS = SUPPORT

    def iter_files(root, filename):
        for folder, dirs, files in os.walk(root, followlinks=True):
            dirs[:] = [d for d in dirs if d not in EXCLUDED
                       and not ('SKILL.md' in files and d in SUPPORT)]
            if filename in files:
                yield Path(folder) / filename

    def parse(content):
        if content.startswith('---\n'):
            return yaml.safe_load(content.split('---', 2)[1]) or {}, ''
        return {}, content

    skills.iter_skill_index_files = iter_files
    skills.parse_frontmatter = parse
    skills.skill_matches_platform = lambda meta: meta.get('platforms') != ['incompatible']
    cli = types.ModuleType('hermes_cli.profiles')
    cli._get_default_hermes_home = lambda: base
    cli._get_profiles_root = lambda: base / 'profiles'
    cli._PROFILE_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')
    cli._check_gateway_running = lambda home: (home / 'gateway.synthetic').exists()
    cache = {}

    def model(home):
        path = home / 'config.yaml'
        stat = path.stat()
        signature = stat.st_mtime_ns, stat.st_size, stat.st_ino
        if path not in cache or cache[path][0] != signature:
            cfg = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
            value = cfg.get('model', {})
            cache[path] = signature, ((value, None) if isinstance(value, str) else
                                     (value.get('default'), value.get('provider')))
        return cache[path][1]

    cli._read_config_model = model
    agent = types.ModuleType('agent')
    agent.skill_utils = skills
    hermes_cli = types.ModuleType('hermes_cli')
    hermes_cli.profiles = cli
    for name, module in [('agent', agent), ('agent.skill_utils', skills),
                         ('hermes_cli', hermes_cli), ('hermes_cli.profiles', cli)]:
        monkeypatch.setitem(sys.modules, name, module)
    return cli
