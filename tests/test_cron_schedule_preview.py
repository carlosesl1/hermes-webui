"""UX11: scheduler-owned metadata/preview; no core import, jobs or providers.

The core boundary is doubled deliberately. Browser coverage uses the production
cron form/helpers and offline responses, not a running scheduler.
"""
import io
import json
import sys
import types
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pytest


@pytest.fixture(scope="session", autouse=True)
def test_server():
    """These read-only component tests need no HTTP server."""
    yield


@pytest.fixture
def scheduler(monkeypatch):
    jobs = types.ModuleType("cron.jobs")
    jobs._hermes_now = lambda: datetime(2026, 10, 11, tzinfo=ZoneInfo("America/Sao_Paulo"))
    parsed = {"kind": "cron", "expr": "0 9 * * *", "display": "0 9 * * *"}
    calls = []
    jobs.parse_schedule = lambda value: calls.append(("parse", value)) or parsed
    jobs.compute_next_run = lambda value: calls.append(("next", value)) or "2026-10-11T09:00:00-03:00"
    # Any persistence/execution call is a test failure, not a fake success.
    def forbidden(*args, **kwargs):
        pytest.fail("preview must not read/write jobs or execute the scheduler")
    for name in ("create_job", "update_job", "list_jobs", "get_job", "run_job", "save_jobs"):
        setattr(jobs, name, forbidden)
    package = types.ModuleType("cron")
    package.__path__ = []
    package.jobs = jobs
    monkeypatch.setitem(sys.modules, "cron", package)
    monkeypatch.setitem(sys.modules, "cron.jobs", jobs)
    return jobs, calls, parsed


class Handler:
    def __init__(self):
        self.wfile = io.BytesIO()
        self.headers = {}
        self.status = None

    def send_response(self, status):
        self.status = status

    def send_header(self, *args):
        pass

    def end_headers(self):
        pass


def test_preview_delegates_without_reinterpreting_core(scheduler):
    from api.cron import preview_schedule
    _, calls, parsed = scheduler
    result = preview_schedule("every monday 9am")
    assert calls == [("parse", "every monday 9am"), ("next", parsed)]
    assert result["schedule"] == parsed
    assert result["status"] == "ready"
    assert result["next_run_at"] == "2026-10-11T09:00:00-03:00"
    assert result["scheduler_timezone"] == "America/Sao_Paulo"


@pytest.mark.parametrize("failure,expected", [(ValueError("bad schedule"), "invalid"), (RuntimeError("broken"), "unavailable")])
def test_preview_failure_never_leaves_a_next_run(scheduler, failure, expected):
    from api.cron import preview_schedule
    jobs, _, _ = scheduler
    def fail(_):
        raise failure
    jobs.parse_schedule = fail
    result = preview_schedule("bad")
    assert result["status"] == expected
    assert result["next_run_at"] is None


def test_preview_unsupported_and_no_next_run(scheduler):
    from api.cron import preview_schedule, scheduler_metadata
    jobs, _, _ = scheduler
    jobs.compute_next_run = lambda _: None
    assert preview_schedule("past")["status"] == "no_next_run"
    del jobs.compute_next_run
    assert scheduler_metadata()["schedule_preview_supported"] is False
    assert preview_schedule("anything")["status"] == "unavailable"
    jobs._hermes_now = lambda: datetime(2026, 1, 1)
    assert scheduler_metadata()["scheduler_timezone"] is None


@pytest.mark.parametrize("path", ["/api/crons", "/api/crons/preview?schedule=0%209%20*%20*%20*"])
def test_get_metadata_and_preview_use_owning_context_even_with_empty_list(monkeypatch, scheduler, path):
    import api.profiles as profiles
    import api.routes as routes
    jobs, calls, _ = scheduler
    inside = []
    @contextmanager
    def scope():
        inside.append(True)
        yield
        inside.pop()
    def clock():
        assert inside, "metadata must be resolved inside the owning cron context"
        return datetime(2026, 10, 11, tzinfo=ZoneInfo("America/Sao_Paulo"))
    jobs._hermes_now = clock
    monkeypatch.setattr(profiles, "cron_profile_context", scope)
    monkeypatch.setattr(routes, "_ensure_agent_cron_import_path", lambda: None)
    monkeypatch.setattr(routes, "_cron_jobs_cross_profile", lambda _: ([], []))
    monkeypatch.setattr(routes, "_get_active_profile_name", lambda: "owner")
    monkeypatch.setattr(routes, "_handle_extension_sidecar_proxy", lambda *args: False)
    handler = Handler()
    routes.handle_get(handler, urlparse(path))
    assert handler.status == 200
    result = json.loads(handler.wfile.getvalue())
    assert result["scheduler_timezone"] == "America/Sao_Paulo"
    assert result["active_profile"] == "owner"
    if path == "/api/crons":
        assert result["jobs"] == []
        assert result["schedule_preview_supported"] is True
        assert calls == []
    else:
        assert result["next_run_at"] == "2026-10-11T09:00:00-03:00"


@pytest.mark.parametrize("query", ["", "schedule=", "schedule=a&schedule=b", "schedule=" + "x" * 1025])
def test_preview_rejects_unbounded_or_ambiguous_input(monkeypatch, query):
    import api.routes as routes
    monkeypatch.setattr(routes, "_handle_extension_sidecar_proxy", lambda *args: False)
    handler = Handler()
    routes.handle_get(handler, urlparse("/api/crons/preview?" + query))
    assert handler.status == 400
