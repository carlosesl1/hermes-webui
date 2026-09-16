# Turn-owned delegation in the WebUI

The in-process Hermes adapter uses `api.turn_delegation.turn_owned_agent_class`
when the runtime exposes its central `_dispatch_delegate_task` seam. This is
instance-local behavior, not a change to the shared tool registry, configuration,
or Hermes core. Other transports retain their own delegation policy. Older
runtimes without that seam retain their original behavior and schema; joined
semantics must not be advertised as supported there.

## Contract

- A `delegate_task` call is a foreground tool result, not a detached notification.
- Tasks in one call still run in parallel inside Hermes. The parent receives their
  ordered outcomes before it can continue inference and produce a final answer.
- Further calls in that turn also join. Concurrent tool calls are joined by the
  existing tool executor. This deliberately sacrifices parent/child overlap in
  favor of one reliable synthesis boundary; group independent work in one call.
- Core schema limits, child permissions, context isolation, progress callbacks,
  summaries, cost accounting and failure outcomes are retained. Only transport
  instructions are changed in a copy of the agent's tools, including refreshes.
- Child conversations are not appended to the human transcript. Their returned
  summaries remain inspectable as normal tool results in the execution worklog.
- The principal's actual answer is never hidden in a background notification
  disclosure. Legacy automatic rows remain available as notification-only
  activity; stored messages and exports are not rewritten.
- Brief progress commentary is allowed. The agent should verify child claims,
  synthesize once, and report errors/timeouts/missing evidence explicitly.

## Cancellation and detached work

`Stop` uses the existing parent interrupt propagation; attached children receive
that signal and the run's existing cancel/writeback ownership guards apply.
Physical cancellation of arbitrary network calls, shell side effects or daemons
is not guaranteed. There is no durable child-resume claim across server restart.
Hermes owns child timeouts and iteration limits; this adapter does not replace
those with a hidden global timeout or mutate shared runtime settings.

Explicit `/background`, cron and terminal jobs stay detached and do not enter the
delegation join. Existing notification delivery/recovery continues to handle those
jobs and legacy already-dispatched results. A new human turn is never fabricated
solely to deliver a newly joined delegation result.

## Verification

`tests/test_turn_delegation.py` covers the local dispatch/schema contract with a
stub runtime. These are not provider-inference proofs. Focused activity browser
tests cover visible principal answers, notification provenance, chronology,
virtualization, focus, accessibility, reload and full/export preservation.

Release acceptance must additionally exercise the real deployed core/model:
parallel staggered children; sequential delegation calls; Stop and a new human
turn; final visibility and reload integrity at desktop/mobile sizes. Inspect the
original run's terminal state and full transcript, not just a success string or
HTTP health response. Separate child lifecycle/inference evidence from fixtures.
