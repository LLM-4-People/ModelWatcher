"""Test: backend/state.py is the only module that derives project paths from __file__.

Catches finding F1: scale_test_db.py and every test recomputed the project root
with Path(__file__).parents[N]; when the seeder moved into scripts/util/ its copy
silently pointed at scripts/ and it could no longer open data/. And F39/F40: the data
dir was fixed to data/, so a seeded or test server read favicons from, and wrote VAPID
keys into, the checkout; MW_DATA_DIR now moves everything the server writes at once.
"""
import ast

import pytest

from backend.state import BACKEND_DIR, BASE_DIR, CONFIG_DIR, FRONTEND_DIR
from scripts.tests.app_child import result

SCRIPTS_DIR = BASE_DIR / "scripts"


def _uses_dunder_file(path) -> bool:
    tree = ast.parse(path.read_text())
    return any(isinstance(node, ast.Name) and node.id == "__file__" for node in ast.walk(tree))


def test_only_state_derives_paths_from_file():
    sources = [*BACKEND_DIR.glob("*.py"), *SCRIPTS_DIR.rglob("*.py")]
    offenders = sorted(str(p.relative_to(BASE_DIR)) for p in sources
                       if p != BACKEND_DIR / "state.py" and _uses_dunder_file(p))
    assert not offenders, f"derive paths from backend.state instead of __file__: {offenders}"


def test_project_paths_point_at_the_repo():
    assert (BASE_DIR / "backend" / "state.py").is_file()
    assert BACKEND_DIR == BASE_DIR / "backend"
    for directory in (CONFIG_DIR, FRONTEND_DIR):
        assert directory.parent == BASE_DIR and directory.is_dir(), directory


_DATA_PATHS = (
    "import backend.db as db, backend.state as st\n"
    "from scripts.tests.app_child import emit\n"
    "emit([str(p) for p in (st.DATA_DIR, db.SQLITE_PATH, st.VAPID_KEY_FILE, st.VAPID_PUB_FILE,\n"
    "                       st.FAVICON_DIR, st.TIKTOKEN_CACHE_DIR)])\n"
)


@pytest.mark.parametrize("override", ["", "absolute"])
def test_everything_the_server_writes_follows_the_data_dir(run_python, tmp_path, override):
    """An empty MW_DATA_DIR means data/ in the project; an absolute one moves every data path."""
    expected = BASE_DIR / "data" if not override else tmp_path / "elsewhere"
    data_dir, *derived = result(run_python("-c", _DATA_PATHS, env={"MW_DATA_DIR": override and str(expected)}))
    assert data_dir == str(expected)
    assert all(path.startswith(data_dir + "/") for path in derived), derived


def test_real_app_writes_only_into_its_data_dir(run_python, example_config_env, tmp_path):
    """F40: a test server on a temp DB still generated or loaded VAPID keys in the checkout's data/."""
    data_dir = tmp_path / "data"
    code = (
        "from starlette.testclient import TestClient\n"
        "from backend.main import app\n"
        "with TestClient(app, base_url='http://localhost'):\n"
        "    pass\n"
    )
    run_python("-c", code, env={**example_config_env, "MW_DATA_DIR": str(data_dir), "MW_DISABLE_TESTS": "1"})
    written = sorted(p.name for p in data_dir.iterdir())
    assert {"metrics.db", "vapid_private.pem", "vapid_public.txt", "favicons"} <= set(written), written
