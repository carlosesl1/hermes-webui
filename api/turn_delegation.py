"""WebUI-only structured delegation: child results belong to the calling turn.

The shared Hermes runtime, registry and other transports are never patched.
Use its normal foreground implementation (parallel children, canonical tool
results, failure accounting and parent interrupt propagation), not a second
completion queue. Explicit /background and terminal notifications are unrelated.
"""
from copy import deepcopy
from functools import lru_cache

_JOINED_HEAD = (
    "Spawn subagents in isolated contexts; only their final summaries return to you. "
    "Pass independent tasks together in `tasks` to run them in parallel. "
    "In this WebUI, delegation is JOINED to the current turn: this tool returns "
    "only after the requested children finish, fail, time out, or are interrupted. "
    "Results arrive as this tool's result, NOT as new user messages or later turns. "
    "Do not end the turn to wait, poll transcripts, or send an awaiting-results final. "
    "After the results arrive, check required outputs and deliver one consolidated "
    "user-facing answer. A further delegation also joins before you can finish. "
    "Report failures or missing evidence honestly; do not silently call them success. "
    "Brief progress updates are allowed. Intentional detached jobs are separate "
    "(/background, cron, or terminal background jobs) and do not block this join.\n\n"
)


def joined_tool_schemas(tools):
    """Copy only the delegate schema; registry/shared schema dictionaries stay intact."""
    if not isinstance(tools, list):
        return tools
    result = []
    for tool in tools:
        function = tool.get('function', {}) if isinstance(tool, dict) else {}
        if function.get('name') != 'delegate_task':
            result.append(tool)
            continue
        copied = deepcopy(tool)
        f = copied['function']
        description = f.get('description', '')
        # Retain the core's current restrictions, capabilities and verification
        # rules. Replace only transport guidance, not task limits/parameters.
        _, marker, rules = description.partition('USE FOR:')
        f['description'] = _JOINED_HEAD + (marker + rules if marker else (
            "Children lack your conversation: include the full brief in context. "
            "Their summaries are self-reports: verify external writes before claiming success."
        ))
        result.append(copied)
    return result


@lru_cache(maxsize=8)
def turn_owned_agent_class(base):
    """An isolated class per runtime type, also covering rebuild and self-heal paths.

    Inherited __init__ keeps constructor capability inspection unchanged. The
    setter covers both initial tools and later schema refreshes. A base without
    the central dispatch seam is not silently given a misleading joined schema.
    """
    if not callable(getattr(base, '_dispatch_delegate_task', None)):
        return base

    class TurnOwnedAgent(base):
        def __setattr__(self, name, value):
            if name == 'tools':
                value = joined_tool_schemas(value)
            super().__setattr__(name, value)

        def _dispatch_delegate_task(self, function_args):
            from tools.delegate_tool import _strip_model_hidden_task_fields, delegate_task
            return delegate_task(
                goal=function_args.get('goal'), context=function_args.get('context'),
                tasks=_strip_model_hidden_task_fields(function_args.get('tasks')),
                max_iterations=function_args.get('max_iterations'), role=function_args.get('role'),
                background=False, images=function_args.get('images'),
                action=function_args.get('action'), subagent_id=function_args.get('subagent_id'),
                message=function_args.get('message'), parent_agent=self,
            )

    TurnOwnedAgent.__name__ = 'WebUITurnOwned' + base.__name__
    return TurnOwnedAgent
