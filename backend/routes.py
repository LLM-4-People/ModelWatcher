"""API route handlers (non-notification) and static file serving."""

import asyncio
import hashlib
import math
import re
import time

import orjson
from fastapi import Request, Query
from fastapi.responses import JSONResponse
from starlette.responses import Response

import backend.state as st
from backend.stats import build_summary_response, build_chart_response, build_history_response, cached_card_buckets, build_model_info_summary, build_model_info_detail
from backend.schemas import ClientErrorBody
from backend.models import get_providers_grouped


# ── Shared utilities (used by push_routes, notifications, etc.) ────────────

_client_error_times: dict[str, list[float]] = {}
_metrics_rebuild_lock = asyncio.Lock()
_model_info_rebuild_lock = asyncio.Lock()
_config_cache: dict = {"raw": None, "etag": None, "expires": 0.0}


def check_rate_limit(buckets: dict, key: str, window_s: float, max_count: int, label: str = "Rate limited") -> JSONResponse | None:
    """Sliding-window rate limiter. Per-key (e.g. per-IP) or global (key='_g')."""
    now = time.monotonic()
    times = buckets.setdefault(key, [])
    recent = [t for t in times if now - t < window_s]
    times[:] = recent
    if len(recent) >= max_count:
        return error_response(label, 429)
    times.append(now)
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
_static_version_cache: float | None = None
_asset_fingerprint_cache: str | None = None


def _static_version() -> float:
    """Mtime of the most recently modified file in FRONTEND_DIR."""
    global _static_version_cache
    if _static_version_cache is not None:
        return _static_version_cache
    try:
        fp = max(f.stat().st_mtime for f in st.FRONTEND_DIR.rglob("*") if f.is_file())
    except (OSError, ValueError):
        fp = 0.0
    _static_version_cache = fp
    return fp


def _asset_fingerprint() -> str:
    """Content-hash fingerprint for all frontend assets (sw.js cache versioning)."""
    global _asset_fingerprint_cache
    if _asset_fingerprint_cache is not None:
        return _asset_fingerprint_cache
    h = hashlib.sha1()
    for f in sorted(st.FRONTEND_DIR.rglob("*")):
        if not f.is_file():
            continue
        try:
            h.update(f.relative_to(st.FRONTEND_DIR).as_posix().encode())
            h.update(f.read_bytes())
        except OSError:
            pass
    fp = h.hexdigest()[:16]
    _asset_fingerprint_cache = fp
    return fp


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
    f = st.FRONTEND_DIR / path_suffix
    if not f.is_file() and path_suffix == "tailwind.min.css":
        f = st.BUILT_CSS_PATH
    is_js = path_suffix.startswith("js/") and path_suffix.endswith(".js")
    try:
        mt = f.stat().st_mtime
    except (OSError, FileNotFoundError):
        return str(_static_version())
    jmt = _js_max_mtime() if is_js else 0.0
    cached = _file_version_cache.get(path_suffix)
    if cached and cached[0] == mt and cached[2] == jmt:
        return cached[1]
    try:
        h = _short_hash(f.read_bytes())
    except (FileNotFoundError, OSError):
        return str(_static_version())
    if is_js and jmt:
        h = _short_hash((h + str(jmt)).encode())
    _file_version_cache[path_suffix] = (mt, h, jmt)
    return h


def _safe_frontend_file(path_suffix: str, *, required_root=None, suffixes: frozenset[str] | None = None):
    """Resolve a frontend-relative path and reject traversal outside required_root."""
    root = (required_root or st.FRONTEND_DIR).resolve()
    try:
        path = (st.FRONTEND_DIR / path_suffix).resolve()
        path.relative_to(root)
    except (OSError, ValueError):
        return None
    if suffixes and path.suffix.lower() not in suffixes:
        return None
    return path if path.is_file() else None


# ── JS import rewriting (adds ?v= to eS module import specifiers) ───────────

