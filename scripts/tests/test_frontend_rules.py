"""Test: frontend source keeps one home for connection status, separators, glyphs and labels.

Catches the source-level patterns behind findings F5 and F6, so they cannot creep back:
- F5: the header dot was styled from two modules with two class maps, the banner relied on a
  Tailwind class to stay hidden, the client probed the readiness endpoint, and close codes and
  timings were literals in ws.js.
- F6: 19 hand-built separators with 3 spellings and 5 spacings, status glyphs that drifted
  (degraded was a triangle in one place and a warning sign elsewhere), test type labels typed in
  three places, and interval fallbacks that silently disagreed with app.yaml.
Also enforces the CONTRIBUTING rule that every JS catch logs or re-throws. F29: the guards
missed forms the old code had used (an escaped triangle, the dot glyph, a path after `${...}`,
em-dash and ' - ' joiners, a fallback on an alias), so each now matches every spelling.
"""
import ast
import json
import re

import pytest

from backend.state import (
    BACKEND_DIR, BASE_DIR, CAPABILITIES, CHART_VIEW_LABELS, FRONTEND_DIR, MODEL_KEY_SEP, STATUS_LABELS, STORAGE_KEYS,
    STORAGE_PREFIX,
)

JS_DIR = FRONTEND_DIR / "js"
JS_FILES = sorted(JS_DIR.glob("*.js"))
INDEX_HTML = (FRONTEND_DIR / "index.html").read_text()


def _code_lines(path):
    """(line number, text) for every line that is not a whole-line comment."""
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip().startswith(("//", "/*", "*")):
            yield n, line


def _char_forms(codepoints) -> str:
    """Regex matching any of the characters as written in JS or HTML: literal, \\uXXXX, \\u{X}, &#x..; or &#..;."""
    forms = []
    for cp in codepoints:
        forms += [re.escape(chr(cp)), rf"\\u{cp:04x}", rf"\\u\{{0*{cp:x}\}}", rf"&#x0*{cp:x};", rf"&#0*{cp};"]
    return "(?i:" + "|".join(forms) + ")"


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

# A string literal that is only a dash (em or en in any spelling, or a spaced hyphen): a hand-built joiner
_DASH_JOINER = rf"""(['"`])(?:\s*(?:{_char_forms((0x2014, 0x2013))}|&[mn]dash;)\s*|\s+-\s+)\1"""


def test_separators_come_from_the_shared_primitive():
    found = _hits(rf"{_char_forms((0xB7,))}|&middot;|score-sep|kv-sep|{_DASH_JOINER}", allowed=[
        ("utils.js", r"^export const SEP = "),
        ("utils.js", r"^const _KV_SEP = "),
        ("utils.js", r"^const _LOG_TAG_SEP = "),
    ])
    assert not found, "build separators with sepHTML/segmentsHTML/SEP_TEXT/kvSep (utils.js):\n" + "\n".join(found)


def test_removed_separator_styles_stay_removed():
    for cls in (".score-sep", ".provider-score-sep"):
        assert cls not in INDEX_HTML


# Every codepoint a status mark has used: STATUS_GLYPH today, the old ok/unknown dot and degraded triangle
_STATUS_CODEPOINTS = (0x2713, 0x2717, 0x2715, 0x26A0, 0x25CB, 0x25CF, 0x25B2)


def test_status_glyphs_have_one_home():
    found = _hits(_char_forms(_STATUS_CODEPOINTS), allowed=[
        ("utils.js", r"^export const (STATUS_GLYPH|TIER_DOT) = "),
        # The sort-direction arrow in the history table is not a status glyph
        ("modal-history.js", r"_sortDir === 'desc'"),
    ])
    assert not found, "use STATUS_GLYPH or TIER_DOT (utils.js):\n" + "\n".join(found)


def test_test_type_labels_are_not_hardcoded():
    found = _hits(r">(Health|Bench|Audit|Probe|HC|BM|AU|PR)<|'(Bench|HC|BM|AU)'|(?<!\w)(Health|Bench|Audit): ")
    assert not found, "read test type labels through testTypeLabel() (format.js):\n" + "\n".join(found)


def _config_state_keys() -> list[str]:
    """State fields applyConfig() fills from the server's interval and enabled settings."""
    keys = re.findall(r"state\.(\w+) = cfg\.\w+_(?:seconds|enabled)\b", (JS_DIR / "state.js").read_text())
    assert keys, "applyConfig no longer maps *_seconds/*_enabled fields"
    return keys


