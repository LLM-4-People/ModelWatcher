"""SQLite database layer - all persistence operations in one module.

Uses WAL mode for concurrent reads, single writer via asyncio.to_thread().
No other module should import sqlite3 directly.
"""

import asyncio
import os
import sqlite3
import threading
import time
from pathlib import Path

import orjson

from backend.state import (
    c, DATA_DIR, log, log_error, model_cache,
    invalidate_metrics_cache, strip_internal,
    TEST_HEALTH, TEST_BENCHMARK,
    parse_model_key, update_healthy_model_count,
)
from backend.batch import PeriodicBatcher
from backend.stats import compute_reliability_score, compute_trends, bench_only, RESULT_KEY_TO_THRESHOLD

def _serialize_trends(trends) -> str | None:
    return orjson.dumps(trends).decode() if trends else None


_db_name = os.environ.get("MW_DB_NAME", "metrics.db")
SQLITE_PATH = DATA_DIR / _db_name
_PAGE_SIZE = 16384
_db_path = SQLITE_PATH

_DB_INTERNAL = frozenset(("error_trace", "id", "rn", "trends_json"))

# ── Result record → column mapping ─────────────────────────────────────────

_RESULT_COLUMNS = (
    "model_key", "provider", "ts_epoch", "timestamp",
    "available", "success",
    "ttft_ms", "tps", "itl_reliable",
    "tpot_ms", "total_latency_ms",
    "token_count", "completion_tokens", "reasoning_tokens",
    "chunk_token_ratio", "chunk_token_cv", "chunk_token_max", "finish_reason",
    "stall_count", "hiccup_count",
    "raw_max_itl_ms", "raw_median_itl_ms", "raw_avg_itl_ms",
    "raw_p99_itl_ms", "effective_median_itl_ms", "effective_avg_itl_ms", "effective_p99_itl_ms",
    "effective_itl_tail_ratio", "effective_itl_tail_ratio_estimated",
    "network_rtt_ms", "thinking_duration_ms",
    "degraded", "degraded_reason", "critical_metrics",
    "retry_attempt", "retry_total", "retry_count",
    "error", "error_trace",
    "test_type",
    "consistency_score", "speed_score",
    "stall_first_pct", "stall_last_pct", "stall_clusters", "stall_ratio",
    "network_jitter_ms", "burst_arrivals", "burst_arrival_pct",
    "shrinkage_factor",
    "frame_batch_pct",
    "request_id",
)

_HISTORY_TABLE_COLS = (
    "model_key", "provider", "ts_epoch", "timestamp",
    "test_type",
    *RESULT_KEY_TO_THRESHOLD.keys(),
    "effective_itl_tail_ratio_estimated",
    "network_jitter_ms",
    "available", "success", "degraded", "degraded_reason",
    "critical_metrics",
    "retry_attempt", "retry_total",
    "error",
    "request_id",
)

_SORT_COLUMNS = {
    "time": "ts_epoch",
    "ttft": "ttft_ms",
    "tps": "tps",
    "stalls": "stall_count",
    "p99": "raw_p99_itl_ms",
    "batch": "chunk_token_ratio",
    "tail": "effective_itl_tail_ratio",
}

_DEFAULT_SORT = "ts_epoch DESC"


def parse_sort(sort_param: str) -> str:
    clauses = []
    for field in sort_param.split(","):
        field = field.strip()
        if not field:
            continue
        desc = field.startswith("-")
        key = field.lstrip("-")
        col = _SORT_COLUMNS.get(key)
        if col:
            clauses.append(f"{col} {'DESC' if desc else 'ASC'}")
    if not clauses:
        return _DEFAULT_SORT
    clauses.append("ts_epoch DESC")
    return ", ".join(clauses)

_INSERT_SQL = (
    f"INSERT INTO test_results ({','.join(_RESULT_COLUMNS)}) "
    f"VALUES ({','.join('?' for _ in _RESULT_COLUMNS)})"
)


def _record_to_row(model_key: str, record: dict) -> tuple:
    """Convert a result dict to a row tuple matching _RESULT_COLUMNS.

    Required booleans (available, success, degraded) default to 0 when missing;
    optional booleans (itl_reliable, effective_itl_tail_ratio_estimated) default
    to NULL so strip_internal() removes them from health records at serve time.
    """
    provider = parse_model_key(model_key)[0]
    row = []
    for col in _RESULT_COLUMNS:
        if col == "model_key":
            row.append(model_key)
        elif col == "provider":
            row.append(provider)
        elif col == "ts_epoch":
            row.append(_extract_ts(record))
        elif col in ("critical_metrics",):
            val = record.get(col)
            row.append(orjson.dumps(val).decode() if val is not None else None)
        elif col in ("available", "success", "degraded"):
            val = record.get(col)
            row.append(int(val) if val is not None else 0)
        elif col == "stall_clusters":
            val = record.get(col)
            row.append(int(val) if val is not None else None)
        elif col in ("itl_reliable", "effective_itl_tail_ratio_estimated"):
            val = record.get(col)
            row.append(int(val) if val is not None else None)
        else:
            row.append(record.get(col))
    return tuple(row)


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a dict, decoding JSON columns.

    Strips DB-internal fields (error_trace, id, rn) and null-valued fields
    so data from SQLite is already in its final form - no post-processing
    needed at serve time.
    """
    d = dict(row)
    cm = d.pop("critical_metrics", None)
    if cm is not None:
        try:
            d["critical_metrics"] = orjson.loads(cm)
        except Exception as e:
            log_error("Corrupt critical_metrics JSON in DB, setting to None", e)
            d["critical_metrics"] = None
    # Convert integer booleans back to bool for API compatibility
    for key in ("available", "success", "degraded",
                "itl_reliable", "effective_itl_tail_ratio_estimated"):
        if key in d and d[key] is not None:
            v = d[key]
            d[key] = bool(int(v)) if isinstance(v, str) else bool(v)
    # Strip DB-internal keys and null values - data from SQLite is ready to serve
    d = {k: v for k, v in d.items() if k not in _DB_INTERNAL and v is not None}
    return d


# ── Schema ──────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS test_results (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    model_key               TEXT NOT NULL,
    provider                TEXT NOT NULL,
    ts_epoch                REAL NOT NULL,
    timestamp               TEXT NOT NULL,
    available               INTEGER NOT NULL,
    success                 INTEGER NOT NULL,
    ttft_ms                 REAL,
    tps                     REAL,
    itl_reliable            INTEGER DEFAULT 0,
    tpot_ms                 REAL,
    total_latency_ms        REAL,
    token_count             INTEGER,
    completion_tokens       INTEGER,
    reasoning_tokens        INTEGER,
    chunk_token_ratio       REAL,
    chunk_token_cv          REAL,
    chunk_token_max         INTEGER,
    finish_reason           TEXT,
    stall_count             INTEGER,
    hiccup_count            INTEGER,
    raw_max_itl_ms          REAL,
    raw_median_itl_ms       REAL,
    raw_avg_itl_ms          REAL,
    raw_p99_itl_ms          REAL,
    effective_median_itl_ms  REAL,
    effective_avg_itl_ms    REAL,
    effective_p99_itl_ms    REAL,
    effective_itl_tail_ratio REAL,
    effective_itl_tail_ratio_estimated INTEGER DEFAULT 0,
    network_rtt_ms          REAL,
    thinking_duration_ms    REAL,
    degraded                INTEGER DEFAULT 0,
    degraded_reason         TEXT,
    critical_metrics        TEXT,
    retry_attempt           INTEGER,
    retry_total             INTEGER,
    retry_count             INTEGER,
    error                   TEXT,
    error_trace             TEXT,
    test_type               TEXT DEFAULT 'benchmark',
    consistency_score       REAL,
    speed_score             REAL,
    stall_first_pct         REAL,
    stall_last_pct          REAL,
    stall_clusters          INTEGER DEFAULT 0,
    stall_ratio             REAL,
    network_jitter_ms       REAL,
    burst_arrivals          INTEGER,
    burst_arrival_pct       REAL,
    shrinkage_factor        REAL,
    frame_batch_pct         REAL,
    request_id              TEXT
);

CREATE INDEX IF NOT EXISTS idx_results_model_ts ON test_results (model_key, ts_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_results_epoch ON test_results (ts_epoch);
CREATE INDEX IF NOT EXISTS idx_results_provider_ts ON test_results (provider, ts_epoch DESC);

CREATE TABLE IF NOT EXISTS model_state (
    model_key       TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'unknown',
    degraded_source TEXT,
    uptime_pct      REAL,
    total_tests     INTEGER NOT NULL DEFAULT 0,
    total_success   INTEGER NOT NULL DEFAULT 0,
    first_ts_epoch  REAL,
    reliability_score REAL,
    trends_json     TEXT,
    archived        INTEGER NOT NULL DEFAULT 0,
    updated_at      REAL
);

CREATE TABLE IF NOT EXISTS providers (
    name            TEXT PRIMARY KEY,
    api_url         TEXT,
    page_title      TEXT,
    logo_path       TEXT,
    last_fetched_at REAL,
    extra           TEXT
);

CREATE TABLE IF NOT EXISTS model_info (
    model_key       TEXT PRIMARY KEY,
    provider        TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    display_name    TEXT,
    context_window  INTEGER,
    output_context  INTEGER,
    supports_cache  INTEGER,
    supports_vision INTEGER,
    supports_tools  INTEGER,
    supports_structured_output INTEGER,
    input_price     REAL,
    output_price    REAL,
    cache_price     REAL,
    description     TEXT,
    modalities      TEXT,
    tokenizer       TEXT,
    reasoning_price REAL,
    image_price     REAL,
    created         REAL,
    owner           TEXT,
    license         TEXT,
    thinking        TEXT,
    quantization    TEXT,
    served_by       TEXT,
    architecture    TEXT,
    param_count     TEXT,
    num_experts     INTEGER,
    num_experts_per_tok INTEGER,
    num_shared_experts  INTEGER,
    moe_intermediate_size INTEGER,
    last_fetched_at REAL,
    updated_at      REAL,
    fingerprint     TEXT,
    engine_version  TEXT,
    tensor_parallel INTEGER,
    served_model    TEXT,
    fp_server       TEXT,
    fp_features     TEXT
);

CREATE INDEX IF NOT EXISTS idx_model_info_provider ON model_info (provider);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint        TEXT PRIMARY KEY,
    p256dh          TEXT NOT NULL,
    auth            TEXT NOT NULL,
    client_id       TEXT NOT NULL,
    prefs           TEXT NOT NULL,
    created_at      REAL NOT NULL,
    last_active     REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_push_client ON push_subscriptions (client_id);

CREATE TABLE IF NOT EXISTS audit_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_key       TEXT NOT NULL,
    ts_epoch        REAL NOT NULL,
    passed          INTEGER NOT NULL,
    total           INTEGER NOT NULL,
    pass_rate       REAL NOT NULL,
    success         INTEGER NOT NULL DEFAULT 1,
    duration_ms     REAL,
    error           TEXT,
    suites_json     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_model_ts ON audit_results (model_key, ts_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_audit_epoch ON audit_results (ts_epoch);

CREATE TABLE IF NOT EXISTS probe_results (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    model_key                   TEXT NOT NULL,
    ts_epoch                    REAL NOT NULL,
    provider                    TEXT NOT NULL,
    success                     INTEGER NOT NULL DEFAULT 0,
    supports_vision             INTEGER,
    supports_tools              INTEGER,
    supports_structured_output  INTEGER,
    supports_cache              INTEGER,
    thinking                    INTEGER,
    reasoning_field             TEXT,
    system_fingerprint          TEXT,
    served_by                   TEXT,
    engine_version              TEXT,
    tensor_parallel             INTEGER,
    served_model                TEXT,
    quantization                TEXT,
    fp_server                   TEXT,
    fp_features                 TEXT,
    error                       TEXT,
    duration_ms                 REAL,
    response_meta               TEXT
);

CREATE INDEX IF NOT EXISTS idx_probe_model_ts ON probe_results (model_key, ts_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_probe_epoch ON probe_results (ts_epoch);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    applied_at  REAL NOT NULL
);
"""


