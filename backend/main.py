"""FastAPI application factory, lifespan, middleware wiring, and route registration."""

import asyncio
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware
from starlette.staticfiles import StaticFiles

import orjson

import backend.state as st
import backend.db as db
import backend.db_push
import backend.db_probe as db_probe
from backend.config import reload_config, config_watcher, apply_db_changes
from backend.metrics import make_cache_entry
from backend.stats import compute_trends, compute_reliability_score, bench_only
from backend.websocket import ws_mgr, websocket_endpoint
from backend.middleware import ConnectionLimiterMiddleware, SecurityHeadersMiddleware, RequestSizeLimitMiddleware
from backend.scheduler import scheduler
from backend.state import TEST_BENCHMARK
from backend import routes
from backend import notifications
from backend import push_routes
from backend import favicons
from backend.schemas import (
    PushSubscribeBody, PushUnsubscribeBody, PushUpdatePrefsBody, PushTestBody, ClientErrorBody,
)


# Load config at import time so st.c is populated before module-level
# code that references st.c.app_name, st.c.static_url_prefix, etc.
# If this fails, the app cannot start - fail fast with a clear error.
try:
    reload_config()
except Exception as _config_err:
    raise SystemExit(f"FATAL: config load failed at import time: {_config_err}") from _config_err


def _async_exception_handler(loop, context):
    msg = context.get("message", "Unhandled exception in async task")
    exc = context.get("exception")
    st.log_error(msg, exc)


