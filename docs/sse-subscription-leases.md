# SSE observation leases

Chat, per-session journal, gateway session-list, global session-list and persistent
session subscriptions rotate after a finite lease. Default: 300 seconds. Set
`HERMES_WEBUI_SSE_LEASE_SECONDS` to a positive finite duration; values above 3600
are capped, and invalid/non-positive values use the default. The lease is checked
on every loop iteration, including continuous traffic: successful proxy writes
cannot extend it. Idle closure can lag by one heartbeat (5 seconds); a blocked
write remains subject to the existing socket deadline (default 20 seconds).

Expiry returns through the handler's exact-queue unsubscribe. It never cancels,
settles or removes the worker. EventSource reconnects; journal streams retain
Last-Event-ID/query-cursor replay. Session invalidation streams retain their
existing snapshot/known_count recovery (not a new durable cursor contract).
A concurrently attached replacement owns a different queue and survives cleanup.
Chat header writes are inside the same cleanup boundary, so a disconnect during
header setup also releases its subscription. Existing gateway/persistent-session
shutdown sentinels still run their exact-queue unsubscribe; the global session-list
stream has no sentinel contract and this slice does not introduce one.

Scope: this slice does not change runner-observe, approval, clarify, or terminal
streams. It does not add retention, change terminal transcript projection, or
claim live-proxy/browser deployment verification. Tests use synthetic local state.
