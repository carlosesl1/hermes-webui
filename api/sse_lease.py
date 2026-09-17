"""Finite observation leases; expiry closes a subscription, never its run."""
import math
import os
import time

DEFAULT_SECONDS = 300.0
MAX_SECONDS = 3600.0


class SSELease:
    def __init__(self):
        try:
            seconds = float(os.environ.get("HERMES_WEBUI_SSE_LEASE_SECONDS", DEFAULT_SECONDS))
        except (TypeError, ValueError):
            seconds = DEFAULT_SECONDS
        if not math.isfinite(seconds) or seconds <= 0:
            seconds = DEFAULT_SECONDS
        self.deadline = time.monotonic() + min(seconds, MAX_SECONDS)

    def active(self):
        return time.monotonic() < self.deadline
