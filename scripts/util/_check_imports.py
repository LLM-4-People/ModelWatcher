"""Check backend modules for circular import risks."""
import ast
import os

BACKEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "backend")
MODULES = sorted(
    f[:-3] for f in os.listdir(BACKEND_DIR)
    if f.endswith(".py") and f != "__init__.py"
)


def find_parent_function(tree, target_node):
    """Return the name of the function containing target_node, or None."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                if child is target_node:
                    return node.name
    return None


def extract_imports(module_name):
    """Extract top-level and lazy backend imports from a module."""
    filepath = os.path.join(BACKEND_DIR, module_name + ".py")
    with open(filepath) as f:
        source = f.read()
    tree = ast.parse(source)

    top_level = []  # [(line, target_module)]
    lazy = []       # [(line, function_name, target_module)]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modname = alias.name
                if modname.startswith("backend."):
                    parts = modname.split(".")
                    if len(parts) >= 2 and parts[1] in MODULES:
                        parent = find_parent_function(tree, node)
                        if parent:
                            lazy.append((node.lineno, parent, parts[1]))
                        else:
                            top_level.append((node.lineno, parts[1]))
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("backend."):
                parts = node.module.split(".")
                if len(parts) >= 2 and parts[1] in MODULES:
                    parent = find_parent_function(tree, node)
                    if parent:
                        lazy.append((node.lineno, parent, parts[1]))
                    else:
                        top_level.append((node.lineno, parts[1]))

    return top_level, lazy


# Documented dependency chain (earlier <- later)
CHAIN = [
    "state",
    "security", "prompts", "websocket", "metrics", "middleware",
    "ping", "stats", "model_info",
    "validation", "favicons", "models", "db",
    "streaming", "push_routes", "notifications", "routes",
    "scheduler", "config",
    "main",
]

CHAIN_RANK = {name: i for i, name in enumerate(CHAIN)}


def main():
    print("=" * 80)
    print("BACKEND CIRCULAR IMPORT ANALYSIS")
    print("=" * 80)
    print()

    all_top = {}  # module -> [(line, target)]
    all_lazy = {}  # module -> [(line, func, target)]

    for mod in MODULES:
        top, lazy = extract_imports(mod)
        all_top[mod] = top
        all_lazy[mod] = lazy

    # --- Top-level imports ---
    print("TOP-LEVEL BACKEND IMPORTS (module load time)")
    print("-" * 80)
    violations = []
    for mod in MODULES:
        if not all_top[mod]:
            continue
        rank = CHAIN_RANK.get(mod, -1)
        for line, target in all_top[mod]:
            target_rank = CHAIN_RANK.get(target, -1)
            direction = "OK" if rank > target_rank else "VIOLATION"
            if direction == "VIOLATION":
                violations.append((mod, line, target))
            print(f"  {mod:20s} -> {target:20s}  (line {line:4d})  "
                  f"[{mod}#{rank} -> {target}#{target_rank}]  {direction}")

    print()
    print("LAZY IMPORTS (inside functions)")
    print("-" * 80)
    for mod in MODULES:
        if not all_lazy[mod]:
            continue
        rank = CHAIN_RANK.get(mod, -1)
        for line, func, target in all_lazy[mod]:
            target_rank = CHAIN_RANK.get(target, -1)
            legit = "legit" if rank < target_rank else "UNNECESSARY"
            print(f"  {mod:20s} -> {target:20s}  (line {line:4d}, in {func}())  "
                  f"[{mod}#{rank} -> {target}#{target_rank}]  {legit}")
    print()

    # --- Circular import detection ---
    print("CIRCULAR IMPORT RISK ANALYSIS")
    print("-" * 80)

    # Build graph from top-level imports only
    graph = {}
    for mod in MODULES:
        graph[mod] = [target for _, target in all_top[mod]]

    # Detect cycles via DFS
    def find_cycles(graph):
        visited = set()
        rec_stack = set()
        cycles = []

        def dfs(node, path):
            visited.add(node)
            rec_stack.add(node)
            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor, path + [neighbor])
                elif neighbor in rec_stack:
                    # Found a cycle
                    cycle_start = path.index(neighbor)
                    cycle = path[cycle_start:] + [neighbor]
                    cycles.append(cycle)
            rec_stack.discard(node)

        for node in graph:
            if node not in visited:
                dfs(node, [node])

        return cycles

    cycles = find_cycles(graph)
    if cycles:
        print("  CIRCULAR IMPORTS DETECTED:")
        for cycle in cycles:
            print(f"    {' -> '.join(cycle)}")
    else:
        print("  No circular imports at module load time.")

    print()

    # --- Violations ---
    if violations:
        print("DEPENDENCY CHAIN VIOLATIONS")
        print("-" * 80)
        for mod, line, target in violations:
            rank = CHAIN_RANK.get(mod, -1)
            target_rank = CHAIN_RANK.get(target, -1)
            print(f"  {mod} (rank {rank}) imports {target} (rank {target_rank}) "
                  f"at top level (line {line})")
            print(f"    -> {target} appears EARLIER in the chain, "
                  f"but {mod} imports it at load time")
            print(f"    -> This means {mod} depends on {target}, "
                  f"but {target} should not depend on {mod}")
    else:
        print("No dependency chain violations found.")

    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"  Modules analyzed: {len(MODULES)}")
    print(f"  Top-level import violations: {len(violations)}")
    print(f"  Circular imports: {len(cycles)}")

    # Count lazy imports per module
    lazy_counts = {}
    for mod in MODULES:
        lazy_counts[mod] = len(all_lazy[mod])
    lazy_modules = {m: c for m, c in lazy_counts.items() if c > 0}
    if lazy_modules:
        print(f"  Lazy imports (break cycles): {sum(lazy_modules.values())} "
              f"in {len(lazy_modules)} modules")
        for m, c in lazy_modules.items():
            print(f"    {m}: {c} lazy import(s)")


if __name__ == "__main__":
    main()
