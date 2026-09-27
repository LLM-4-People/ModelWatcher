"""Test: every backend `except` block logs through the central logger or re-raises.

Catches finding F13: 43 handlers neither logged nor re-raised, while the old version of
this test only flagged `except Exception: pass`. The rule (CONTRIBUTING.md):

- a handler re-raises, or logs through `backend.state`: `log_error()` for unexpected
  failures, `log.warning`/`log.info`/`log.debug` for expected ones (a `log_*` helper that
  itself logs that way counts);
- a broad handler (bare `except`, `Exception`, `BaseException`) cannot know its failure is
  expected, so it needs `log_error()`, `log.warning` or higher, or a re-raise (finding F47);
- control flow, where an expected exception becomes a normal result, is listed in
  CONTROL_FLOW below with the reason. Entries are keyed by module, enclosing function and
  exception types, never by line, and an entry that no longer matches a handler fails too.
"""
import ast

import pytest

from backend.state import BACKEND_DIR

_LEVELS = {"debug", "info", "warning", "error", "exception", "critical"}
_SERIOUS_LEVELS = {"warning", "error", "exception", "critical"}
_BROAD = {None, "Exception", "BaseException"}
_NOT_OWN = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef, ast.ExceptHandler)

# (module, enclosing function, exception types as written): reason
CONTROL_FLOW = {
    ("audit.py", "_parse_tool_args", "ValueError"): "tool-call arguments that are not JSON stay the provider's string",
    ("audit.py", "_parse_synbad_output", "ValueError"): "a response block that is not JSON is kept as truncated text",
    ("audit.py", "_run_synbad_suite", "ProcessLookupError"): "the timed-out process exited before the kill",
    ("batch.py", "PeriodicBatcher.stop", "asyncio.CancelledError"): "awaiting the flush task stop() just cancelled",
    ("favicons.py", "_icon_priority", "ValueError"): "a sizes attribute that is not WxH (e.g. 'any') ranks like a missing one",
    ("favicons.py", "provider_logo_data_uri", "FileNotFoundError"): "the logo was removed after provider_logo_path() found it",
    ("main.py", "_shutdown", "asyncio.CancelledError"): "awaiting tasks _shutdown() just cancelled",
    ("middleware.py", "RequestSizeLimitMiddleware.__call__", "ValueError"): "a malformed Content-Length is ignored; the streaming cap still applies",
    ("middleware.py", "RequestSizeLimitMiddleware.__call__", "_BodyTooLargeError"): "internal signal from limited_receive, answered with 413",
    ("model_info.py", "_as_int", "(ValueError, TypeError)"): "coercion helper: None means not a number",
    ("model_info.py", "_as_float", "(ValueError, TypeError)"): "coercion helper: None means not a number",
    ("model_info.py", "_fetch_hf_model", "ValueError"): "an unparseable createdAt from the Hub is left out",
    ("notifications.py", "handle_get_notifications", "ValueError"): "an invalid since parameter is answered with 400",
    ("probe.py", "_looks_like_json", "(TypeError, ValueError)"): "predicate: the text is not JSON",
    ("probe.py", "_probe_json_mode", "(IndexError, TypeError)"): "a response without choices does not show the capability",
    ("routes.py", "_query_float", "ValueError"): "an invalid query value is answered with 400",
    ("routes.py", "_query_int", "ValueError"): "an invalid query value is answered with 400",
    ("routes.py", "_mtime", "FileNotFoundError"): "a frontend file that is not built yet or was removed while scanning",
    ("routes.py", "_read_bytes", "FileNotFoundError"): "a frontend file that is not built yet or was removed while scanning",
    ("scheduler.py", "scheduler", "asyncio.TimeoutError"): "the sleep elapsed without a wake event",
    ("state.py", "<module>", "ImportError"): "optional dependency; the flag next to it records whether it is installed",
    ("state.py", "sanitize_prefs", "(ValueError, TypeError)"): "an invalid tier pref is dropped",
    ("state.py", "is_ip_literal", "ValueError"): "predicate: the host is a name, not an IP address",
    ("streaming.py", "aiter_sse_events", "StopAsyncIteration"): "end of the response stream",
}


def _qualname_map(tree: ast.Module) -> dict[ast.ExceptHandler, str]:
    """Map every except handler to the qualified name of the function or class around it."""
    found: dict[ast.ExceptHandler, str] = {}

    def visit(node: ast.AST, scope: list[str]):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, scope + [child.name])
                continue
            if isinstance(child, ast.ExceptHandler):
                found[child] = ".".join(scope) or "<module>"
            visit(child, scope)

    visit(tree, [])
    return found


def _own_nodes(stmts: list[ast.stmt]):
    """Nodes on the handler's own path: nested defs and nested handlers are someone else's."""
    stack = list(stmts)
    while stack:
        node = stack.pop()
        if isinstance(node, _NOT_OWN):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _log_level(call: ast.Call, helpers: set[str]) -> str | None:
    """'error' for log_error(), the level for log.<level>(), 'helper' for a log_* helper, else None."""
    func = call.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
    if name == "log_error":
        return "error"
    if name in helpers:
        return "helper"
    if isinstance(func, ast.Attribute) and func.attr in _LEVELS:
        owner = func.value
        if (isinstance(owner, ast.Name) and owner.id == "log") or (isinstance(owner, ast.Attribute) and owner.attr == "log"):
            return func.attr
    return None


