"""Push subscription persistence operations.

Split from backend.db - push subscriptions are a self-contained concern.
Imports the shared write connection and lock from backend.db.
"""

import orjson

import backend.db as db
from backend.state import log_error


def _serialize_prefs(prefs) -> str:
    return orjson.dumps(prefs).decode() if isinstance(prefs, dict) else "{}"


def _row_to_push_sub(row) -> dict:
    d = dict(row)
    try:
        d["prefs"] = orjson.loads(d.get("prefs", "{}"))
    except Exception as e:
        log_error("Corrupt push prefs JSON in DB, resetting to empty", e)
        d["prefs"] = {}
    d["keys"] = {"p256dh": d.pop("p256dh", ""), "auth": d.pop("auth", "")}
    return d


def get_push_sub_on_write(endpoint: str) -> dict | None:
    """Get a push subscription using the write connection.

    Reads from _write_conn directly (not via _read_conn) to avoid WAL snapshot
    visibility races - a fresh read connection can get a snapshot from before
    a recent commit, so an upsert immediately followed by a read would miss
    the just-written row. Must be called under _write_lock.
    """
    if db._write_conn is None:
        return None
    with db._write_lock:
        row = db._write_conn.execute("SELECT * FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).fetchone()
        return _row_to_push_sub(row) if row else None


def get_push_sub_by_client(client_id: str) -> dict | None:
    """Get a push subscription by client_id."""
    with db._ReadConn() as conn:
        row = conn.execute("SELECT * FROM push_subscriptions WHERE client_id = ?", (client_id,)).fetchone()
        return _row_to_push_sub(row) if row else None


def upsert_push_sub(endpoint: str, p256dh: str, auth: str, client_id: str,
                    prefs: dict, created_at: float, last_active: float) -> bool:
    """Insert or update a push subscription. Returns True on success."""
    if db._write_conn is None:
        return False
    with db._write_lock:
        db._write_conn.execute(
            "INSERT INTO push_subscriptions (endpoint, p256dh, auth, client_id, prefs, created_at, last_active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (endpoint) DO UPDATE SET "
            "p256dh = excluded.p256dh, auth = excluded.auth, client_id = excluded.client_id, "
            "prefs = excluded.prefs, last_active = excluded.last_active",
            (endpoint, p256dh, auth, client_id,
             _serialize_prefs(prefs),
             created_at, last_active),
        )
        db._write_conn.commit()
    return True


def delete_push_sub(endpoint: str) -> int:
    """Delete a push subscription. Returns number of rows deleted."""
    if db._write_conn is None:
        return 0
    with db._write_lock:
        cursor = db._write_conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        db._write_conn.commit()
        return cursor.rowcount


def delete_push_subs_by_client(client_id: str) -> int:
    """Delete ALL push subscriptions for a client_id. Returns number of rows deleted."""
    if db._write_conn is None:
        return 0
    with db._write_lock:
        cursor = db._write_conn.execute("DELETE FROM push_subscriptions WHERE client_id = ?", (client_id,))
        db._write_conn.commit()
        return cursor.rowcount


def update_push_sub_prefs(endpoint: str, prefs: dict, last_active: float) -> int:
    """Update prefs and last_active. Returns number of rows updated."""
    if db._write_conn is None:
        return 0
    with db._write_lock:
        cursor = db._write_conn.execute(
            "UPDATE push_subscriptions SET prefs = ?, last_active = ? WHERE endpoint = ?",
            (_serialize_prefs(prefs), last_active, endpoint),
        )
        db._write_conn.commit()
        return cursor.rowcount


def update_all_push_sub_prefs_by_client(client_id: str, prefs: dict, last_active: float) -> int:
    """Update prefs and last_active for ALL subscriptions belonging to a client_id."""
    if db._write_conn is None:
        return 0
    with db._write_lock:
        cursor = db._write_conn.execute(
            "UPDATE push_subscriptions SET prefs = ?, last_active = ? WHERE client_id = ?",
            (_serialize_prefs(prefs), last_active, client_id),
        )
        db._write_conn.commit()
        return cursor.rowcount


def all_push_subs() -> list[dict]:
    """Get all push subscriptions (for push sending)."""
    with db._ReadConn() as conn:
        rows = conn.execute("SELECT * FROM push_subscriptions").fetchall()
        return [_row_to_push_sub(r) for r in rows]
