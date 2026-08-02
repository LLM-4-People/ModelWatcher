"""Audit and probe result persistence operations.

Split from backend.db - audit_results and probe_results tables are a
self-contained concern. Imports the shared write connection and lock
from backend.db.
"""

import time

import orjson

from backend.state import log, log_error
import backend.db as db


# ── Audit results ────────────────────────────────────────────────────────────

def insert_audit_result(model_key: str, result: dict):
    """Insert an audit result row. Called via asyncio.to_thread."""
    if db._write_conn is None:
        log.error("insert_audit_result: DB write connection is None - result DROPPED for %s", model_key)
        return
    suites_json = orjson.dumps(result.get("suites", [])).decode()
    with db._write_lock:
        db._write_conn.execute(
            "INSERT INTO audit_results (model_key, ts_epoch, passed, total, pass_rate, success, duration_ms, error, suites_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (model_key, result["ts_epoch"],
             result.get("passed", 0), result.get("total", 0), result.get("pass_rate", 0.0),
             1 if result.get("success", False) else 0,
             result.get("duration_ms"), result.get("error"), suites_json),
        )
        db._write_conn.commit()


def get_audit_history(model_key: str, limit: int = 50, since: float | None = None) -> list[dict]:
    """Get audit results for a model, oldest-first."""
    with db._ReadConn() as conn:
        conditions = ["model_key = ?"]
        params: list = [model_key]
        if since is not None:
            conditions.append("ts_epoch >= ?")
            params.append(since)
        where = " AND ".join(conditions)
        rows = conn.execute(
            f"SELECT * FROM (SELECT * FROM audit_results WHERE {where} "
            f"ORDER BY ts_epoch DESC LIMIT ?) ORDER BY ts_epoch ASC",
            (*params, limit),
        ).fetchall()
        return [_audit_row_to_dict(r) for r in rows]


def get_latest_audit_results() -> dict[str, dict]:
    """Get the most recent audit result per model."""
    with db._ReadConn() as conn:
        rows = conn.execute(
            "SELECT a.* FROM audit_results a "
            "INNER JOIN (SELECT model_key, MAX(ts_epoch) AS max_ts FROM audit_results GROUP BY model_key) latest "
            "ON a.model_key = latest.model_key AND a.ts_epoch = latest.max_ts"
        ).fetchall()
        return {r["model_key"]: _audit_row_to_dict(r) for r in rows}


def delete_old_audit_results(cutoff_epoch: float) -> int:
    """Delete audit results older than cutoff. Returns count deleted."""
    if db._write_conn is None:
        return 0
    with db._write_lock:
        cur = db._write_conn.execute("DELETE FROM audit_results WHERE ts_epoch < ?", (cutoff_epoch,))
        db._write_conn.commit()
        return cur.rowcount


def _audit_row_to_dict(row) -> dict:
    d = dict(row)
    d.pop("id", None)
    if "success" in d and d["success"] is not None:
        d["success"] = bool(d["success"])
    sj = d.pop("suites_json", None)
    d["suites"] = orjson.loads(sj) if sj else []
    return {k: v for k, v in d.items() if v is not None}


# ── Probe results ──────────────────────────────────────────────────────────────

def insert_probe_result(model_key: str, result: dict):
    """Insert a probe result row. Called via asyncio.to_thread."""
    if db._write_conn is None:
        log.error("insert_probe_result: DB write connection is None - result DROPPED for %s", model_key)
        return
    rm = result.get("response_meta")
    rm_json = orjson.dumps(rm).decode() if rm else None
    with db._write_lock:
        db._write_conn.execute(
            "INSERT INTO probe_results "
            "(model_key, ts_epoch, provider, success, supports_vision, supports_tools, "
            "supports_structured_output, supports_cache, thinking, reasoning_field, "
            "system_fingerprint, served_by, engine_version, tensor_parallel, served_model, "
            "quantization, fp_server, fp_features, "
            "error, duration_ms, response_meta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (model_key, result.get("ts_epoch", time.time()),
             result.get("provider", ""),
             1 if result.get("success") else 0,
             1 if result.get("supports_vision") else (0 if result.get("supports_vision") is False else None),
             1 if result.get("supports_tools") else (0 if result.get("supports_tools") is False else None),
             1 if result.get("supports_structured_output") else (0 if result.get("supports_structured_output") is False else None),
             1 if result.get("supports_cache") else (0 if result.get("supports_cache") is False else None),
             1 if result.get("thinking") else (0 if result.get("thinking") is False else None),
             result.get("reasoning_field"),
             result.get("system_fingerprint"), result.get("served_by"),
             result.get("engine_version"), result.get("tensor_parallel"),
             result.get("served_model"),
             result.get("quantization"), result.get("fp_server"), result.get("fp_features"),
             result.get("error"), result.get("duration_ms"), rm_json),
        )
        db._write_conn.commit()


def get_latest_probe_results() -> dict[str, dict]:
    """Get the most recent probe result per model."""
    with db._ReadConn() as conn:
        rows = conn.execute(
            "SELECT p.* FROM probe_results p "
            "INNER JOIN (SELECT model_key, MAX(ts_epoch) AS max_ts FROM probe_results GROUP BY model_key) latest "
            "ON p.model_key = latest.model_key AND p.ts_epoch = latest.max_ts"
        ).fetchall()
        return {r["model_key"]: _probe_row_to_dict(r) for r in rows}


def get_probe_history(model_key: str, limit: int = 20, since: float | None = None) -> list[dict]:
    """Get probe results for a model, oldest-first."""
    with db._ReadConn() as conn:
        conditions = ["model_key = ?"]
        params: list = [model_key]
        if since is not None:
            conditions.append("ts_epoch >= ?")
            params.append(since)
        where = " AND ".join(conditions)
        rows = conn.execute(
            f"SELECT * FROM (SELECT * FROM probe_results WHERE {where} "
            f"ORDER BY ts_epoch DESC LIMIT ?) ORDER BY ts_epoch ASC",
            (*params, limit),
        ).fetchall()
        return [_probe_row_to_dict(r) for r in rows]


def delete_old_probe_results(cutoff_epoch: float) -> int:
    """Delete probe results older than cutoff. Returns count deleted."""
    if db._write_conn is None:
        return 0
    with db._write_lock:
        cur = db._write_conn.execute("DELETE FROM probe_results WHERE ts_epoch < ?", (cutoff_epoch,))
        db._write_conn.commit()
        return cur.rowcount


def _probe_row_to_dict(row) -> dict:
    d = dict(row)
    d.pop("id", None)
    for k in ("success", "supports_vision", "supports_tools", "supports_structured_output", "supports_cache", "thinking"):
        if k in d and d[k] is not None:
            v = d[k]
            d[k] = bool(int(v)) if isinstance(v, str) else bool(v)
    rm = d.pop("response_meta", None)
    if rm:
        try:
            d["response_meta"] = orjson.loads(rm)
        except Exception as e:
            log.debug("Corrupt response_meta JSON in probe_results for %s", d.get("model_key", "?"))
    return {k: v for k, v in d.items() if v is not None}
