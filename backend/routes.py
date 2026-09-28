"""API route handlers (non-notification) and static file serving."""

import asyncio
import hashlib
import math
import re
from pathlib import Path

import orjson
from fastapi import Request, Query
from fastapi.responses import JSONResponse
from starlette.responses import Response

import backend.state as st
from backend.stats import build_summary_response, build_chart_response, build_history_response, cached_card_buckets, build_model_info_summary, build_model_info_detail
from backend.schemas import ClientErrorBody
from backend.models import get_providers_grouped
from backend.websocket import connection_config


# ── Shared utilities (used by push_routes, notifications, etc.) ────────────

_client_error_times: dict[str, list[float]] = {}
_metrics_rebuild_lock = asyncio.Lock()
_model_info_rebuild_lock = asyncio.Lock()
# The /api/config body, rebuilt after a config reload (which clears raw) or a scheduler start or stop
_config_cache: dict = {"raw": None, "etag": None, "scheduler": None}


def check_rate_limit(buckets: dict, key: str, window_s: float, max_count: int, label: str = "Rate limited") -> JSONResponse | None:
    """Per-key (e.g. per-IP) or global (key='_g') sliding-window limit as an HTTP 429."""
    if st.rate_limited(buckets.setdefault(key, []), window_s, max_count):
        return error_response(label, 429)
    return None


def client_ip(request: Request) -> str:
    """Extract client IP for rate limiting.

    Uses request.client.host (set by uvicorn --proxy-headers when behind a
    reverse proxy). Never trusts X-Forwarded-For - it is client-controlled
    and trivially spoofed, allowing per-IP rate limit bypass.
    """
    return request.client.host if request.client else "_unknown"


# ── Placeholders ───────────────────────────────────────────────────────────

_PLACEHOLDER_PREFIX = "__STATIC_PREFIX__"
_PLACEHOLDER_NAME = "__APP_NAME__"
_PLACEHOLDER_DESC = "__APP_DESCRIPTION__"

# Pre-compiled regex patterns - rebuilt when static_url_prefix changes
_prefix_re_cache: dict[str, re.Pattern] = {}
_prefix_re_key: str = ""


def _short_hash(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:16]


def _get_prefix_re(pattern_template: str) -> re.Pattern:
    """Get or compile a regex with the current static_url_prefix escaped."""
    global _prefix_re_key
    prefix = st.c.static_url_prefix
    if prefix != _prefix_re_key:
        _prefix_re_cache.clear()
        _prefix_re_key = prefix
    cached = _prefix_re_cache.get(pattern_template)
    if cached:
        return cached
    compiled = re.compile(pattern_template.format(re.escape(prefix)))
    _prefix_re_cache[pattern_template] = compiled
    return compiled


_file_version_cache: dict[str, tuple[float, str, float]] = {}
_asset_fingerprint_cache: dict = {"signature": None, "value": None}


def _mtime(path: Path) -> float | None:
    """A frontend file's mtime; None when it does not exist (not built yet, or removed while scanning)."""
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return None


