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
    "bg_history",
    "message_preview_expand",
    "message_preview_loading",
    "composer_description_steer",
    "composer_description_queue",
    "composer_description_interrupt",
    "composer_description_stop",
    "history_paging_changed",
    "history_preview_notice",
    "history_preview_expand",
    "history_preview_error",
}


CALLABLE_FALLBACK_KEYS = {"bg_history"}


@pytest.mark.parametrize("mutation", [
    "none", "missing_english", "broken_fallback", "missing_callable",
    "bad_callable", "broken_callable_dispatch",
])
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
    if mutation == "missing_callable":
        source += "\ndelete LOCALES.en.bg_history;\n"
    if mutation == "bad_callable":
        source += "\nLOCALES.en.bg_history = () => 'Background activity · 0 completed';\n"
    if mutation == "broken_callable_dispatch":
        assert "return val(...args)" in source
        source = source.replace("return val(...args)", "return val()")
    checks = """
const assert = require('node:assert/strict');
const callableKeys = new Set(inputCallableKeys);
assert.ok([...callableKeys].every(key => keys.includes(key)));
const render = (value, args) => typeof value === 'function' ? value(...args) : value;
for (const key of keys) {
  const kind = callableKeys.has(key) ? 'function' : 'string';
  assert.equal(typeof LOCALES.en[key], kind, `English copy missing: ${key}`);
  if (kind === 'string') assert.ok(LOCALES.en[key].trim(), `Empty English copy: ${key}`);
}
for (const count of [0, 1, 3]) {
  assert.equal(LOCALES.en.bg_history(count), `Background activity · ${count} completed`,
    `English callable behavior: bg_history(${count})`);
}
for (const lang of Object.keys(LOCALES)) {
  setLocale(lang);
  for (const key of keys) {
    const callable = callableKeys.has(key);
    for (const args of callable ? [[0], [1], [3]] : [[]]) {
    const locale = LOCALES[lang];
    const value = locale[key] ?? LOCALES.en[key];
    assert.equal(typeof value, callable ? 'function' : 'string', `${lang}: type ${key}`);
    const expected = render(value, args);
    assert.equal(typeof expected, 'string', `${lang}: result type ${key}`);
    assert.ok(expected.trim(), `${lang}: empty ${key}`);
    assert.equal(t(key, ...args), expected, `${lang}: ${key}(${args})`);
    assert.notEqual(t(key, ...args), key, `${lang}: untranslated key ${key}`);
    if (lang === 'en') continue;
    const hadOwn = Object.hasOwn(locale, key);
    const saved = locale[key];
    try {
      delete locale[key];
      assert.equal(t(key, ...args), render(LOCALES.en[key], args), `${lang}: absent ${key}`);
      locale[key] = null;
      assert.equal(t(key, ...args), render(LOCALES.en[key], args), `${lang}: null ${key}`);
      locale[key] = callable ? count => `Localized override ${count}` : 'Localized override';
      const override = callable ? `Localized override ${args[0]}` : 'Localized override';
      assert.equal(t(key, ...args), override, `${lang}: override ${key}`);
    } finally {
      if (hadOwn) locale[key] = saved;
      else delete locale[key];
    }
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
  inputCallableKeys: input.callableKeys,
  document: { documentElement: {} },
  localStorage: { setItem() {} },
});
"""
    result = subprocess.run(
        [node, "-e", harness],
        input=json.dumps({
            "source": source, "checks": checks,
            "keys": sorted(NEW_COPY_FALLBACK_KEYS),
            "callableKeys": sorted(CALLABLE_FALLBACK_KEYS),
        }),
        text=True,
        capture_output=True,
        timeout=15,
    )
    if mutation == "none":
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0, f"Runtime guard did not detect {mutation}"
        assert "AssertionError" in result.stderr, result.stderr
