"""Test: frontend source keeps one home for connection status, separators, glyphs and labels.

Catches the source-level patterns behind findings F5 and F6, so they cannot creep back:
- F5: the header dot was styled from two modules with two class maps, the banner relied on a
  Tailwind class to stay hidden, the client probed the readiness endpoint, and close codes and
  timings were literals in ws.js.
- F6: 19 hand-built separators with 3 spellings and 5 spacings, status glyphs that drifted
  (degraded was a triangle in one place and a warning sign elsewhere), test type labels typed in
  three places, and interval fallbacks that silently disagreed with app.yaml.
Also enforces the CONTRIBUTING rule that every JS catch logs or re-throws.
"""
import ast
import json
import re

import pytest

from backend.state import BACKEND_DIR, BASE_DIR, FRONTEND_DIR

JS_DIR = FRONTEND_DIR / "js"
JS_FILES = sorted(JS_DIR.glob("*.js"))
INDEX_HTML = (FRONTEND_DIR / "index.html").read_text()


def _code_lines(path):
    """(line number, text) for every line that is not a whole-line comment."""
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip().startswith(("//", "/*", "*")):
            yield n, line


def _hits(pattern, allowed=()):
    """Lines matching pattern outside the allowed (file name, line regex) homes."""
    rx = re.compile(pattern)
    found = []
    for f in JS_FILES:
        for n, line in _code_lines(f):
            if rx.search(line) and not any(f.name == name and re.search(home, line) for name, home in allowed):
                found.append(f"{f.name}:{n}: {line.strip()[:100]}")
    return found


# ── F6: separators, glyphs, labels, config fallbacks ────────────────────────

def test_separators_come_from_the_shared_primitive():
    found = _hits(r"·|\\u00b7|&middot;|score-sep|kv-sep", allowed=[
        ("utils.js", r"^export const SEP = "),
        ("utils.js", r"^const _KV_SEP = "),
    ])
    assert not found, "build separators with sepHTML/segmentsHTML/SEP_TEXT/kvSep (utils.js):\n" + "\n".join(found)


def test_removed_separator_styles_stay_removed():
    for cls in (".score-sep", ".provider-score-sep"):
        assert cls not in INDEX_HTML


def test_status_glyphs_have_one_home():
    found = _hits(r"[✓✗✕⚠○▲]|\\u(2713|2717|2715|26a0|25cb)", allowed=[("utils.js", r"^export const STATUS_GLYPH = ")])
    # The sort-direction arrow in the history table is not a status glyph
    found = [f for f in found if not re.search(r"_sortDir === 'desc'", f)]
    assert not found, "use STATUS_GLYPH (utils.js):\n" + "\n".join(found)


def test_test_type_labels_are_not_hardcoded():
    found = _hits(r">(Health|Bench|Audit|Probe|HC|BM|AU|PR)<|'(Bench|HC|BM|AU)'|(?<!\w)(Health|Bench|Audit): ")
    assert not found, "read test type labels through testTypeLabel() (format.js):\n" + "\n".join(found)


def test_no_fallbacks_for_config_values():
    found = _hits(r"state\.\w+(Interval|Enabled)\s*(\|\||\?\?)")
    assert not found, "config values have no in-code fallback:\n" + "\n".join(found)


def test_config_derived_state_starts_unknown():
    """Every interval/enabled value applyConfig() sets starts as null, not as a guess."""
    state_js = (JS_DIR / "state.js").read_text()
    keys = re.findall(r"state\.(\w+) = cfg\.\w+_(?:seconds|enabled)\b", state_js)
    assert keys, "applyConfig no longer maps *_seconds/*_enabled fields"
    guessed = [k for k in keys if not re.search(rf"^\s+{k}: null,", state_js, re.M)]
    assert not guessed, f"initialise these to null in state.js: {guessed}"


# ── F5: connection status, liveness, close codes ────────────────────────────

def test_connection_ui_has_one_writer():
    found = _hits(r"ws-status|backend-down-banner", allowed=[("conn.js", r"getElementById")])
    assert not found, "only conn.js renders the connection dot and banner:\n" + "\n".join(found)


