"""English-owned new copy follows docs/GUIDELINES.md's canonical fallback rule.

Keep this explicit: existing locale parity must still catch unrelated omissions.
The runtime checks below verify these exceptions instead of merely ignoring them.
"""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


NEW_COPY_FALLBACK_KEYS = {
    "background_activity_title",
    "background_activity_running",
    "background_activity_available",
    "background_activity_failure",
    "bg_tasks",
    "bg_task",
    "bg_status_running",
    "bg_status_done",
    "bg_status_error",
    "bg_status_no_response",
    "bg_status_interrupted",
    "bg_status_cancelled",
    "bg_status_unknown",
    "bg_task_running",
    "composer_description_steer",
    "composer_description_queue",
    "composer_description_interrupt",
    "composer_description_stop",
    "history_paging_changed",
    "history_preview_notice",
    "history_preview_expand",
    "history_preview_error",
}


@pytest.mark.parametrize("mutation", ["none", "missing_english", "broken_fallback"])
def test_new_copy_resolves_through_real_locale_fallback(mutation):
    """Exercise all locales; mutations prove missing English/fallback still fail."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the locale runtime checks")
    source = (Path(__file__).resolve().parents[1] / "static" / "i18n.js").read_text(
        encoding="utf-8"
    )
    if mutation == "broken_fallback":
        assert "_locale[key] ?? LOCALES.en[key]" in source
        source = source.replace("_locale[key] ?? LOCALES.en[key]", "_locale[key]")
    if mutation == "missing_english":
        source += "\ndelete LOCALES.en.history_preview_notice;\n"
    checks = """
const assert = require('node:assert/strict');
for (const key of keys) {
  assert.equal(typeof LOCALES.en[key], 'string', `English copy missing: ${key}`);
  assert.ok(LOCALES.en[key].trim(), `Empty English copy: ${key}`);
}
for (const lang of Object.keys(LOCALES)) {
  setLocale(lang);
  for (const key of keys) {
    const locale = LOCALES[lang];
    const expected = locale[key] ?? LOCALES.en[key];
    assert.equal(t(key), expected, `${lang}: ${key}`);
    assert.notEqual(t(key), key, `${lang}: untranslated key ${key}`);
    if (lang === 'en') continue;
    const hadOwn = Object.hasOwn(locale, key);
    const saved = locale[key];
    try {
      delete locale[key];
      assert.equal(t(key), LOCALES.en[key], `${lang}: absent ${key}`);
      locale[key] = null;
      assert.equal(t(key), LOCALES.en[key], `${lang}: null ${key}`);
      locale[key] = 'Localized override';
      assert.equal(t(key), 'Localized override', `${lang}: override ${key}`);
    } finally {
      if (hadOwn) locale[key] = saved;
      else delete locale[key];
    }
  }
  assert.equal(t('__unknown_locale_test_key__'), '__unknown_locale_test_key__');
}
"""
    harness = """
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
vm.runInNewContext(input.source + '\\n' + input.checks, {
  require,
  keys: input.keys,
  document: { documentElement: {} },
  localStorage: { setItem() {} },
});
"""
    result = subprocess.run(
        [node, "-e", harness],
        input=json.dumps({"source": source, "checks": checks, "keys": sorted(NEW_COPY_FALLBACK_KEYS)}),
        text=True,
        capture_output=True,
        timeout=15,
    )
    if mutation == "none":
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0, f"Runtime guard did not detect {mutation}"
        assert "AssertionError" in result.stderr, result.stderr