# ── Connection management ───────────────────────────────────────────────────

_write_conn: sqlite3.Connection | None = None
_write_lock = threading.RLock()
_READ_POOL_SIZE = 2
_read_pool: list[sqlite3.Connection] = []
_read_pool_lock = threading.Lock()


def _apply_pragmas(conn: sqlite3.Connection, *, read_only: bool = False, reduced: bool = False):
    """Set WAL mode, cache size, and busy timeout on a connection.

    read_only connections skip journal_mode/synchronous/wal_autocheckpoint (read-only).
    reduced=True uses smaller cache/mmap for transient resource pressure recovery.
    """
    if not read_only:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA wal_autocheckpoint = 5000")
    cache = -16384 if reduced else -131072
    mmap = 268435456 if reduced else 4294967296
    for sql in (
        f"PRAGMA cache_size = {cache}",
        f"PRAGMA mmap_size = {mmap}",
        "PRAGMA busy_timeout = 5000",
        "PRAGMA temp_store = MEMORY",
        "PRAGMA foreign_keys = ON",
    ):
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            if "foreign_keys" in sql or "busy_timeout" in sql:
                raise
            log.warning("PRAGMA failed (reduced=%s): %s", reduced, sql)


def init(db_path: Path | None = None):
    """Open the database, set PRAGMAs, create tables. Call once at startup."""
    global _write_conn, _db_path
    from backend import migrations

    path = db_path or SQLITE_PATH
    _db_path = path
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = sqlite3.connect(str(path))
    cur = tmp.execute("PRAGMA page_size")
    current_ps = cur.fetchone()[0]
    if current_ps != _PAGE_SIZE:
        tmp.execute(f"PRAGMA page_size = {_PAGE_SIZE}")
        tmp.execute("VACUUM")
        log.info("VACUUMed database to apply page_size change: %d -> %d", current_ps, _PAGE_SIZE)
    _apply_pragmas(tmp)

    tmp.executescript(_SCHEMA_SQL)
    migrations.run_migrations(tmp)
    tmp.execute("PRAGMA optimize")
    tmp.close()

    _write_conn = sqlite3.connect(str(path), check_same_thread=False)
    _apply_pragmas(_write_conn)
    _write_conn.row_factory = sqlite3.Row
    log.info("Database initialized: %s (page_size=%d)", path, _PAGE_SIZE)


def close():
    """Close database connections cleanly. Call at shutdown."""
    global _write_conn
    if _write_conn:
        try:
            _write_conn.execute("PRAGMA optimize")
            _write_conn.close()
        except Exception as e:
            log_error("DB shutdown close failed", e)
        _write_conn = None
    with _read_pool_lock:
        while _read_pool:
            conn = _read_pool.pop()
            try:
                conn.close()
            except Exception as e:
                log_error("DB read connection close failed", e)


def _read_conn() -> sqlite3.Connection:
    """Get a read connection from the persistent pool.

    Uses a small pool of long-lived connections. In WAL mode, readers never
    block writers and vice versa. Each connection gets a fresh snapshot by
    beginning a new transaction on first use after checkout.

    Validates pooled connections with a lightweight PRAGMA before returning
    them - broken connections (from disk I/O errors, etc.) are discarded
    and a fresh one is created.

    On I/O errors during new connection creation, retries once with reduced
    PRAGMA settings (smaller cache/mmap) to handle transient resource pressure.
    """
    with _read_pool_lock:
        while _read_pool:
            conn = _read_pool.pop()
            try:
                conn.execute("SELECT 1").fetchone()
                return conn
            except sqlite3.OperationalError:
                try:
                    conn.close()
                except Exception as e:
                    log_error("Failed to close broken read connection", e)
                continue
    for attempt in range(2):
        reduced = attempt > 0
        try:
            conn = sqlite3.connect(str(_db_path), check_same_thread=False)
            _apply_pragmas(conn, read_only=True, reduced=reduced)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.OperationalError:
            try:
                conn.close()
            except Exception as e:
                log_error("Failed to close failed read connection", e)
            if reduced:
                raise
            log.warning("Read connection failed with default PRAGMAs, retrying reduced")
    raise sqlite3.OperationalError("Read connection failed with reduced PRAGMAs")


def _return_read_conn(conn: sqlite3.Connection | None, *, discard: bool = False):
    """Return a read connection to the pool.

    If *discard* is True (or conn is None), the connection is closed instead
    of pooled - used when the connection hit an error and may be in a bad state.
    """
    if conn is None:
        return
    if discard:
        try:
            conn.close()
        except Exception as e:
            log_error("Failed to close discarded read connection", e)
        return
    with _read_pool_lock:
        if len(_read_pool) < _READ_POOL_SIZE:
            _read_pool.append(conn)
            return
    conn.close()


class _ReadConn:
    """Context manager for pooled read connections. Replaces try/finally boilerplate."""

    __slots__ = ("_conn", "_discard")

    def __init__(self, discard: bool = False):
        self._conn: sqlite3.Connection | None = None
        self._discard = discard

    def __enter__(self) -> sqlite3.Connection:
        self._conn = _read_conn()
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        discard = self._discard or exc_type is not None
        _return_read_conn(self._conn, discard=discard)
        self._conn = None
        return False


# ── Test results: write ────────────────────────────────────────────────────

def insert_result(model_key: str, record: dict):
    """Insert a test result row. Must be called from thread executor."""
    if _write_conn is None:
        log.error("insert_result: DB write connection is None - result DROPPED for %s", model_key)
        return
    with _write_lock:
        _write_conn.execute(_INSERT_SQL, _record_to_row(model_key, record))