_JS_FROM_RE = re.compile(r"""(from\s+)(['"])(\.[^'"]+\.js)\2""")
_JS_DYNAMIC_RE = re.compile(r"""(import\(\s*)(['"])(\.[^'"]+\.js)\2""")
_js_rewrite_cache: dict[str, tuple[float, float, bytes]] = {}


def _js_max_mtime() -> float:
    """Max mtime of all files in frontend/js/ - any JS change invalidates all rewrites."""
    try:
        return max(f.stat().st_mtime for f in (st.FRONTEND_DIR / "js").rglob("*.js") if f.is_file())
    except (OSError, ValueError):
        return 0.0


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
        try:
            rel = target.relative_to(st.FRONTEND_DIR)
        except ValueError:
            return m.group(0)
        target_suffix = str(rel).replace("\\", "/")
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
    try:
        mt = f.stat().st_mtime
    except OSError:
        mt = 0
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


def _replace_placeholders(text: str) -> str:
    """Replace __STATIC_PREFIX__, __APP_NAME__, __APP_DESCRIPTION__ placeholders with configured values."""
    text = text.replace(_PLACEHOLDER_PREFIX, st.c.static_url_prefix)
    text = text.replace(_PLACEHOLDER_NAME, st.c.app_name)
    text = text.replace(_PLACEHOLDER_DESC, st.c.app_description)
    return text


_CONSOLE_LEVELS = {"debug": 0, "info": 1, "warning": 2, "error": 3}
_METRICS_RESPONSE_TYPES = frozenset({"card", "modal", "history"})
_TEST_TYPES = frozenset((st.TEST_BENCHMARK, st.TEST_HEALTH))
_CHART_VIEWS = frozenset({"speed", "consistency", "scores", "health"})
_MAX_BUCKETS = 500
_MAX_HISTORY_LIMIT = 5000
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
    _file_version_cache.clear()
    _js_rewrite_cache.clear()
    global _static_version_cache, _asset_fingerprint_cache
    _static_version_cache = None
    _asset_fingerprint_cache = None
    html = (st.FRONTEND_DIR / "index.html").read_text()
    html = _replace_placeholders(html)

    def _version_replacer(m):
        prefix_part = m.group(1)
        file_path = m.group(2)
        v = _file_version(file_path)
        return f"{prefix_part}{prefix}/{file_path}?v={v}\""

    html = _get_prefix_re(r'((?:href|src)="){}/([^"]+)"').sub(_version_replacer, html)

    _MODULE_PRELOAD_ORDER = [
        "state", "utils",
        "format", "api",
        "tooltips", "cache",
        "chart", "dom",
        "modal-loader",
        "prefs", "notifications",
        "help", "ws",
        "frame", "theme",
        "app",
    ]
    preload_tags = []
    for m in _MODULE_PRELOAD_ORDER:
        v = _file_version(f"js/{m}.js")
        preload_tags.append(f'<link rel="modulepreload" href="{prefix}/js/{m}.js?v={v}">')
    preload_block = "\n".join(preload_tags)
    html = html.replace('<script type="module"', preload_block + "\n<script type=\"module\"", 1)
    if nonce:
        # FOUC prevention - inject blocking script + style immediately after <head>
        # 1. Theme: reads localStorage, sets .dark before paint
        # 2. Bell icon: reads localStorage notif settings, sets bell-fouc-* class on <html>
        #    CSS uses hardcoded colors (not CSS vars) because the stylesheet may not be loaded yet
        #    initPush() removes the FOUC class when push init completes
        bell_fouc_css = (
            '<style>'
            'html.bell-fouc-on #notify-btn,html.bell-fouc-active #notify-btn{color:#2563eb}'
            'html.dark.bell-fouc-on #notify-btn,html.dark.bell-fouc-active #notify-btn{color:#60a5fa}'
            'html.bell-fouc-active #notify-btn #notify-icon-bell{fill:currentColor}'
            '</style>'
        )
        fouc_script = (
            f'<script nonce="{nonce}">'
            '(function(){'
            'var t=localStorage.getItem("mw_theme");'
            'var d=t==="dark"||(t!=="light"&&window.matchMedia("(prefers-color-scheme:dark)").matches);'
            'document.documentElement.classList.toggle("dark",d);'
            'var m=document.querySelector(\'meta[name="theme-color"]\');'
            'if(m)m.content=d?"#0c1220":"#f8fafc";'
            'var ns=JSON.parse(localStorage.getItem("mw_notif_settings")||"{}");'
            'if(ns.enabled){'
            'var nl=localStorage.getItem("mw_notif_local")==="1";'
            'var np=typeof Notification!=="undefined"&&Notification.permission==="granted";'
            'document.documentElement.classList.add((nl||np)?"bell-fouc-active":"bell-fouc-on");'
            '}'
            'try{localStorage.removeItem("mw_dh");localStorage.removeItem("mw_pf")}catch(e){}'
            '})()</script>'
        )
        html = html.replace('<head>', '<head>' + bell_fouc_css + fouc_script, 1)
        _console_level = _CONSOLE_LEVELS.get(st.c.log_level, 2)
        html = html.replace(
            '</head>',
            f'<script nonce="{nonce}">window.__STATIC_PREFIX__="{prefix}";window.__APP_NAME__={orjson.dumps(st.c.app_name).decode()};window.__LOG_LEVEL__={_console_level}</script></head>',
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
        buckets, err = _query_int(request, "buckets", 20, min_value=1, max_value=_MAX_BUCKETS)
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

            limit, err = _query_int(request, "limit", 50, min_value=1, max_value=_MAX_HISTORY_LIMIT)
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
                    pname = k.split("::")[0]
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
    """Merged config endpoint - intervals, thresholds, labels, time ranges. ETag-cached (10s TTL)."""
    now = time.monotonic()
    if _config_cache["raw"] is None or now >= _config_cache["expires"]:
        last_ago = round(now - st.last_run_time) if st.last_run_time else None
        next_in = round(st.next_run_time - now) if st.next_run_time else None
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
            "last_run_ago_seconds": last_ago,
            "next_run_in_seconds": max(next_in, 0) if next_in is not None else None,
            "color_thresholds": st.c.color_thresholds,
            "time_ranges": st.c.time_ranges,
            "status_values": st.STATUS_VALUES,
            "test_types": st.TEST_TYPES,
            "chart_views": st.CHART_VIEWS,
            "event_labels": st.EVENT_LABELS,
            "metric_labels": st.METRIC_LABELS,
        })
        _config_cache["raw"] = raw
        _config_cache["etag"] = _compute_etag(raw)
        _config_cache["expires"] = now + 10
    return _etag_response(request, body=_config_cache["raw"], etag=_config_cache["etag"])


