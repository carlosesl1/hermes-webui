# Durable `/background` tasks

`/background <prompt>` runs a separate task under the current parent session.
The result appears in a collapsed **Background task** disclosure below that
session's conversation. Expand it for the full plain-text output. It does not
append an assistant message, rewrite the parent session, or enter parent model
context. `/btw` remains ephemeral and is not covered by this store.

## State and delivery contract

- `HERMES_WEBUI_STATE_DIR/background_tasks.sqlite3` owns task metadata and
  results. SQLite transactions commit a terminal outcome before child-session
  output can be deleted. Failed persistence retains the child file.
- `(parent_session_id, task_id)` is the durable identity. The server captures
  the resolved parent and execution inputs before launching a worker; workers
  never save a potentially stale parent session/cache object.
- `GET /api/background/status?session_id=...` returns a parent-scoped `tasks`
  snapshot, including running tasks. The compatibility `results` field contains
  terminal tasks. Neither read consumes, acknowledges, nor deletes any result.
  Multiple results, repeated requests, and multiple tabs see the same history.
- One browser poll owner follows the visible parent. Navigation aborts the old
  request and rejects late replies. Reloading reattaches to the durable ledger;
  idle polling also discovers tasks launched in another tab. Disclosures retain
  their open state during refresh, and output is rendered as text, not HTML.
- Outcomes distinguish completed, failed, cancelled, no-answer, and interrupted
  work. On first access in a new server process, previously running tasks become
  **Interrupted**. The server does not claim to resume daemon worker threads.
- Launch requests are not automatically retried after network failure, since
  retrying a non-idempotent launch could start duplicate work. A lost response
  can be reconciled by the next status snapshot.

## Limits

The supported runtime is one WebUI server process per state directory. This is
not a distributed worker queue. Terminal history has no automatic retention or
pruning policy yet (including when a parent is deleted). Back up the SQLite file
alongside WebUI state. Tasks that completed before this feature was installed
cannot be recovered from the former in-memory tracker. If writing the final
result fails, the retained child file is recovery evidence; the ledger may remain
running until restart marks it interrupted. No automatic inference/retry is run.

## Verification

Use the supported repository test runner:

```bash
./scripts/test.sh tests/test_background_tasks.py tests/test_background_durable.py -q
```

The deterministic browser harness runs the production polling/rendering code in
Chromium with controlled HTTP responses and timers, without a model or server:

```bash
python tests/browser_background_tasks.py
```

It requires an already installed Playwright/Chromium environment.
`BACKGROUND_ARTIFACT_DIR` optionally saves desktop, narrow, and mobile evidence.
`BG_BASELINE=1` exercises the original regression against baseline `94fd2da`.
This is component browser coverage, not a live-provider end-to-end test.
