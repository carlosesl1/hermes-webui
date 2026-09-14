# Async delegation delivery identity and admission

This describes the WebUI bridge for `delegate_task` completion events, not the
separate `/background` command. State owners are the core's durable delivery
ledger and WebUI's bounded process-local compatibility dedupe.

- The final completion uses its original `delegation_id` in both consumers
  (background wakeup and next-turn injection) and in the core ledger.
- `task_failure_notice=true` is an intermediate per-child notice. WebUI keys it
  separately by batch and child (`results[0].task_index`, including zero, with
  legacy task-id fallback). It never claims, completes, releases, or marks the
  final batch's durable identity, even on cores predating interim-event support.
  Duplicate notices are suppressed by the existing bounded local dedupe; they
  are not durable backlog entries. A notice retry must not restore the final
  batch row as a substitute.
- A foreground-busy precheck leaves a completion unclaimed. The admission
  race can still return `409` for an active stream, or credential state can
  return `409 / process_wakeup_paused`. These are deferrals, not failed
  deliveries. The bridge refunds the owned claim before scheduling a retry.
- Modern cores provide `defer_completion_delivery(id, token)`. For older
  durable cores exposing `_update_delivery(sql, params)`, the compatibility
  path performs the same atomic, pending-row/token-scoped refund. It does not
  open a database independently or call a failure-counting release. This is an
  explicit private-core compatibility dependency on the existing ledger schema.
- If neither refund API is available, or refund fails, WebUI preserves the
  durable lease and logs the failure without scheduling a new retry. It cannot
  promise automatic admission recovery on an unknown core contract; upgrade
  the core. Existing core lease/restart recovery remains core-owned. A verified
  absent ledger row can still use the legacy local retry path.
- Actual dispatch/ACK failures retain failure-counting release semantics and
  the finite core attempt budget. Successful admission is ACKed only afterward.
  Durable retries continue to share one restore timer; no per-event timers or
  additional ownership registries are introduced. Retry helpers reject NaN,
  infinity, nonnumeric values, and delays exceeding the platform wait limit
  before scheduling a timer or sleeping; invalid delays never become hot loops.
- Both consumers route notices and final results to `origin_ui_session_id` when
  present, even when the session-key index points to another tab. The notice's
  local dedupe identity is not a session return address or a core ledger ID.
  Legacy events without an exact origin retain the existing session-key fallback:
  WebUI cannot reconstruct a missing immutable owner after an upstream capture
  race or index reassignment. This change does not claim to fix that core limit.

## Regression checks

```sh
./scripts/test.sh tests/test_async_delivery_admission.py \
  tests/test_async_delegation_webui_bridge.py \
  tests/test_notify_on_complete_webui.py tests/test_wakeup_defer_race.py \
  tests/test_background_process_wakeup_format.py -q
```

Set `HERMES_WEBUI_TEST_CORE_SOURCE` to a read-only core checkout to also exercise
its actual delivery functions against SQLite `:memory:`. Those tests AST-load
only the named delivery functions, never import the core runtime, and provide
their own transaction, lock, and ledger. Without that explicit source, the
core-SQL integration cases skip; compatibility and bridge unit tests still run.
Use isolated `HOME`, `HERMES_HOME`, and `HERMES_WEBUI_STATE_DIR` for test runs.