def _read_bytes(path: Path) -> bytes | None:
    """A frontend file's content; None when it does not exist (see _mtime)."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _newest_mtime(root: Path, pattern: str) -> float:
    """Newest mtime of the files under root that match pattern; 0.0 when there are none."""
    return max((m for f in root.rglob(pattern) if f.is_file() and (m := _mtime(f)) is not None), default=0.0)


def _frontend_files() -> list[Path]:
    """Every file the page is built from: frontend/ and the built stylesheet, which the Docker image
    keeps outside it (st.BUILT_CSS_PATH)."""
    files = sorted(f for f in st.FRONTEND_DIR.rglob("*") if f.is_file())
    if not st.BUILT_CSS_PATH.is_relative_to(st.FRONTEND_DIR) and st.BUILT_CSS_PATH.is_file():
        files.append(st.BUILT_CSS_PATH)
    return files


def _frontend_signature() -> tuple[float, int]:
    """Newest mtime and number of the frontend files: cheap enough for every deploy poll, and it
    changes when a file is edited, added, removed or the stylesheet is rebuilt. Versions used to be
    cached until some client loaded /, so a lone open tab never saw a deploy (finding F87)."""
    files = _frontend_files()
    return max((m for f in files if (m := _mtime(f)) is not None), default=0.0), len(files)


def _static_version() -> float:
    """Mtime of the most recently modified frontend file (the /api/deploy-version value)."""
    return _frontend_signature()[0]


def _asset_fingerprint() -> str:
    """Content hash of all frontend assets (sw.js cache version), recomputed when the signature changes."""
    signature = _frontend_signature()
    if _asset_fingerprint_cache["signature"] != signature:
        h = hashlib.sha1()
        for f in _frontend_files():
            body = _read_bytes(f)
            if body is not None:
                h.update(f.name.encode() if f == st.BUILT_CSS_PATH else f.relative_to(st.FRONTEND_DIR).as_posix().encode())
                h.update(body)
        _asset_fingerprint_cache.update(signature=signature, value=h.hexdigest()[:16])
    return _asset_fingerprint_cache["value"]


# ── Theme tokens (frontend/input.css is their one home, finding F82) ────────────

_THEME_BLOCKS = {"light": ":root", "dark": ".dark"}
_TOKEN_RE = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")
# Painted before the stylesheet loads: page background and text, and the notification bell
_EARLY_TOKENS = ("--color-base", "--color-text-primary", "--color-tier-accent")
_theme_tokens_cache: dict = {"mtime": None, "value": None}


def theme_tokens() -> dict[str, dict[str, str]]:
    """Custom properties of input.css's light (:root) and dark (.dark) blocks, cached by mtime.

    The early-paint style, the theme-color meta tag and the manifest take their colours from here,
    so a palette change in input.css reaches them without a second copy.
    """
    path = st.FRONTEND_DIR / "input.css"
    mt = _mtime(path)
    if _theme_tokens_cache["mtime"] != mt:
        css = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)
        tokens = {}
        for scheme, selector in _THEME_BLOCKS.items():
            m = re.search(rf"^{re.escape(selector)}\s*\{{(.*?)^\}}", css, re.S | re.M)
            if not m:
                raise ValueError(f"{path}: no {selector} block with the {scheme} theme tokens")
            tokens[scheme] = dict(_TOKEN_RE.findall(m.group(1)))
        _theme_tokens_cache.update(mtime=mt, value=tokens)
    return _theme_tokens_cache["value"]


def _early_theme_css() -> str:
    """The early-paint tokens for both themes, so the page has its colours before the stylesheet."""
    tokens = theme_tokens()
    block = lambda scheme: ";".join(f"{t}:{tokens[scheme][t]}" for t in _EARLY_TOKENS)
    return f"<style>{_THEME_BLOCKS['light']}{{{block('light')}}}{_THEME_BLOCKS['dark']}{{{block('dark')}}}</style>"


def _file_version(path_suffix: str) -> str:
    """Content-based version hash for a frontend file, cached by mtime.

    Returns a short SHA1 hex digest that changes only when the file's
    content changes, enabling effective browser caching via ?v= params.
    Falls back to global mtime for missing files.

    For JS files under js/, the hash also incorporates _js_max_mtime()
    so that when ANY JS file changes, ALL JS ?v= values change in the
    HTML. This prevents stale browser ES module caches: without it, a
    file like app.js whose own content didn't change keeps its old ?v=,
    and the browser reuses its cached module which contains outdated
    import rewrite hashes (e.g. from './modal.js?v=old' despite modal.js
    having changed).
    """
    f = st.BUILT_CSS_PATH if path_suffix == st.BUILT_CSS_NAME else st.FRONTEND_DIR / path_suffix
    is_js = path_suffix.startswith("js/") and path_suffix.endswith(".js")
    mt = _mtime(f)
    if mt is None:
        return str(_static_version())
    jmt = _js_max_mtime() if is_js else 0.0
    cached = _file_version_cache.get(path_suffix)
    if cached and cached[0] == mt and cached[2] == jmt:
        return cached[1]
    body = _read_bytes(f)
    if body is None:
        return str(_static_version())
    h = _short_hash(body)
    if is_js and jmt:
        h = _short_hash((h + str(jmt)).encode())
    _file_version_cache[path_suffix] = (mt, h, jmt)
    return h


def _safe_frontend_file(path_suffix: str, *, required_root=None, suffixes: frozenset[str] | None = None):
    """Resolve a frontend-relative path and reject traversal outside required_root."""
    if "\x00" in path_suffix:  # client input; resolve() would raise on it
        return None
    root = (required_root or st.FRONTEND_DIR).resolve()
    path = (st.FRONTEND_DIR / path_suffix).resolve()
    if not path.is_relative_to(root):
        return None
    if suffixes and path.suffix.lower() not in suffixes:
        return None
    return path if path.is_file() else None


# ── JS import rewriting (adds ?v= to eS module import specifiers) ───────────

_JS_FROM_RE = re.compile(r"""(from\s+)(['"])(\.[^'"]+\.js)\2""")
_JS_DYNAMIC_RE = re.compile(r"""(import\(\s*)(['"])(\.[^'"]+\.js)\2""")
_js_rewrite_cache: dict[str, tuple[float, float, bytes]] = {}
_JS_ENTRY = "js/app.js"


def module_preload_order() -> list[str]:
    """Frontend paths of every module the entry module loads statically, dependencies first.

    Follows the same `from '...'` specifiers that _rewrite_js_imports versions, so a new
    module is preloaded without editing a list (finding F36: a hand-kept list missed conn.js
    and filter.js). Targets of dynamic import() load on demand and stay out.
    """
    order: list[str] = []
    seen: set[str] = set()

    def visit(rel: str):
        if rel in seen:
            return
        seen.add(rel)
        path = st.FRONTEND_DIR / rel
        for m in _JS_FROM_RE.finditer(path.read_text()):
            visit((path.parent / m.group(3)).resolve().relative_to(st.FRONTEND_DIR).as_posix())
        order.append(rel)

    visit(_JS_ENTRY)
    return order


def _js_max_mtime() -> float:
    """Max mtime of all files in frontend/js/ - any JS change invalidates all rewrites."""
    return _newest_mtime(st.FRONTEND_DIR / "js", "*.js")


def _rewrite_js_imports(content: bytes, path_suffix: str) -> bytes:
    """Rewrite ES module import specifiers to add ?v= content-hash params.

    Transforms e.g. `from './chart.js'` → `from './chart.js?v=abc123'`.
    This ensures that when any transitive dependency changes, the browser
    fetches the new version instead of serving the stale module from its
    ES module cache.
    """
    file_dir = (st.FRONTEND_DIR / path_suffix).parent

    def _replacer(m):
        prefix = m.group(1)
        quote = m.group(2)
        specifier = m.group(3)
        target = (file_dir / specifier).resolve()
        if not target.is_relative_to(st.FRONTEND_DIR):
            return m.group(0)
        target_suffix = target.relative_to(st.FRONTEND_DIR).as_posix()
        v = _file_version(target_suffix)
        return f"{prefix}{quote}{specifier}?v={v}{quote}"

    text = content.decode("utf-8")
    text = _JS_FROM_RE.sub(_replacer, text)
    text = _JS_DYNAMIC_RE.sub(_replacer, text)
    return text.encode("utf-8")


def serve_js(request: Request, path_suffix: str) -> Response:
    """Serve a JS file with ?v= params added to ES module import specifiers.

    Cache key is (file_mtime, js_max_mtime) - any JS file change on disk
    invalidates all cached rewrites so import ?v= hashes stay fresh.
    """
    f = _safe_frontend_file(
        path_suffix,
        required_root=st.FRONTEND_DIR / "js",
        suffixes=frozenset({".js", ".mjs"}),
    )
    if f is None:
        return Response(status_code=404)
    mt = _mtime(f)
    jmt = _js_max_mtime()
    cached = _js_rewrite_cache.get(path_suffix)
    if cached and cached[0] == mt and cached[1] == jmt:
        body = cached[2]
    else:
        body = _rewrite_js_imports(f.read_bytes(), path_suffix)
        _js_rewrite_cache[path_suffix] = (mt, jmt, body)

    etag = _compute_etag(body)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
    return Response(content=body, media_type="application/javascript", headers={"ETag": etag, "Cache-Control": "no-cache"})


_CACHE_VER_PLACEHOLDER = '__CACHE_VERSION__'
# The app's colour where there is one for both themes (manifest, first theme-color): the dark base
_PLACEHOLDER_THEME = "__THEME_COLOR__"


def _replace_placeholders(text: str) -> str:
    """Replace the __STATIC_PREFIX__, __APP_NAME__, __APP_DESCRIPTION__ and __THEME_COLOR__ placeholders."""
    text = text.replace(_PLACEHOLDER_PREFIX, st.c.static_url_prefix)
    text = text.replace(_PLACEHOLDER_NAME, st.c.app_name)
    text = text.replace(_PLACEHOLDER_DESC, st.c.app_description)
    text = text.replace(_PLACEHOLDER_THEME, theme_tokens()["dark"]["--color-base"])
    return text


def ui_config() -> dict:
    """Browser settings (app.yaml ui.* and the in-app notification settings), in the page
    bootstrap and in /api/config, so the page has them from its first line on (finding F20)."""
    return {**st.c.ui, "toast_duration_ms": st.c.notif_in_app_toast_ms,
            "notif_history_size": st.c.notif_in_app_history_size}


def page_bootstrap() -> dict:
    """Everything the page needs before its first request, as window.__MW_BOOT__.

    One object instead of one global per value, so the page checks it once at start (finding F32)
    and a missing value is one clear error rather than scattered TypeErrors.
    """
    return {
        "static_prefix": st.c.static_url_prefix,
        "app_name": st.c.app_name,
        "log_level": st.LOG_LEVELS.index(st.c.log_level),
        "conn": connection_config(),
        "storage_keys": st.STORAGE_KEYS,
        "storage_prefix": st.STORAGE_PREFIX,
        "themes": st.THEMES,
        "model_key_sep": st.MODEL_KEY_SEP,
        "ui": ui_config(),
    }


_METRICS_RESPONSE_TYPES = frozenset({"card", "modal", "history"})
_TEST_TYPES = frozenset((st.TEST_BENCHMARK, st.TEST_HEALTH))
_CHART_VIEWS = frozenset({"speed", "consistency", "scores", "health"})
_VALID_SORT_KEYS = frozenset({"time", "ttft", "tps", "stalls", "p99", "batch", "tail"})


def _query_error(message: str) -> JSONResponse:
    return error_response(message)


def _validate_model_key(model_key: str | None) -> JSONResponse | None:
    """Validate a model_key query param. Returns error response or None if valid."""
    if model_key is None:
        return None
    if not model_key or len(model_key) > st.MAX_MODEL_KEY_LEN:
        return _query_error("Invalid model")
    return None


def _query_choice(request: Request, name: str, default: str, allowed: frozenset[str]):
    value = request.query_params.get(name, default)
    if value not in allowed:
        return None, _query_error(f"Invalid {name}")
    return value, None


def _query_float(request: Request, name: str):
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return None, None
    try:
        value = float(raw)
    except ValueError:
        return None, _query_error(f"Invalid {name}")
    if not math.isfinite(value) or value < 0:
        return None, _query_error(f"Invalid {name}")
    return value, None


def _query_int(request: Request, name: str, default: int, *, min_value: int, max_value: int):
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return default, None
    try:
        value = int(raw)
    except ValueError:
        return None, _query_error(f"Invalid {name}")
    if value < min_value or value > max_value:
        return None, _query_error(f"Invalid {name}")
    return value, None


def _compute_etag(body: bytes) -> str:
    """Compute a short ETag hash from a response body."""
    return f'"{_short_hash(body)}"'


def orjson_response(data, *, status_code=200, headers=None):
    return Response(content=orjson.dumps(data), media_type="application/json",
                    status_code=status_code, headers=headers or {})


def error_response(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


def _etag_response(request: Request, data=None, *, body=None, etag=None) -> Response:
    """Return JSON response with ETag, or 304 if client sends matching If-None-Match.

    Simple usage: _etag_response(request, data_dict)
    Cached usage: _etag_response(request, body=bytes, etag=string)
    """
    if body is None:
        body = orjson.dumps(data)
    if etag is None:
        etag = _compute_etag(body)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return Response(content=body, media_type="application/json", headers={"ETag": etag})


# ── Route handler functions ──────────────────────────────────────────────────

def index(request: Request):
    """Serve index.html with placeholder replacement, asset versioning, and CSP nonce injection.

    Replaces __STATIC_PREFIX__/__APP_NAME__/__APP_DESCRIPTION__, appends per-file
    content-hash ?v= params to asset URLs, injects modulepreload tags, and adds the
    FOUC + CSP nonce scripts. Clears all version caches at the start of each request
    so fresh hashes are computed.
    """
    nonce = getattr(request.state, 'csp_nonce', '')
    prefix = st.c.static_url_prefix
    html = (st.FRONTEND_DIR / "index.html").read_text()
    html = _replace_placeholders(html)

    def _version_replacer(m):
        prefix_part = m.group(1)
        file_path = m.group(2)
        v = _file_version(file_path)
        return f"{prefix_part}{prefix}/{file_path}?v={v}\""

    html = _get_prefix_re(r'((?:href|src)="){}/([^"]+)"').sub(_version_replacer, html)

    preload_block = "\n".join(f'<link rel="modulepreload" href="{prefix}/{rel}?v={_file_version(rel)}">'
                              for rel in module_preload_order())
    html = html.replace('<script type="module"', preload_block + "\n<script type=\"module\"", 1)
    # Theme tokens before anything paints; the inline critical CSS in index.html reads them
    html = html.replace('<head>', '<head>' + _early_theme_css(), 1)
    if nonce:
        # FOUC prevention, a blocking script right after the early tokens:
        # 1. Theme: the stored preference (or the system setting) sets .dark before paint, and the
        #    theme-color meta takes the base colour of the result
        # 2. Bell icon: stored notification settings set bell-fouc-* on <html>; the bell colour is a
        #    token, and initPush() removes the class when push init completes
        keys = st.STORAGE_KEYS
        light, dark = st.THEMES
        bell_fouc_css = (
            '<style>'
            'html.bell-fouc-on #notify-btn,html.bell-fouc-active #notify-btn{color:var(--color-tier-accent)}'
            'html.bell-fouc-active #notify-btn #notify-icon-bell{fill:currentColor}'
            '</style>'
        )
        fouc_script = (
            f'<script nonce="{nonce}">'
            '(function(){'
            'var ls=window.localStorage,h=document.documentElement;'
            f'var t=ls.getItem({orjson.dumps(keys["THEME"]).decode()});'
            f'var d=t==={orjson.dumps(dark).decode()}||(t!=={orjson.dumps(light).decode()}'
            '&&window.matchMedia("(prefers-color-scheme:dark)").matches);'
            'h.classList.toggle("dark",d);'
            'var m=document.querySelector(\'meta[name="theme-color"]\');'
            'if(m)m.content=getComputedStyle(h).getPropertyValue("--color-base").trim();'
            f'var ns=JSON.parse(ls.getItem({orjson.dumps(keys["NOTIF_SETTINGS"]).decode()})||"{{}}");'
            'if(ns.enabled){'
            f'var nl=ls.getItem({orjson.dumps(keys["NOTIF_LOCAL"]).decode()})==="1";'
            'var np=typeof Notification!=="undefined"&&Notification.permission==="granted";'
            'h.classList.add((nl||np)?"bell-fouc-active":"bell-fouc-on");'
            '}'
            '})()</script>'
        )
        html = html.replace('</style>', '</style>' + bell_fouc_css + fouc_script, 1)
        html = html.replace(
            '</head>',
            f'<script nonce="{nonce}">window.__MW_BOOT__={orjson.dumps(page_bootstrap()).decode()}</script></head>',
            1,
        )
        html = html.replace('<script type="module"', f'<script type="module" nonce="{nonce}"', 1)
    return html


def service_worker():
    content = _replace_placeholders((st.FRONTEND_DIR / "sw.js").read_text())
    content = content.replace(_CACHE_VER_PLACEHOLDER, _asset_fingerprint())
    return Response(content=content, media_type="application/javascript")


def manifest():
    content = _replace_placeholders((st.FRONTEND_DIR / "manifest.json").read_text())
    # Add content-hash ?v= params to icon URLs so browsers re-fetch when icons change
    def _icon_version(m):
        prefix_part = m.group(1)
        file_path = m.group(2)
        v = _file_version(file_path)
        return f'{prefix_part}{st.c.static_url_prefix}/{file_path}?v={v}"'
    content = _get_prefix_re(r'("src":\s*"){}/([^"]+\.png)"').sub(_icon_version, content)
    return Response(content=content, media_type="application/manifest+json")


async def list_providers(request: Request, providers: str = Query(default=None, max_length=512)):
    prov_set = set(providers.split(",")) if providers else None
    if prov_set is None:
        if st.providers_cache["dirty"] or st.providers_cache["data"] is None:
            cache_version = st.providers_cache.get("version", 0)
            data = await asyncio.to_thread(get_providers_grouped)
            body = orjson.dumps(data)
            st.providers_cache["data"] = data
            st.providers_cache["etag"] = _compute_etag(body)
            st.providers_cache["raw"] = body
            st.providers_cache["dirty"] = st.providers_cache.get("version", 0) != cache_version
        return _etag_response(request, body=st.providers_cache["raw"], etag=st.providers_cache["etag"])
    return orjson_response(await asyncio.to_thread(get_providers_grouped, prov_set))


def deploy_version():
    return orjson_response({"version": _static_version()})


async def get_metrics(request: Request):
    """Handle /api/metrics in collection mode (all models) or single-model mode.

    Collection mode: ETag-cached, rebuilt from model_cache when dirty. Supports
    providers/detail_providers filters and card_buckets=1 for pre-computed chart data.
    Single-model mode (model=): returns chart data (type=card|modal), history
    (type=history), filtered by test_type and view.
    """
    model_key = request.query_params.get("model")
    if model_key is not None:
        err = _validate_model_key(model_key)
        if err:
            return err
        # Single-model mode
        type_, err = _query_choice(request, "type", "card", _METRICS_RESPONSE_TYPES)
        if err:
            return err
        since, err = _query_float(request, "since")
        if err:
            return err
        until, err = _query_float(request, "until")
        if err:
            return err
        if since is not None and until is not None and since > until:
            return _query_error("Invalid time range")
        buckets, err = _query_int(request, "buckets", 20, min_value=1, max_value=st.c.max_chart_buckets)
        if err:
            return err
        test_type, err = _query_choice(request, "test_type", st.TEST_BENCHMARK, _TEST_TYPES)
        if err:
            return err
        view, err = _query_choice(request, "view", "speed", _CHART_VIEWS)
        if err:
            return err
        if type_ == "history":
            before, err = _query_float(request, "before")
            if err:
                return err
            sort = request.query_params.get("sort")
            if sort:
                for part in sort.split(","):
                    key = part.strip().lstrip("-")
                    if key and key not in _VALID_SORT_KEYS:
                        return _query_error("Invalid sort")

            limit, err = _query_int(request, "limit", 50, min_value=1, max_value=st.c.history_query_limit)
            if err:
                return err
            result = await asyncio.to_thread(build_history_response, model_key, before, limit, test_type, since, until, sort)
            if isinstance(result, tuple):
                return JSONResponse(status_code=result[1], content=result[0])
            return orjson_response(result)
        result = await asyncio.to_thread(build_chart_response, model_key, since, buckets, type_, test_type, view, until)
        if isinstance(result, tuple):
            return JSONResponse(status_code=result[1], content=result[0])
        return orjson_response(result)
    # Collection mode
    type_raw = request.query_params.get("type")
    if type_raw is not None:
        if type_raw not in _METRICS_RESPONSE_TYPES:
            return _query_error("Invalid type")
        return _query_error("type parameter requires model parameter")
    providers_str = request.query_params.get("providers")
    providers = [p.strip() for p in providers_str.split(",") if p.strip()] if providers_str else None
    detail_str = request.query_params.get("detail_providers")
    # detail_providers=None means "not specified" (inherit from providers)
    # detail_providers=[] means "explicitly empty" (summaries only, no per-model data)
    detail_providers = [p.strip() for p in detail_str.split(",") if p.strip()] if detail_str is not None else None
    include_card_buckets = request.query_params.get("card_buckets") == "1"
    # Filtered requests: try fast path from cached full response
    if providers is not None or detail_providers is not None:
        cached_data = st.metrics_cache.get("data")
        if cached_data is not None and not st.metrics_cache.get("dirty", True):
            provider_set = set(providers) if providers else None
            model_filter_set = set(detail_providers) if detail_providers is not None else provider_set
            skip_models = detail_providers is not None and not detail_providers
            filtered = {}
            for k, v in cached_data.items():
                if k == "providers":
                    if provider_set:
                        filtered["providers"] = {pn: ps for pn, ps in v.items() if pn in provider_set}
                    else:
                        filtered["providers"] = v
                    continue
                if skip_models:
                    continue
                if model_filter_set:
                    pname = st.parse_model_key(k)[0]
                    if pname not in model_filter_set:
                        continue
                if include_card_buckets:
                    if "card_buckets" not in v:
                        entry = st.model_cache.get(k)
                        cb = cached_card_buckets(entry) if entry else {}
                        filtered[k] = {**v, "card_buckets": cb}
                    else:
                        filtered[k] = v
                else:
                    filtered[k] = {mk: mv for mk, mv in v.items() if mk != "card_buckets"}
            return _etag_response(request, data=filtered)
        filtered_resp = await asyncio.to_thread(build_summary_response, providers, detail_providers=detail_providers, include_card_buckets=include_card_buckets)
        return _etag_response(request, data=filtered_resp)
    if st.metrics_cache["dirty"] or st.metrics_cache["data"] is None:
        async with _metrics_rebuild_lock:
            if st.metrics_cache["dirty"] or st.metrics_cache["data"] is None:
                cache_version = st.metrics_cache.get("version", 0)
                data = await asyncio.to_thread(build_summary_response, include_card_buckets=True)
                body = orjson.dumps(data)
                st.metrics_cache["data"] = data
                st.metrics_cache["etag"] = _compute_etag(body)
                st.metrics_cache["raw"] = body
                stripped = {}
                for k, v in data.items():
                    if k == "providers":
                        stripped[k] = v
                    else:
                        stripped[k] = {mk: mv for mk, mv in v.items() if mk != "card_buckets"}
                stripped_body = orjson.dumps(stripped)
                st.metrics_cache["stripped_raw"] = stripped_body
                st.metrics_cache["stripped_etag"] = _compute_etag(stripped_body)
                st.metrics_cache["dirty"] = st.metrics_cache.get("version", 0) != cache_version
    if not include_card_buckets:
        return _etag_response(request, body=st.metrics_cache["stripped_raw"], etag=st.metrics_cache["stripped_etag"])
    return _etag_response(request, body=st.metrics_cache["raw"], etag=st.metrics_cache["etag"])


def get_config(request: Request):
    """Merged config endpoint - intervals, thresholds, labels, time ranges. ETag-cached until the
    config reloads or the scheduler starts or stops."""
    scheduler = st.scheduler_state()
    if _config_cache["raw"] is None or _config_cache["scheduler"] != scheduler:
        raw = orjson.dumps({
            "app_name": st.c.app_name,
            "benchmark_interval_seconds": st.c.benchmark_interval,
            "health_interval_seconds": st.c.health_interval,
            "health_enabled": st.c.health_enabled,
            "audit_enabled": st.c.audit_enabled,
            "audit_interval_seconds": st.c.audit_interval,
            "audit_suites": {k: {"enabled": v.get("enabled", False), "url": v.get("url")}
                             for k, v in st.c.audit_suites.items()} if st.c.audit_suites else {},
            "probe_enabled": st.c.probe_enabled,
            "probe_interval_seconds": st.c.probe_interval,
            "scheduler": scheduler,
            "degraded_critical_metrics": st.c.degraded_critical_metrics,
            "stalls": {"visible_threshold_ms": st.c.stall_visible_ms, "hiccup_multiplier": st.c.hiccup_multiplier},
            "scores": {"consistency": st.c.scores_consistency_weights, "speed": st.c.scores_speed_weights,
                       "reliability": {"availability_weight": st.c.scores_reliability_avail_weight,
                                       "quality_weight": st.c.scores_reliability_quality_weight}},
            "color_thresholds": st.c.color_thresholds,
            "time_ranges": st.c.time_ranges,
            "ui": ui_config(),
            "status_values": st.STATUS_VALUES,
            "status_labels": st.STATUS_LABELS,
            "test_types": st.TEST_TYPES,
            "test_type_labels": st.TEST_TYPE_LABELS,
            "chart_views": st.CHART_VIEWS,
            "chart_view_labels": st.CHART_VIEW_LABELS,
            "capabilities": st.CAPABILITIES,
            "event_labels": st.EVENT_LABELS,
            "metric_labels": st.METRIC_LABELS,
            "metric_short_labels": st.METRIC_SHORT_LABELS,
        })
        _config_cache["raw"] = raw
        _config_cache["etag"] = _compute_etag(raw)
        _config_cache["scheduler"] = scheduler
    return _etag_response(request, body=_config_cache["raw"], etag=_config_cache["etag"])


def health_check():
    """Return 200 if scheduler is alive and at least one model is online/degraded, else 503.

    Strips server-side details to prevent information leakage.
    """
    alive = st.scheduler_running
    models_ok = st._healthy_model_count
    models_total = len(st.model_registry)
    healthy = alive and models_total > 0 and models_ok > 0
    # Docker polls this every 30s: log when readiness changes, not on every failing poll (F18)
    if st.condition_changed("readiness", not healthy):
        if healthy:
            st.log.info("Health check passing again")
        else:
            st.log.warning("Health check failing: scheduler_running=%s, models_ok=%d, models_total=%d",
                           alive, models_ok, models_total)
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "healthy" if healthy else "degraded"},
    )


