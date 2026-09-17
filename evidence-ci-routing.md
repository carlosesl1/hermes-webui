# CI routing compatibility evidence

Baseline: `4b14942c18d63fed70d0aa70dba0af2df4b82360`.

## Changes
- Added KiloCode to the WebUI built-in provider display registry. Catalog-backed provider repair now recognizes this built-in without optional `hermes_cli.auth`; arbitrary or unconfigured custom catalog owners remain rejected.
- Restored legacy `/api/session/archive` requests without an explicit profile for DB-only sessions. Owner comes from a unique authoritative row; absent/ambiguous owners, invisible foreign rows and isolated-profile escape attempts still fail before persistence. Explicit owner matching, read-only/subagent guards and metadata-only archive persistence remain intact.
- Updated metadata/display static guards for the shared model/provider resolver, preserving cache-only requirements rather than removing expectations. Added actual metadata GET tripwires against display resolution, catalog access and live rebuild.
- Expanded owner-boundary disk-readback tests and core-unavailable routing tests. No syncchat, save/draft, core, production, network or deployment changes.

## Verification
Credential-free, network-blocked, serialized supported runner:

```sh
python3 ../verify.py . tests/test_routing_snapshot_coherence.py tests/test_upstream_media_archive_boundaries.py tests/test_issue5731_session_model_provider_repair.py tests/test_gateway_sync.py tests/test_session_metadata_fast_path.py tests/test_session_display_resolver_no_live_rebuild.py
```

Result: **180 passed in 45.44s**, exit 0.
Log: `/data/apps/hermes-webui-fork-release/upstream-fixes/ci-routing-fv_kqono/pytest.log`.

`git diff --check` passed. The initial diagnostic run exposed both legacy archive failures and both outdated static assertions; final verification covers all six files, including added tests. Shared-runner lock contention increased wall-clock time; no remaining failures in this scope.
