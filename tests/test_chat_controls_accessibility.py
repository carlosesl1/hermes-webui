"""Opt-in real-browser gate; invoke through scripts/test.sh with a browser Python."""
import json
import os
from pathlib import Path
import subprocess

import pytest


@pytest.fixture(scope='module')
def browser_results(tmp_path_factory):
    python = os.environ.get('CHAT_A11Y_BROWSER_PYTHON')
    if not python:
        pytest.skip('set CHAT_A11Y_BROWSER_PYTHON to a Python with Playwright installed')
    artifacts = Path(os.environ.get('CHAT_A11Y_ARTIFACT_DIR', tmp_path_factory.mktemp('chat-a11y')))
    env = dict(os.environ, CHAT_A11Y_ARTIFACT_DIR=str(artifacts))
    result = subprocess.run([python, str(Path(__file__).with_name('browser_chat_controls_accessibility.py'))], env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    return {row['case']: row for row in json.loads((artifacts / 'results.json').read_text())}


@pytest.mark.parametrize('width', [1440, 522, 390])
@pytest.mark.parametrize('case', ['reasoning', 'panel', 'busy'])
def test_chat_control_accessibility(browser_results, width, case):
    row = browser_results[f'{width}-{case}']
    assert row['passed'], row