def upsert_model_state(model_key: str, state_kwargs: dict):
    """Insert or update model state row. Must be called from thread executor.

    The ``archived`` column is intentionally excluded from the ON CONFLICT
    UPDATE SET - it is managed exclusively by ``set_archived()`` so test-result
    writes never clobber the archive flag.
    """
    if _write_conn is None:
        log.error("upsert_model_state: DB write connection is None for %s", model_key)
        return
    now = time.time()
    tt = state_kwargs.get("total_tests", 0)
    ts_ok = state_kwargs.get("total_success", 0)
    with _write_lock:
        _write_conn.execute(
            "INSERT INTO model_state (model_key, status, degraded_source, uptime_pct, total_tests, total_success, first_ts_epoch, reliability_score, trends_json, archived, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?) "
            "ON CONFLICT (model_key) DO UPDATE SET "
            "status = excluded.status, degraded_source = excluded.degraded_source, uptime_pct = excluded.uptime_pct, "
            "total_tests = excluded.total_tests, total_success = excluded.total_success, "
            "first_ts_epoch = COALESCE(model_state.first_ts_epoch, excluded.first_ts_epoch), "
            "reliability_score = COALESCE(excluded.reliability_score, model_state.reliability_score), "
            "trends_json = COALESCE(excluded.trends_json, model_state.trends_json), "
            "updated_at = excluded.updated_at",
            (model_key, state_kwargs["status"], state_kwargs.get("degraded_source"),
             state_kwargs.get("uptime_pct"), tt, ts_ok,
             state_kwargs.get("first_ts_epoch"), state_kwargs.get("reliability_score"),
             state_kwargs.get("trends_json"), now),
        )


def set_archived(model_keys: set[str], archived: bool):
    """Set the archived flag for multiple models, persisting to SQLite.

    Creates model_state rows if they don't exist (with default status='unknown').
    Must be called from thread executor.
    """
    if not model_keys or _write_conn is None:
        return
    val = 1 if archived else 0
    with _write_lock:
        _write_conn.executemany(
            "INSERT INTO model_state (model_key, archived) VALUES (?, ?) "
            "ON CONFLICT (model_key) DO UPDATE SET archived = excluded.archived",
            [(k, val) for k in model_keys],
        )
        _write_conn.commit()


def load_all_archived() -> set[str]:
    """Return set of model_keys with archived=1. Must be called from thread executor."""
    with _ReadConn() as conn:
        return {r["model_key"] for r in conn.execute(
            "SELECT model_key FROM model_state WHERE archived = 1"
        ).fetchall()}


def prune_trailing_failures(model_keys: set[str]) -> dict[str, int]:
    """Delete trailing failed test_results after each model's last success.

    For each model, finds the most recent successful test (available=1 AND
    success=1) and deletes every test_result with a strictly later timestamp,
    trimming the final run of failures that preceded archiving while
    preserving the full successful history.  Models without any successful
    test are left untouched.

    Recalculates model_state (counters, uptime, status) for affected models
    and syncs the in-memory model_cache so unarchiving later shows correct
    data immediately.  Must be called from the thread executor.
    Returns {model_key: deleted_count}.
    """
    if not model_keys or _write_conn is None:
        return {}
    deleted: dict[str, int] = {}
    cutoffs: dict[str, float] = {}
    recompute: dict[str, tuple] = {}
    with _write_lock:
        for mk in model_keys:
            row = _write_conn.execute(
                "SELECT MAX(ts_epoch) AS last_ts FROM test_results "
                "WHERE model_key = ? AND available = 1 AND success = 1",
                (mk,),
            ).fetchone()
            last_ts = row["last_ts"] if row else None
            if last_ts is None:
                continue
            cur = _write_conn.execute(
                "DELETE FROM test_results WHERE model_key = ? AND ts_epoch > ?",
                (mk, last_ts),
            )
            n = cur.rowcount
            if n:
                deleted[mk] = n
                cutoffs[mk] = last_ts
        if deleted:
            for mk in deleted:
                recompute[mk] = _recompute_model_state(mk, _write_conn)
            _write_conn.commit()
    # Sync in-memory cache (safe: archived models have no concurrent writers)
    for mk, last_ts in cutoffs.items():
        _sync_cache_after_prune(mk, last_ts, recompute.get(mk))
        log.info("Pruned %d trailing failure(s) for archived model %s", deleted[mk], mk)
    return deleted


def _recompute_model_state(model_key: str, conn) -> tuple:
    """Recalculate model_state counters, uptime, and status from test_results.

    Uses SQL directly for uptime (not calc_uptime_pct) because the in-memory
    recent_history may still hold stale entries at call time.
    """
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM test_results WHERE model_key = ?", (model_key,)
    ).fetchone()["c"]
    ok = conn.execute(
        "SELECT COUNT(*) AS c FROM test_results "
        "WHERE model_key = ? AND available = 1 AND success = 1",
        (model_key,),
    ).fetchone()["c"]
    status, degraded_source = "unknown", None
    for r in conn.execute(
        "SELECT test_type, available, degraded, retry_attempt FROM test_results "
        "WHERE model_key = ? ORDER BY ts_epoch ASC, id ASC",
        (model_key,),
    ).fetchall():
        status, degraded_source = _derive_status_from_result(
            status, degraded_source,
            r["test_type"], bool(r["available"]), bool(r["degraded"]),
            r["retry_attempt"],
        )
    w = c.uptime_window
    cutoff = time.time() - w
    urow = conn.execute(
        "SELECT SUM(available) AS ok, COUNT(*) AS total FROM test_results "
        "WHERE model_key = ? AND ts_epoch > ? AND retry_attempt IS NULL",
        (model_key, cutoff),
    ).fetchone()
    uptime = round(urow["ok"] * 100.0 / urow["total"], 1) if urow and urow["total"] else None
    conn.execute(
        "UPDATE model_state SET total_tests = ?, total_success = ?, status = ?, "
        "degraded_source = ?, uptime_pct = ? WHERE model_key = ?",
        (total, ok, status, degraded_source, uptime, model_key),
    )
    return total, ok, status, degraded_source, uptime


def _sync_cache_after_prune(model_key: str, cutoff_ts: float, recompute_info):
    """Sync in-memory model_cache after trailing failures were pruned from DB."""
    ce = model_cache.get(model_key)
    if ce is None:
        return
    # Filter recent_history to remove pruned entries
    rh = ce.get("recent_history", [])
    if rh:
        ce["recent_history"] = [
            r for r in rh
            if (r.get("ts_epoch") or r.get("_ts_epoch") or 0) <= cutoff_ts
        ]
    # Re-derive last_test + benchmark epoch from pruned history (oldest-first)
    bench_rh = bench_only(ce["recent_history"])
    if bench_rh:
        last_b = bench_rh[-1]
        ce["last_test"] = last_b
        ce["last_benchmark_epoch"] = last_b.get("ts_epoch") or last_b.get("_ts_epoch")
    else:
        ce["last_test"] = None
        ce["last_benchmark_epoch"] = None
    # last_success_test is at or before cutoff - unchanged
    # Re-derive health fields from pruned history
    health_rh = [r for r in ce["recent_history"] if r.get("test_type") == TEST_HEALTH]
    if health_rh:
        last_h = health_rh[-1]
        ce["last_health_epoch"] = last_h.get("ts_epoch") or last_h.get("_ts_epoch")
        ok_h = bool(last_h.get("available", False) and last_h.get("success", False))
        ce["last_health_success"] = ok_h
        ce["last_health_error"] = last_h.get("error") if not ok_h else None
        ce["last_health_ttft_ms"] = last_h.get("ttft_ms")
    else:
        ce["last_health_epoch"] = None
        ce["last_health_success"] = None
        ce["last_health_error"] = None
        ce["last_health_ttft_ms"] = None
    # Counters + status + uptime from DB recompute
    if recompute_info:
        total, ok, status, dsrc, uptime = recompute_info
        ce["total_tests"] = total
        ce["total_success"] = ok
        ce["status"] = status
        ce["degraded_source"] = dsrc
        ce["uptime_pct"] = uptime
    # Clear degradation tracking - trailing failures that set these are gone
    ce["degraded_since"] = None
    ce["tps_degraded_since"] = None
    ce["ttft_degraded_since"] = None
    # Recompute trends + reliability from pruned history
    ce["trends"] = compute_trends(bench_rh) if bench_rh else {}
    ce["reliability_score"] = compute_reliability_score(model_key)
    ce["_scores_version"] = ce.get("_scores_version", 0) + 1
    ce["_cached_scores"] = None
    ce["_card_buckets"] = None


def commit():
    """Commit the current transaction. Must be called from thread executor."""
    if _write_conn is None:
        log.error("commit: DB write connection is None")
        return
    with _write_lock:
        _write_conn.commit()


def persist_trends(trends_map: dict[str, str], reliability_map: dict[str, float] | None = None):
    """Batch-update trends_json and optionally reliability_score in model_state.

    trends_map: {model_key: trends_json_string}
    reliability_map: {model_key: reliability_score} (optional)
    Must be called from thread executor.
    """
    if _write_conn is None or not trends_map:
        return
    with _write_lock:
        for mk, tj in trends_map.items():
            _write_conn.execute(
                "UPDATE model_state SET trends_json = ? WHERE model_key = ?",
                (tj, mk),
            )
        if reliability_map:
            for mk, rs in reliability_map.items():
                _write_conn.execute(
                    "UPDATE model_state SET reliability_score = ? WHERE model_key = ?",
                    (rs, mk),
                )
        _write_conn.commit()


