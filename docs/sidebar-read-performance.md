# Sidebar read performance

Sidebar counts and last-message timestamps remain live `COUNT(*)`/`MAX(timestamp)` projections, not cached counters. `message_stats_table` in `api/agent_sessions.py` inspects the current schema and selects an existing complete covering index with a leading `session_id` key for these aggregates. This avoids reading large transcript payload pages when SQLite's cost estimates otherwise choose `(session_id, id)`.

Both `read_session_lineage_metadata` and `_read_state_db_sidebar_overrides` use this narrow hint. No schema, statistics, transcript or provider-context writes are performed. No metadata TTL or invalidation scheme is introduced. Partial/expression indexes and non-BINARY key collations are excluded. Unsupported or index-free legacy schemas retain the original unhinted SQL. Schema inspection errors also fall back to the original query.

Regression coverage: `tests/test_sidebar_covering_stats.py` checks covering query plans, equivalent counts/timestamps, no schema changes, subsequent writes observed on a second call, legacy schemas, missing indexes, and identifier quoting. Existing lineage, read-only access, top-N and reconciliation tests remain applicable.

Operational validation must distinguish SQL-reader timings from end-to-end `/api/sessions` and browser boot. Measure both before/after on the same workload; a microbenchmark is not a cold-boot latency guarantee. Never drop host caches, rewrite SQLite statistics, vacuum or prune user history merely to validate this optimization.
