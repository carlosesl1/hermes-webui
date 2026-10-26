"""Upstream #8187/#8201 compatibility regressions."""
import sys

def test_streaming_cronjob_wrapper_tolerates_agent_without_dynamic_schema_overrides(monkeypatch):
    """Older hermes-agent ToolEntry/register() lack ``dynamic_schema_overrides`` (#8186).

    Installing the wrapper must not raise AttributeError (which aborted every
    chat stream) and must not pass the unknown kwarg to an older register().
    """
    import types

    from api import streaming as st

    def original_handler(args, **kwargs):
        return "ok"

    class Entry:
        name = "cronjob"
        toolset = "cronjob"
        schema = {"name": "cronjob"}
        handler = staticmethod(original_handler)
        check_fn = None
        requires_env = []
        is_async = False
        description = ""
        emoji = "⏰"
        max_result_size_chars = None

    entry = Entry()
    registered = []

    class Registry:
        def get_entry(self, name):
            return entry

        def register(self, name, toolset, schema, handler, check_fn=None,
                     requires_env=None, is_async=False, description="", emoji="",
                     max_result_size_chars=None):
            registered.append(name)
            entry.handler = handler

    tools_pkg = types.ModuleType("tools")
    registry_mod = types.ModuleType("tools.registry")
    registry_mod.__dict__["registry"] = Registry()
    monkeypatch.setitem(sys.modules, "tools", tools_pkg)
    monkeypatch.setitem(sys.modules, "tools.registry", registry_mod)
    monkeypatch.setattr(st, "_STREAMING_CRONJOB_WRAPPER_INSTALLED", False)

    st._install_streaming_cronjob_profile_wrapper()

    assert registered == ["cronjob"]
    assert st._STREAMING_CRONJOB_WRAPPER_INSTALLED is True
    assert entry.handler({"action": "list"}) == "ok"

def test_streaming_cronjob_wrapper_wraps_the_renamed_cronjob_manage_tool(tmp_path, monkeypatch):
    """Current hermes-agent registers ``cronjob_manage``, not ``cronjob`` (#8189).

    Looking up the old name alone made the wrapper return at its debug log, so a
    cron tool call made from a chat ran against the default profile.
    """
    import types

    from api import profiles as p
    from api import streaming as st

    profile_home = tmp_path / "home" / "profiles" / "ops"
    events = []

    class Ctx:
        def __init__(self, home):
            self.home = str(home)

        def __enter__(self):
            events.append(("enter", self.home))
            return self

        def __exit__(self, exc_type, exc, tb):
            events.append(("exit", self.home))
            return False

    def original_handler(args, **kwargs):
        events.append(("handler", st._STREAMING_CRON_PROFILE_HOME.get()))
        return "ok"

    class Entry:
        name = "cronjob_manage"
        toolset = "cronjob"
        schema = {"name": "cronjob_manage"}
        handler = staticmethod(original_handler)
        check_fn = None
        requires_env = []
        is_async = False
        description = ""
        emoji = "⏰"
        max_result_size_chars = None
        dynamic_schema_overrides = None

    entry = Entry()

    class Registry:
        def get_entry(self, name):
            return entry if name == entry.name else None

        def register(self, **kwargs):
            events.append(("register", kwargs["name"]))
            entry.handler = kwargs["handler"]

    registry_mod = types.ModuleType("tools.registry")
    registry_mod.__dict__["registry"] = Registry()
    monkeypatch.setitem(sys.modules, "tools.registry", registry_mod)
    monkeypatch.setattr(st, "_STREAMING_CRONJOB_WRAPPER_INSTALLED", False)
    monkeypatch.setattr(p, "cron_profile_context_for_home", Ctx)

    st._install_streaming_cronjob_profile_wrapper()

    assert events == [("register", "cronjob_manage")]
    assert st._STREAMING_CRONJOB_WRAPPER_INSTALLED is True

    token = st._STREAMING_CRON_PROFILE_HOME.set(str(profile_home))
    try:
        assert entry.handler({"action": "list"}) == "ok"
    finally:
        st._STREAMING_CRON_PROFILE_HOME.reset(token)

    assert events == [
        ("register", "cronjob_manage"),
        ("enter", str(profile_home)),
        ("handler", str(profile_home)),
        ("exit", str(profile_home)),
    ]

