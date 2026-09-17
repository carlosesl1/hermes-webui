"""Minimal contextual-home runtime contract for standalone WebUI unit tests.

This is not an agent replacement. Installed runtimes must never be masked.
"""
import os
from contextvars import ContextVar
from pathlib import Path

_home_override = ContextVar("test_hermes_home_override", default=None)


def get_hermes_home_override():
    return _home_override.get()


def set_hermes_home_override(home):
    return _home_override.set(Path(home).expanduser() if home is not None else None)


def reset_hermes_home_override(token):
    _home_override.reset(token)


def get_hermes_home():
    return _home_override.get() or Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()
