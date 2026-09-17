"""Bounded memo regression tests; all credentials are synthetic."""
from concurrent.futures import ThreadPoolExecutor
import sys

import pytest
from api import helpers


def test_large_fixed_redactor_is_memoized():
    memo = helpers._BoundedTextMemo(max_entries=4, max_bytes=300000, max_entry_bytes=150000)
    calls = []
    text = "ordinary text " * 2000
    rule = object()
    for _ in range(2):
        assert memo.apply(text, rule, lambda value: calls.append(1) or value) == text
    assert calls == [1]


def test_live_redactor_rotation_never_reuses_stale_results(monkeypatch):
    state = {"mask": "first"}
    monkeypatch.setattr(helpers, "_redact_fn_uncached", lambda text: state["mask"])
    assert helpers._redact_fn_cached("synthetic") == "first"
    state["mask"] = "rotated"
    assert helpers._redact_fn_cached("synthetic") == "rotated"


def test_budget_eviction_unicode_oversize_and_rule_rotation():
    memo = helpers._BoundedTextMemo(max_entries=2, max_bytes=4000, max_entry_bytes=3000)
    first, second = object(), object()
    assert memo.apply("same", first, lambda _: "old") == "old"
    assert memo.apply("same", second, lambda _: "new") == "new"
    for i in range(20):
        text = str(i) + "🙂" * 100
        assert memo.apply(text, first, lambda value: value) == text
        assert memo.bytes <= 4000
        assert len(memo.entries) <= 2
    before = memo.bytes
    text = "🙂" * 10000
    assert memo.apply(text, first, lambda value: value) == text
    assert memo.bytes == before
    assert all(isinstance(key, bytes) for key in memo.entries)
    assert memo.bytes == sum(entry[-1] for entry in memo.entries.values())
    assert memo.bytes >= sum(sys.getsizeof(entry[1]) for entry in memo.entries.values())


def test_concurrent_budget_and_rule_identity():
    memo = helpers._BoundedTextMemo(max_entries=5, max_bytes=8000, max_entry_bytes=4000)
    rules = [object(), object()]
    def run(i):
        text = str(i % 11) + " plain" * 40
        expected = text + str(i % 2)
        return memo.apply(text, rules[i % 2], lambda _: expected) == expected
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(run, range(500)))
    assert len(memo.entries) <= 5
    assert memo.bytes <= 8000
    assert memo.bytes == sum(entry[-1] for entry in memo.entries.values())


def test_digest_collision_is_not_a_hit(monkeypatch):
    memo = helpers._BoundedTextMemo(max_entries=5, max_bytes=8000, max_entry_bytes=4000)
    monkeypatch.setattr(memo, "_key", lambda text: b"collision")
    rule = object()
    assert memo.apply("first", rule, str.upper) == "FIRST"
    assert memo.apply("second", rule, str.upper) == "SECOND"


@pytest.mark.parametrize("enabled", [True, False])
def test_projection_has_no_mutable_aliases(monkeypatch, enabled):
    monkeypatch.setattr("api.config.load_settings", lambda: {"api_redact_enabled": enabled})
    source = {"messages": [{"role": "user", "content": [{"type": "text", "text": "plain", "_row_id": 1}], "tool_calls": [{"function": {"arguments": {"_row_id": [1]}, "api_content": "private"}}]}], "todo_state": {"items": [{"text": "plain"}]}}
    first = helpers.redact_session_data(source)
    second = helpers.redact_session_data(source)
    def mutable_ids(value):
        if isinstance(value, dict):
            return {id(value)}.union(*(mutable_ids(v) for v in value.values()))
        if isinstance(value, list):
            return {id(value)}.union(*(mutable_ids(v) for v in value))
        return set()
    assert mutable_ids(first).isdisjoint(mutable_ids(source))
    assert mutable_ids(first).isdisjoint(mutable_ids(second))
    assert "_row_id" not in first["messages"][0]["content"][0]
    assert "api_content" not in first["messages"][0]["tool_calls"][0]["function"]
    first["messages"][0]["tool_calls"][0]["function"]["arguments"]["_row_id"].append(2)
    first["todo_state"]["items"][0]["text"] = "changed"
    assert second == helpers.redact_session_data(source)
    assert source["messages"][0]["tool_calls"][0]["function"]["arguments"]["_row_id"] == [1]


def test_prefilter_rules_rotate_without_stale_negative(monkeypatch):
    text = "unique ordinary marker zzzfixture"
    assert not helpers._might_contain_sensitive_text(text)
    monkeypatch.setattr(helpers, "_SENSITIVE_CASE_MARKERS", ("zzzfixture",))
    assert helpers._might_contain_sensitive_text(text)


def test_prefilter_exact_equivalence():
    samples = ["", "clean " * 5000, "\ud800", "İK🙂", "123456789:" + "x" * 35,
               '<@123456789012345678>', '+12345678901', 'password=example',
               '-----BEGIN PRIVATE KEY-----example-----END PRIVATE KEY-----']
    samples += ["before " + marker + "syntheticXYZ1234567890" for marker in helpers._SENSITIVE_CASE_MARKERS + helpers._SENSITIVE_LOWER_MARKERS]
    rules = helpers._sensitive_rules()
    for sample in samples:
        assert helpers._might_contain_sensitive_text(sample) == helpers._might_contain_sensitive_text_uncached(sample, rules)


