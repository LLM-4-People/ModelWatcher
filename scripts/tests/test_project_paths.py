"""Test: backend/state.py is the only module that derives project paths from __file__.

Catches finding F1: scale_test_db.py and every test recomputed the project root
with Path(__file__).parents[N]; when the seeder moved into scripts/util/ its copy
silently pointed at scripts/ and it could no longer open data/.
"""
import ast

from backend.state import BACKEND_DIR, BASE_DIR, CONFIG_DIR, DATA_DIR, FRONTEND_DIR

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
    for directory in (CONFIG_DIR, DATA_DIR, FRONTEND_DIR):
        assert directory.parent == BASE_DIR and directory.is_dir(), directory
