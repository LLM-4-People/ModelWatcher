"""Test: schema migrations check the schema instead of probing it with statements that may fail.

Catches the migrations part of finding F13: `_migration_fp_substructure` ran every
ALTER TABLE and swallowed any exception (`except Exception: pass`), so a real failure
(locked or read-only database) looked the same as "column already there", and other
migrations probed for columns with a SELECT that was expected to fail. All of them now
read the columns once through `_table_columns`, so a fresh database runs no ALTER at all.
"""
import sqlite3

import pytest

import backend.migrations as migrations
from backend.db import _SCHEMA_SQL


class _Recording:
    """A connection that records every statement handed to execute(), including ones that fail to
    prepare (SQLite's own trace hook never sees those, and they are exactly the swallowed ALTERs)."""

    def __init__(self, conn: sqlite3.Connection, statements: list[str]):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_statements", statements)

    def execute(self, sql, *args):
        self._statements.append(sql)
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        setattr(self._conn, name, value)


@pytest.fixture
def traced():
    """An in-memory connection that records every statement it is asked to run."""
    conn = sqlite3.connect(":memory:")
    statements: list[str] = []
    yield _Recording(conn, statements), statements
    conn.close()


def _schema_changes(statements: list[str]) -> list[str]:
    return [s for s in statements if s.lstrip().upper().startswith("ALTER TABLE")]


def test_fresh_schema_needs_no_alter(traced):
    conn, statements = traced
    conn.executescript(_SCHEMA_SQL)
    statements.clear()
    migrations.run_migrations(conn)
    assert _schema_changes(statements) == []
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == len(migrations._MIGRATIONS)


def test_missing_columns_are_added_once(traced):
    conn, statements = traced
    conn.execute("CREATE TABLE probe_results (id INTEGER PRIMARY KEY, model_key TEXT, ts_epoch REAL, system_fingerprint TEXT)")
    conn.execute("CREATE TABLE model_info (model_key TEXT PRIMARY KEY)")
    migrations._migration_fp_substructure(conn)
    added = _schema_changes(statements)
    assert len(added) == 5
    assert {"quantization", "fp_server", "fp_features"} <= migrations._table_columns(conn, "probe_results")
    statements.clear()
    migrations._migration_fp_substructure(conn)
    assert _schema_changes(statements) == [], "a second run finds the columns and changes nothing"


def test_table_columns_of_a_missing_table_is_empty(traced):
    conn, _ = traced
    assert migrations._table_columns(conn, "nope") == set()


def test_a_failing_alter_is_not_swallowed(tmp_path):
    path = tmp_path / "ro.db"
    setup = sqlite3.connect(path)
    setup.execute("CREATE TABLE test_results (id INTEGER PRIMARY KEY)")
    setup.commit()
    setup.close()
    readonly = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        migrations._migrate_columns(readonly, "test_results", "request_id", [("request_id", "TEXT")])
    readonly.close()


def test_drop_ping_jitter_only_when_present(traced):
    conn, statements = traced
    conn.execute("CREATE TABLE test_results (id INTEGER PRIMARY KEY, ping_jitter_ms REAL)")
    migrations._migration_drop_ping_jitter(conn)
    assert "ping_jitter_ms" not in migrations._table_columns(conn, "test_results")
    statements.clear()
    migrations._migration_drop_ping_jitter(conn)
    assert _schema_changes(statements) == []
    assert not any("ping_jitter_ms" in s for s in statements), "no probing SELECT that is expected to fail"
