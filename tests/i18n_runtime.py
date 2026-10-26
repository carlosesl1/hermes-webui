"""Exercise shipped t()/setLocale(), including English fallback and interpolation."""
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

LOCALES = {"en", "it", "ja", "ru", "es", "de", "zh", "zh-Hant", "pt", "ko", "fr", "cs", "tr", "pl", "vi"}


def assert_locale_contract(keys):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for real i18n runtime coverage")
    script = r"""
const fs = require('fs'), vm = require('vm');
const context = {
  localStorage: {getItem() {return null;}, setItem() {}},
  navigator: {language: 'en', languages: ['en']},
  document: {documentElement: {}, querySelectorAll() {return [];}}, console
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
vm.runInContext(String.raw`
const keys = ${process.argv[2]};
globalThis.result = {};
for (const locale of Object.keys(LOCALES)) {
  setLocale(locale);
  result[locale] = {lang: document.documentElement.lang, values: {}};
  for (const key of keys) {
    const own = LOCALES[locale][key];
    const canonical = LOCALES.en[key];
    const selected = own ?? canonical;
    const raw = typeof selected === 'function' ? selected(2, 3) : selected;
    const expected = typeof selected === 'function' ? raw :
      String(raw).replace(/\{0\}/g, '2').replace(/\{1\}/g, '3');
    result[locale].values[key] = {
      raw, expected, rendered: t(key, 2, 3),
      canonical: typeof canonical === 'function' ? canonical(2, 3) : canonical,
      fallback: own == null
    };
  }
}
`, context);
console.log(JSON.stringify(context.result));
"""
    result = subprocess.run(
        [node, "-e", script, str(Path(__file__).resolve().parents[1] / "static/i18n.js"), json.dumps(list(keys))],
        capture_output=True, text=True, check=True, timeout=15,
    )
    locales = json.loads(result.stdout)
    assert set(locales) == LOCALES
    values = {}
    for locale, record in locales.items():
        assert record["lang"], locale
        values[locale] = {}
        for key, item in record["values"].items():
            canonical = item.get("canonical")
            assert isinstance(canonical, str) and canonical and canonical != key, key
            raw = item.get("raw")
            assert isinstance(raw, str) and raw and raw != key, (locale, key)
            assert sorted(re.findall(r"\{[^{}]+\}", raw)) == sorted(re.findall(r"\{[^{}]+\}", canonical)), (locale, key, raw)
            assert item["rendered"] == item["expected"], (locale, key, item)
            if item["fallback"]:
                assert item["rendered"] == locales["en"]["values"][key]["rendered"], (locale, key)
            values[locale][key] = item["rendered"]
    return values