def test_streaming_cronjob_wrapper_wraps_both_names_when_both_are_registered(monkeypatch):
    """A build that keeps the old name next to the new one must wrap both.

    Each wrapped handler must call its own original: the loop that wraps them
    would otherwise let the last entry's handler win for all of them.
    """
    import types

    from api import streaming as st

    events = []

    def make_entry(name):
        entry = types.SimpleNamespace(
            name=name,
            toolset="cronjob",
            schema={"name": name},
            check_fn=None,
            requires_env=[],
            is_async=False,
            description="",
            emoji="⏰",
            max_result_size_chars=None,
            dynamic_schema_overrides=None,
        )
        entry.handler = (lambda args, _name=name, **kwargs: "ok:%s" % _name)
        return entry

    entries = {name: make_entry(name) for name in st._STREAMING_CRONJOB_TOOL_NAMES}

    class Registry:
        def get_entry(self, name):
            return entries.get(name)

        def register(self, **kwargs):
            events.append(("register", kwargs["name"]))
            entries[kwargs["name"]].handler = kwargs["handler"]

    registry_mod = types.ModuleType("tools.registry")
    registry_mod.__dict__["registry"] = Registry()
    monkeypatch.setitem(sys.modules, "tools.registry", registry_mod)
    monkeypatch.setattr(st, "_STREAMING_CRONJOB_WRAPPER_INSTALLED", False)

    st._install_streaming_cronjob_profile_wrapper()

    assert events == [("register", "cronjob_manage"), ("register", "cronjob")]
    # No streaming profile is set, so each wrapper must call its own original.
    assert entries["cronjob_manage"].handler({}) == "ok:cronjob_manage"
    assert entries["cronjob"].handler({}) == "ok:cronjob"

def test_streaming_cronjob_wrapper_never_propagates_a_registration_failure(monkeypatch):
    """A future agent shape change must not abort the chat stream (#8186)."""
    import types

    from api import streaming as st

    entry = types.SimpleNamespace(
        name="cronjob_manage",
        toolset="cronjob",
        schema={"name": "cronjob_manage"},
        handler=lambda args, **kwargs: "ok",
        check_fn=None,
        requires_env=[],
        is_async=False,
        description="",
        emoji="⏰",
        max_result_size_chars=None,
        dynamic_schema_overrides=None,
    )
    calls = []
    warnings = []

    class Registry:
        def get_entry(self, name):
            return entry if name == entry.name else None

        def register(self, **kwargs):
            calls.append(kwargs["name"])
            raise AttributeError("unexpected agent shape")

    registry_mod = types.ModuleType("tools.registry")
    registry_mod.__dict__["registry"] = Registry()
    monkeypatch.setitem(sys.modules, "tools.registry", registry_mod)
    monkeypatch.setattr(st, "_STREAMING_CRONJOB_WRAPPER_INSTALLED", False)
    monkeypatch.setattr(st, "_STREAMING_CRONJOB_WRAPPER_WARNED", False)
    monkeypatch.setattr(st.logger, "warning", lambda *args, **kwargs: warnings.append(args))

    st._install_streaming_cronjob_profile_wrapper()

    assert calls == ["cronjob_manage"]
    assert len(warnings) == 1
    # The warning is logged once, but the failure is not recorded as installed:
    # the next chat stream retries, so one bad registration cannot pin every
    # later job to the default profile (#8201).
    assert st._STREAMING_CRONJOB_WRAPPER_WARNED is True
    assert st._STREAMING_CRONJOB_WRAPPER_INSTALLED is False

