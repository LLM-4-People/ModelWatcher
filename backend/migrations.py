"""Database schema migrations - version-tracked, ordered, idempotent.

Each migration is a numbered step that runs exactly once. The
``schema_migrations`` table records applied versions. On a fresh
database the full schema (``db._SCHEMA_SQL``) already contains all
current columns, so the probe-based column helpers silently skip the
ALTERs - only the version record is inserted.

To add a new migration:
    1. Append a ``(version, name, callable)`` entry to ``_MIGRATIONS``.
    2. If the migration adds columns, also add them to
       ``db._SCHEMA_SQL`` (for fresh-DB creation) and
       ``db._RESULT_COLUMNS`` (for test_results INSERT) as needed.
    3. That's it - ``run_migrations()`` picks it up automatically.
"""

import sqlite3

from backend.state import log, log_error

_SCHEMA_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    applied_at  REAL NOT NULL
);
"""


def _migrate_columns(conn, table: str, probe_column: str,
                      columns: list[tuple[str, str]],
                      post_sql: list[str] | None = None,
                      label: str = ""):
    try:
        conn.execute(f"SELECT {probe_column} FROM {table} LIMIT 1")
    except sqlite3.OperationalError:
        for col_name, col_type in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
        for sql in (post_sql or []):
            conn.execute(sql)
        conn.commit()
        names = ", ".join(c[0] for c in columns)
        log.info("Migrated %s: added %s column%s", table, names, f" ({label})" if label else "")


def _migration_test_type(conn: sqlite3.Connection):
    _migrate_columns(conn, "test_results", "test_type",
                     [("test_type", "TEXT DEFAULT 'benchmark'")])


def _migration_first_ts_epoch(conn: sqlite3.Connection):
    _migrate_columns(conn, "model_state", "first_ts_epoch",
                     [("first_ts_epoch", "REAL")])


def _migration_jitter_burst(conn: sqlite3.Connection):
    _migrate_columns(conn, "test_results", "network_jitter_ms",
                     [("network_jitter_ms", "REAL"),
                      ("burst_arrivals", "INTEGER"),
                      ("burst_arrival_pct", "REAL")])


def _migration_chunk_token_stats(conn: sqlite3.Connection):
    _migrate_columns(conn, "test_results", "chunk_token_cv",
                     [("chunk_token_cv", "REAL"),
                      ("chunk_token_max", "INTEGER")])


def _migration_itl_reliable(conn: sqlite3.Connection):
    _migrate_columns(conn, "test_results", "itl_reliable",
                     [("itl_reliable", "INTEGER DEFAULT 0")])


def _migration_scores_stall_detail(conn: sqlite3.Connection):
    _migrate_columns(conn, "test_results", "consistency_score",
                     [("consistency_score", "REAL"),
                      ("speed_score", "REAL"),
                      ("stall_first_pct", "REAL"),
                      ("stall_last_pct", "REAL"),
                      ("stall_clusters", "INTEGER DEFAULT 0"),
                      ("stall_ratio", "REAL")])


def _migration_reliability_trends(conn: sqlite3.Connection):
    _migrate_columns(conn, "model_state", "reliability_score",
                     [("reliability_score", "REAL"),
                      ("trends_json", "TEXT")])


def _migration_degraded_source(conn: sqlite3.Connection):
    _migrate_columns(conn, "model_state", "degraded_source",
                     [("degraded_source", "TEXT")])


def _migration_frame_batch_pct(conn: sqlite3.Connection):
    _migrate_columns(conn, "test_results", "frame_batch_pct",
                     [("frame_batch_pct", "REAL")])


def _migration_effective_itl(conn: sqlite3.Connection):
    _migrate_columns(conn, "test_results", "effective_median_itl_ms",
                     [("effective_median_itl_ms", "REAL"),
                      ("effective_avg_itl_ms", "REAL"),
                      ("effective_p99_itl_ms", "REAL")])


def _migration_itl_renames(conn: sqlite3.Connection):
    try:
        conn.execute("SELECT raw_max_itl_ms FROM test_results LIMIT 1")
    except sqlite3.OperationalError:
        _itl_renames = [
            ("max_itl_ms", "raw_max_itl_ms"),
            ("median_itl_ms", "raw_median_itl_ms"),
            ("avg_itl_ms", "raw_avg_itl_ms"),
            ("p99_itl_ms", "raw_p99_itl_ms"),
            ("itl_tail_ratio", "effective_itl_tail_ratio"),
            ("itl_tail_ratio_estimated", "effective_itl_tail_ratio_estimated"),
        ]
        for old_col, new_col in _itl_renames:
            try:
                conn.execute(f"ALTER TABLE test_results RENAME COLUMN {old_col} TO {new_col}")
            except sqlite3.OperationalError as e:
                log.warning("Column rename %s -> %s skipped: %s", old_col, new_col, e)
        conn.commit()
        log.info("Migrated test_results: renamed ITL metric columns (max/median/avg/p99/tail_ratio)")


def _migration_model_info_metadata(conn: sqlite3.Connection):
    _migrate_columns(conn, "model_info", "context_window",
                     [("context_window", "INTEGER"),
                      ("output_context", "INTEGER"),
                      ("supports_cache", "INTEGER"),
                      ("supports_vision", "INTEGER"),
                      ("supports_tools", "INTEGER"),
                      ("input_price", "REAL"),
                      ("output_price", "REAL"),
                      ("cache_price", "REAL")])


def _migration_model_info_extra_rename(conn: sqlite3.Connection):
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(model_info)").fetchall()]
        if "extra" in cols and "description" not in cols:
            conn.execute("ALTER TABLE model_info RENAME COLUMN extra TO description")
            conn.commit()
            log.info("Migrated model_info: renamed 'extra' to 'description'")
    except Exception as e:
        log_error("model_info column migration check failed", e)


def _migration_model_info_supports_null(conn: sqlite3.Connection):
    try:
        conn.execute("UPDATE model_info SET supports_vision = NULL WHERE supports_vision = 0")
        conn.execute("UPDATE model_info SET supports_tools = NULL WHERE supports_tools = 0")
        conn.execute("UPDATE model_info SET supports_cache = NULL WHERE supports_cache = 0")
        conn.commit()
    except Exception as e:
        log_error("model_info supports_* null migration failed", e)


def _migration_model_info_extended(conn: sqlite3.Connection):
    _migrate_columns(conn, "model_info", "modalities",
                     [("modalities", "TEXT"),
                      ("tokenizer", "TEXT"),
                      ("reasoning_price", "REAL"),
                      ("image_price", "REAL"),
                      ("created", "REAL"),
                      ("owner", "TEXT")])


def _migration_model_info_license(conn: sqlite3.Connection):
    _migrate_columns(conn, "model_info", "license",
                     [("license", "TEXT")])


def _migration_model_info_moe(conn: sqlite3.Connection):
    _migrate_columns(conn, "model_info", "thinking",
                     [("thinking", "TEXT"),
                      ("quantization", "TEXT"),
                      ("served_by", "TEXT"),
                      ("architecture", "TEXT"),
                      ("param_count", "TEXT")])


def _migration_model_info_structured_output(conn: sqlite3.Connection):
    _migrate_columns(conn, "model_info", "supports_structured_output",
                     [("supports_structured_output", "INTEGER")],
                     post_sql=["UPDATE model_info SET supports_structured_output = NULL WHERE supports_structured_output = 0"])


def _migration_model_info_moe_detail(conn: sqlite3.Connection):
    _migrate_columns(conn, "model_info", "num_experts",
                     [("num_experts", "INTEGER"),
                      ("num_experts_per_tok", "INTEGER"),
                      ("num_shared_experts", "INTEGER"),
                      ("moe_intermediate_size", "INTEGER")])


def _migration_request_id(conn: sqlite3.Connection):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(test_results)").fetchall()]
    if "request_id" not in cols:
        conn.execute("ALTER TABLE test_results ADD COLUMN request_id TEXT")
        conn.commit()
        log.info("Migrated test_results: added request_id column")


def _migration_probe_results(conn: sqlite3.Connection):
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    if "probe_results" not in tables:
        conn.executescript("""
            CREATE TABLE probe_results (
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
                error                       TEXT,
                duration_ms                 REAL,
                response_meta               TEXT
            );
            CREATE INDEX idx_probe_model_ts ON probe_results (model_key, ts_epoch DESC);
            CREATE INDEX idx_probe_epoch ON probe_results (ts_epoch);
        """)
        conn.commit()
        log.info("Migrated: created probe_results table")


def _migration_probe_add_success(conn: sqlite3.Connection):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(probe_results)").fetchall()}
    if "success" not in cols:
        conn.execute("ALTER TABLE probe_results ADD COLUMN success INTEGER NOT NULL DEFAULT 0")
        conn.commit()
        log.info("Migrated: added success column to probe_results")


def _migration_probe_fingerprint(conn: sqlite3.Connection):
    _migrate_columns(conn, "probe_results", "served_model",
                     [("engine_version", "TEXT"),
                      ("tensor_parallel", "INTEGER"),
                      ("served_model", "TEXT")])
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, system_fingerprint FROM probe_results "
        "WHERE system_fingerprint IS NOT NULL AND (served_by IS NULL OR engine_version IS NULL)"
    ).fetchall()
    if rows:
        from backend.state import parse_fingerprint
        for row in rows:
            fp = parse_fingerprint(row["system_fingerprint"])
            if fp:
                engine = fp.get("engine")
                ev = fp.get("engine_version")
                tp = fp.get("tensor_parallel")
                sets = []
                vals = []
                if engine:
                    sets.append("served_by = ?")
                    vals.append(engine)
                if ev:
                    sets.append("engine_version = ?")
                    vals.append(ev)
                if tp is not None:
                    sets.append("tensor_parallel = ?")
                    vals.append(tp)
                if sets:
                    vals.append(row["id"])
                    conn.execute(
                        f"UPDATE probe_results SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
        log.info("Backfilled probe_results: parsed %d fingerprints", len(rows))
    conn.row_factory = None


def _migration_model_info_fingerprint(conn: sqlite3.Connection):
    _migrate_columns(conn, "model_info", "fingerprint",
                     [("fingerprint", "TEXT"),
                      ("engine_version", "TEXT"),
                      ("tensor_parallel", "INTEGER"),
                      ("served_model", "TEXT")])
    from backend.state import parse_fingerprint
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT mi.model_key, pr.system_fingerprint, pr.served_by, "
        "pr.engine_version, pr.tensor_parallel, pr.served_model "
        "FROM model_info mi "
        "INNER JOIN (SELECT model_key, MAX(ts_epoch) AS max_ts FROM probe_results GROUP BY model_key) latest "
        "ON mi.model_key = latest.model_key "
        "INNER JOIN probe_results pr ON mi.model_key = pr.model_key AND pr.ts_epoch = latest.max_ts "
        "WHERE pr.system_fingerprint IS NOT NULL AND mi.fingerprint IS NULL"
    ).fetchall()
    if rows:
        for row in rows:
            sets = []
            vals = []
            if row["system_fingerprint"]:
                sets.append("fingerprint = ?")
                vals.append(row["system_fingerprint"])
            sb = row["served_by"]
            ev = row["engine_version"]
            tp = row["tensor_parallel"]
            sm = row["served_model"]
            fp = parse_fingerprint(row["system_fingerprint"])
            if fp:
                if not sb and fp.get("engine"):
                    sets.append("served_by = ?")
                    vals.append(fp["engine"])
                if not ev and fp.get("engine_version"):
                    sets.append("engine_version = ?")
                    vals.append(fp["engine_version"])
                if tp is None and fp.get("tensor_parallel") is not None:
                    sets.append("tensor_parallel = ?")
                    vals.append(fp["tensor_parallel"])
            if ev:
                sets.append("engine_version = ?")
                vals.append(ev)
            if tp is not None:
                sets.append("tensor_parallel = ?")
                vals.append(tp)
            if sm:
                sets.append("served_model = ?")
                vals.append(sm)
            if sets:
                vals.append(row["model_key"])
                conn.execute(f"UPDATE model_info SET {', '.join(sets)} WHERE model_key = ?", vals)
        conn.commit()
        log.info("Backfilled model_info: fingerprint data for %d models", len(rows))
    conn.row_factory = None


def _migration_fingerprint_backfill(conn: sqlite3.Connection):
    from backend.state import parse_fingerprint
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, system_fingerprint FROM probe_results "
        "WHERE system_fingerprint IS NOT NULL AND engine_version IS NULL"
    ).fetchall()
    if rows:
        for row in rows:
            fp = parse_fingerprint(row["system_fingerprint"])
            if fp:
                sets = []
                vals = []
                if fp.get("engine"):
                    sets.append("served_by = ?")
                    vals.append(fp["engine"])
                if fp.get("engine_version"):
                    sets.append("engine_version = ?")
                    vals.append(fp["engine_version"])
                if fp.get("tensor_parallel") is not None:
                    sets.append("tensor_parallel = ?")
                    vals.append(fp["tensor_parallel"])
                if sets:
                    vals.append(row["id"])
                    conn.execute(
                        f"UPDATE probe_results SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
        log.info("Backfilled probe_results fingerprints: %d rows", len(rows))
    mi_rows = conn.execute(
        "SELECT mi.model_key, pr.system_fingerprint, pr.served_by, "
        "pr.engine_version, pr.tensor_parallel, pr.served_model "
        "FROM model_info mi "
        "INNER JOIN (SELECT model_key, MAX(ts_epoch) AS max_ts FROM probe_results GROUP BY model_key) latest "
        "ON mi.model_key = latest.model_key "
        "INNER JOIN probe_results pr ON mi.model_key = pr.model_key AND pr.ts_epoch = latest.max_ts "
        "WHERE pr.system_fingerprint IS NOT NULL AND mi.fingerprint IS NULL"
    ).fetchall()
    if mi_rows:
        for row in mi_rows:
            sets = []
            vals = []
            if row["system_fingerprint"]:
                sets.append("fingerprint = ?")
                vals.append(row["system_fingerprint"])
            sb = row["served_by"]
            ev = row["engine_version"]
            tp = row["tensor_parallel"]
            sm = row["served_model"]
            fp = parse_fingerprint(row["system_fingerprint"])
            if fp:
                if not sb and fp.get("engine"):
                    sets.append("served_by = ?")
                    vals.append(fp["engine"])
                if not ev and fp.get("engine_version"):
                    sets.append("engine_version = ?")
                    vals.append(fp["engine_version"])
                if tp is None and fp.get("tensor_parallel") is not None:
                    sets.append("tensor_parallel = ?")
                    vals.append(fp["tensor_parallel"])
            if ev:
                sets.append("engine_version = ?")
                vals.append(ev)
            if tp is not None:
                sets.append("tensor_parallel = ?")
                vals.append(tp)
            if sm:
                sets.append("served_model = ?")
                vals.append(sm)
            if sets:
                vals.append(row["model_key"])
                conn.execute(f"UPDATE model_info SET {', '.join(sets)} WHERE model_key = ?", vals)
        conn.commit()
        log.info("Backfilled model_info fingerprints: %d models", len(mi_rows))
    conn.row_factory = None


def _migration_fp_substructure(conn: sqlite3.Connection):
    from backend.state import parse_fingerprint
    for table, cols in (
        ("probe_results", ("quantization TEXT", "fp_server TEXT", "fp_features TEXT")),
        ("model_info", ("fp_server TEXT", "fp_features TEXT")),
    ):
        for col_def in cols:
            col = col_def.split()[0]
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
            except Exception:
                pass
    conn.commit()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, system_fingerprint FROM probe_results "
        "WHERE system_fingerprint IS NOT NULL AND quantization IS NULL AND fp_server IS NULL"
    ).fetchall()
    if rows:
        for row in rows:
            fp = parse_fingerprint(row["system_fingerprint"])
            if fp:
                sets = []
                vals = []
                if fp.get("quantization"):
                    sets.append("quantization = ?")
                    vals.append(fp["quantization"])
                if fp.get("fp_server"):
                    sets.append("fp_server = ?")
                    vals.append(fp["fp_server"])
                if fp.get("fp_features"):
                    sets.append("fp_features = ?")
                    vals.append(fp["fp_features"])
                if sets:
                    vals.append(row["id"])
                    conn.execute(
                        f"UPDATE probe_results SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
        log.info("Backfilled probe_results fp sub-structure: %d rows", len(rows))
    mi_rows = conn.execute(
        "SELECT mi.model_key, pr.system_fingerprint "
        "FROM model_info mi "
        "INNER JOIN (SELECT model_key, MAX(ts_epoch) AS max_ts FROM probe_results GROUP BY model_key) latest "
        "ON mi.model_key = latest.model_key "
        "INNER JOIN probe_results pr ON mi.model_key = pr.model_key AND pr.ts_epoch = latest.max_ts "
        "WHERE pr.system_fingerprint IS NOT NULL AND mi.fp_server IS NULL"
    ).fetchall()
    if mi_rows:
        for row in mi_rows:
            fp = parse_fingerprint(row["system_fingerprint"])
            if fp:
                sets = []
                vals = []
                if fp.get("quantization"):
                    sets.append("quantization = ?")
                    vals.append(fp["quantization"])
                if fp.get("fp_server"):
                    sets.append("fp_server = ?")
                    vals.append(fp["fp_server"])
                if fp.get("fp_features"):
                    sets.append("fp_features = ?")
                    vals.append(fp["fp_features"])
                if sets:
                    vals.append(row["model_key"])
                    conn.execute(
                        f"UPDATE model_info SET {', '.join(sets)} WHERE model_key = ?", vals)
        conn.commit()
        log.info("Backfilled model_info fp sub-structure: %d models", len(mi_rows))
    conn.row_factory = None


def _migration_thinking_normalize(conn: sqlite3.Connection):
    """Normalize stale thinking values in model_info and probe_results.

    Older code stored model_info.thinking as TEXT '0'/'1'/'false'/'true'
    instead of NULL/'enabled', and probe_results.thinking as TEXT '0'/'1'
    instead of INTEGER 0/1. This migration cleans both tables.
    """
    _THINKING_MAP = {"0": None, "1": "enabled", "false": None, "true": "enabled",
                     "yes": "enabled", "no": None, "disabled": None}
    conn.row_factory = None

    mi_rows = conn.execute("SELECT model_key, thinking FROM model_info WHERE thinking IS NOT NULL AND typeof(thinking) = 'text'").fetchall()
    fixed = 0
    for mk, tv in mi_rows:
        nv = _THINKING_MAP.get(str(tv).lower())
        if nv is None:
            conn.execute("UPDATE model_info SET thinking = NULL WHERE model_key = ?", (mk,))
        else:
            conn.execute("UPDATE model_info SET thinking = ? WHERE model_key = ?", (nv, mk))
        fixed += 1
    if fixed:
        conn.commit()
        log.info("Normalized model_info.thinking: %d rows", fixed)

    pr_rows = conn.execute("SELECT rowid, thinking FROM probe_results WHERE thinking IS NOT NULL AND typeof(thinking) = 'text'").fetchall()
    fixed_pr = 0
    for rid, tv in pr_rows:
        nv = 1 if str(tv).lower() in ("1", "true", "yes") else 0
        conn.execute("UPDATE probe_results SET thinking = ? WHERE rowid = ?", (nv, rid))
        fixed_pr += 1
    if fixed_pr:
        conn.commit()
        log.info("Normalized probe_results.thinking: %d rows", fixed_pr)


def _migration_drop_ping_jitter(conn: sqlite3.Connection):
    """Drop the ping_jitter_ms column (PING system removed)."""
    try:
        conn.execute("SELECT ping_jitter_ms FROM test_results LIMIT 1")
    except sqlite3.OperationalError:
        return
    conn.execute("ALTER TABLE test_results DROP COLUMN ping_jitter_ms")
    conn.commit()
    log.info("Dropped ping_jitter_ms column from test_results")


def _migration_archived_flag(conn: sqlite3.Connection):
    """Add archived column to model_state for persistent archive state."""
    _migrate_columns(conn, "model_state", "archived",
                     [("archived", "INTEGER NOT NULL DEFAULT 0")])


def _migration_prune_archived_trailing(conn: sqlite3.Connection):
    """One-shot: prune trailing failed test_results for already-archived models.

    Models archived before the prune-on-archive feature was added still
    have their final run of failures in test_results. This deletes every
    test_result after each archived model's last successful test and
    recomputes model_state. Runs before model_cache is populated, so the
    cache loads from already-pruned data.
    """
    conn.row_factory = sqlite3.Row
    archived = [r["model_key"] for r in conn.execute(
        "SELECT model_key FROM model_state WHERE archived = 1"
    ).fetchall()]
    if not archived:
        conn.row_factory = None
        return

    from backend.db import _recompute_model_state

    total_deleted = 0
    for mk in archived:
        row = conn.execute(
            "SELECT MAX(ts_epoch) AS last_ts FROM test_results "
            "WHERE model_key = ? AND available = 1 AND success = 1",
            (mk,),
        ).fetchone()
        last_ts = row["last_ts"] if row else None
        if last_ts is None:
            continue
        cur = conn.execute(
            "DELETE FROM test_results WHERE model_key = ? AND ts_epoch > ?",
            (mk, last_ts),
        )
        if cur.rowcount:
            total_deleted += cur.rowcount
            _recompute_model_state(mk, conn)

    if total_deleted:
        conn.commit()
        log.info("Pruned %d trailing failure(s) for archived models", total_deleted)
    conn.row_factory = None


_MIGRATIONS: list[tuple[int, str, object]] = [
    (1,  "test_type",                   _migration_test_type),
    (2,  "first_ts_epoch",             _migration_first_ts_epoch),
    (3,  "jitter_burst",               _migration_jitter_burst),
    (4,  "chunk_token_stats",          _migration_chunk_token_stats),
    (5,  "itl_reliable",               _migration_itl_reliable),
    (6,  "scores_stall_detail",        _migration_scores_stall_detail),
    (7,  "reliability_trends",         _migration_reliability_trends),
    (8,  "degraded_source",            _migration_degraded_source),
    (9,  "frame_batch_pct",            _migration_frame_batch_pct),
    (10, "effective_itl",              _migration_effective_itl),
    (11, "itl_renames",                _migration_itl_renames),
    (12, "model_info_metadata",        _migration_model_info_metadata),
    (13, "model_info_extra_rename",    _migration_model_info_extra_rename),
    (14, "model_info_supports_null",   _migration_model_info_supports_null),
    (15, "model_info_extended",        _migration_model_info_extended),
    (16, "model_info_license",         _migration_model_info_license),
    (17, "model_info_moe",             _migration_model_info_moe),
    (18, "model_info_structured_output", _migration_model_info_structured_output),
    (19, "model_info_moe_detail",      _migration_model_info_moe_detail),
    (20, "request_id",                 _migration_request_id),
    (21, "probe_results",              _migration_probe_results),
    (22, "probe_add_success",          _migration_probe_add_success),
    (23, "probe_fingerprint",          _migration_probe_fingerprint),
    (24, "model_info_fingerprint",     _migration_model_info_fingerprint),
    (25, "fingerprint_backfill",       _migration_fingerprint_backfill),
    (26, "fp_substructure",            _migration_fp_substructure),
    (27, "thinking_normalize",          _migration_thinking_normalize),
    (28, "drop_ping_jitter",            _migration_drop_ping_jitter),
    (29, "archived_flag",               _migration_archived_flag),
    (30, "prune_archived_trailing",     _migration_prune_archived_trailing),
]

_LATEST_VERSION = _MIGRATIONS[-1][0]


def run_migrations(conn: sqlite3.Connection):
    """Run all pending schema migrations in version order.

    Called from db.init() on the setup connection after _SCHEMA_SQL has
    executed, so all tables/indexes exist (possibly without all columns
    on upgraded databases). Each migration runs once and is recorded in
    schema_migrations.
    """
    import time as _time

    conn.executescript(_SCHEMA_MIGRATIONS_TABLE)

    applied = {
        row[0] for row in
        conn.execute("SELECT version FROM schema_migrations").fetchall()
    }

    any_run = False
    for version, name, fn in _MIGRATIONS:
        if version in applied:
            continue
        fn(conn)
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
            (version, name, _time.time()),
        )
        conn.commit()
        log.info("Schema migration v%d applied: %s", version, name)
        any_run = True

    if not any_run:
        log.info("Schema up to date (v%d)", _LATEST_VERSION)
