# Live-only journal telemetry

`metering` is live observation, not recoverable transcript data. Live writers
publish it under the same per-run lock and cancellation fence as durable events,
without allocating a sequence or appending a row. The explicit null event cursor
must not inherit the newest global cursor. Both SSE journal replay emitters skip
legacy metering rows **after** readers validate the complete sequence. Raw journal
import remains supported; existing journals are not rewritten or deleted.

The offline channel buffer excludes metering. Durable token/tool/terminal events
retain their ordered cursor, fsync, replay and cancellation behavior. Canonical
sidecars, full-content reads and exports are unchanged by this transport slice.