async def get_audit(request: Request):
    """GET /api/audit - Audit test results for models.

    Without ?model= returns all models' latest results.
    With ?model= returns latest result + history for that model.
    With ?model=&type=evals&id=N returns individual eval details for a specific result.
    """
    import backend.db_probe as db_probe
    since, err = _query_float(request, "since")
    if err:
        return err
    limit, err = _query_int(request, "limit", 50, min_value=1, max_value=st.c.history_query_limit)
    if err:
        return err
    model_key = request.query_params.get("model")
    if model_key is not None:
        err = _validate_model_key(model_key)
        if err:
            return err
        history = await asyncio.to_thread(db_probe.get_audit_history, model_key, limit, since)
        entry = st.model_cache.get(model_key, {})
        latest = entry.get("last_audit_result")
        return orjson_response({"latest": latest, "history": history})
    latest_all = await asyncio.to_thread(db_probe.get_latest_audit_results)
    return orjson_response(latest_all)


async def get_model_info(request: Request, model: str = Query(default=None, max_length=256),
                        history: int = Query(default=0)):
    """GET /api/model-info - Model capability and metadata.

    No params: lightweight capability summary for all models (ETag-cached).
    ?model=X: full model_info detail for a single model.
    ?model=X&history=1: same + probe history.
    """
    if model:
        err = _validate_model_key(model)
        if err:
            return err
        detail = build_model_info_detail(model)
        if detail is None:
            return error_response("Model not found", 404)
        response_data = {"latest": detail}
        if history:
            import backend.db_probe as db_probe
            probe_history = await asyncio.to_thread(db_probe.get_probe_history, model, 50)
            history = []
            for r in probe_history:
                r = st.strip_internal(r)
                t = r.get("thinking")
                if isinstance(t, bool):
                    r["thinking"] = "enabled" if t else None
                    if r["thinking"] is None:
                        r.pop("thinking", None)
                history.append(r)
            response_data["history"] = history
        return orjson_response(response_data)
    if st.model_info_response_cache["dirty"] or st.model_info_response_cache["data"] is None:
        async with _model_info_rebuild_lock:
            if st.model_info_response_cache["dirty"] or st.model_info_response_cache["data"] is None:
                cache_version = st.model_info_response_cache.get("version", 0)
                data = build_model_info_summary()
                body = orjson.dumps(data)
                st.model_info_response_cache["data"] = data
                st.model_info_response_cache["etag"] = _compute_etag(body)
                st.model_info_response_cache["raw"] = body
                st.model_info_response_cache["dirty"] = st.model_info_response_cache.get("version", 0) != cache_version
    return _etag_response(request, body=st.model_info_response_cache["raw"], etag=st.model_info_response_cache["etag"])

