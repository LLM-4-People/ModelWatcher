"""Test: the import checker derives the load order from the import graph and reports only real problems.

Catches finding F10: scripts/util/_check_imports.py ranked modules with a hand-kept
CHAIN that missed audit, db_probe, db_push, migrations and others and still listed the
removed ping module, so it reported false VIOLATIONs; it also missed `from backend import x`.
"""
from graphlib import CycleError

import pytest

from backend.state import BACKEND_DIR
from scripts.util._check_imports import backend_imports, lazy_import_needed, load_order, main


def test_real_backend_has_a_load_order_covering_every_module():
    graph, _ = backend_imports()
    order = load_order(graph)
    assert sorted(order) == sorted(f.stem for f in BACKEND_DIR.glob("*.py") if f.name != "__init__.py")
    position = {mod: n for n, mod in enumerate(order)}
    late = [(mod, dep) for mod, deps in graph.items() for dep in deps if position[dep] > position[mod]]
    assert not late, f"modules placed before what they import: {late}"


def test_from_package_imports_count():
    graph, _ = backend_imports()
    assert {"routes", "notifications", "favicons"} <= graph["main"], "main.py uses `from backend import ...`"


def test_script_reports_no_violation(run_python):
    out = run_python("-m", "scripts.util._check_imports").stdout
    assert "VIOLATION" not in out and "no cycles" in out


def _write(tmp_path, files: dict[str, str]):
    for name, source in files.items():
        (tmp_path / f"{name}.py").write_text(source)
    return tmp_path


def test_lazy_imports_are_classified_by_the_graph(tmp_path):
    backend_dir = _write(tmp_path, {
        "a": "import backend.b\n",
        "b": "def f():\n    import backend.a\n",
        "c": "def g():\n    from backend.b import f\n",
    })
    graph, lazy = backend_imports(backend_dir)
    assert graph == {"a": {"b"}, "b": set(), "c": set()}
    verdicts = {(imp.module, imp.target): lazy_import_needed(graph, imp) for imp in lazy}
    assert verdicts == {("b", "a"): True, ("c", "b"): False}


def test_cycle_is_an_error(tmp_path, capsys):
    backend_dir = _write(tmp_path, {"a": "from backend import b\n", "b": "import backend.a as a\n"})
    graph, _ = backend_imports(backend_dir)
    with pytest.raises(CycleError):
        load_order(graph)
    assert main(backend_dir) == 1
    assert "LOAD-TIME IMPORT CYCLE" in capsys.readouterr().out
