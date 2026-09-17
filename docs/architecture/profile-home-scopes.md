# In-process worker home scopes

Worker home ownership is the session/request profile, not the process environment.
`api.profiles.install_profile_home_scope` feature-detects the actual Hermes API:
`hermes_constants.set_hermes_home_override(path)` returns a ContextVar token and
`reset_hermes_home_override(token)` restores the previous scope. No version-string
comparison is needed. Both APIs exist in current Hermes source; `get_hermes_home`
consults the override before the process environment.

Streaming, background workers (including title, synchronous chat and manual
compression callers of `profile_env_for_background_worker`), and cron home scopes
must not set, delete, or restore the process `HERMES_HOME`. Profile `.env` data
cannot smuggle that key into the worker's process-env update. Nested worker scopes,
including an explicit default profile, restore the outer home on normal exit,
exceptions, and cancellation. Worker setup failures propagate without running the
body in the wrong profile.

## Older runtimes

Without both home override functions, work is supported only when the requested
home equals the current process home. Cross-home work fails closed with an upgrade
or separate-process instruction. A setup/restore-only lock is not a safe fallback:
unscoped readers and child processes do not participate in it. A separate WebUI
process launched with the required `HERMES_HOME` remains the legacy option.

## Scope and limits

This is a home-routing guarantee, not complete in-process tenant isolation.
Direct `os.getenv('HERMES_HOME')` readers see the launch/process home, never a
worker's selected home; profile-aware code must use `get_hermes_home` or an explicit
path. Raw new threads need an explicit worker scope (or a copied context); child
processes need an explicitly constructed environment. Existing credential,
terminal/session environment bridges and legacy skill/cron module-global patches
are not eliminated by this change. Cron module patches remain serialized between
threads; these synchronous scopes must not span interleaved asyncio tasks on the
same thread. Only the Hermes home binding is ContextVar-local; WebUI's auxiliary
thread-local env bridge is not an asyncio-task isolation mechanism. Startup
initialization and explicit `switch_profile(process_wide=True)` remain deliberate
process-wide operations, not worker scopes; do not use them concurrently with
background work. Per-client profile switching does not use that path.

Tests: `tests/test_issue6857_contextual_home.py` uses the real Hermes home resolver,
concurrent A/B workers, process-home write/delete spies, nested default scopes,
exception/cancellation unwind, missing-runtime and broken-setter cases, plus
streaming/cron scope composition. Existing #5567 tests exercise real config/skill
readers and streaming teardown; state-sync and compression tests cover neighboring
worker callers. Tests use temporary homes and no production state or providers.
