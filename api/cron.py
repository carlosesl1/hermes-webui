"""Read-only scheduler metadata; never infer a cron zone from the browser/host."""

def scheduler_timezone():
    """Return the actual scheduler clock's IANA zone, or None on older cores.

    Called while routes holds the owning profile's cron context. A server-local
    fixed offset is not an IANA zone and must not be mislabeled as one.
    """
    try:
        from cron import jobs
        clock = getattr(jobs, "_hermes_now", None)
        if not callable(clock):
            return None
        return getattr(clock().tzinfo, "key", None)
    except Exception:
        return None
