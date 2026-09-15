# Paginated render text budgets

`api/render_payload.py` owns a read-only display projection, not canonical
transcript storage, export data, or model context. `content_full=1` bypasses it.
No routes or full-content response semantics change in this fix.

## Contract change

Previously, rows consumed a shared character budget in arrival order and every
clipped text leaf appended an implementation notice. Early tool outputs and
hydrated scenes could therefore replace late short conversation turns with just
that notice.

The projection now reserves max-min fair shares for user/assistant `content`
strings and standard `text`/`input_text`/`output_text` blocks before processing
reasoning, tool arguments/results and scenes. Short prose is satisfied first;
remaining capacity is divided across larger rows, then across their text blocks.
The existing per-field (8192; tool 4096), per-row (32768) and shared page (262144)
limits remain. Budgets count retained data-bearing characters, not IDs or JSON
structure. There is no structural-JSON or row-count cap. Giant opaque metadata
strings retain the existing omission guard, now represented by empty strings.

Clipping never appends instructions or strips a magic string from source text.
A user who actually typed the old notice retains that text subject to the same
budget as any other prose. Array/dictionary shapes, order, IDs, provenance and
coordinates are preserved; the input is not mutated.

- `_content_truncated`: authoritative row-wide flag for any omission.
- `_content_original_chars`: unchanged original top-level string length.
- `_preview_content_truncated`: present on clipped rows; true only when the
  user/assistant conversation text described above was clipped. False for
  tool-role rows and reasoning/tool/scene/attachment-only omissions. Unclipped
  rows need no additional metadata. Consumers should use metadata, not text
  matching, for full-content affordances.

## Verification

Focused tests: `tests/test_render_payload_preview_budget.py`,
`tests/test_session_tail_payload.py`, `tests/test_local_large_session_safety.py`.
The new regressions reproduce notice-only late turns on the old implementation.
They cover early huge tools/scenes, many huge assistant turns, nested mixed
content blocks, zero budgets, row/field/shared-page accounting, source identity,
input immutability and a literal human-pasted notice. Existing route tests cover
explicit lossless full-content retrieval and canonical compression isolation.

Use the repository test runner in a development checkout. Where dependencies
already exist and installs are prohibited, an isolated runner may reuse that
interpreter with cleared credentials, temporary HOME/HERMES_HOME/state/TMPDIR,
plugin autoload disabled, live-state/database-write and network audit guards;
run only the focused files above. The fake route handler includes the slow-log
method so large explicit-full fixtures remain testable on slower machines.
