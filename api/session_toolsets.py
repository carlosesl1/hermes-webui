"""Durable capability snapshot invalidation; caller holds the session agent lock."""
from contextlib import closing
from pathlib import Path
import sqlite3


def invalidate_tool_snapshot(session):
    """Invalidate only the owning profile, before publishing new tool settings.

    Missing DB/row means no snapshot exists. All other failures propagate. Do
    not use _agent_state_db_path: its read fallback can select the active DB.
    """
    from api.profiles import (
        _PROFILE_ID_RE, _is_root_profile, _resolve_profile_home_for_name,
        _is_isolated_profile_mode, _isolated_profile_name, _profiles_match,
    )

    profile = getattr(session, "profile", None) or "default"
    if not isinstance(profile, str) or not (_is_root_profile(profile) or _PROFILE_ID_RE.fullmatch(profile)):
        raise ValueError("Invalid session owner profile")
    if _is_isolated_profile_mode() and not _profiles_match(profile, _isolated_profile_name()):
        raise ValueError("Session owner is outside the isolated profile")
    path = Path(_resolve_profile_home_for_name(profile)) / "state.db"
    try:
        path.stat()
    except FileNotFoundError:
        return
    # mode=rw prevents a disappearing DB from being recreated as an empty one.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=2)) as conn:
        with conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
            if not columns:
                raise RuntimeError("Session database has no sessions table")
            fields = [name for name in ("tool_names", "system_prompt_hash") if name in columns]
            if fields:  # Older agent schemas cannot contain these snapshots.
                assignments = ", ".join(f"{name}=NULL" for name in fields)
                conn.execute(f"UPDATE sessions SET {assignments} WHERE id=?", (session.session_id,))
