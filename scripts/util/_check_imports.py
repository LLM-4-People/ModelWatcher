"""Check the backend import graph: no cycles at load time, and which lazy imports are needed.

Run from the project root: python3 -m scripts.util._check_imports

The load order is derived from the import graph itself (finding F10: a hand-kept rank
list went stale and reported false violations). A lazy import (one inside a function) is
needed when its target imports the importer at load time, directly or through other
modules; otherwise it could move to the top of the module. Exits 1 on a load-time cycle.
"""
import ast
import sys
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from pathlib import Path

from backend.state import BACKEND_DIR

_PACKAGE = "backend"


@dataclass(frozen=True)
class LazyImport:
    module: str
    line: int
    function: str
    target: str


def _imports_with_scope(tree: ast.Module):
    """Yield (import node, name of the enclosing function or None at load time)."""
    def visit(node: ast.AST, function: str | None):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                yield child, function
            inner = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else function
            yield from visit(child, inner)
    yield from visit(tree, None)


def _targets(node: ast.Import | ast.ImportFrom, modules: set[str]) -> set[str]:
    """Backend modules an import statement loads (import backend.x, from backend.x import y, from backend import x)."""
    if isinstance(node, ast.Import):
        names = [alias.name for alias in node.names]
    elif node.module == _PACKAGE:
        names = [f"{_PACKAGE}.{alias.name}" for alias in node.names]
    else:
        names = [node.module or ""]
    return {name.split(".")[1] for name in names
            if name.startswith(f"{_PACKAGE}.") and name.split(".")[1] in modules}


def backend_imports(backend_dir: Path = BACKEND_DIR) -> tuple[dict[str, set[str]], list[LazyImport]]:
    """The load-time graph {module: backend modules it imports at load} and the lazy imports."""
    modules = {f.stem for f in backend_dir.glob("*.py") if f.name != "__init__.py"}
    graph: dict[str, set[str]] = {mod: set() for mod in modules}
    lazy: list[LazyImport] = []
    for mod in sorted(modules):
        tree = ast.parse((backend_dir / f"{mod}.py").read_text())
        for node, function in _imports_with_scope(tree):
            for target in _targets(node, modules) - {mod}:
                if function:
                    lazy.append(LazyImport(mod, node.lineno, function, target))
                else:
                    graph[mod].add(target)
    return graph, lazy


def load_order(graph: dict[str, set[str]]) -> list[str]:
    """Modules in an order where each follows everything it imports at load time; CycleError on a cycle."""
    return list(TopologicalSorter(graph).static_order())


def lazy_import_needed(graph: dict[str, set[str]], lazy: LazyImport) -> bool:
    """Whether moving the import to load time would close a cycle: the target reaches the importer."""
    seen, stack = set(), [lazy.target]
    while stack:
        mod = stack.pop()
        if mod == lazy.module:
            return True
        if mod not in seen:
            seen.add(mod)
            stack.extend(graph.get(mod, ()))
    return False


def main(backend_dir: Path = BACKEND_DIR) -> int:
    graph, lazy = backend_imports(backend_dir)
    try:
        order = load_order(graph)
    except CycleError as e:
        print(f"LOAD-TIME IMPORT CYCLE: {' -> '.join(e.args[1])}")
        return 1

    print("LOAD ORDER (each module after everything it imports at load time)")
    print("  " + ", ".join(order))
    print()
    print("LAZY IMPORTS (inside functions)")
    needed = 0
    for imp in lazy:
        is_needed = lazy_import_needed(graph, imp)
        needed += is_needed
        verdict = f"needed ({imp.target} imports {imp.module} at load time)" if is_needed else "could be top-level"
        print(f"  {imp.module}.py:{imp.line} {imp.function}() -> {imp.target}: {verdict}")
    print()
    print(f"SUMMARY: {len(graph)} modules, {sum(map(len, graph.values()))} load-time imports, no cycles, "
          f"{len(lazy)} lazy imports ({needed} needed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
