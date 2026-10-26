"""Project session defaults: explicit ownership, no workspace auto-assignment.

No fallback/stub supplies PROJECTS_CATALOG_LOCK: the persistence lane must
provide the actual shared lock before these route tests can import.
"""
from types import SimpleNamespace

import pytest

from api import routes


@pytest.fixture
def project(monkeypatch, tmp_path):
    workspace = tmp_path / "project-space"
    workspace.mkdir()
    project = {
        "project_id": "p1", "profile": "default", "workspaces": [str(workspace)],
        "default_workspace": str(workspace), "model": "gpt-4o",
        "model_provider": "openai", "reasoning_effort": "high", "auto_assign": True,
    }
    monkeypatch.setattr(routes, "load_projects", lambda: [project])
    monkeypatch.setattr(routes, "get_active_profile_name", lambda: "default")
    return project


def test_defaults_are_copied_without_mutating_request(project):
    body = {"project_id": "p1"}
    resolved = routes._project_new_session_defaults(body)
    assert body == {"project_id": "p1"}
    assert resolved["workspace"] == project["default_workspace"]
    assert (resolved["model"], resolved["model_provider"]) == ("gpt-4o", "openai")
    assert "reasoning_effort" not in resolved
    assert "auto_assign" not in resolved


@pytest.mark.parametrize("explicit", [None, "", "another-model"])
def test_explicit_model_never_inherits_project_provider(project, explicit):
    resolved = routes._project_new_session_defaults({"project_id": "p1", "model": explicit})
    assert resolved["model"] == explicit
    assert "model_provider" not in resolved


@pytest.mark.parametrize("explicit", [None, "", "/another/workspace"])
def test_explicit_workspace_wins_even_when_empty(project, explicit):
    resolved = routes._project_new_session_defaults({"project_id": "p1", "workspace": explicit})
    assert resolved["workspace"] == explicit


@pytest.mark.parametrize("pid", [None, "", "missing"])
def test_no_implicit_assignment(project, pid):
    body = {"project_id": pid, "workspace": project["default_workspace"]}
    if pid == "missing":
        with pytest.raises(ValueError, match="Project not found"):
            routes._project_new_session_defaults(body)
    else:
        assert routes._project_new_session_defaults(body) == body


def test_foreign_project_not_disclosed(project):
    project["profile"] = "foreign"
    with pytest.raises(ValueError, match="Project not found"):
        routes._project_new_session_defaults({"project_id": "p1"})


def test_body_profile_cannot_bypass_request_profile(project):
    project["profile"] = "foreign"
    with pytest.raises(ValueError, match="Project not found"):
        routes._project_new_session_defaults({"project_id": "p1", "profile": "foreign"})


def test_new_session_route_uses_project_defaults(project, monkeypatch):
    captured = {}
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    monkeypatch.setattr(routes, "read_body", lambda handler: {"project_id": "p1", "worktree": False})
    monkeypatch.setattr(routes, "j", lambda handler, payload, **kw: captured.update(payload=payload) or True)
    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(messages=[], compact=lambda: {"session_id": "new", **kwargs})
    monkeypatch.setattr(routes, "new_session", create)
    routes.handle_post(SimpleNamespace(command="POST"), SimpleNamespace(path="/api/session/new"))
    assert captured["workspace"] == project["default_workspace"]
    assert captured["model"] == "gpt-4o"
    assert captured["model_provider"] == "openai"
    assert captured["project_id"] == "p1"
