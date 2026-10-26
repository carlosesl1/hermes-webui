"""Upstream duration recovery regressions, isolated from frontend ownership."""
import base64
import types
import json
import sys
from tests.test_provider_quota_status import _FakeResponse
from types import SimpleNamespace
import pytest
from api import providers as providers_mod

def test_codex_singleton_probe_sends_residency_and_keeps_account_override(monkeypatch):
    """The singleton probe must send the residency header the installed Agent sends.

    ``chatgpt_data_residency`` wins over ``chatgpt_compute_residency``. The explicit
    account id from credential resolution still replaces the JWT account id.
    """
    import api.providers as providers

    def b64url(payload: bytes) -> str:
        return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")

    def token_for(auth: dict) -> str:
        return ".".join((
            b64url(b'{"alg":"none","typ":"JWT"}'),
            b64url(json.dumps({"https://api.openai.com/auth": auth}).encode("utf-8")),
            b64url(b"signature"),
        ))

    agent_mod = types.ModuleType("agent")
    agent_mod.__path__ = []
    account_usage_mod = types.ModuleType("agent.account_usage")
    account_usage_mod.fetch_account_usage = lambda *_a, **_k: None
    monkeypatch.setitem(sys.modules, "agent", agent_mod)
    monkeypatch.setitem(sys.modules, "agent.account_usage", account_usage_mod)

    source = providers._ACCOUNT_USAGE_SUBPROCESS_CODE
    definitions = source.split("if len(sys.argv) > 1")[0]
    namespace = {"__name__": "__not_main__"}
    exec(definitions, namespace)

    seen = []

    def fake_urlopen(req, timeout):
        seen.append((req, timeout))
        payload = {
            "plan_type": "pro",
            "rate_limit": {
                "primary_window": {
                    "used_percent": 15,
                    "reset_at": "2030-03-17T17:30:00Z",
                    "limit_window_seconds": 18_000,
                },
            },
        }
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(namespace["urllib_request"], "urlopen", fake_urlopen)

    def install(token, account_id):
        def resolve(base_url, api_key):
            assert base_url is None
            assert api_key is None
            return token, "https://chatgpt.com/backend-api/codex", account_id
        account_usage_mod._resolve_codex_usage_credentials = resolve

    def probe():
        seen.clear()
        snapshot = namespace["_fetch_codex_singleton_snapshot"](None)
        assert snapshot is not None
        assert len(seen) == 1
        request, timeout = seen[0]
        assert request.full_url == "https://chatgpt.com/backend-api/wham/usage"
        assert timeout == 4.0
        return {key.lower(): value for key, value in request.header_items()}

    install(token_for({
        "chatgpt_account_id": "acct-from-jwt",
        "chatgpt_data_residency": " eu ",
        "chatgpt_compute_residency": "us",
    }), "acct-override")
    headers = probe()
    assert headers["x-openai-internal-codex-residency"] == "eu"
    assert headers["chatgpt-account-id"] == "acct-override"
    assert headers["authorization"].startswith("Bearer ")

    install(token_for({
        "chatgpt_account_id": "acct-from-jwt",
        "chatgpt_data_residency": "",
        "chatgpt_compute_residency": "  us  ",
    }), None)
    headers = probe()
    assert headers["x-openai-internal-codex-residency"] == "us"
    assert headers["chatgpt-account-id"] == "acct-from-jwt"

    install(token_for({"chatgpt_account_id": "acct-from-jwt"}), "acct-override")
    headers = probe()
    assert "x-openai-internal-codex-residency" not in headers
    assert headers["chatgpt-account-id"] == "acct-override"

    install(token_for({
        "chatgpt_data_residency": "   ",
        "chatgpt_compute_residency": "eu",
    }), "acct-override")
    headers = probe()
    assert "x-openai-internal-codex-residency" not in headers
    assert headers["chatgpt-account-id"] == "acct-override"

def test_codex_singleton_backfill_matches_windows_by_label_not_position(monkeypatch):
    """The Agent object and the WebUI probe are two separate requests.

    If the Agent skipped a window (used_percent missing) its list is shorter, so a
    positional join would hand the Session duration to the Weekly window. That is the
    same mislabeling this change exists to prevent, so the join is keyed on label.
    """
    import api.providers as providers
    from types import SimpleNamespace

    # The embedded worker imports the Agent at module scope; stub it so the
    # source can be exec'd here the same way the other worker tests do.
    agent_pkg = types.ModuleType("agent")
    account_usage_mod = types.ModuleType("agent.account_usage")
    account_usage_mod.fetch_account_usage = lambda *_a, **_k: None
    monkeypatch.setitem(sys.modules, "agent", agent_pkg)
    monkeypatch.setitem(sys.modules, "agent.account_usage", account_usage_mod)

    # Exec only the definitions: the worker source ends with a CLI driver that
    # reads sys.argv and would run a real fetch.
    source = providers._ACCOUNT_USAGE_SUBPROCESS_CODE
    definitions = source.split('if len(sys.argv) > 1')[0]
    assert "_codex_singleton_payload" in definitions
    namespace = {"__name__": "__not_main__"}
    exec(definitions, namespace)

    # Agent dropped the Session window entirely and carries no duration.
    agent_snapshot = SimpleNamespace(
        provider="openai-codex",
        windows=(SimpleNamespace(label="Weekly", used_percent=42.0, reset_at=None, detail=None),),
    )
    # The WebUI's own probe still sees both windows, Session first.
    raw_snapshot = SimpleNamespace(
        provider="openai-codex",
        windows=(
            SimpleNamespace(
                label="Session", used_percent=7.0, reset_at=None, detail=None,
                limit_window_seconds=18_000,
            ),
            SimpleNamespace(
                label="Weekly", used_percent=42.0, reset_at=None, detail=None,
                limit_window_seconds=604_800,
            ),
        ),
    )
    namespace["_fetch_codex_singleton_snapshot"] = lambda *_a, **_k: raw_snapshot

    payload = namespace["_codex_singleton_payload"](agent_snapshot)
    windows = payload["windows"]

    assert [window["label"] for window in windows] == ["Weekly"]
    # A positional join would have written 18000 here, mislabeling Weekly as 5-hour.
    assert windows[0]["limit_window_seconds"] == 604_800

    for payload_count, probe_count in ((2, 2), (1, 2), (2, 1)):
        agent_snapshot.windows = (SimpleNamespace(label="Session"),) * payload_count
        raw_snapshot.windows = (raw_snapshot.windows[0],) * probe_count
        payload = namespace["_codex_singleton_payload"](agent_snapshot)
        assert [w["limit_window_seconds"] for w in payload["windows"]] == [None] * payload_count