def test_streaming_cronjob_wrapper_retries_after_a_failed_install(monkeypatch):
    """A later stream retries an install that failed once (#8201).

    Master retried and wrote the next chat-created job into the named profile;
    recording the failure as "installed" left every later job in the default
    profile's cron store.
    """
    import types

    from api import streaming as st

    entry = types.SimpleNamespace(
        name="cronjob_manage",
        toolset="cronjob",
        schema={"name": "cronjob_manage"},
        handler=lambda args, **kwargs: "ok",
        check_fn=None,
        requires_env=[],
        is_async=False,
        description="",
        emoji="⏰",
        max_result_size_chars=None,
        dynamic_schema_overrides=None,
    )
    calls = []
    warnings = []
    registry_state = {"fail": True}

    class Registry:
        def get_entry(self, name):
            return entry if name == entry.name else None

        def register(self, **kwargs):
            calls.append(kwargs["name"])
            if registry_state["fail"]:
                raise AttributeError("unexpected agent shape")
            entry.handler = kwargs["handler"]

    registry_mod = types.ModuleType("tools.registry")
    registry_mod.__dict__["registry"] = Registry()
    monkeypatch.setitem(sys.modules, "tools.registry", registry_mod)
    monkeypatch.setattr(st, "_STREAMING_CRONJOB_WRAPPER_INSTALLED", False)
    monkeypatch.setattr(st, "_STREAMING_CRONJOB_WRAPPER_WARNED", False)
    monkeypatch.setattr(st.logger, "warning", lambda *args, **kwargs: warnings.append(args))

    st._install_streaming_cronjob_profile_wrapper()
    assert st._STREAMING_CRONJOB_WRAPPER_INSTALLED is False

    # Same process, next chat stream, agent shape now accepted.
    registry_state["fail"] = False
    st._install_streaming_cronjob_profile_wrapper()

    assert calls == ["cronjob_manage", "cronjob_manage"]
    assert len(warnings) == 1
    assert st._STREAMING_CRONJOB_WRAPPER_INSTALLED is True
    assert entry.handler({"action": "list"}) == "ok"

    # A third stream is a no-op now that the wrapper is installed.
    st._install_streaming_cronjob_profile_wrapper()
    assert calls == ["cronjob_manage", "cronjob_manage"]

def test_streaming_cronjob_wrapper_wraps_the_global_entry_not_a_profile_override(monkeypatch):
    """A profile-scoped override must not be promoted to every profile (#8201).

    ``registry.get_entry()`` resolves the merged view, where the active profile's
    scoped override wins over the global slot. Re-registering that entry without a
    scope moved one profile's handler into the global slot, so every other profile
    ran it. Only the global slot may be wrapped.
    """
    import types

    from api import streaming as st

    events = []

    def make_entry(handler):
        return types.SimpleNamespace(
            name="cronjob_manage",
            toolset="cronjob",
            schema={"name": "cronjob_manage"},
            handler=handler,
            check_fn=None,
            requires_env=[],
            is_async=False,
            description="",
            emoji="⏰",
            max_result_size_chars=None,
            dynamic_schema_overrides=None,
        )

    global_entry = make_entry(lambda args, **kwargs: "ok:global")
    scoped_entry = make_entry(lambda args, **kwargs: "ok:scoped")

    class Registry:
        def get_entry(self, name):
            # Merged view: this process profile overrides the global entry.
            return scoped_entry if name == scoped_entry.name else None

        def snapshot_registration(self, name, *, scope=None):
            events.append(("snapshot", name, scope))
            return global_entry if name == global_entry.name else None

        def register(self, **kwargs):
            events.append(("register", kwargs["name"]))
            global_entry.handler = kwargs["handler"]

    registry_mod = types.ModuleType("tools.registry")
    registry_mod.__dict__["registry"] = Registry()
    monkeypatch.setitem(sys.modules, "tools.registry", registry_mod)
    monkeypatch.setattr(st, "_STREAMING_CRONJOB_WRAPPER_INSTALLED", False)
    monkeypatch.setattr(st, "_STREAMING_CRONJOB_WRAPPER_WARNED", False)

    st._install_streaming_cronjob_profile_wrapper()

    assert events == [
        ("snapshot", "cronjob_manage", None),
        ("snapshot", "cronjob", None),
        ("register", "cronjob_manage"),
    ]
    assert st._STREAMING_CRONJOB_WRAPPER_INSTALLED is True
    # The global handler is the one that got wrapped...
    assert global_entry.handler({}) == "ok:global"
    # ...and the profile's own override was left alone.
    assert scoped_entry.handler({}) == "ok:scoped"
