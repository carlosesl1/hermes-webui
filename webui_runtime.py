"""Durable WebUI entry point across Hermes-managed interpreter relaunches.

Only stdlib may be imported before the core bootstrap. See
``docs/runtime-entrypoint.md`` for the optional dependency overlay contract.
"""
import importlib.util
import os
from pathlib import Path
import runpy
import sys


def _prefer_app(app_root: Path) -> None:
    root = str(app_root)
    sys.path[:] = [entry for entry in sys.path if entry != root]
    sys.path.insert(0, root)


def main() -> None:
    app_root = Path(__file__).resolve().parent
    _prefer_app(app_root)
    # Keep a durable script identity for the core's run_path relaunch and the
    # WebUI's POSIX restart. Do not replace it with server.py or a -c string.
    sys.argv[0] = str(app_root / "webui_runtime.py")
    agent_dir = os.environ.get("HERMES_WEBUI_AGENT_DIR")
    if agent_dir:
        # Explicit source selection, not discovery through private host paths.
        source = str(Path(agent_dir).expanduser().resolve())
        if source not in sys.path:
            sys.path.insert(1, source)
    if importlib.util.find_spec("hermes_bootstrap") is not None:
        # Import side effects own activation/re-exec. Errors in an installed
        # bootstrap must surface; only its absence is the legacy fallback.
        import hermes_bootstrap  # noqa: F401
    _prefer_app(app_root)

    overlay = os.environ.get("HERMES_WEBUI_RUNTIME_DEPS")
    if overlay:
        base = Path(overlay).expanduser()
        if not base.is_absolute():
            raise RuntimeError("HERMES_WEBUI_RUNTIME_DEPS must be an absolute directory")
        selected = base / f"python{sys.version_info.major}.{sys.version_info.minor}"
        if not selected.is_dir():
            raise RuntimeError(f"Missing WebUI dependency overlay for this interpreter: {selected}")
        # Never process .pth files, inherit the old venv, scan other ABIs, or
        # override core-selected dependencies. Provisioning is operator-owned.
        sys.path.append(str(selected))

    # alter_sys=False preserves launcher argv[0] for in-process restart handlers
    # and does not replace the process's __main__ module in sys.modules.
    runpy.run_module("server", run_name="__main__", alter_sys=False)


if __name__ == "__main__":
    main()