def _config_fallbacks(path, keys) -> list[str]:
    """`||`/`??` after a config-derived state field, read directly or through a local alias."""
    lines = list(_code_lines(path))
    field = rf"state\.(?:{'|'.join(keys)})\b"
    aliases = set()
    for _, line in lines:
        aliases.update(re.findall(rf"\b(?:const|let|var)\s+(\w+)\s*=\s*{field}(?!\s*(?:\|\||\?\?))", line))
        for group in re.findall(r"\b(?:const|let|var)\s*\{([^}]*)\}\s*=\s*state\b", line):
            for key, alias in re.findall(r"(\w+)(?:\s*:\s*(\w+))?", group):
                if key in keys:
                    aliases.add(alias or key)
    used = rf"(?:{field}|\b(?:{'|'.join(map(re.escape, aliases))})\b)" if aliases else field
    rx = re.compile(rf"{used}\s*(?:\|\||\?\?)")
    return [f"{path.name}:{n}: {line.strip()[:100]}" for n, line in lines if rx.search(line)]


def test_no_fallbacks_for_config_values():
    keys = _config_state_keys()
    found = [hit for f in JS_FILES for hit in _config_fallbacks(f, keys)]
    assert not found, "config values have no in-code fallback:\n" + "\n".join(found)


def test_fallback_scanner_follows_aliases(tmp_path):
    sample = tmp_path / "sample.js"
    sample.write_text(
        "const a = state.benchmarkInterval || 60;\n"
        "const bi = state.benchmarkInterval;\nconst x = bi ?? 3600;\n"
        "const { healthInterval, probeEnabled: pe } = state;\nf(healthInterval || 1, pe ?? true);\n"
        "const ok = state.benchmarkInterval;\nconst other = 1 || 2;\n"
    )
    assert _config_fallbacks(sample, ["benchmarkInterval", "healthInterval", "probeEnabled"]) == [
        "sample.js:1: const a = state.benchmarkInterval || 60;",
        "sample.js:3: const x = bi ?? 3600;",
        "sample.js:5: f(healthInterval || 1, pe ?? true);",
    ]


def test_config_derived_state_starts_unknown():
    """Every interval/enabled value applyConfig() sets starts as null, not as a guess."""
    state_js = (JS_DIR / "state.js").read_text()
    guessed = [k for k in _config_state_keys() if not re.search(rf"^\s+{k}: null,", state_js, re.M)]
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
    found = _hits(r"/(?:health|ws)(?![\w.-])|\b(1008|1012|1013|4000)\b|_STALE_MS|_wsBackoff = \d")
    assert not found, "paths, close codes and timings come from state.conn (window.__MW_BOOT__.conn):\n" + "\n".join(found)


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


def _silent_catches(path, logs=_LOGS):
    src = path.read_text()
    silent = [m.start() for m in re.finditer(r"\bcatch\s*(\([^)]*\))?\s*\{", src)
              if not logs.search(_balanced(src, m.end() - 1, "{", "}"))]
    silent += [m.start() for m in re.finditer(r"\.catch\(", src)
               if not logs.search(_balanced(src, m.end() - 1, "(", ")"))]
    return [f"{path.name}:{src.count(chr(10), 0, pos) + 1}" for pos in sorted(silent)]


@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.name)
def test_js_catches_log_or_rethrow(path):
    found = _silent_catches(path)
    assert not found, f"catch without logError/logWarn/logInfo/logDebug or throw: {found}"


def test_catch_scanner_detects_silent_catches(tmp_path):
    sample = tmp_path / "sample.js"
    sample.write_text("p.catch(() => {});\ntry { x(); } catch (e) { y = 1; }\ntry { x(); } catch (e) { logError('t', e); }\n")
    assert _silent_catches(sample) == ["sample.js:1", "sample.js:2"]


# ── Frontend group round: one home per value, label and key ─────────────────

SW_JS = FRONTEND_DIR / "sw.js"


def test_storage_keys_come_from_the_registry():
    """F19: every storage key is LS.<NAME> (backend/state.py STORAGE_KEYS through the bootstrap)."""
    prefix = re.escape(STORAGE_PREFIX)
    found = _hits(rf"""['"`]{prefix}""")
    found += [f"sw.js:{n}" for n, line in _code_lines(SW_JS) if re.search(rf"""['"`]{prefix}""", line)]
    assert not found, "use LS.<NAME> or BOOT.storage_prefix instead of key literals:\n" + "\n".join(found)