def health_check():
    """Return 200 if scheduler is alive and at least one model is online/degraded, else 503.

    Strips server-side details to prevent information leakage.
    """
    alive = st.scheduler_running
    models_ok = st._healthy_model_count
    models_total = len(st.model_registry)
    healthy = alive and models_total > 0 and models_ok > 0
    if not healthy:
        st.log.warning("Health check failing: scheduler_running=%s, models_ok=%d, models_total=%d", alive, models_ok, models_total)
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
    import backend.db as db; import backend.db_probe as db_probe
    since, err = _query_float(request, "since")
    if err:
        return err
    limit, err = _query_int(request, "limit", 50, min_value=1, max_value=_MAX_HISTORY_LIMIT)
    if err:
        return err
    model_key = request.query_params.get("model")
    if model_key is not None:
        err = _validate_model_key(model_key)
        if err:
            return err
        history = await asyncio.to_thread(db_probe.get_audit_history, model_key, limit, since)
        # Read latest from SQLite (full data with suites), not model_cache (lightweight, suites stripped)
        latest = history[-1] if history else None
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
    return Response(content=st.BUILT_CSS_PATH.read_bytes(), media_type="text/css")


async def handle_client_error(request: Request, body: ClientErrorBody):
    """POST /api/client-error - Receive client-side error reports.

    Rate limited per-IP (10/min). Logs the error with context for server-side
    observability.
    """
    rl = check_rate_limit(_client_error_times, client_ip(request), 60, c.notif_rate_limit_client_error)
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
