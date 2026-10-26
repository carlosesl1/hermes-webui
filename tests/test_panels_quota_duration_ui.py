"""Production Codex window-label regression, ported from upstream #6893."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
def test_codex_quota_window_labels_use_duration_not_position():
    """The production helper must label Codex windows from truthful duration metadata."""
    panels = (ROOT / "static" / "panels.js").read_text(encoding="utf-8")
    start = panels.index("function _formatProviderQuotaWindowLabel")
    end = panels.index("\nfunction _formatProviderQuotaLastChecked", start)
    helper = panels[start:end]
    cases = [
        [{"provider": "openai-codex"}, {"label": "Weekly", "limit_window_seconds": 18_000}],
        [{"provider": "openai-codex"}, {"label": "Session", "limit_window_seconds": 604_800}],
        [{"provider": "openai-codex"}, {"label": "Session"}],
        [{"provider": "openai-codex"}, {"label": "Weekly", "limit_window_seconds": "invalid"}],
        [{"provider": "openai-codex"}, {"label": "Session", "limit_window_seconds": 86_400}],
        [{"provider": "openai-codex"}, {"label": "Daily", "limit_window_seconds": 86_400}],
        [{"provider": "anthropic"}, {"label": "Session", "limit_window_seconds": 604_800}],
        [{"provider": "openai-codex"}, {"label": "Session", "limit_window_seconds": 604800.9}],
    ]
    translations = {
        "provider_quota_session_limit": "5-hour limit",
        "provider_quota_weekly_limit": "Weekly limit",
        "provider_quota_usage_limit": "Usage limit",
        "provider_quota_window_fallback": "Window",
    }
    script = f"""
const translations = {json.dumps(translations)};
function t(key) {{ return translations[key] || key; }}
{helper}
const cases = {json.dumps(cases)};
process.stdout.write(JSON.stringify(cases.map(([limits, window]) =>
  _formatProviderQuotaWindowLabel(limits, window)
)));
"""

    result = subprocess.run(
        [shutil.which("node"), "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == [
        "5-hour limit",
        "Weekly limit",
        "5-hour limit",
        "Usage limit",
        "Usage limit",
        "Daily",
        "Session",
        "Weekly limit",
    ]