async def _startup():
    """Initialize DB, populate caches, start background tasks.

    Steps: config reload, DB init/sync, model_cache population from SQLite,
    auto-archive, trend computation, WriteBatcher start, VAPID init, push sub
    cleanup, and scheduler/config-watcher/favicon/model-info task startup.
    Skips test-related tasks when MW_DISABLE_TESTS is set.
    """
    asyncio.get_running_loop().set_exception_handler(_async_exception_handler)

    result = reload_config()

    await asyncio.to_thread(db.init)
    await apply_db_changes(result)
    await asyncio.to_thread(db.reconcile_model_state)

    # Save reset_epoch keys - applied after model_cache is populated
    pending_reset_keys = result.get("reset_keys", set())

    (last_results, last_benchmark_results, last_successful_benchmarks,
     last_health_results, last_successful_health, latest_audit_results,
     latest_probe_results, recent_history, model_states) = await asyncio.gather(
        asyncio.to_thread(db.load_all_last_results),
        asyncio.to_thread(db.load_all_last_benchmark_results),
        asyncio.to_thread(db.load_all_last_successful_benchmarks),
        asyncio.to_thread(db.load_all_last_health_results),
        asyncio.to_thread(db.load_all_last_successful_health_results),
        asyncio.to_thread(db_probe.get_latest_audit_results),
        asyncio.to_thread(db_probe.get_latest_probe_results),
        asyncio.to_thread(db.load_all_recent_history),
        asyncio.to_thread(db.get_all_model_states),
    )

    for entry in st.model_registry:
        mk = entry["id"]
        lr = last_results.get(mk)
        lbr = last_benchmark_results.get(mk)
        lsb = last_successful_benchmarks.get(mk)
        lhr = last_health_results.get(mk)
        lsh = last_successful_health.get(mk)
        lar = latest_audit_results.get(mk)
        lpr = latest_probe_results.get(mk)
        rh = recent_history.get(mk, [])
        ms = model_states.get(mk, {})
        if lr:
            status = ms.get("status", "unknown")
            uptime_pct = ms.get("uptime_pct")
        else:
            status = "unknown"
            uptime_pct = None
        last_benchmark_epoch = lbr.get("ts_epoch") if lbr else None
        cache_entry = make_cache_entry(
            status=status,
            degraded_source=ms.get("degraded_source"),
            uptime_pct=uptime_pct,
            last_test=lbr if lbr else None,
            last_success_test=lsb if lsb else None,
            last_success_epoch=lsb.get("ts_epoch") if lsb else None,
            last_benchmark_epoch=last_benchmark_epoch,
            last_health_epoch=lhr.get("ts_epoch") if lhr else None,
            last_health_success=bool(lhr.get("available", False) and lhr.get("success", False)) if lhr else None,
            last_health_error=lhr.get("error") if lhr and not (lhr.get("available", False) and lhr.get("success", False)) else None,
            last_health_ttft_ms=lhr.get("ttft_ms") if lhr else None,
            last_health_request_id=lhr.get("request_id") if lhr else None,
            last_health_success_epoch=lsh.get("ts_epoch") if lsh else None,
            last_audit_epoch=lar.get("ts_epoch") if lar else None,
            last_audit_result=lar if lar else None,
            last_probe_epoch=lpr.get("ts_epoch") if lpr else None,
            last_probe_result=lpr if lpr else None,
            first_ts_epoch=ms.get("first_ts_epoch"),
            total_tests=ms.get("total_tests", 0),
            total_success=ms.get("total_success", 0),
            recent_history=rh,
            reliability_score=ms.get("reliability_score"),
            _scores_version=len(rh),
        )
        trends_json = ms.get("trends_json")
        if trends_json:
            try:
                cache_entry["trends"] = orjson.loads(trends_json)
            except Exception as e:
                st.log_error(f"Corrupt trends_json in model_state for {mk}", e)
        if lr and lr.get("retry_attempt") and lr.get("retry_total"):
            ra = lr["retry_attempt"]
            rt = lr["retry_total"]
            ts = lr.get("ts_epoch") or 0
            test_type = lr.get("test_type", TEST_BENCHMARK)
            interval, _ = st.test_type_schedule(test_type)
            if ra < rt and (time.time() - ts) <= interval:
                cache_entry["_pending_retry"] = {"attempt": ra, "total": rt, "test_type": test_type}
                st.log.debug("%s: pending retry detected (attempt %d/%d, %.0fs ago) - will resume", mk, ra, rt, time.time() - ts)
        st.model_cache[mk] = cache_entry

    for mk, entry in st.model_cache.items():
        if entry.get("status") in ("error", "degraded"):
            ts = None
            lt = entry.get("last_test")
            if lt:
                ts = lt.get("ts_epoch")
            if not ts:
                he = entry.get("last_health_epoch")
                if he:
                    ts = he
            if ts:
                entry["degraded_since"] = ts

    from backend.scheduler import apply_auto_archive
    await apply_auto_archive()

    st.update_healthy_model_count()

    if pending_reset_keys:
        from backend.config import apply_reset_epochs
        apply_reset_epochs(pending_reset_keys)

    trends_to_persist = {}
    reliability_to_persist = {}
    for mk, entry in st.model_cache.items():
        rh = entry.get("recent_history", [])
        bench_rh = bench_only(rh)
        if bench_rh:
            entry["trends"] = compute_trends(bench_rh)
        trends = entry.get("trends")
        if trends:
            trends_to_persist[mk] = orjson.dumps(trends).decode()
        rs = compute_reliability_score(mk)
        if rs is not None:
            entry["reliability_score"] = rs
            reliability_to_persist[mk] = rs

    if trends_to_persist:
        await asyncio.to_thread(db.persist_trends, trends_to_persist, reliability_to_persist)

    st.invalidate_metrics_cache()

    db.write_batcher = db.WriteBatcher(flush_interval=st.c.write_batch_interval, max_buffer=st.c.write_batch_max_buffer)
    db.write_batcher.start()
    st.log.info("WriteBatcher started (interval=%.1fs, max_buffer=%d)", st.c.write_batch_interval, st.c.write_batch_max_buffer)

    all_model_info = await asyncio.to_thread(db.load_all_model_info)
    st.model_info_cache.update(all_model_info)
    st.log.info("Loaded model info for %d models", len(all_model_info))

    push_routes.init_vapid()

    await asyncio.to_thread(push_routes.cleanup_invalid_push_subs)

    push_subs = await asyncio.to_thread(backend.db_push.all_push_subs)
    if push_subs:
        for ps in push_subs:
            st.log.debug("Push sub: hash=%s prefs=%s created=%s", push_routes._sub_hash(ps.get("endpoint", "")), ps.get("prefs", {}), ps.get("created_at"))

    if not st.c.allowed_ws_origins:
        st.log.warning("allowed_ws_origins is empty - all WebSocket origins accepted")

    _disable_tests = bool(os.environ.get("MW_DISABLE_TESTS"))
    if _disable_tests:
        st.log.info("MW_DISABLE_TESTS set - scheduler, favicons, ping disabled")
    else:
        st._scheduler_task = st.create_task(scheduler(), name="scheduler")
        from backend.scheduler import BroadcastBatcher
        import backend.scheduler as _sched
        _sched.broadcast_batcher = BroadcastBatcher(flush_interval=st.c.write_batch_interval)
        _sched.broadcast_batcher.start()
        st.log.info("BroadcastBatcher started (interval=%.1fs)", st.c.write_batch_interval)
        if st.awatch:
            st._config_watcher_task = st.create_task(config_watcher(), name="config_watcher")
        favicons.start_favicon_fetch()
        from backend import model_info as _mi
        _mi.start_model_info_fetch()

    st.log.info("Startup complete - %d models registered", len(st.model_registry))


