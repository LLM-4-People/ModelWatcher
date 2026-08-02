"""Test: db_push and db_probe modules use live binding for db._write_conn.

Catches bug family #6: `from backend.db import _write_conn` captured None
at import time (before db.init() ran), causing all writes to silently fail
with `if _write_conn is None: return False`.

Catches bug family #7: callers not updated after db split (db.all_push_subs
→ db_push.all_push_subs).
"""
import ast
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[2] / "backend"


def test_db_push_uses_module_import_not_from_import():
    """db_push.py imports db as module, not `from backend.db import _write_conn`."""
    src = (BACKEND / "db_push.py").read_text()
    assert "import backend.db as db" in src, "db_push.py should use 'import backend.db as db' for live binding"
    assert "from backend.db import _write_conn" not in src, \
        "db_push.py should NOT use 'from backend.db import _write_conn' (captures None at import)"


def test_db_probe_uses_module_import_not_from_import():
    """db_probe.py imports db as module, not `from backend.db import _write_conn`."""
    src = (BACKEND / "db_probe.py").read_text()
    assert "import backend.db as db" in src, "db_probe.py should use 'import backend.db as db' for live binding"
    assert "from backend.db import _write_conn" not in src, \
        "db_probe.py should NOT use 'from backend.db import _write_conn' (captures None at import)"


def test_db_push_accesses_write_conn_via_db_dot():
    """db_push.py accesses _write_conn via db._write_conn (live binding)."""
    src = (BACKEND / "db_push.py").read_text()
    assert "db._write_conn" in src, "db_push.py should access db._write_conn at call time"
    assert "db._write_lock" in src, "db_push.py should access db._write_lock at call time"


def test_db_probe_accesses_write_conn_via_db_dot():
    """db_probe.py accesses _write_conn via db._write_conn (live binding)."""
    src = (BACKEND / "db_probe.py").read_text()
    assert "db._write_conn" in src, "db_probe.py should access db._write_conn at call time"
    assert "db._write_lock" in src, "db_probe.py should access db._write_lock at call time"


def test_db_py_does_not_have_push_functions():
    """db.py should NOT contain push sub functions (moved to db_push.py)."""
    src = (BACKEND / "db.py").read_text()
    for fn in ("def all_push_subs", "def upsert_push_sub", "def delete_push_sub",
               "def get_push_sub_on_write", "def get_push_sub_by_client",
               "def update_push_sub_prefs", "def update_all_push_sub_prefs_by_client",
               "def delete_push_subs_by_client", "def _row_to_push_sub"):
        assert fn not in src, f"db.py still has {fn} (should be in db_push.py)"


def test_db_py_does_not_have_audit_probe_functions():
    """db.py should NOT contain audit/probe functions (moved to db_probe.py)."""
    src = (BACKEND / "db.py").read_text()
    for fn in ("def insert_audit_result", "def get_audit_history",
               "def get_latest_audit_results", "def delete_old_audit_results",
               "def insert_probe_result", "def get_latest_probe_results",
               "def get_probe_history", "def delete_old_probe_results"):
        assert fn not in src, f"db.py still has {fn} (should be in db_probe.py)"


def test_db_push_has_all_push_functions():
    """db_push.py has all 9 push sub functions."""
    import backend.db_push as db_push
    for fn in ("all_push_subs", "upsert_push_sub", "delete_push_sub",
               "get_push_sub_on_write", "get_push_sub_by_client",
               "update_push_sub_prefs", "update_all_push_sub_prefs_by_client",
               "delete_push_subs_by_client"):
        assert hasattr(db_push, fn), f"db_push missing: {fn}"


def test_db_probe_has_all_audit_probe_functions():
    """db_probe.py has all 10 audit/probe functions."""
    import backend.db_probe as db_probe
    for fn in ("insert_audit_result", "get_audit_history",
               "get_latest_audit_results", "delete_old_audit_results",
               "insert_probe_result", "get_latest_probe_results",
               "get_probe_history", "delete_old_probe_results"):
        assert hasattr(db_probe, fn), f"db_probe missing: {fn}"


def test_no_stale_db_dot_push_refs():
    """No caller uses db.<push_function> (should use db_push.<push_function>)."""
    import re
    push_fns = ("all_push_subs", "upsert_push_sub", "delete_push_sub",
                "get_push_sub_on_write", "get_push_sub_by_client",
                "update_push_sub_prefs", "update_all_push_sub_prefs_by_client",
                "delete_push_subs_by_client")
    for f in BACKEND.glob("*.py"):
        if f.name in ("db_push.py", "db.py"):
            continue
        src = f.read_text()
        for fn in push_fns:
            pattern = rf'\bdb\.{fn}\b'
            if re.search(pattern, src):
                pytest.fail(f"{f.name} uses db.{fn} (should use db_push.{fn})")


def test_no_stale_db_dot_audit_probe_refs():
    """No caller uses db.<audit/probe_function> (should use db_probe.<function>)."""
    import re
    probe_fns = ("insert_audit_result", "get_audit_history", "get_latest_audit_results",
                 "delete_old_audit_results", "insert_probe_result", "get_latest_probe_results",
                 "get_probe_history", "delete_old_probe_results")
    for f in BACKEND.glob("*.py"):
        if f.name in ("db_probe.py", "db.py"):
            continue
        src = f.read_text()
        for fn in probe_fns:
            pattern = rf'\bdb\.{fn}\b'
            if re.search(pattern, src):
                pytest.fail(f"{f.name} uses db.{fn} (should use db_probe.{fn})")