# ── Test results: read ─────────────────────────────────────────────────────


def get_resume_attempt(model_key: str, max_age: float, test_type: str = TEST_BENCHMARK) -> int:
    """Return the 0-indexed attempt to resume from for an interrupted retry cycle.

    Returns 0 (start fresh) if the last record is a final result, if there are
    no records, or if the last retry is older than *max_age* seconds.  Returns
    the next attempt index (equal to retry_attempt of the last retry record)
    when a recent interrupted retry cycle is detected.

    Only considers retry records matching the given test_type.
    On transient DB errors (disk I/O, locked), logs a warning and returns 0
    so the test can proceed rather than being silently abandoned.
    """
    try:
        with _ReadConn() as conn:
            row = conn.execute(
                "SELECT MAX(ts_epoch) AS t FROM test_results "
                "WHERE model_key = ? AND retry_attempt IS NULL AND test_type = ?",
                (model_key, test_type),
            ).fetchone()
            last_final = row["t"] if row and row["t"] else 0

            row = conn.execute(
                "SELECT MAX(retry_attempt) AS ra, MAX(ts_epoch) AS t "
                "FROM test_results "
                "WHERE model_key = ? AND retry_attempt IS NOT NULL AND ts_epoch > ? AND test_type = ?",
                (model_key, last_final, test_type),
            ).fetchone()
            if not row or row["ra"] is None:
                return 0
            if row["t"] is not None and (time.time() - row["t"]) > max_age:
                return 0
            return row["ra"]
    except sqlite3.OperationalError as e:
        log.warning("get_resume_attempt DB error for %s (starting fresh): %s", model_key, e)
        return 0