def built_css():
    # Every page load requests it: log when it becomes unreadable and when it is back,
    # not on every request, and without a traceback that adds nothing to the path (F44)
    condition = f"built_css:{st.BUILT_CSS_PATH}"
    try:
        body = st.BUILT_CSS_PATH.read_bytes()
    except OSError as e:
        if st.condition_changed(condition, True):
            st.log.error("Built CSS unreadable at %s (%s) - run `%s` or point MW_BUILT_CSS_PATH at the built file",
                         st.BUILT_CSS_PATH, e.strerror or e, st.BUILT_CSS_BUILD_CMD)
        return error_response("Stylesheet not built", 404)
    if st.condition_changed(condition, False):
        st.log.info("Built CSS readable again at %s", st.BUILT_CSS_PATH)
    return Response(content=body, media_type="text/css")


async def handle_client_error(request: Request, body: ClientErrorBody):
    """POST /api/client-error - Receive client-side error reports.

    Rate limited per IP (notifications.rate_limits.client_error_per_minute). Logs the
    error with context for server-side observability.
    """
    rl = check_rate_limit(_client_error_times, client_ip(request), 60, st.c.notif_rate_limit_client_error)
    if rl:
        return rl
    msg = body.message[:500]
    if not msg.strip():
        return _query_error("Missing message")
    source = body.source[:200]
    line = body.line
    col = body.col
    stack = body.stack[:2000]
    err_type = body.type[:20]
    url = body.url[:200]
    ua = body.ua[:200]
    ip = client_ip(request)
    loc_parts = [source, line, col]
    loc = ":".join(str(p) for p in loc_parts if p is not None) if any(p is not None for p in loc_parts) else ""
    st.log.error("[CLIENT] %s %s%s ip=%s%s url=%s ua=%s",
                err_type or "error", msg[:200],
                f" at {loc}" if loc else "",
                ip,
                f" stack={stack[:300]}" if stack else "",
                url, ua)
    return orjson_response({"ok": True})


def get_notifications(since: str | None = None, client_id: str | None = None):
    """GET /api/notifications - delegate to notifications module (owns history + should_notify)."""
    from backend.notifications import handle_get_notifications
    return handle_get_notifications(since, client_id)
