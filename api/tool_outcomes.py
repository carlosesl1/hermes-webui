"""Explicit tool outcome protocol; never classify arbitrary output by keywords.

Core's display classifier also has a prose heuristic, so the WebUI deliberately
uses only its structured failure signals plus explicit callback metadata.
"""
import json
from collections.abc import Mapping


def tool_result_is_error(name, result, *, is_error=None):
    if isinstance(is_error, bool):
        return is_error
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (ValueError, TypeError):
            return False
    if not isinstance(result, Mapping):
        return False
    for key in ('is_error', 'isError', 'failed'):
        if isinstance(result.get(key), bool):
            return result[key]
    if result.get('cancelled') is True or result.get('canceled') is True:
        return True
    if str(result.get('status') or '').lower() in {'error', 'failed', 'cancelled', 'canceled', 'interrupted'}:
        return True
    if isinstance(result.get('success'), bool):
        return not result['success']
    code = result.get('exit_code')
    if isinstance(code, (int, float)) and not isinstance(code, bool) and code != 0:
        return True
    return bool(result.get('error'))