def get_model_history(model_key: str, limit: int = 500,
                      test_type: str | None = None,
                      since: float | None = None,
                      until: float | None = None,
                      before: float | None = None,
                      columns: tuple | None = None,
                      sort: str | None = None) -> list[dict]:
    """Get recent test results for a model, newest-first by default.

    Filters: test_type, since (>=), until (<=), before (< for cursor pagination).
    columns selects specific columns; sort applies whitelist-validated ordering
    (dash-prefix for descending, e.g. '-tps').
    """
    conditions = ["model_key = ?"]
    params: list = [model_key]
    if test_type:
        conditions.append("test_type = ?")
        params.append(test_type)
    if since is not None:
        conditions.append("ts_epoch >= ?")
        params.append(since)
    if until is not None:
        conditions.append("ts_epoch <= ?")
        params.append(until)
    if before is not None:
        conditions.append("ts_epoch < ?")
        params.append(before)
    where = " AND ".join(conditions)
    params.append(limit)
    select = ", ".join(columns) if columns else "*"
    order = parse_sort(sort) if sort else _DEFAULT_SORT
    with _ReadConn() as conn:
        rows = conn.execute(
            f"SELECT {select} FROM test_results WHERE {where} ORDER BY {order} LIMIT ?",
            params,
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def query_time_range(model_key: str, test_type: str, since: float,
                     until: float | None = None) -> tuple[float, float] | None:
    """Return (min_ts_epoch, max_ts_epoch) for a model's test results since `since`.
    If until is specified, only return results with ts_epoch <= until.
    Returns None if no results found."""
    until_clause = "AND ts_epoch <= ?" if until else ""
    until_args: tuple = (until,) if until else ()
    with _ReadConn() as conn:
        row = conn.execute(
            f"SELECT MIN(ts_epoch) AS mn, MAX(ts_epoch) AS mx "
            f"FROM test_results WHERE model_key = ? AND ts_epoch >= ? "
            f"AND test_type = ? AND retry_attempt IS NULL {until_clause}",
            (model_key, since, test_type, *until_args)).fetchone()
        if row and row["mn"] is not None and row["mx"] is not None:
            return (row["mn"], row["mx"])
        return None


def query_bucketed_history(model_key: str, since: float, bucket_width: float,
                            test_type: str = "benchmark",
                            detail: str = "card",
                            view: str = "speed",
                            until: float | None = None) -> list[dict]:
    """SQL-based bucketed aggregation for ranges exceeding recent_history.

    Uses MIN/MAX as approximations for P10/P90 percentile bands (accurate for
    buckets with few data points, which is the typical case in chart buckets).
    detail: "card" (flat metrics) or "modal" (nested avg/p10/p90).
    view: "speed", "consistency", "scores", or "health" - determines which
          metrics to SELECT in the card path.
    until: optional upper bound on ts_epoch.
    Returns list of bucket dicts ordered by time ASC.
    """
    if bucket_width <= 0:
        return []
    until_clause = "AND ts_epoch <= ?" if until else ""
    until_args: tuple = (until,) if until else ()
    with _ReadConn() as conn:
        if detail == "card":
            base_cols = """
                FLOOR(ts_epoch / ?) * ? AS bucket_ts,
                AVG(ts_epoch) AS ts,
                SUM(CASE WHEN available THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS available_rate,
                SUM(CASE WHEN degraded THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS degraded_rate,
                COUNT(*) AS count"""
            view_cols = {
                "speed": ", AVG(tps) AS tps, AVG(ttft_ms) AS ttft_ms, MIN(tps) AS tps_p10, MAX(tps) AS tps_p90, MIN(ttft_ms) AS ttft_ms_p10, MAX(ttft_ms) AS ttft_ms_p90",
                "consistency": ", AVG(raw_p99_itl_ms) AS raw_p99_itl_ms, AVG(chunk_token_ratio) AS chunk_token_ratio, MIN(raw_p99_itl_ms) AS raw_p99_itl_ms_p10, MAX(raw_p99_itl_ms) AS raw_p99_itl_ms_p90, MIN(chunk_token_ratio) AS chunk_token_ratio_p10, MAX(chunk_token_ratio) AS chunk_token_ratio_p90",
                "scores": ", AVG(consistency_score) AS consistency_score, AVG(speed_score) AS speed_score",
                "health": ", AVG(ttft_ms) AS ttft_ms, MIN(ttft_ms) AS ttft_ms_p10, MAX(ttft_ms) AS ttft_ms_p90",
            }
            extra = view_cols.get(view, view_cols["speed"])
            rows = conn.execute(f"""
                SELECT
                    {base_cols}
                    {extra}
                FROM test_results
                WHERE model_key = ? AND ts_epoch >= ? AND test_type = ? AND retry_attempt IS NULL
                    {until_clause}
                GROUP BY bucket_ts
                ORDER BY bucket_ts ASC
            """, (bucket_width, bucket_width, model_key, since, test_type, *until_args)).fetchall()
            result = []
            for r in rows:
                b = {"bucket_ts": r["bucket_ts"], "ts": r["ts"],
                     "available_rate": r["available_rate"],
                     "degraded_rate": r["degraded_rate"],
                     "count": r["count"]}
                for col in (view_cols.get(view, view_cols["speed"])
                           .lstrip(", ").split(", ")):
                    alias = col.split(" AS ")[-1].strip()
                    b[alias] = r[alias]
                # For single-point buckets, p10/p90 should equal the value
                if r["count"] == 1:
                    for field_key in ["tps", "ttft_ms", "raw_p99_itl_ms", "chunk_token_ratio"]:
                        if field_key in b and b[field_key] is not None:
                            p10_key = field_key + "_p10"
                            p90_key = field_key + "_p90"
                            if p10_key not in b or b[p10_key] is None:
                                b[p10_key] = b[field_key]
                            if p90_key not in b or b[p90_key] is None:
                                b[p90_key] = b[field_key]
                result.append(b)
            return result
        else:  # modal
            rows = conn.execute(f"""
                SELECT
                    FLOOR(ts_epoch / ?) * ? AS bucket_ts,
                    AVG(ts_epoch) AS ts,
                    AVG(tps) AS tps_avg, MIN(tps) AS tps_p10, MAX(tps) AS tps_p90,
                    AVG(ttft_ms) AS ttft_ms_avg, MIN(ttft_ms) AS ttft_ms_p10, MAX(ttft_ms) AS ttft_ms_p90,
                    AVG(raw_p99_itl_ms) AS raw_p99_itl_ms_avg, MIN(raw_p99_itl_ms) AS raw_p99_itl_ms_p10, MAX(raw_p99_itl_ms) AS raw_p99_itl_ms_p90,
                    AVG(chunk_token_ratio) AS chunk_token_ratio_avg,
                    MIN(chunk_token_ratio) AS chunk_token_ratio_p10,
                    MAX(chunk_token_ratio) AS chunk_token_ratio_p90,
                    AVG(stall_count) AS stall_count_avg,
                    MIN(stall_count) AS stall_count_p10,
                    MAX(stall_count) AS stall_count_p90,
                    AVG(effective_itl_tail_ratio) AS effective_itl_tail_ratio_avg,
                    MIN(effective_itl_tail_ratio) AS effective_itl_tail_ratio_p10,
                    MAX(effective_itl_tail_ratio) AS effective_itl_tail_ratio_p90,
                    AVG(consistency_score) AS consistency_score_avg,
                    AVG(speed_score) AS speed_score_avg,
                    SUM(CASE WHEN available THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS available_rate,
                    SUM(CASE WHEN degraded THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS degraded_rate,
                    COUNT(*) AS count
                FROM test_results
                WHERE model_key = ? AND ts_epoch >= ? AND test_type = ? AND retry_attempt IS NULL
                    {until_clause}
                GROUP BY bucket_ts
                ORDER BY bucket_ts ASC
            """, (bucket_width, bucket_width, model_key, since, test_type, *until_args)).fetchall()
            result = []
            for r in rows:
                bucket = {
                    "bucket_ts": r["bucket_ts"],
                    "ts": r["ts"],
                    "tps": {"avg": r["tps_avg"], "p10": r["tps_p10"], "p90": r["tps_p90"]} if r["tps_avg"] is not None else None,
                    "ttft_ms": {"avg": r["ttft_ms_avg"], "p10": r["ttft_ms_p10"], "p90": r["ttft_ms_p90"]} if r["ttft_ms_avg"] is not None else None,
                    "raw_p99_itl_ms": {"avg": r["raw_p99_itl_ms_avg"], "p10": r["raw_p99_itl_ms_p10"], "p90": r["raw_p99_itl_ms_p90"]} if r["raw_p99_itl_ms_avg"] is not None else None,
                    "chunk_token_ratio": {"avg": r["chunk_token_ratio_avg"], "p10": r["chunk_token_ratio_p10"], "p90": r["chunk_token_ratio_p90"]} if r["chunk_token_ratio_avg"] is not None else None,
                    "stall_count": {"avg": r["stall_count_avg"], "p10": r["stall_count_p10"], "p90": r["stall_count_p90"]} if r["stall_count_avg"] is not None else None,
                    "effective_itl_tail_ratio": {"avg": r["effective_itl_tail_ratio_avg"], "p10": r["effective_itl_tail_ratio_p10"], "p90": r["effective_itl_tail_ratio_p90"]} if r["effective_itl_tail_ratio_avg"] is not None else None,
                    "consistency_score": {"avg": round(r["consistency_score_avg"], 1)} if r["consistency_score_avg"] is not None else None,
                    "speed_score": {"avg": round(r["speed_score_avg"], 1)} if r["speed_score_avg"] is not None else None,
                    "available_rate": r["available_rate"],
                    "degraded_rate": r["degraded_rate"],
                    "count": r["count"],
                }
                result.append(bucket)
            return result


def query_bucketed_health(model_key: str, since: float, bucket_width: float,
                            flat: bool = False, until: float | None = None) -> list[dict]:
    """SQL-based bucketed aggregation for health check results.

    Uses MIN/MAX as approximations for P10/P90 percentile bands (accurate for
    buckets with few data points, which is typical for health checks).
    flat=True returns scalar ttft_ms with p10/p90 fields.
    flat=False returns nested {avg, p10, p90} (for modal charts).
    until: optional upper bound on ts_epoch (reduces query for large ranges).
    """
    if bucket_width <= 0:
        return []
    until_clause = "AND ts_epoch <= ?" if until else ""
    until_args: tuple = (until,) if until else ()
    with _ReadConn() as conn:
        if flat:
            rows = conn.execute(f"""
                SELECT
                    FLOOR(ts_epoch / ?) * ? AS bucket_ts,
                    AVG(ts_epoch) AS ts,
                    AVG(ttft_ms) AS ttft_ms,
                    MIN(ttft_ms) AS ttft_ms_p10,
                    MAX(ttft_ms) AS ttft_ms_p90,
                    SUM(CASE WHEN available THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS available_rate,
                    COUNT(*) AS count
                FROM test_results
                WHERE model_key = ? AND ts_epoch >= ? AND test_type = 'health' AND retry_attempt IS NULL
                    {until_clause}
                GROUP BY bucket_ts
                ORDER BY bucket_ts ASC
            """, (bucket_width, bucket_width, model_key, since, *until_args)).fetchall()
            return [
                {"bucket_ts": r["bucket_ts"], "ts": r["ts"], "ttft_ms": r["ttft_ms"],
                 "ttft_ms_p10": r["ttft_ms_p10"], "ttft_ms_p90": r["ttft_ms_p90"],
                 "available_rate": r["available_rate"], "count": r["count"]}
                for r in rows
            ]
        rows = conn.execute(f"""
            SELECT
                FLOOR(ts_epoch / ?) * ? AS bucket_ts,
                AVG(ts_epoch) AS ts,
                AVG(ttft_ms) AS ttft_ms_avg,
                MIN(ttft_ms) AS ttft_ms_p10,
                MAX(ttft_ms) AS ttft_ms_p90,
                SUM(CASE WHEN available THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS available_rate,
                COUNT(*) AS count
            FROM test_results
            WHERE model_key = ? AND ts_epoch >= ? AND test_type = 'health' AND retry_attempt IS NULL
                {until_clause}
            GROUP BY bucket_ts
            ORDER BY bucket_ts ASC
        """, (bucket_width, bucket_width, model_key, since, *until_args)).fetchall()
        return [
            {"bucket_ts": r["bucket_ts"], "ts": r["ts"],
             "ttft_ms": {"avg": r["ttft_ms_avg"], "p10": r["ttft_ms_p10"], "p90": r["ttft_ms_p90"]} if r["ttft_ms_avg"] is not None else None,
             "available_rate": r["available_rate"],
             "count": r["count"]}
            for r in rows
        ]


def query_markers_bucketed(model_key: str, test_type: str, since: float,
                           until: float, bucket_width: float) -> list[dict]:
    """Bucketed failure/degraded marker counts for cross-type chart overlay.

    Returns only buckets with failures or degraded results (HAVING clause).
    """
    if bucket_width <= 0 or not until:
        return []
    with _ReadConn() as conn:
        rows = conn.execute("""
            SELECT FLOOR(ts_epoch / ?) * ? AS bucket_ts,
                   SUM(CASE WHEN NOT available THEN 1 ELSE 0 END) AS failure_count,
                   SUM(CASE WHEN degraded THEN 1 ELSE 0 END) AS degraded_count
            FROM test_results
            WHERE model_key = ? AND ts_epoch >= ? AND ts_epoch <= ?
                  AND test_type = ? AND retry_attempt IS NULL
            GROUP BY bucket_ts
            HAVING failure_count > 0 OR degraded_count > 0
            ORDER BY bucket_ts ASC
        """, (bucket_width, bucket_width, model_key, since, until, test_type)).fetchall()
        return [dict(r) for r in rows]


def calc_uptime_pct(model_key: str, window: float | None = None, conn=None) -> float | None:
    """Compute rolling uptime percentage.

    Reads from model_cache recent_history when available (fast path).
    Falls back to SQL when the model isn't cached (startup, reconcile).
    """
    entry = model_cache.get(model_key)
    if entry:
        w = window if window is not None else c.uptime_window
        cutoff = time.time() - w
        rh = list(entry.get("recent_history", []))
        in_window = [r for r in rh if (r.get("ts_epoch") or 0) > cutoff and r.get("retry_attempt") is None]
        if not in_window:
            return None
        ok = sum(1 for r in in_window if r.get("available"))
        return round(ok * 100.0 / len(in_window), 1)

    # SQL fallback (startup / reconcile - model not yet in cache)
    if conn is None:
        return None
    w = window if window is not None else c.uptime_window
    cutoff = time.time() - w
    row = conn.execute(
        "SELECT SUM(available) AS ok, COUNT(*) AS total FROM test_results "
        "WHERE model_key = ? AND ts_epoch > ? AND retry_attempt IS NULL",
        (model_key, cutoff),
    ).fetchone()
    if not row or not row["total"]:
        return None
    return round(row["ok"] * 100.0 / row["total"], 1)


# ── Model state ────────────────────────────────────────────────────────────




def batch_sync_registry(entries: list[dict], providers_cfg: list[dict]):
    """Upsert provider and model_info rows for the entire registry in one transaction.

    Called via asyncio.to_thread() from config reload to ensure thread safety.
    Registry entries may include optional metadata fields (context_window,
    supports_vision, etc.) from models.yaml - these take precedence over
    API-fetched values since COALESCE keeps existing non-NULL values.
    """
    from backend.state import MODEL_INFO_FIELDS
    if _write_conn is None:
        return
    _META_COLS = sorted(MODEL_INFO_FIELDS - {"display_name"})
    with _write_lock:
        provider_urls = {p.get("name"): p.get("provider_url") or p.get("api_url", "") for p in providers_cfg}
        for entry in entries:
            provider_name = entry["provider"]
            if provider_name in provider_urls:
                _write_conn.execute(
                    "INSERT INTO providers (name, api_url) VALUES (?, ?) "
                    "ON CONFLICT (name) DO UPDATE SET api_url = COALESCE(excluded.api_url, providers.api_url)",
                    (provider_name, provider_urls[provider_name]),
                )
            meta = {k: entry.get(k) for k in _META_COLS if entry.get(k) is not None}
            if meta:
                cols = ["model_key", "provider", "model_id", "display_name"] + list(meta.keys()) + ["updated_at"]
                placeholders = ", ".join(["?"] * len(cols))
                col_str = ", ".join(cols)
                update_parts = [
                    "provider = excluded.provider",
                    "model_id = excluded.model_id",
                    "display_name = COALESCE(excluded.display_name, model_info.display_name)",
                    "updated_at = excluded.updated_at",
                ]
                for k in meta:
                    update_parts.append(f"{k} = COALESCE(excluded.{k}, model_info.{k})")
                update_str = ", ".join(update_parts)
                values = [entry["id"], provider_name, entry["model_id"], entry["name"]]
                values.extend(meta[k] for k in meta)
                values.append(time.time())
                _write_conn.execute(
                    f"INSERT INTO model_info ({col_str}) VALUES ({placeholders}) "
                    f"ON CONFLICT (model_key) DO UPDATE SET {update_str}",
                    values,
                )
            else:
                _write_conn.execute(
                    "INSERT INTO model_info (model_key, provider, model_id, display_name, updated_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (model_key) DO UPDATE SET "
                    "provider = excluded.provider, model_id = excluded.model_id, "
                    "display_name = COALESCE(excluded.display_name, model_info.display_name), "
                    "updated_at = excluded.updated_at",
                    (entry["id"], provider_name, entry["model_id"], entry["name"], time.time()),
                )
        _write_conn.commit()


def load_all_model_info() -> dict[str, dict]:
    """Load all model_info rows into a dict keyed by model_key.

    Only includes metadata fields (context_window, pricing, capabilities).
    Called at startup to populate model_info_cache.
    """
    from backend.state import MODEL_INFO_FIELDS, normalize_thinking
    with _ReadConn() as conn:
        rows = conn.execute("SELECT * FROM model_info").fetchall()
        result = {}
        _META = MODEL_INFO_FIELDS | {"last_fetched_at"}
        for row in rows:
            d = dict(row)
            mk = d.get("model_key")
            if not mk:
                continue
            info = {}
            for k in _META:
                v = d.get(k)
                if v is not None:
                    if k == "thinking":
                        v = normalize_thinking(v)
                    if v is not None:
                        info[k] = v
            if info:
                result[mk] = info
        return result


def update_model_info(model_key: str, info: dict, overwrite: bool = False):
    """Upsert metadata fields for a model_info row (thread-safe).

    Uses INSERT ... ON CONFLICT to handle models that don't yet have a
    model_info row (e.g. probe-only models not yet populated by model_info.py).

    overwrite=True:  unconditionally sets all provided fields (provider API - authoritative).
    overwrite=False: only fills NULL columns via COALESCE (HuggingFace - supplementary).
    Called via asyncio.to_thread() from model_info.py and scheduler.py.
    """
    from backend.state import MODEL_INFO_FIELDS, MODEL_INFO_BOOL_FIELDS
    if _write_conn is None or not info:
        return
    filtered = {k: v for k, v in info.items() if k in MODEL_INFO_FIELDS and v is not None}
    if not filtered:
        return
    provider = info.get("provider", "")
    model_id = info.get("model_id", model_key.split("::", 1)[-1] if "::" in model_key else model_key)
    display_name = info.get("display_name", model_id)
    with _write_lock:
        sets = []
        for k in filtered:
            if overwrite:
                sets.append(f"{k} = excluded.{k}")
            elif k in MODEL_INFO_BOOL_FIELDS:
                sets.append(f"{k} = COALESCE(NULLIF(model_info.{k}, 0), excluded.{k})")
            else:
                sets.append(f"{k} = COALESCE(model_info.{k}, excluded.{k})")
        cols = list(filtered.keys())
        sql = (
            f"INSERT INTO model_info (model_key, provider, model_id, display_name, {', '.join(cols)}, last_fetched_at, updated_at) "
            f"VALUES (?, ?, ?, ?, {', '.join('?' * len(cols))}, ?, ?) "
            f"ON CONFLICT (model_key) DO UPDATE SET {', '.join(sets)}, "
            f"last_fetched_at = excluded.last_fetched_at, updated_at = excluded.updated_at"
        )
        now = time.time()
        values = [model_key, provider, model_id, display_name] + list(filtered.values()) + [now, now]
        _write_conn.execute(sql, values)
        _write_conn.commit()


def get_all_model_states() -> dict[str, dict]:
    """Get all model states as {model_key: {status, uptime_pct, ...}}."""
    with _ReadConn() as conn:
        rows = conn.execute("SELECT * FROM model_state").fetchall()
        return {r["model_key"]: dict(r) for r in rows}


# ── Startup bulk load ──────────────────────────────────────────────────────

def _load_all_last(where_clause: str = "", params: tuple = ()) -> dict[str, dict]:
    """Get the most recent result per model with optional WHERE filter.

    Parameterized helper used by load_all_last_* functions.
    """
    with _ReadConn() as conn:
        rows = conn.execute(f"""
            SELECT tr.* FROM test_results tr
            INNER JOIN (
                SELECT model_key, MAX(ts_epoch) AS max_ts
                FROM test_results{f' WHERE {where_clause}' if where_clause else ''} GROUP BY model_key
            ) latest ON tr.model_key = latest.model_key AND tr.ts_epoch = latest.max_ts
        """, params).fetchall()
        return {r["model_key"]: _row_to_dict(r) for r in rows}


def load_all_last_results() -> dict[str, dict]:
    """Get the most recent result per model in one query."""
    return _load_all_last()


def load_all_last_health_results() -> dict[str, dict]:
    """Get the most recent health check result per model."""
    return _load_all_last("test_type = ?", ('health',))


def load_all_last_benchmark_results() -> dict[str, dict]:
    """Get the most recent benchmark result per model."""
    return _load_all_last("test_type = ?", ('benchmark',))


def load_all_last_successful_benchmarks() -> dict[str, dict]:
    """Get the most recent successful benchmark result per model."""
    return _load_all_last("test_type = ? AND available = 1", ('benchmark',))


def load_all_last_successful_health_results() -> dict[str, dict]:
    """Get the most recent successful health check result per model."""
    return _load_all_last("test_type = ? AND available = 1 AND success = 1", ('health',))


def load_all_recent_history(limit: int | None = None) -> dict[str, list[dict]]:
    """Get the last N test results per model (benchmarks + health).

    Includes all test types so recent_history covers the configured duration.
    Enforces both count cap and time window (recent_history_seconds).
    """
    if limit is None:
        limit = _effective_history_cap()
    cutoff = time.time() - c.recent_history_seconds
    with _ReadConn() as conn:
        rows = conn.execute("""
            SELECT * FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY model_key ORDER BY ts_epoch DESC) AS rn
                FROM test_results WHERE ts_epoch >= ?
            ) WHERE rn <= ?
            ORDER BY model_key, ts_epoch ASC
        """, (cutoff, limit)).fetchall()
        result: dict[str, list[dict]] = {}
        for r in rows:
            mk = r["model_key"]
            result.setdefault(mk, []).append(_row_to_dict(r))
        return result


# ── Retention ──────────────────────────────────────────────────────────────

def delete_old_results(cutoff_epoch: float) -> int:
    """Delete test results older than cutoff. Returns count deleted."""
    if _write_conn is None:
        return 0
    with _write_lock:
        cur = _write_conn.execute(
            "DELETE FROM test_results WHERE ts_epoch < ?", (cutoff_epoch,)
        )
        # Recalculate first_ts_epoch for models whose oldest result was deleted
        _write_conn.execute(
            "UPDATE model_state SET first_ts_epoch = ("
            "  SELECT MIN(ts_epoch) FROM test_results WHERE model_key = model_state.model_key"
            ") WHERE first_ts_epoch IS NOT NULL AND first_ts_epoch < ?",
            (cutoff_epoch,),
        )
        _write_conn.commit()
        return cur.rowcount


def delete_removed_entries(registered_model_keys: set[str], registered_provider_names: set[str]) -> dict:
    """Delete DB rows for models and providers no longer in the registry.

    Scans every per-model table for stale model keys and removes them from all
    such tables. A model may have rows in some tables but not others (e.g.
    after a partial cleanup), so the stale set is the union across all tables
    rather than trusting a single source. Returns counts of deleted rows.
    """
    if _write_conn is None:
        return {"models": 0, "results": 0, "providers": 0, "model_info": 0}
    with _write_lock:
        # Gather every stale model key across all per-model tables.
        stale: set[str] = set()
        for table in ("model_state", "model_info", "test_results", "audit_results", "probe_results"):
            stale.update(
                row["model_key"]
                for row in _write_conn.execute(f"SELECT DISTINCT model_key FROM {table}").fetchall()
                if row["model_key"] not in registered_model_keys
            )

        model_count = 0
        result_count = 0
        info_count = 0
        for k in stale:
            _write_conn.execute("DELETE FROM model_state WHERE model_key = ?", (k,))
            r = _write_conn.execute("DELETE FROM test_results WHERE model_key = ?", (k,))
            _write_conn.execute("DELETE FROM audit_results WHERE model_key = ?", (k,))
            _write_conn.execute("DELETE FROM probe_results WHERE model_key = ?", (k,))
            i = _write_conn.execute("DELETE FROM model_info WHERE model_key = ?", (k,))
            model_count += 1
            result_count += r.rowcount
            info_count += i.rowcount

        # Stale providers
        stale_providers = [row["name"] for row in
                           _write_conn.execute("SELECT name FROM providers").fetchall()
                           if row["name"] not in registered_provider_names]
        provider_count = 0
        for name in stale_providers:
            _write_conn.execute("DELETE FROM providers WHERE name = ?", (name,))
            provider_count += 1

        if stale or stale_providers:
            _write_conn.commit()
        return {"models": model_count, "results": result_count, "providers": provider_count, "model_info": info_count}


def reconcile_model_state():
    """Derive all model_state rows from actual test_results.

    Sets status='unknown' and uptime_pct=NULL for models with no test_results.
    Replays retained results in timestamp order so retry/degradation source
    rules match runtime behavior exactly.
    Called at startup to prevent stale statuses from appearing.
    """
    if _write_conn is None:
        return
    with _write_lock:
        cur = _write_conn.execute("""
            UPDATE model_state SET status = 'unknown', degraded_source = NULL, uptime_pct = NULL
            WHERE model_key NOT IN (SELECT DISTINCT model_key FROM test_results)
        """)
        orphaned = cur.rowcount

        status_by_model: dict[str, tuple[str, str | None]] = {}
        result_rows = _write_conn.execute("""
            SELECT model_key, test_type, available, degraded, retry_attempt
            FROM test_results
            ORDER BY model_key ASC, ts_epoch ASC, id ASC
        """).fetchall()
        for result in result_rows:
            model_key = result["model_key"]
            old_status, old_source = status_by_model.get(model_key, ("unknown", None))
            status_by_model[model_key] = _derive_status_from_result(
                old_status,
                old_source,
                result["test_type"],
                bool(result["available"]),
                bool(result["degraded"]),
                result["retry_attempt"],
            )

        reconciled = 0
        for row in _write_conn.execute("SELECT model_key FROM model_state").fetchall():
            model_key = row["model_key"]
            status_state = status_by_model.get(model_key)
            if status_state is None:
                continue
            status, degraded_source = status_state
            uptime_pct = calc_uptime_pct(model_key, conn=_write_conn)
            cur = _write_conn.execute(
                "UPDATE model_state SET status = ?, degraded_source = ?, uptime_pct = ? WHERE model_key = ?",
                (status, degraded_source, uptime_pct, model_key),
            )
            reconciled += cur.rowcount

        # Backfill first_ts_epoch for existing models
        cur = _write_conn.execute(
            "UPDATE model_state SET first_ts_epoch = sub.min_ts "
            "FROM (SELECT model_key, MIN(ts_epoch) AS min_ts FROM test_results GROUP BY model_key) sub "
            "WHERE model_state.model_key = sub.model_key AND model_state.first_ts_epoch IS NULL"
        )
        if cur.rowcount:
            log.info("Backfilled first_ts_epoch for %d models", cur.rowcount)
        _write_conn.commit()
    total = orphaned + reconciled
    if total:
        log.info("Reconciled %d model_state rows (%d orphaned, %d replayed)", total, orphaned, reconciled)


# ── Provider metadata ──────────────────────────────────────────────────────

def upsert_provider(name: str, api_url: str = None, page_title: str = None,
                    logo_path: str = None, last_fetched_at: float = None, extra: dict = None):
    """Insert or update a provider row."""
    if _write_conn is None:
        return
    with _write_lock:
        _write_conn.execute(
            "INSERT INTO providers (name, api_url, page_title, logo_path, last_fetched_at, extra) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (name) DO UPDATE SET "
            "api_url = COALESCE(excluded.api_url, providers.api_url), "
            "page_title = COALESCE(excluded.page_title, providers.page_title), "
            "logo_path = COALESCE(excluded.logo_path, providers.logo_path), "
            "last_fetched_at = COALESCE(excluded.last_fetched_at, providers.last_fetched_at), "
            "extra = COALESCE(excluded.extra, providers.extra)",
            (name, api_url, page_title, logo_path, last_fetched_at,
             orjson.dumps(extra).decode() if extra else None),
        )
        _write_conn.commit()


def get_provider(name: str) -> dict | None:
    """Get a provider row by name."""
    with _ReadConn() as conn:
        row = conn.execute("SELECT * FROM providers WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None


def get_providers_batch(names: list[str]) -> dict[str, dict]:
    """Get multiple provider rows by name in a single query."""
    if not names:
        return {}
    with _ReadConn() as conn:
        placeholders = ",".join("?" * len(names))
        rows = conn.execute(f"SELECT * FROM providers WHERE name IN ({placeholders})", names).fetchall()
        return {row["name"]: dict(row) for row in rows}


def providers_needing_fetch(ttl_seconds: float = 86400) -> list[str]:
    """Return provider names where metadata is stale or missing."""
    cutoff = time.time() - ttl_seconds
    with _ReadConn() as conn:
        rows = conn.execute(
            "SELECT name FROM providers WHERE last_fetched_at IS NULL OR last_fetched_at < ?",
            (cutoff,),
        ).fetchall()
        return [r["name"] for r in rows]


# ── Async wrappers ─────────────────────────────────────────────────────────


def _effective_history_cap() -> int:
    """Compute recent_history cap to cover recent_history_seconds at both test intervals.

    Cap = (seconds / health_interval + seconds / benchmark_interval) * 1.2 buffer.
    """
    window = c.recent_history_seconds
    health_entries = window / max(c.health_interval, 1) if c.health_enabled else 0
    bench_entries = window / max(c.benchmark_interval, 1)
    return int((health_entries + bench_entries) * 1.2) or 1


def _extract_ts(record: dict) -> float:
    """Extract timestamp from a record dict, falling back to current time."""
    return float(record.get("_ts_epoch") or record.get("ts_epoch") or time.time())


def _append_history(entry, record):
    """Append a test record to recent_history with count + time cap enforcement."""
    entry["recent_history"].append(record)
    cap = _effective_history_cap()
    if len(entry["recent_history"]) > cap:
        entry["recent_history"] = entry["recent_history"][-cap:]
    # Trim records older than the configured window
    cutoff = time.time() - c.recent_history_seconds
    rh = entry["recent_history"]
    if rh:
        oldest = rh[0].get("ts_epoch", rh[0].get("_ts_epoch", 0))
        if oldest < cutoff:
            entry["recent_history"] = [r for r in rh if (r.get("ts_epoch") or r.get("_ts_epoch") or 0) >= cutoff]
    entry["_scores_version"] = entry.get("_scores_version", 0) + 1
    entry["_cached_scores"] = None
    entry["_card_buckets"] = None


def _derive_status_from_result(
    old_status: str,
    old_degraded_source: str | None,
    test_type: str,
    available: bool,
    degraded: bool,
    retry_attempt: int | None = None,
) -> tuple[str, str | None]:
    """Derive (new_status, degraded_source) from a test result.

    Health checks cannot clear benchmark-owned degradation - a clean health success
    preserves a benchmark-owned degraded state. Retry records always set degraded
    status, except a health retry preserves an existing benchmark-owned source so a
    later health success doesn't incorrectly clear benchmark degradation.
    """
    if retry_attempt is not None:
        if old_degraded_source == TEST_BENCHMARK and test_type == TEST_HEALTH:
            return "degraded", TEST_BENCHMARK
        return "degraded", test_type
    if test_type == TEST_HEALTH:
        benchmark_owns = old_degraded_source == TEST_BENCHMARK
        if available and not degraded:
            if benchmark_owns:
                return "degraded", TEST_BENCHMARK
            return "online", None
        if available and degraded:
            if benchmark_owns:
                return "degraded", TEST_BENCHMARK
            return "degraded", test_type
        if not available:
            if benchmark_owns:
                return "error", TEST_BENCHMARK
            return "error", None
    if available:
        return ("degraded", test_type) if degraded else ("online", None)
    return "error", None


def _status_change_event(old_status: str, new_status: str) -> str | None:
    """Map a status transition to a notification event name.

    error→degraded yields "recovered" (partial improvement triggers a
    partially_recovered notification). record_retry_async overrides this to
    "degraded" so retries that cause degradation always notify as degraded.
    """
    if old_status == new_status or old_status == "unknown":
        return None
    if new_status == "online":
        return "recovered"
    if new_status == "degraded":
        return "recovered" if old_status == "error" else "degraded"
    if new_status == "error":
        return "offline"
    return None


def _track_degraded_since(entry: dict, new_status: str, old_status: str) -> float | None:
    """Track degradation start time for recovery grounding. Returns degraded_since if recovered."""
    if new_status in ("error", "degraded") and old_status not in ("error", "degraded", "unknown"):
        entry["degraded_since"] = time.time()
        return None
    if new_status == "online":
        ds = entry.get("degraded_since")
        entry["degraded_since"] = None
        return ds
    return None


def _build_state_kwargs(entry: dict, new_status: str, degraded_source: str | None,
                         uptime_pct: float | None,
                         first_ts_epoch: float | None = None) -> dict:
    """Build the state_kwargs dict for upsert_model_state (shared by record_result/retry)."""
    return {
        "status": new_status,
        "uptime_pct": uptime_pct,
        "total_tests": entry.get("total_tests", 0),
        "total_success": entry.get("total_success", 0),
        "first_ts_epoch": first_ts_epoch or entry.get("first_ts_epoch") or 0,
        "reliability_score": entry.get("reliability_score"),
        "trends_json": _serialize_trends(entry.get("trends")),
        "degraded_source": degraded_source,
    }


async def _persist_record(model_key: str, record: dict, state_kwargs: dict):
    """Shared persist logic: batcher or immediate write. Returns True on success."""
    if write_batcher is not None:
        if write_batcher.add(model_key, record, state_kwargs):
            asyncio.ensure_future(write_batcher.flush())
        return True
    if _write_conn is None:
        log.error("_persist_record: DB write connection is None for %s", model_key)
        return False
    def _write():
        with _write_lock:
            insert_result(model_key, record)
            upsert_model_state(model_key, state_kwargs)
            commit()
        return True
    ok = await asyncio.to_thread(_write)
    if not ok:
        return False
    return True


# ── Write batcher: buffers SQLite writes, flushes periodically ──────────────

class WriteBatcher(PeriodicBatcher):
    """Buffers SQLite INSERT + upsert operations and flushes them in a single transaction.

    model_cache is updated immediately (hot reads must be fast).
    Only the SQLite persistence is deferred - flushed every `_flush_interval` seconds
    or when `_max_buffer` rows are buffered, whichever comes first.

    This dramatically reduces _write_lock contention at high volume: instead of
    N separate lock+INSERT+upsert+commit cycles, we do one lock+executemany+N-upserts+commit.
    """

    def __init__(self, flush_interval: float = 2.0, max_buffer: int = 200):
        super().__init__(flush_interval)
        self._max_buffer = max_buffer
        self._rows: list[tuple[str, tuple, dict]] = []
        self._buf_lock = threading.Lock()
        self._consecutive_failures = 0

    def add(self, model_key: str, record: dict, state_kwargs: dict) -> bool:
        """Buffer a write. Called after cache has been updated.

        Returns True if a flush should be triggered (buffer hit max_buffer).
        Thread-safe: protected by _buf_lock.
        """
        row = _record_to_row(model_key, record)
        with self._buf_lock:
            self._rows.append((model_key, row, state_kwargs))
            return len(self._rows) >= self._max_buffer

    async def flush(self):
        """Flush the buffer to SQLite in a single transaction."""
        with self._buf_lock:
            if not self._rows:
                return
            batch = self._rows[:]
            self._rows.clear()

        ok = await asyncio.to_thread(self._flush_batch, batch)
        if not ok:
            log.error("WriteBatcher: flush failed, %d rows re-queued for retry", len(batch))
            with self._buf_lock:
                self._rows.extend(batch)
                self._consecutive_failures += 1
                if self._consecutive_failures >= 5:
                    log.critical("WriteBatcher: %d consecutive flush failures, %d rows buffered - possible disk/SQLite issue",
                                 self._consecutive_failures, len(self._rows))
            return
        with self._buf_lock:
            self._consecutive_failures = 0

    def _flush_batch(self, batch: list[tuple[str, tuple, dict]]) -> bool:
        """Execute batched writes under _write_lock. Must run in thread executor."""
        if _write_conn is None:
            log.error("WriteBatcher._flush_batch: DB write connection is None")
            return False
        try:
            with _write_lock:
                _write_conn.executemany(_INSERT_SQL, [row for _, row, _ in batch])
                for model_key, _, skw in batch:
                    upsert_model_state(model_key, skw)
                _write_conn.commit()
            return True
        except Exception as e:
            log_error("WriteBatcher._flush_batch error", e)
            return False


# Singleton - created in init(), started/stopped by main.py lifespan
write_batcher: WriteBatcher | None = None


async def record_result_async(model_key: str, record: dict, available: bool,
                               model_cache: dict) -> tuple[float | None, str | None, str | None, float | None]:
    """Full record_result flow: SQLite insert + cache update + uptime + model_state upsert.

    Returns (uptime_pct, status_changed, prev_status, degraded_since).
    """
    entry = model_cache.get(model_key)
    if not entry:
        log.warning("record_result_async: %s not in cache - result dropped", model_key)
        return None, None, None, None

    old_status = entry["status"]
    old_degraded_source = entry.get("degraded_source")
    test_type = record.get("test_type", TEST_BENCHMARK)

    degraded = bool(record.get("degraded", False))
    new_status, degraded_source = _derive_status_from_result(
        old_status, old_degraded_source, test_type, available, degraded,
    )

    tt = entry.get("total_tests", 0) + 1
    ts_ok = entry.get("total_success", 0) + (1 if available else 0)

    ts = _extract_ts(record)
    record["ts_epoch"] = ts
    stripped = strip_internal(record)
    _append_history(entry, stripped)
    entry["status"] = new_status
    entry["degraded_source"] = degraded_source
    entry["total_tests"] = tt
    entry["total_success"] = ts_ok
    if test_type == TEST_HEALTH:
        entry["testing_health"] = False
    else:
        entry["testing_benchmark"] = False

    uptime_pct = calc_uptime_pct(model_key)
    entry["uptime_pct"] = uptime_pct

    if entry.get("first_ts_epoch") is None:
        entry["first_ts_epoch"] = ts

    if test_type == TEST_BENCHMARK:
        entry["last_test"] = stripped
        entry["last_benchmark_epoch"] = ts
        if available and record.get("success", False):
            entry["last_success_test"] = stripped
            entry["last_success_epoch"] = ts
        entry["reliability_score"] = compute_reliability_score(model_key)
        entry["trends"] = compute_trends(bench_only(entry.get("recent_history", [])))
    else:
        entry["last_health_epoch"] = ts
        entry["last_health_success"] = available and record.get("success", False)
        entry["last_health_error"] = record.get("error") if not (available and record.get("success", False)) else None
        entry["last_health_ttft_ms"] = record.get("ttft_ms")
        entry["last_health_request_id"] = record.get("request_id")
        if available and record.get("success", False):
            entry["last_health_success_epoch"] = ts

    _lt = entry.get("last_test") or {}
    if new_status == "error" and _lt.get("degraded"):
        entry["last_test"] = {**_lt, "degraded": False}

    degraded_since = _track_degraded_since(entry, new_status, old_status)

    status_changed = old_status != new_status
    invalidate_metrics_cache()
    if status_changed:
        update_healthy_model_count()

    state_kwargs = _build_state_kwargs(entry, new_status, degraded_source, uptime_pct, first_ts_epoch=ts)

    if not await _persist_record(model_key, record, state_kwargs):
        log.warning("record_result_async %s: write failed", model_key)
        return None, None, None, None

    changed = _status_change_event(old_status, new_status)
    return uptime_pct, changed, old_status, degraded_since


async def record_retry_async(model_key: str, record: dict, model_cache: dict):
    """Insert a retry record and mark the model degraded until the final attempt resolves."""
    entry = model_cache.get(model_key)
    if not entry:
        log.warning("record_retry_async: %s not in cache", model_key)
        return None, None, None, None

    old_status = entry["status"]
    test_type = record.get("test_type", TEST_BENCHMARK)
    new_status, degraded_source = _derive_status_from_result(
        old_status, entry.get("degraded_source"), test_type,
        bool(record.get("available")), bool(record.get("degraded")),
        record.get("retry_attempt"),
    )

    rec = dict(record)
    ts = _extract_ts(rec)
    rec["ts_epoch"] = ts
    stripped = strip_internal(rec)
    _append_history(entry, stripped)
    entry["status"] = new_status
    entry["degraded_source"] = degraded_source
    uptime_pct = calc_uptime_pct(model_key)
    entry["uptime_pct"] = uptime_pct
    if test_type == TEST_BENCHMARK:
        entry["last_test"] = stripped
        entry["last_benchmark_epoch"] = ts
        entry["reliability_score"] = compute_reliability_score(model_key)
        entry["trends"] = compute_trends(bench_only(entry.get("recent_history", [])))
    else:
        entry["last_health_epoch"] = ts
        entry["last_health_success"] = False
        entry["last_health_error"] = record.get("error")
        entry["last_health_ttft_ms"] = record.get("ttft_ms")
        entry["last_health_request_id"] = record.get("request_id")

    degraded_since = _track_degraded_since(entry, new_status, old_status)

    status_changed = old_status != new_status
    invalidate_metrics_cache()
    if status_changed:
        update_healthy_model_count()

    state_kwargs = _build_state_kwargs(entry, new_status, degraded_source, uptime_pct)

    if not await _persist_record(model_key, rec, state_kwargs):
        return None, None, None, None

    changed = _status_change_event(old_status, new_status)
    if record.get("retry_attempt") is not None and new_status == "degraded" and old_status not in ("unknown", "degraded"):
        changed = "degraded"
    return uptime_pct, changed, old_status, degraded_since