def test_every_storage_key_name_is_registered_and_used():
    """F19: LS.<NAME> must exist in STORAGE_KEYS (a typo stores under "undefined"), and a key no
    module reads any more leaves the registry, so pruneStorage clears it from browsers."""
    used = {name for f in JS_FILES for name in re.findall(r"\bLS\.([A-Z_]+)", f.read_text())}
    assert used - set(STORAGE_KEYS) == set(), f"unregistered: {used - set(STORAGE_KEYS)}"
    assert set(STORAGE_KEYS) - used == set(), f"registered but unused: {set(STORAGE_KEYS) - used}"


def test_model_keys_are_built_with_the_backend_separator():
    """F45: the frontend joins and splits model keys only through makeModelKey/parseModelKey."""
    sep = re.escape(MODEL_KEY_SEP)
    found = _hits(rf"""['"`]{sep}['"`]|\${{[^}}]+}}{sep}\${{""")
    assert not found, "build model keys with makeModelKey/parseModelKey (state.js):\n" + "\n".join(found)


def test_then_chains_end_in_a_catch():
    """F33: a promise chain without a .catch() turns a failure into an unhandled rejection."""
    found = []
    for f in JS_FILES:
        src = f.read_text()
        for m in re.finditer(r"\.then\(", src):
            end = m.end() - 1 + len(_balanced(src, m.end() - 1, "(", ")"))
            while link := re.match(r"\s*\.(?:then|finally)\(", src[end:]):
                start = end + link.end() - 1
                end = start + len(_balanced(src, start, "(", ")"))
            if not re.match(r"\s*\.catch\(", src[end:]):
                found.append(f"{f.name}:{src.count(chr(10), 0, m.start()) + 1}")
    assert not found, f".then() chains without a final .catch(): {sorted(set(found))}"


def test_service_worker_catches_log():
    """F35: sw.js has no page logger; every catch there goes through swLogError, and the promise
    each event handler waits for ends in one, so a failed activate or push is not silent."""
    found = _silent_catches(SW_JS, logs=re.compile(r"\bswLogError\(|\bthrow\b"))
    src = SW_JS.read_text()
    for m in re.finditer(r"\.waitUntil\(", src):
        arg = _balanced(src, m.end() - 1, "(", ")")[1:-1].rstrip()
        last = arg.rfind(".catch(")
        tail = arg[last + len(".catch"):] if last >= 0 else ""
        if not (tail and _balanced(tail, 0, "(", ")") == tail and "swLogError(" in tail):
            found.append(f"sw.js:{src.count(chr(10), 0, m.start()) + 1} waitUntil without a final .catch(swLogError)")
    assert not found, f"sw.js: {found}"


def test_no_timer_period_or_ui_setting_is_a_literal():
    """F20: refresh periods, TTLs, ratios and the toast settings come from app.yaml ui.* (bootstrap and /api/config)."""
    found = _hits(r"setInterval\([^;]*,\s*[\d_]+\s*\)\s*;?\s*$")
    found += _hits(r"\b(?:state\.ui|BOOT\.ui|ui)(?:\.\w+)+\s*(?:\|\||\?\?)")
    assert not found, "timings come from state.ui (app.yaml ui.*), without in-code fallbacks:\n" + "\n".join(found)


def test_backend_constant_lists_start_empty():
    """F22: statusValues and chartViews are filled by /api/config, never seeded with copies."""
    state_js = (JS_DIR / "state.js").read_text()
    for field in ("statusValues", "chartViews"):
        assert re.search(rf"^\s+{field}: \[\],", state_js, re.M), f"{field} starts as []"
    found = _hits(r"""\[\s*'online',\s*'degraded'|\[\s*'speed',\s*'consistency'|\[\s*'benchmark',\s*'health'""")
    assert not found, "backend constant lists come from /api/config:\n" + "\n".join(found)


# Old spellings the findings named, which no current label uses
_OLD_LABELS = ("Errors", "Reasoning", "Structured Output", "Structured output")


def _label_pairs() -> list[tuple[str, str]]:
    pairs = [*STATUS_LABELS.items(), *CHART_VIEW_LABELS.items(), *((c["key"], c["label"]) for c in CAPABILITIES)]
    assert pairs
    return pairs


def test_labels_are_not_redefined_in_the_frontend():
    """F76, F77, F80: status, chart view and capability labels come from /api/config.

    The copies were maps from a backend key to its label (error: 'Offline', speed: 'TPS + TTFT'),
    capability options typed into index.html and filter.js, and old spellings that drifted.
    """
    found = []
    for key, label in _label_pairs():
        found += _hits(rf"""\b{re.escape(key)}['"]?\s*:\s*['"`]{re.escape(label)}['"`]""", allowed=[
            # The glossary title of the scores help topic, not the chart view label
            ("help.js", r"^\s+scores: 'Scores',"),
        ])
    for cap in CAPABILITIES:
        found += _hits(rf"""['"]{re.escape(cap["key"])}['"]\s*,\s*label\s*:""")
    found += _hits(rf"""['"`](?:{'|'.join(map(re.escape, _OLD_LABELS))})['"`]""")
    found += [f"index.html: data-cap {cap}" for cap in re.findall(r'data-cap="(\w+)"', INDEX_HTML)]
    assert not found, "read labels through statusLabel/chartViewLabel/state.capabilities:\n" + "\n".join(found)