def test_codex_singleton_raw_durations_survive_failed_probe(monkeypatch):
    """Durations already on snapshot.raw survive a failed usage probe.

    The installed Agent window has no duration field, but snapshot.raw is the
    usage body. Once that backfill supplies every duration the probe must not
    run; a 503 after a partial backfill must not clear the Weekly duration raw
    already supported (Weekly limit / 5-hour limit).
    """
    import api.providers as providers

    agent_pkg = types.ModuleType("agent")
    account_usage_mod = types.ModuleType("agent.account_usage")
    account_usage_mod.fetch_account_usage = lambda *_a, **_k: None
    monkeypatch.setitem(sys.modules, "agent", agent_pkg)
    monkeypatch.setitem(sys.modules, "agent.account_usage", account_usage_mod)

    source = providers._ACCOUNT_USAGE_SUBPROCESS_CODE
    namespace = {"__name__": "__not_main__"}
    exec(source.split("if len(sys.argv) > 1")[0], namespace)

    probe_calls = []

    def fail_probe(*_args, **_kwargs):
        probe_calls.append(1)
        return None

    namespace["_fetch_codex_singleton_snapshot"] = fail_probe

    def _window(label, used):
        return SimpleNamespace(label=label, used_percent=used, reset_at=None, detail=None)

    both = SimpleNamespace(
        provider="openai-codex",
        windows=(_window("Weekly", 40.0), _window("Session", 15.0)),
        raw={
            "rate_limit": {
                "primary_window": {"used_percent": 40, "limit_window_seconds": 604_800},
                "secondary_window": {"used_percent": 15, "limit_window_seconds": 18_000},
            },
        },
    )
    payload = namespace["_codex_singleton_payload"](both)
    assert probe_calls == []
    assert [(w["label"], w["limit_window_seconds"]) for w in payload["windows"]] == [
        ("Weekly", 604_800),
        ("Session", 18_000),
    ]

    duplicate = SimpleNamespace(
        provider="openai-codex", windows=(_window("Session", 42), _window("Session", 7)),
        raw={"rate_limit": {
            "primary_window": {"used_percent": 42, "limit_window_seconds": 86400},
            "secondary_window": {"used_percent": 7, "limit_window_seconds": 18000},
        }},
    )
    payload = namespace["_codex_singleton_payload"](duplicate)
    assert [w["limit_window_seconds"] for w in payload["windows"]] == [86400, 18000]
    assert probe_calls == []

    shifted = SimpleNamespace(
        provider="openai-codex", windows=(_window("Weekly", 40.0), _window("Session", 15.0)),
        raw={"rate_limit": {"secondary_window": {"used_percent": 15, "limit_window_seconds": 18000}}},
    )
    payload = namespace["_codex_singleton_payload"](shifted)
    assert [w["limit_window_seconds"] for w in payload["windows"]] == [None, None]
    probe_calls.clear()

    partial = SimpleNamespace(
        provider="openai-codex",
        windows=(_window("Weekly", 40.0), _window("Session", 15.0)),
        raw={
            "rate_limit": {
                "primary_window": {"used_percent": 40, "limit_window_seconds": 604_800},
            },
        },
    )
    payload = namespace["_codex_singleton_payload"](partial)
    assert probe_calls == [1]
    assert [(w["label"], w["limit_window_seconds"]) for w in payload["windows"]] == [
        ("Weekly", 604_800),
        ("Session", None),
    ]

@pytest.mark.parametrize("seconds,label", [(604800, "Weekly"), (18000, "Session"), (None, "Session"), (float("nan"), "Session"), (float("inf"), "Session")])
def test_weekly_primary_null_secondary_and_nonfinite(monkeypatch, seconds, label):
    agent = types.ModuleType("agent")
    usage = types.ModuleType("agent.account_usage")
    usage.fetch_account_usage = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.account_usage", usage)
    ns = {}
    exec(providers_mod._ACCOUNT_USAGE_SUBPROCESS_CODE.split("if len(sys.argv) > 1")[0], ns)
    raw = {"rate_limit": {"primary_window": {"used_percent": 25, "limit_window_seconds": seconds}, "secondary_window": None}}
    snapshot = ns["_codex_snapshot_from_usage_payload"](raw)
    assert snapshot.windows[0].label == label
    old = SimpleNamespace(provider="openai-codex", windows=(SimpleNamespace(label="Session", used_percent=25, reset_at=None),), raw=raw)
    ns["_fetch_codex_singleton_snapshot"] = lambda *a: None
    payload = ns["_codex_singleton_payload"](old)
    result = providers_mod._serialize_account_usage_snapshot(providers_mod._account_usage_payload_to_snapshot(payload))
    assert result["windows"][0]["label"] == label
    for number in (float("nan"), float("inf"), "-inf", "nan"):
        assert ns["_number"](number) is None
        assert providers_mod._quota_number(number) is None