async def _shutdown():
    """Graceful shutdown: notify WS clients, cancel tasks, close DB and HTTP client."""
    st._shutting_down = True
    try:
        await ws_mgr.shutdown_notify("restart")
        await asyncio.sleep(0.1)
    except Exception as e:
        st.log_error("WS shutdown notify failed", e)
    try:
        await ws_mgr.close_all(code=1001, reason="server restarting")
    except Exception as e:
        st.log_error("WS close_all failed", e)

    for task in (st._scheduler_task, st._config_watcher_task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    for task in list(st._background_tasks):
        if not task.done():
            task.cancel()
    if st._background_tasks:
        await asyncio.sleep(0.2)
        for task in list(st._background_tasks):
            if not task.done():
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    st.log_error("Background task cleanup error", e)

    if st._http_client and not st._http_client.is_closed:
        await st._http_client.aclose()
    if db.write_batcher:
        await db.write_batcher.stop()
        db.write_batcher = None
    import backend.scheduler as _sched2
    if _sched2.broadcast_batcher:
        await _sched2.broadcast_batcher.stop()
        _sched2.broadcast_batcher = None
    await asyncio.to_thread(db.close)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _startup()
    yield
    await _shutdown()


_TAGS = [
    {"name": "Metrics", "description": "Model metrics, chart data, and history queries."},
    {"name": "Providers", "description": "Provider and model registry listings."},
    {"name": "Config", "description": "Runtime configuration and deployment version."},
    {"name": "Audit", "description": "SynBad-based audit test results."},
    {"name": "Model Info", "description": "Model capability and metadata (vision, tools, pricing, etc.)."},
    {"name": "Notifications", "description": "In-app notification history and push subscription management."},
    {"name": "Health", "description": "Service health check."},
]

app = FastAPI(
    title=st.c.app_name,
    lifespan=lifespan,
    default_response_class=JSONResponse,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    openapi_tags=_TAGS,
)


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    st.log_error("Unhandled exception", exc)
    return JSONResponse(status_code=500, content={"error": "Internal Server Error"})


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    if errors:
        first = errors[0]
        loc = ".".join(str(p) for p in first.get("loc", []) if p not in ("query", "body", "path"))
        msg = first.get("msg", "Validation error")
        detail = f"Invalid {loc}: {msg}" if loc else msg
    else:
        detail = "Validation error"
    return JSONResponse(status_code=422, content={"error": detail})


# ── Middleware (outer → inner) ────────────────────────────────────────────────

app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(ConnectionLimiterMiddleware)


# ── Frontend assets (excluded from OpenAPI schema) ──────────────────────────

@app.get(f"{st.c.static_url_prefix}/tailwind.min.css", include_in_schema=False)
def _built_css():
    return routes.built_css()


@app.get(f"{st.c.static_url_prefix}/manifest.json", include_in_schema=False)
def _manifest():
    return routes.manifest()


@app.get(f"{st.c.static_url_prefix}/js/{{filepath:path}}", include_in_schema=False)
def _serve_js_file(request: Request, filepath: str):
    return routes.serve_js(request, f"js/{filepath}")


app.mount(st.c.static_url_prefix, StaticFiles(directory=str(st.FRONTEND_DIR)), name="frontend")


# ── HTML & service worker (excluded from OpenAPI schema) ────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def _index(request: Request):
    return routes.index(request)


@app.get("/sw.js", include_in_schema=False)
def _service_worker():
    return routes.service_worker()


# ── Core API routes ──────────────────────────────────────────────────────────

@app.get("/api/providers", tags=["Providers"], summary="List providers and models",
         description="Returns all providers with their models. Optional `providers` filter to limit results to specific providers. ETag-cached when unfiltered.")
async def _list_providers(request: Request, providers: str = Query(default=None, max_length=512, description="Comma-separated provider name filter")):
    return await routes.list_providers(request, providers)


@app.get("/api/config", tags=["Config"], summary="Get runtime configuration",
         description="Returns color thresholds, time ranges, intervals, labels, and chart view definitions.")
def _get_config(request: Request):
    return routes.get_config(request)


@app.get("/api/deploy-version", tags=["Config"], summary="Get deployment version",
         description="Returns a content-hash version string that changes on each deploy. Polled by the frontend to detect updates.")
def _deploy_version():
    return routes.deploy_version()


@app.post("/api/client-error", tags=["Config"], summary="Report a client-side error",
         description="Receives error reports from the browser's `window.onerror` and `unhandledrejection` handlers. "
                     "Rate-limited per IP.",
         responses={429: {"description": "Rate limited"}})
async def _client_error(request: Request, body: ClientErrorBody):
    return await routes.handle_client_error(request, body)


@app.get("/api/metrics", tags=["Metrics"], summary="Get model metrics and chart data",
         description="**Collection mode** (no `model` param): returns all models' status, scores, trends, and optional card chart buckets. "
                     "ETag-cached. **Single-model mode** (with `model`): returns chart data (`type=card|modal`), "
                     "history table (`type=history`), or individual model detail. "
                     "Filter by test type (`benchmark` or `health`) and chart view (`speed`, `consistency`, `scores`, `health`).",
         responses={
             400: {"description": "Invalid parameter (bad type, bad time range, type without model)"},
         })
async def _get_metrics(request: Request,
                      model: str = Query(default=None, max_length=256, description="Model key (`Provider::model_id`). Triggers single-model mode."),
                      type: str = Query(default=None, description="Response type (requires `model`).", json_schema_extra={"enum": ["card", "modal", "history"]}),
                      since: float = Query(default=None, description="Unix epoch (float) - only results at or after this timestamp."),
                      until: float = Query(default=None, description="Unix epoch (float) - only results before this timestamp."),
                      before: float = Query(default=None, description="Unix epoch (float) - history pagination cursor (return rows before this timestamp)."),
                      buckets: int = Query(default=20, ge=1, le=500, description="Number of chart buckets for `card`/`modal` types (single-model mode)."),
                      test_type: str = Query(default="benchmark", description="Filter chart data by test type.", json_schema_extra={"enum": ["benchmark", "health"]}),
                      view: str = Query(default="speed", description="Chart view for `card`/`modal` types.", json_schema_extra={"enum": ["speed", "consistency", "scores", "health"]}),
                      providers: str = Query(default=None, max_length=512, description="Comma-separated provider names to filter collection-mode results."),
                      detail_providers: str = Query(default=None, description="Comma-separated provider names for per-model detail filtering (collection mode). Use empty value for summaries only."),
                      card_buckets: str = Query(default=None, description="Set to `1` to include pre-computed card chart buckets in collection-mode response.", json_schema_extra={"enum": ["1"]}),
                      limit: int = Query(default=50, ge=1, le=5000, description="Maximum history rows to return (`type=history` only)."),
                      sort: str = Query(default=None, description="Comma-separated sort keys for `type=history`. Valid keys: `time`, `ttft`, `tps`, `stalls`, `p99`, `batch`, `tail`. Prefix with `-` for descending (e.g. `-tps`).")):
    return await routes.get_metrics(request)


@app.get("/health", tags=["Health"], summary="Service health check",
         description="Returns `200 {\"status\":\"healthy\"}` if the scheduler is alive and at least one model is online or degraded. "
                     "Returns `503` otherwise. Server-side details are omitted to prevent information leakage.",
         responses={503: {"description": "Service degraded or unhealthy"}})
def _health_check():
    return routes.health_check()


@app.get("/api/audit", tags=["Audit"], summary="Get audit test results",
         description="Without `model`: returns all models' latest audit results. "
                     "With `model`: returns latest result plus history for that model.",
         responses={400: {"description": "Invalid model key"}})
async def _get_audit(request: Request,
                     model: str = Query(default=None, max_length=256, description="Model key (`Provider::model_id`). Triggers single-model mode with history."),
                     limit: int = Query(default=50, ge=1, le=5000, description="Maximum history rows to return (single-model mode)."),
                     since: float = Query(default=None, description="Unix epoch (float) - only return results at or after this timestamp.")):
    return await routes.get_audit(request)


@app.get("/api/model-info", tags=["Model Info"], summary="Get model capability and metadata",
         description="No params: lightweight capability summary for all models (ETag-cached). "
                     "`?model=X`: full model_info detail for a single model. "
                     "`?model=X&history=1`: same + probe history.",
         responses={
             400: {"description": "Invalid model key"},
             404: {"description": "Model not found"},
         })
async def _get_model_info(request: Request, model: str = Query(default=None, max_length=256, description="Model key (`Provider::model_id`). Triggers single-model detail mode."),
                          history: int = Query(default=0, ge=0, le=1, description="Set to `1` to include probe history (single-model mode).")):
    return await routes.get_model_info(request, model, history)


# ── Notification / push routes ───────────────────────────────────────────────

@app.get("/api/vapid-key", tags=["Notifications"], summary="Get VAPID public key",
         description="Returns the VAPID public key for web push subscriptions.",
         responses={503: {"description": "Push not available"}})
def _vapid_key():
    return push_routes.handle_vapid_key()


@app.post("/api/push/subscribe", tags=["Notifications"], summary="Subscribe to web push",
         description="Subscribe to web push notifications. Rate-limited per IP. "
                     "Returns 409 if endpoint already registered to a different client.",
         responses={
             400: {"description": "Invalid endpoint, keys, or client_id"},
             409: {"description": "Endpoint already registered to another client"},
             429: {"description": "Rate limited"},
             503: {"description": "Push not available"},
         })
async def _push_subscribe(request: Request, body: PushSubscribeBody):
    return await push_routes.handle_push_subscribe(request, body)


@app.delete("/api/push/subscribe", tags=["Notifications"], summary="Unsubscribe from web push",
         description="Unsubscribe. If `client_id` is provided, deletes ALL subscriptions for that client. "
                     "Otherwise deletes the single `endpoint`.",
         responses={400: {"description": "Missing endpoint and client_id"}})
async def _push_unsubscribe(request: Request, body: PushUnsubscribeBody):
    return await push_routes.handle_push_unsubscribe(request, body)


@app.put("/api/push/preferences", tags=["Notifications"], summary="Update notification preferences",
         description="Update notification prefs for a push subscriber. If `client_id` is provided, "
                     "updates ALL subscriptions for that client (sync across tabs/devices). Rate-limited per IP.",
         responses={
             400: {"description": "Invalid prefs or missing endpoint/client_id"},
             404: {"description": "Unknown subscription"},
             429: {"description": "Rate limited"},
         })
async def _push_update_prefs(request: Request, body: PushUpdatePrefsBody):
    return await push_routes.handle_push_update_prefs(request, body)


@app.post("/api/push/test", tags=["Notifications"], summary="Send a test push notification",
         description="Sends a test push to verify the subscription works. Rate-limited globally.",
         responses={
             400: {"description": "No matching push subscription"},
             429: {"description": "Rate limited"},
             502: {"description": "Push delivery failed"},
             503: {"description": "Push not available"},
         })
async def _push_test(request: Request, body: PushTestBody):
    return await push_routes.handle_push_test(request, body)


@app.get("/api/push/validate", tags=["Notifications"], summary="Validate push endpoint registration",
         description="Check whether a push endpoint is still registered for a specific client. "
                     "Requires both `client_id` and `endpoint`. Returns `{valid: false}` without `client_id` to block enumeration.",
         responses={429: {"description": "Rate limited"}})
async def _push_validate(request: Request,
                         endpoint: str = Query(default="", max_length=2048, description="Push endpoint URL (from the browser's PushSubscription)."),
                         client_id: str = Query(default="", max_length=64, description="Client identifier (generated by the frontend, stored in localStorage).")):
    return await push_routes.handle_push_validate(request, endpoint, client_id)


@app.get("/api/notifications", tags=["Notifications"], summary="Get notification config and history",
         description="Returns notification config (`app_name`, `enabled`, `in_app`) and in-app notification history. "
                     "History is scoped by `client_id` (only notifications after subscription time) and `since` timestamp.",
         responses={400: {"description": "Invalid client_id or since parameter"}})
def _get_notifications(since: str = Query(default=None, description="ISO 8601 datetime string - only return notifications after this timestamp."),
                      client_id: str = Query(default=None, max_length=64, description="Client identifier. Scopes history to notifications after the client's subscription `created_at`.")):
    return routes.get_notifications(since, client_id)


# ── WebSocket endpoint ───────────────────────────────────────────────────────

app.websocket("/ws")(websocket_endpoint)


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        st.log.info("uvloop not available, using default event loop")
    uvicorn.run(
        "backend.main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        reload=st.c.debug,
        log_level="warning",
        ws_ping_interval=30,
        ws_ping_timeout=90,
    )