def _log_helpers(trees: dict[str, ast.Module]) -> set[str]:
    """Functions named log_*/_log_* whose own body logs through the central logger."""
    helpers = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.lstrip("_").startswith("log_"):
                if any(isinstance(n, ast.Call) and _log_level(n, set()) for n in _own_nodes(node.body)):
                    helpers.add(node.name)
    return helpers


def _handler_verdict(handler: ast.ExceptHandler, helpers: set[str]) -> str | None:
    """None when the handler complies; otherwise why it does not."""
    levels = set()
    for node in _own_nodes(handler.body):
        if isinstance(node, ast.Raise):
            return None
        if isinstance(node, ast.Call) and (level := _log_level(node, helpers)):
            levels.add(level)
    exc = ast.unparse(handler.type) if handler.type else None
    if exc in _BROAD:
        if levels & (_SERIOUS_LEVELS | {"helper"}):
            return None
        return "broad catch logged below warning" if levels else "broad catch neither logs nor re-raises"
    return None if levels else "neither logs nor re-raises"


def scan(sources: dict[str, str]) -> tuple[list[str], set[tuple[str, str, str]]]:
    """Return (violations, allowlist keys that matched) for {module name: source}."""
    trees = {name: ast.parse(src) for name, src in sources.items()}
    helpers = _log_helpers(trees)
    violations, used = [], set()
    for name, tree in trees.items():
        for handler, scope in _qualname_map(tree).items():
            verdict = _handler_verdict(handler, helpers)
            if verdict is None:
                continue
            key = (name, scope, ast.unparse(handler.type) if handler.type else "<bare>")
            if key in CONTROL_FLOW and verdict == "neither logs nor re-raises":
                used.add(key)
                continue
            violations.append(f"{name}:{handler.lineno} in {scope}: except {key[2]}: {verdict}")
    return violations, used


@pytest.fixture(scope="module")
def backend_scan():
    return scan({f.name: f.read_text() for f in sorted(BACKEND_DIR.glob("*.py"))})


def test_every_except_logs_or_reraises(backend_scan):
    violations, _ = backend_scan
    assert not violations, "log through backend.state (or add a control-flow entry with its reason):\n" + "\n".join(violations)


def test_control_flow_allowlist_has_no_stale_entries(backend_scan):
    _, used = backend_scan
    stale = sorted(set(CONTROL_FLOW) - used)
    assert not stale, f"these CONTROL_FLOW entries match no handler any more, remove them: {stale}"


@pytest.mark.parametrize("body, verdict", [
    ("try:\n    f()\nexcept ValueError:\n    pass\n", "neither logs nor re-raises"),
    ("try:\n    f()\nexcept ValueError:\n    log.debug('x')\n", None),
    ("try:\n    f()\nexcept ValueError:\n    st.log.info('x')\n", None),
    ("try:\n    f()\nexcept ValueError as e:\n    raise RuntimeError('x') from e\n", None),
    ("try:\n    f()\nexcept Exception:\n    log_error('x')\n", None),
    ("try:\n    f()\nexcept Exception:\n    st.log_error('x')\n", None),
    ("try:\n    f()\nexcept Exception:\n    log.warning('x')\n", None),
    ("try:\n    f()\nexcept Exception:\n    log.debug('x')\n", "broad catch logged below warning"),
    ("try:\n    f()\nexcept:\n    pass\n", "broad catch neither logs nor re-raises"),
    ("try:\n    f()\nexcept BaseException:\n    x = 1\n", "broad catch neither logs nor re-raises"),
    ("try:\n    f()\nexcept Exception:\n    _log_thing('x')\n", None),
    # A log call that only runs when a nested handler fires does not cover the outer path
    ("try:\n    f()\nexcept ValueError:\n    try:\n        g()\n    except OSError:\n        log.debug('x')\n",
     "neither logs nor re-raises"),
    # Nor does one inside a nested function that the handler merely defines
    ("try:\n    f()\nexcept ValueError:\n    def later():\n        log.debug('x')\n", "neither logs nor re-raises"),
    # A logger that is not the central one does not count
    ("try:\n    f()\nexcept ValueError:\n    _log.error('x')\n", "neither logs nor re-raises"),
])
def test_scanner_verdicts(body, verdict):
    helper = "def _log_thing(msg):\n    log.warning(msg)\n"
    violations, _ = scan({"sample.py": helper + body})
    assert [v.rsplit(": ", 1)[1] for v in violations] == ([verdict] if verdict else [])


def test_allowlist_is_scoped_to_module_function_and_type():
    body = "def parse(v):\n    try:\n        return int(v)\n    except ValueError:\n        return None\n"
    assert scan({"routes.py": body.replace("parse", "_query_float")})[0] == []
    assert scan({"routes.py": body})[0], "same handler in another function must not be allowlisted"
    assert scan({"routes.py": body.replace("parse", "_query_float").replace("ValueError", "TypeError")})[0]


def test_allowlisted_handlers_are_narrow():
    broad = [key for key in CONTROL_FLOW if key[2] in {"<bare>", "Exception", "BaseException"}]
    assert not broad, f"control flow is never a broad catch: {broad}"