def test_rule_equality_cannot_impersonate_identity():
    class EqualRule:
        def __eq__(self, other):
            return True
    memo = helpers._BoundedTextMemo()
    assert memo.apply("same", EqualRule(), lambda _: "old") == "old"
    assert memo.apply("same", EqualRule(), lambda _: "new") == "new"


def test_mutable_results_are_not_cached():
    memo = helpers._BoundedTextMemo()
    scope = object()
    first = memo.apply("same", scope, lambda _: [1])
    first.append(2)
    assert memo.apply("same", scope, lambda _: [1]) == [1]
    assert not memo.entries


def test_strict_budget_includes_mapping_after_churn():
    memo = helpers._BoundedTextMemo(max_entries=100, max_bytes=5000)
    scope = object()
    for i in range(400):
        memo.apply(str(i) + "🙂" * (i % 40), scope, str.upper)
        assert memo.bytes + sys.getsizeof(memo.entries) <= memo.max_bytes
    before = memo.bytes
    memo.apply("huge output", scope, lambda _: "🙂" * 10000)
    assert memo.bytes == before


@pytest.mark.parametrize("name", ["_SENSITIVE_TELEGRAM_MARKER_RE", "_SENSITIVE_DISCORD_MARKER_RE", "_SENSITIVE_PHONE_MARKER_RE"])
def test_custom_regex_needs_no_default_delimiter(monkeypatch, name):
    import re
    text = "Kustom marker"
    assert not helpers._might_contain_sensitive_text(text)
    monkeypatch.setattr(helpers, name, re.compile("kustom", re.IGNORECASE))
    assert helpers._might_contain_sensitive_text(text)


def test_mutable_custom_search_bypasses_negative_memo(monkeypatch):
    class Search:
        enabled = False
        def search(self, text):
            return self.enabled
    search = Search()
    monkeypatch.setattr(helpers, "_SENSITIVE_PHONE_MARKER_RE", search)
    assert not helpers._might_contain_sensitive_text("ordinary fixture")
    search.enabled = True
    assert helpers._might_contain_sensitive_text("ordinary fixture")


def test_agent_live_environment_rotation(monkeypatch):
    import os
    import types
    module = types.ModuleType("agent.redact")
    module.redact_sensitive_text = lambda text, **kw: text.replace(os.environ["SYNTHETIC_REDACTION_KEY"], "[masked]")
    monkeypatch.setitem(sys.modules, "agent.redact", module)
    monkeypatch.setattr(helpers, "_redact_fn_uncached", helpers._build_redact_fn())
    text = "token=fixture_alpha fixture_beta"
    monkeypatch.setenv("SYNTHETIC_REDACTION_KEY", "fixture_alpha")
    first = helpers._redact_text(text, _enabled=True)
    monkeypatch.setenv("SYNTHETIC_REDACTION_KEY", "fixture_beta")
    second = helpers._redact_text(text, _enabled=True)
    assert first != second
    assert "fixture_beta" not in second


def test_prefilter_unicode_matches_original_algorithm():
    rules = helpers._sensitive_rules()
    def original(text):
        return (any(m in text for m in rules[0])
                or any(m in text.lower() for m in rules[1])
                or bool(":" in text and rules[2].search(text))
                or bool("<@" in text and rules[3].search(text))
                or bool("+" in text and rules[4].search(text)))
    samples = ["İ" * 18000, "🙂" * 18000, "\ud800" * 18000,
               "١٢٣٤٥٦٧٨:" + "x" * 35, "<@１２３４５６７８９０１２３４５６７８>",
               "+1١٢٣٤٥٦٧٨", "Authorization: Bearer synthetic_fixture"]
    for text in samples:
        assert helpers._might_contain_sensitive_text(text) == original(text)


def test_synthetic_projection_benchmark(monkeypatch, request):
    import json
    from statistics import median
    from time import perf_counter
    monkeypatch.setattr("api.config.load_settings", lambda: {"api_redact_enabled": True})
    payload = {"messages": [{"role": "assistant", "content": ("ordinary text " * 2000) + str(i),
                             "tool_calls": [{"function": {"arguments": {"token": "sk-" + "X" * 30}}}]} for i in range(24)]}
    memo = helpers._BoundedTextMemo()
    monkeypatch.setattr(helpers, "_redact_memo", memo)
    expected = helpers.redact_session_data(payload)
    def measure():
        values = []
        for _ in range(5):
            start = perf_counter()
            assert helpers.redact_session_data(payload) == expected
            values.append(perf_counter() - start)
        return median(values)
    warm = measure()
    with monkeypatch.context() as uncached:
        uncached.setattr(memo, "apply", lambda text, rules, transform: transform(text))
        cold = measure()
    metrics = {"messages": 24, "input_chars": sum(len(m["content"]) for m in payload["messages"]),
               "uncached_median_ms": round(cold * 1000, 3), "warm_median_ms": round(warm * 1000, 3),
               "speedup": round(cold / warm, 2), "retained_charge_bytes": memo.bytes,
               "mapping_bytes": sys.getsizeof(memo.entries), "entries": len(memo.entries)}
    request.config.pluginmanager.getplugin("terminalreporter").write_line("REDACTION_BENCHMARK " + json.dumps(metrics))
