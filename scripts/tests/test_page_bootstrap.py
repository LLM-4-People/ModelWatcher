"""Test: what the page gets before its first request, and what tells it about a deploy.

Catches:
- F19: localStorage keys were literals in nine modules and in the inline theme script; the script
  now reads them from backend/state.py STORAGE_KEYS through the page bootstrap's own list.
- F32: the page read four separate globals and failed with scattered TypeErrors without them;
  there is one bootstrap object, window.__MW_BOOT__.
- F82: the base colours were copied into the meta tag, the critical CSS, the inline script, the
  manifest and the bell colours; all of them come from frontend/input.css now.
- F87: /api/deploy-version and the sw.js fingerprint were cached until some client loaded /, so a
  lone open tab never saw a deploy, and the built stylesheet was not part of the version.
- F90: /api/config and the hello frame did not say whether tests run at all.
- F20: /api/config was cached for a fixed 10 s.
"""
import json
import os
import re
import shutil
import time

import pytest

import backend.routes as routes
import backend.state as st
from scripts.tests.app_child import result


def _real_app_page(run_python, env):
    code = (
        "import json\n"
        "from starlette.testclient import TestClient\n"
        "import backend.state as st\n"
        "from backend.main import app\n"
        "from scripts.tests.app_child import emit, receive_until\n"
        "with TestClient(app, base_url='http://localhost') as client:\n"
        "    out = {'page': client.get('/').text, 'manifest': client.get(st.c.static_url_prefix + '/manifest.json').text,\n"
        "           'config': client.get('/api/config').json()}\n"
        "    with client.websocket_connect('ws://localhost' + st.WS_PATH, headers={'origin': 'http://localhost'}) as ws:\n"
        "        out['hello'] = receive_until(ws, lambda f: len(f) >= 1, 5)[0]\n"
        "    st.scheduler_running = True\n"
        "    out['scheduler_after_start'] = client.get('/api/config').json()['scheduler']\n"
        "emit(out)\n"
    )
    return result(run_python("-c", code, env={**env, "MW_DISABLE_TESTS": "1"}))


@pytest.fixture(scope="module")
def served(run_python, example_config_env):
    return _real_app_page(run_python, example_config_env)


def _boot(page: str) -> dict:
    return json.loads(page.split("window.__MW_BOOT__=", 1)[1].split("</script>", 1)[0])


def test_one_bootstrap_object_holds_what_the_page_needs_first(served):
    boot = _boot(served["page"])
    assert set(boot) == {"static_prefix", "app_name", "log_level", "conn", "storage_keys", "storage_prefix",
                         "themes", "model_key_sep", "ui"}
    assert boot["storage_keys"] == st.STORAGE_KEYS
    assert boot["model_key_sep"] == st.MODEL_KEY_SEP
    assert boot["ui"] == served["config"]["ui"], "the bootstrap and /api/config send the same browser settings"
    for old in ("__MW_CONN__", "__STATIC_PREFIX__=", "__APP_NAME__=", "__LOG_LEVEL__"):
        assert old not in served["page"], f"{old} is gone: the page reads window.__MW_BOOT__ only"


def test_storage_keys_share_one_prefix():
    assert all(v.startswith(st.STORAGE_PREFIX) for v in st.STORAGE_KEYS.values())
    assert len(set(st.STORAGE_KEYS.values())) == len(st.STORAGE_KEYS)


def test_inline_theme_script_reads_the_registry_keys(served):
    script = re.search(r"<script nonce=\"[^\"]+\">\(function\(\)\{(.*?)\}\)\(\)</script>", served["page"]).group(1)
    used = set(re.findall(rf"\"({re.escape(st.STORAGE_PREFIX)}\w+)\"", script))
    assert used == {st.STORAGE_KEYS[k] for k in ("THEME", "NOTIF_SETTINGS", "NOTIF_LOCAL")}
    assert "catch(" not in script, "the inline script has no silent catch (F35)"


def test_early_colours_come_from_input_css(served):
    tokens = routes.theme_tokens()
    page = served["page"]
    head = page[:page.index("</head>")]
    for scheme, selector in (("light", ":root"), ("dark", ".dark")):
        for token in ("--color-base", "--color-text-primary", "--color-tier-accent"):
            assert f"{token}:{tokens[scheme][token]}" in head.split(selector + "{", 1)[1].split("}", 1)[0]
    dark_base = tokens["dark"]["--color-base"]
    assert f'<meta name="theme-color" content="{dark_base}">' in page
    manifest = json.loads(served["manifest"])
    assert manifest["theme_color"] == manifest["background_color"] == dark_base


_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
# Hex digits that are not colours: HTML entities and anchors are matched with their context
_NOT_COLOR = re.compile(r"&#x?[0-9a-fA-F]+;|href=\"#|#[0-9a-fA-F]{3,8}\b(?=[^\"']*[\"'][^>]*href)")


def _hex_colors(text: str) -> list[str]:
    return _HEX.findall(_NOT_COLOR.sub("", text))


def test_no_colour_literal_outside_input_css():
    """Palette values live in input.css only; everything else reads a token (F82)."""
    sources = [*sorted((st.FRONTEND_DIR / "js").glob("*.js")), st.FRONTEND_DIR / "index.html",
               st.FRONTEND_DIR / "sw.js", st.FRONTEND_DIR / "manifest.json", st.BACKEND_DIR / "routes.py"]
    found = {p.name: _hex_colors(p.read_text()) for p in sources}
    found = {k: v for k, v in found.items() if v}
    assert not found, f"colour literals outside frontend/input.css: {found}"


def test_scheduler_state_reaches_the_page(served):
    """F90: with MW_DISABLE_TESTS the page can say testing is paused, from config and from the hello."""
    for where in (served["config"]["scheduler"], served["hello"]["scheduler"]):
        assert where["running"] is False
        assert where["paused"] is True
    assert "last_run_ago_seconds" not in served["config"], "scheduler fields live under scheduler"
    # The response is cached until the config reloads or the scheduler starts or stops, not for a
    # fixed 10 s (F20), so a start shows at once
    assert served["scheduler_after_start"]["running"] is True


def test_deploy_version_follows_frontend_edits_without_a_page_load(monkeypatch, tmp_path):
    """F87: an edit or a rebuilt stylesheet changes the version and the fingerprint at once."""
    frontend = tmp_path / "frontend"
    shutil.copytree(st.FRONTEND_DIR / "js", frontend / "js")
    (frontend / "index.html").write_text("<html></html>")
    css = tmp_path / "built.css"
    css.write_text("a{}")
    monkeypatch.setattr(st, "FRONTEND_DIR", frontend)
    monkeypatch.setattr(st, "BUILT_CSS_PATH", css)
    v0, f0 = routes.deploy_version().body, routes._asset_fingerprint()

    def touch(path, text):
        path.write_text(text)
        stamp = time.time() + 5
        os.utime(path, (stamp, stamp))

    touch(frontend / "js" / "utils.js", "// edited\n")
    v1, f1 = routes.deploy_version().body, routes._asset_fingerprint()
    assert v1 != v0 and f1 != f0, "an edited module changes the version with no page load in between"
    touch(css, "b{}")
    time.sleep(0.01)
    assert routes._asset_fingerprint() != f1, "the built stylesheet is part of the fingerprint"