def test_capability_keys_come_from_config():
    found = _hits(r"""['"]supports_(?:vision|tools|cache|structured_output)['"]""")
    assert not found, "capability keys come from state.capabilities:\n" + "\n".join(found)


def test_score_tiers_come_from_config():
    """F81: score boundaries and tier names are color_thresholds.scores and color_thresholds.tiers."""
    found = _hits(r"""[<>]=?\s*(?:80|60|40|20)\b(?!\s*[*/])|['"`](?:Excellent|Good|Bad|Critical)['"`]""", allowed=[
        # Seconds and minutes in duration formatting
        ("format.js", r"\b[sm] < 60\b"),
    ])
    assert not found, "score tiers come from state.colorThresholds:\n" + "\n".join(found)


def test_breakpoint_and_default_ranges_have_one_home():
    """F85: 640 is BP_SM in utils.js; default ranges are app.yaml ui.default_ranges."""
    found = _hits(r"\b640\b", allowed=[("utils.js", r"^export const BP_SM = 640;")])
    found += _hits(r"<=\s*BP_SM")
    found += _hits(r"""(?<!getContext\()['"`]\d+[hdw]['"`]""")
    assert not found, "use isPhone()/BP_SM and state.ui.default_ranges:\n" + "\n".join(found)


def test_help_texts_do_not_restate_config_values():
    """F78: the stall threshold, hiccup multiplier and degradation rule come from /api/config."""
    help_block = re.search(r"^export const HELP = \{(.*?)^\};", (JS_DIR / "utils.js").read_text(), re.S | re.M).group(1)
    # "1x = token-by-token" defines the batching unit; a multiplier above 1 is a setting
    found = re.findall(r"\d+\s*ms\b|\b(?:[2-9]|\d{2,})(?:\.\d+)?\s*(?:x|\u00d7|\\u00d7)(?!\w)|one or more (?:metric|Critical)", help_block)
    assert not found, f"HELP restates config values: {found}"


def test_no_rendered_em_or_en_dashes():
    """F79: prose uses no em or en dashes (CONTRIBUTING), in code, markup and the labels the backend sends."""
    dash = re.compile(rf"{_char_forms((0x2014, 0x2013))}|&[mn]dash;")
    sources = [*JS_FILES, SW_JS, FRONTEND_DIR / "index.html", FRONTEND_DIR / "manifest.json",
               BACKEND_DIR / "state.py", BACKEND_DIR / "routes.py"]
    found = [f"{f.name}:{n}" for f in sources for n, line in enumerate(f.read_text().splitlines(), 1) if dash.search(line)]
    assert not found, f"em/en dashes: {found}"


def test_every_static_id_is_used():
    """F57: #config-warning was markup nothing ever filled; an id in index.html is read by a script or a style."""
    code = "\n".join(f.read_text() for f in JS_FILES) + INDEX_HTML
    unused = []
    for id_ in re.findall(r'\sid="([\w-]+)"', INDEX_HTML):
        uses = len(re.findall(rf"#{re.escape(id_)}\b|['\"`]{re.escape(id_)}['\"`]|(?:for|aria-\w+|href)=\"#?{re.escape(id_)}\"", code))
        prefixed = re.search(rf"['\"`]{re.escape(id_.rsplit('-', 1)[0])}-['\"`]?\s*\+|`{re.escape(id_.rsplit('-', 1)[0])}-\$\{{", code)
        if not uses and not prefixed:
            unused.append(id_)
    assert "config-warning" not in INDEX_HTML
    assert not unused, f"ids nothing reads: {unused}"


# ── npm scripts ─────────────────────────────────────────────────────────────

def test_npm_test_runs_python_and_js_suites():
    scripts = json.loads((BASE_DIR / "package.json").read_text())["scripts"]
    assert "pytest scripts/tests" in scripts["test"] and "npm run test:js" in scripts["test"]
    for name, folder in (("test:js", "tests/js"), ("test:e2e", "tests/e2e")):
        assert folder in scripts[name]
        assert list((BASE_DIR / folder).glob("*.test.mjs")), f"{name} finds no tests in {folder}"
