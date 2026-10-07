# Managed-core runtime entry point

Start from any working directory with `python /path/to/webui/webui_runtime.py`.
Docker now invokes this script instead of `server.py`; its existing environment,
working directory, installation steps, privilege handling and error/exit handling
are unchanged. This is not an installer or a replacement for Hermes' bootstrap.

## Why a wrapper is required

The portability audit found that the deployment used a private `python -c`
launcher restoring `/app` to `sys.path`, while the public Docker launcher ran
`server.py` directly. This was functional divergence, not merely configuration.

The authoritative [PM documentation](https://hermes-agent.nousresearch.com/docs/reference/package-management)
says stale source installs sync and restart the same command on managed Python
before application imports. The [installation guide](https://hermes-agent.nousresearch.com/docs/getting-started/installation)
describes PM-owned Python and dependencies. The exact inspected source contract is:

- `hermes_bootstrap.py` imports stdlib first, hardens core's import path, calls
  `prepare_launch`, then `relaunch_command`, and uses `os.execv` on POSIX (a child
  process on Windows). Importing the module applies bootstrap; no explicit
  activation call is required.
- `hermes_cli/venv_sync.py::relaunch_command` builds an isolated `python -I -c`
  invocation, prepends the core source root and uses `runpy.run_path` for a script.
  Isolation drops environment `PYTHONPATH` and `run_path` on a file does not add
  its containing directory. A directly relaunched WebUI server can consequently
  lose access to its own `api` package even when launched from the app directory.

The durable script restores its own directory from `__file__` before and after
bootstrap, including when the core re-enters it via `run_path`. It runs `server`
as `__main__` without replacing `sys.modules` entries and retains launcher
`sys.argv[0]` for POSIX WebUI restarts. Application argument tails are preserved.
No host installation path or private configuration is copied into the launcher.

## Core selection and compatibility

The launcher uses an importable `hermes_bootstrap`, optionally adding the explicit
`HERMES_WEBUI_AGENT_DIR` source directory first (behind the app directory). Docker's
existing editable core installation provides normal import discovery. For a
managed checkout not on the interpreter's import path, set that existing variable
to its absolute source directory. Do not point it at a dependency generation.
There is no new host-path scan or subprocess discovery.

Older core installations without `hermes_bootstrap` proceed normally. Errors
inside a present bootstrap are not swallowed as an old-core fallback. The wrapper
neither calls package managers nor changes managed core source/facts. Importing a
modern core still delegates to its normal bootstrap, which may sync dependencies;
that behavior is owned by Hermes, not disabled or simulated in production.

## Optional WebUI-only dependency overlay

Default: **no overlay**, including when a state directory happens to contain
`runtime-deps`. A private installation's packages are not a portable requirement.

If the managed interpreter lacks separately provisioned WebUI dependencies, an
operator may explicitly set `HERMES_WEBUI_RUNTIME_DEPS` to an absolute directory.
Only its `python<major>.<minor>` child matching the **post-bootstrap interpreter**
is appended to `sys.path`, for example `/srv/webui-deps/python3.14` under an
explicit `/srv/webui-deps` root. Missing or relative configured roots fail clearly.
Provision packages separately for that interpreter, platform and ABI; the naming
convention is not a validation of binary wheel compatibility. Do not copy an old
venv, mix minor versions, or share free-threaded and regular-build binary packages.

The launcher never installs packages, scans other version directories, loads
`.pth` files, or prioritizes overlay packages over managed dependencies. Keep the
overlay operator-controlled because it contains executable Python. It is not
inferred from `HERMES_WEBUI_STATE_DIR` and may be omitted on ordinary installs.

## Verification and scope

`tests/test_webui_runtime_entrypoint.py` copies the real wrapper into a disposable
application next to a synthetic core. Real subprocess `execv` performs the
isolated `-c`/`run_path` transition. Tests cover a second independent invocation,
a POSIX-style process restart, unrelated cwd, paths/arguments containing spaces,
app/core name collisions, inherited environment, missing old-core bootstrap,
bootstrap failure, optional overlays and unchanged Docker launch shape. Child
processes use `-S` and synthetic core paths; no real core bootstrap, package install,
provider request or production service is exercised.

Docker, bootstrap foreground/detached launches and source-mode Windows restarts
use the durable wrapper; frozen Windows restarts retain their packaging command.
`ctl.sh` recognizes both the durable entry point and legacy direct-server processes.
Direct `python server.py` remains available but bypasses this re-exec protection.
No managed Python 3.14 production boot or cross-version binary dependency
compatibility is certified by the synthetic same-interpreter re-exec tests.