def test_banner_is_hidden_by_attribute_not_by_stylesheet():
    banner = re.search(r'<div id="backend-down-banner"[^>]*>', INDEX_HTML).group(0)
    assert re.search(r"\shidden(\s|>)", banner), "the banner must start with the hidden attribute"
    assert not re.search(r'class="[^"]*\bhidden\b', banner), "a class cannot hide the banner without the stylesheet"


def test_client_never_hardcodes_server_paths_or_close_codes():
    found = _hits(r"['\"`]/health|['\"`]/ws\b|\b(1008|1012|1013|4000)\b|_STALE_MS|_wsBackoff = \d")
    assert not found, "paths, close codes and timings come from state.conn (window.__MW_CONN__):\n" + "\n".join(found)


def test_every_connection_state_is_styled_and_explained():
    conn_js = (JS_DIR / "conn.js").read_text()
    states = json.loads(re.search(r"export const CONN_STATES = (\[[^\]]*\])", conn_js).group(1).replace("'", '"'))
    utils_js = (JS_DIR / "utils.js").read_text()
    help_js = (JS_DIR / "help.js").read_text()
    for s in states:
        assert f'#ws-status[data-state="{s}"]' in INDEX_HTML, f"no CSS for connection state {s}"
        assert re.search(rf"^\s+ws_{s}: ", utils_js, re.M), f"no HELP tip ws_{s}"
        assert re.search(rf"\bws_{s}: '", help_js), f"no glossary label ws_{s}"
    stale = set(re.findall(r"\bws_(\w+)\b", utils_js + help_js)) - set(states)
    assert not stale, f"tips or labels for states that no longer exist: {stale}"


def test_server_close_codes_come_from_close_codes():
    tree = ast.parse((BACKEND_DIR / "websocket.py").read_text())
    literal = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.keyword) and node.arg == "code" and isinstance(node.value, ast.Constant)
    ]
    assert not literal, f"websocket.py closes with literal codes on lines {literal}; use CLOSE_CODES"


# ── CONTRIBUTING: every catch logs or re-throws ─────────────────────────────

_LOGS = re.compile(r"\blog(Error|Warn|Info|Debug)\(|\bthrow\b")


def _balanced(src, start, open_ch, close_ch):
    depth = 0
    for i in range(start, len(src)):
        depth += (src[i] == open_ch) - (src[i] == close_ch)
        if depth == 0:
            return src[start:i + 1]
    return src[start:]


def _silent_catches(path):
    src = path.read_text()
    silent = [m.start() for m in re.finditer(r"\bcatch\s*(\([^)]*\))?\s*\{", src)
              if not _LOGS.search(_balanced(src, m.end() - 1, "{", "}"))]
    silent += [m.start() for m in re.finditer(r"\.catch\(", src)
               if not _LOGS.search(_balanced(src, m.end() - 1, "(", ")"))]
    return [f"{path.name}:{src.count(chr(10), 0, pos) + 1}" for pos in sorted(silent)]


@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.name)
def test_js_catches_log_or_rethrow(path):
    found = _silent_catches(path)
    assert not found, f"catch without logError/logWarn/logInfo/logDebug or throw: {found}"


def test_catch_scanner_detects_silent_catches(tmp_path):
    sample = tmp_path / "sample.js"
    sample.write_text("p.catch(() => {});\ntry { x(); } catch (e) { y = 1; }\ntry { x(); } catch (e) { logError('t', e); }\n")
    assert _silent_catches(sample) == ["sample.js:1", "sample.js:2"]


# ── npm scripts ─────────────────────────────────────────────────────────────

def test_npm_test_runs_python_and_js_suites():
    scripts = json.loads((BASE_DIR / "package.json").read_text())["scripts"]
    assert "pytest scripts/tests" in scripts["test"] and "npm run test:js" in scripts["test"]
    for name, folder in (("test:js", "tests/js"), ("test:e2e", "tests/e2e")):
        assert folder in scripts[name]
        assert list((BASE_DIR / folder).glob("*.test.mjs")), f"{name} finds no tests in {folder}"
