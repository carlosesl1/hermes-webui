"""Read-only scheduler metadata; never infer a cron zone from the browser/host.

Call under the owning profile's cron context. Preview uses the same pure
parse/next-run functions as create/update, never a job writer or ticker. It is a
schedule candidate evaluated now, not a promise of delivery or job activation.
"""

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


def scheduler_metadata():
    """Capabilities and effective zone, including for a profile with no jobs."""
    supported = False
    try:
        from cron import jobs
        supported = all(callable(getattr(jobs, name, None)) for name in (
            "parse_schedule", "compute_next_run",
        ))
    except ImportError:
        pass
    return {
        "scheduler_timezone": scheduler_timezone(),
        "schedule_preview_supported": supported,
    }


def preview_schedule(schedule):
    """Return core-derived schedule/instant, or an explicit non-ready result.

    No job lookup: edits preview the submitted schedule as evaluated now, just
    like a schedule update; paused/completed job state is not changed or implied.
    Older cores without the pure helpers remain usable without guessed dates.
    """
    result = {
        **scheduler_metadata(), "status": "unavailable",
        "schedule": None, "next_run_at": None,
    }
    if not result["schedule_preview_supported"]:
        return result
    try:
        from cron import jobs
        parsed = jobs.parse_schedule(schedule)
        next_run = jobs.compute_next_run(parsed)
        if not isinstance(parsed, dict) or (next_run is not None and not isinstance(next_run, str)):
            return result
        result.update(schedule=parsed, next_run_at=next_run,
                      status="ready" if next_run else "no_next_run")
    except ValueError as exc:
        result.update(status="invalid", error=str(exc))
    except Exception:
        # Runtime incompatibility is not proof that the user's schedule is bad.
        pass
    return result
