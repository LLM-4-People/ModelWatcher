"""Shared mutable state, paths, and core constants.

Root of the dependency tree - no imports from other backend modules.
All domain modules import from here.
"""

import asyncio
import logging
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx

if sys.platform == "win32":
    import mimetypes
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("application/javascript", ".mjs")

import os as _os
if _os.environ.get("TZ"):
    import time as _time
    _time.tzset()

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("modelwatcher")

# Suppress framework loggers at import time (before uvicorn starts).
# uvicorn.error at ERROR suppresses WatchFiles reload messages (WARNING level).
for _fw_name, _fw_level in [("uvicorn", logging.WARNING), ("uvicorn.error", logging.ERROR),
                             ("uvicorn.access", logging.WARNING), ("httpx", logging.WARNING), ("httpcore", logging.WARNING)]:
    logging.getLogger(_fw_name).setLevel(_fw_level)


def log_error(msg: str, exc: BaseException | None = None):
    """Log an error with optional exception. Use in every except block instead of bare pass.

    When exc is None, automatically captures the current exception via sys.exc_info(),
    so callers inside except blocks don't need to pass exc explicitly.
    """
    if exc is None:
        exc = sys.exc_info()[1]
    if exc is not None:
        log.error("%s", msg, exc_info=exc)
    else:
        log.error("%s", msg)


_LOG_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}

def apply_log_level(level: str):
    """Apply the configured log level to the modelwatcher logger."""
    log.setLevel(_LOG_LEVELS.get(level, logging.WARNING))


# ── Paths ────────────────────────────────────────────────────────────────────

# backend/ → project root (where config/, data/, frontend/ live)
_BASE_DIR = Path(__file__).resolve().parent.parent

CONFIG_DIR = _BASE_DIR / "config"
DATA_DIR = _BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

VAPID_KEY_FILE = DATA_DIR / "vapid_private.pem"
VAPID_PUB_FILE = DATA_DIR / "vapid_public.txt"
FRONTEND_DIR = _BASE_DIR / "frontend"
BUILT_CSS_PATH = Path(_os.environ.get("MW_BUILT_CSS_PATH", "/opt/frontend/tailwind.min.css"))

# ── Runtime config namespace ──────────────────────────────────────────────────
# Populated by config.reload_config() - no defaults here; app.yaml is the sole source of truth.

c = SimpleNamespace()


# ── Raw config dicts (populated by config.reload_config) ─────────────────────

app_cfg: dict = {}
models_cfg: dict = {}


# ── Model registry (populated by config.reload_config) ───────────────────────

model_registry: list = []

# Set of model keys marked archived - persisted in SQLite (model_state.archived)
# and loaded into memory by config.apply_db_changes on startup and config reload.
# Three sources of archival:
# 1. Manual archive:  `archived: true` in models.yaml (force-archive via DB)
# 2. Manual unarchive: `archived: false` in models.yaml (force-unarchive via DB)
# 3. Auto:   models offline for >= auto_archive.offline_duration (added by
#            scheduler.apply_auto_archive, persisted to DB)
# Archived models stay in the registry (preserving historical data) but are skipped
# by the scheduler and excluded from provider summary counts.
# Disabling auto-archive stops NEW auto-archiving but does NOT unarchive existing
# auto-archived models - use `archived: false` in models.yaml to explicitly unarchive.
# Per-model opt-out: `auto_archive: false` in models.yaml exempts from auto-archiving.
_archived_model_keys: set = set()


# ── Provider RTT history (for jitter calculation) ──────────────────────────────
#
# Tracks the last N TCP+TLS RTT samples per provider, captured from httpx
# trace events on every streaming test (both health checks and benchmarks).
# This gives ~100+ RTT samples per hour per provider with health checks enabled,
# measured on the EXACT network path used for streaming (not a separate probe).
# Jitter (std dev of RTTs) helps users distinguish network-induced metric distortion
# from actual server-side inference performance issues.

_PROVIDER_RTT_MAXLEN = 50
provider_rtt_history: dict[str, deque] = {}


def update_provider_rtt(provider: str, rtt_ms: float | None):
    """Record a new RTT sample for a provider from httpx trace events."""
    if rtt_ms is None or rtt_ms <= 0:
        return
    if provider not in provider_rtt_history:
        provider_rtt_history[provider] = deque(maxlen=_PROVIDER_RTT_MAXLEN)
    provider_rtt_history[provider].append(rtt_ms)


def get_provider_jitter(provider: str) -> float | None:
    """Compute jitter (robust standard deviation) of recent RTTs for a provider.

    Uses MAD-based (Median Absolute Deviation) estimation instead of population
    stdev to resist occasional TCP+TLS setup outliers that would inflate the
    jitter estimate. MAD * 1.4826 gives a robust stdev consistent with the
    normal distribution.

    Returns None if fewer than 2 samples are available (insufficient data).
    """
    samples = list(provider_rtt_history.get(provider, []))
    if len(samples) < 2:
        return None

    def _median(vals: list[float]) -> float:
        s = sorted(vals)
        mid = len(s) // 2
        return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2

    med = _median(samples)
    mad = _median([abs(s - med) for s in samples])
    return round(mad * 1.4826, 1)


# ── Model cache (in-memory, populated from SQLite at startup) ────────────────
# _pending_retry: {"attempt": int, "total": int, "test_type": str} - set at startup when the last
#   result is a retry record with remaining attempts; consumed by run_test().
#   Cleared when the test completes or if too old (> interval).

model_cache: dict[str, dict] = {}


# ── Model info cache (in-memory, populated from SQLite at startup) ────────────
# Source: model_info SQLite table + API fetches. YAML values take precedence.

model_info_cache: dict[str, dict] = {}


# ── HTTP client singleton ───────────────────────────────────────────────────

_http_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """Get or create the shared httpx client.

    Uses HTTP/1.1 (not HTTP/2) to guarantee fresh TCP+TLS connections per request.
    This is required for accurate network RTT measurement via trace events -
    HTTP/2 multiplexes over existing connections, skipping TCP connect/TLS handshake
    events and producing None RTT values.
    max_keepalive_connections=0 ensures connections are closed after each request.
    """
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=float(c.http_connect_timeout)),
            limits=httpx.Limits(max_connections=c.http_pool_max, max_keepalive_connections=0),
        )
    return _http_client


def reset_http_client():
    """Mark the HTTP client for recreation on next use (e.g. after config reload)."""
    global _http_client
    _http_client = None


# ── Tracked background tasks ─────────────────────────────────────────────────

_background_tasks: set[asyncio.Task] = set()


def create_task(coro, *, name: str | None = None) -> asyncio.Task:
    """Create a tracked background task with automatic error logging and cleanup.

    Every fire-and-forget task should use this instead of asyncio.create_task()
    to guarantee unhandled exceptions are logged even if the task is never awaited.
    """
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    task.add_done_callback(_task_done)
    return task


def _task_done(task: asyncio.Task):
    _background_tasks.discard(task)
    if task.cancelled():
        return
    if exc := task.exception():
        log_error(f"Background task failed: {task.get_name()}", exc)


# ── Scheduler state ──────────────────────────────────────────────────────────

scheduler_running: bool = False
last_run_time: float | None = None
next_run_time: float | None = None
config_changed: asyncio.Event | None = None
_wake_event: asyncio.Event | None = None

_scheduler_task = None
_config_watcher_task = None
_shutting_down: bool = False


# ── Metrics cache (ETag-based) ───────────────────────────────────────────────

metrics_cache: dict = {"data": None, "etag": None, "raw": None, "dirty": True, "version": 0}

providers_cache: dict = {"data": None, "etag": None, "raw": None, "dirty": True, "version": 0}

model_info_response_cache: dict = {"data": None, "etag": None, "raw": None, "dirty": True, "version": 0}


def invalidate_providers_cache():
    """Mark the providers cache as needing rebuild."""
    providers_cache["dirty"] = True
    providers_cache["version"] += 1


def invalidate_metrics_cache():
    """Mark /api/metrics ETag cache as dirty."""
    metrics_cache["dirty"] = True
    metrics_cache["version"] += 1


def invalidate_model_info_response_cache():
    """Mark the model-info response cache as needing rebuild."""
    model_info_response_cache["dirty"] = True
    model_info_response_cache["version"] += 1


# ── Aggregated model health counter (avoids o(n) scan in health_check endpoint) ──

_healthy_model_count: int = 0


def update_healthy_model_count():
    """Recompute count of models with status online or degraded."""
    global _healthy_model_count
    _healthy_model_count = sum(1 for v in model_cache.values() if v.get("status") in ("online", "degraded"))


# ── Input length limits (shared across routes) ───────────────────────────────

MAX_ENDPOINT_LEN = 2048
MAX_CLIENT_ID_LEN = 64
MAX_MODEL_KEY_LEN = 256
MAX_REQUEST_BODY_BYTES = 1_048_576

# ── Core constants ───────────────────────────────────────────────────────────

# ── Shared helpers (DRY) ──────────────────────────────────────────────────────

# Type-specific pref keys - derived from VALID_PREFS so adding a new pref
# only requires updating VALID_PREFS and the appropriate type set below.
_PREF_BOOL_KEYS = frozenset({
    "enabled", "offline", "recovered", "recovered_offline", "recovered_degraded", "degraded",
    "degraded_tps", "recovered_tps", "degraded_ttft", "recovered_ttft",
    "provider_changed", "model_changed",
})
_PREF_TIER_KEYS = frozenset({"degraded_tps_tier", "degraded_ttft_tier"})
_PREF_LIST_KEYS = frozenset({"providers"})
_PREF_STR_KEYS = frozenset({"enabled_at"})
# Derive VALID_PREFS from type sets - single source of truth
VALID_PREFS = _PREF_BOOL_KEYS | _PREF_TIER_KEYS | _PREF_LIST_KEYS | _PREF_STR_KEYS
_TIER_MAX = 4


def sanitize_prefs(prefs: dict) -> dict:
    """Filter and coerce prefs: only VALID_PREFS keys, values forced to correct types."""
    if not isinstance(prefs, dict):
        return {}
    result = {}
    for k, v in prefs.items():
        if k not in VALID_PREFS:
            continue
        if k in _PREF_BOOL_KEYS:
            result[k] = bool(v)
        elif k in _PREF_TIER_KEYS:
            if v is None:
                continue
            try:
                result[k] = max(0, min(int(v), _TIER_MAX))
            except (ValueError, TypeError):
                continue
        elif k in _PREF_LIST_KEYS:
            if isinstance(v, list):
                result[k] = [str(p) for p in v]
        elif k in _PREF_STR_KEYS:
            if isinstance(v, str) and v:
                result[k] = v
    return result


def utc_now_iso() -> str:
    """Current UTC time as ISO format string."""
    return datetime.now(timezone.utc).isoformat()


def parse_model_key(model_key: str) -> tuple[str, str]:
    """Split a model key into (provider_name, model_id)."""
    idx = model_key.find("::")
    if idx < 0:
        return "", model_key
    return model_key[:idx], model_key[idx + 2:]


TEST_HEALTH = "health"
TEST_BENCHMARK = "benchmark"
TEST_AUDIT = "audit"
TEST_PROBE = "probe"


def test_type_schedule(test_type: str) -> tuple[float, str]:
    """Return (interval, epoch_key) for a test type. Single source of truth."""
    if test_type == TEST_HEALTH:
        return c.health_interval, "last_health_epoch"
    if test_type == TEST_AUDIT:
        return c.audit_interval, "last_audit_epoch"
    if test_type == TEST_PROBE:
        return c.probe_interval, "last_probe_epoch"
    return c.benchmark_interval, "last_benchmark_epoch"


def test_type_allows_status(test_type: str, status: str | None) -> bool:
    """Whether a test type should run on a model with this status.

    Offline (error) models only get health checks - all other test types
    wait until a successful health check brings the model back online.
    """
    return status != "error" or test_type == TEST_HEALTH


# ── Canonical label / value dicts - single source of truth ──────────────────
# Consumed by notifications.py (notification bodies), routes.py (/api/config),
# and the frontend (via /api/config → state). Do NOT duplicate these elsewhere.

STATUS_VALUES = ("online", "degraded", "error", "unknown")
TEST_TYPES = (TEST_BENCHMARK, TEST_HEALTH, TEST_AUDIT, TEST_PROBE)
CHART_VIEWS = ("speed", "consistency", "scores", "health")

EVENT_LABELS = {
    "offline": "Offline",
    "recovered": "Recovered",
    "recovered_offline": "Recovered",
    "recovered_degraded": "Recovered",
    "partially_recovered": "Partially Recovered",
    "degraded": "Degraded",
    "degraded_tps": "TPS Degraded",
    "recovered_tps": "TPS Recovered",
    "degraded_ttft": "TTFT Degraded",
    "recovered_ttft": "TTFT Recovered",
    "provider_changed": "Provider Changed",
    "model_changed": "Model Changed",
}

METRIC_LABELS = {
    "tps": "TPS", "ttft": "TTFT", "stall_count": "Stalls",
    "raw_p99_itl_ms": "P99 ITL (raw)", "raw_median_itl_ms": "Med ITL (raw)",
    "raw_avg_itl_ms": "Avg ITL (raw)", "raw_max_itl_ms": "Max ITL (raw)",
    "effective_median_itl_ms": "Med ITL (eff.)", "effective_avg_itl_ms": "Avg ITL (eff.)",
    "effective_p99_itl_ms": "P99 ITL (eff.)", "effective_itl_tail_ratio": "Tail ratio (eff.)",
    "chunk_token_ratio": "Batching",
    "network_jitter_ms": "Net jitter", "burst_arrival_pct": "Burst %",
    "chunk_token_cv": "Chunk CV",
    "consistency_score": "Consistency", "speed_score": "Speed",
    "reliability": "Reliability", "tpot_ms": "TPOT",
}


# ── Test concurrency guard ───────────────────────────────────────────────────

running_tests: set[str] = set()
running_health: set[str] = set()
running_audit: set[str] = set()
running_probe: set[str] = set()


# ── Core constants ───────────────────────────────────────────────────────────

VALID_PUSH_HOSTS = frozenset({
    "android.googleapis.com",
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "updates-autopush.stage.mozaws.net",
    "updates-autopush.dev.mozaws.net",
})

# Wildcard suffixes - any subdomain of these domains is accepted
VALID_PUSH_SUFFIXES = (
    ".notify.windows.com",
    ".push.apple.com",
)

INTERNAL_KEYS = frozenset(("error_trace", "_ts_epoch", "id", "rn", "trends_json"))

MODEL_INFO_FIELDS = frozenset((
    "context_window", "output_context", "supports_vision", "supports_tools",
    "supports_cache", "supports_structured_output", "input_price", "output_price",
    "cache_price", "display_name", "description", "modalities", "tokenizer",
    "reasoning_price", "image_price", "created", "owner", "license",
    "thinking", "quantization", "served_by", "architecture", "param_count",
    "num_experts", "num_experts_per_tok", "num_shared_experts", "moe_intermediate_size",
    "fingerprint", "engine_version", "tensor_parallel", "served_model",
    "fp_server", "fp_features",
))

MODEL_INFO_BOOL_FIELDS = frozenset((
    "supports_vision", "supports_tools", "supports_cache", "supports_structured_output",
))

_FALSEY_THINKING = frozenset(("", "0", "false", "disabled", "none", "no", "off"))
_TRUTHY_THINKING = frozenset(("1", "true", "yes"))


def normalize_thinking(v) -> str | None:
    """Normalize a thinking value to canonical form.

    Accepts strings ("enabled", "1", "0", etc.), booleans, or None.
    Returns "enabled", a rich string ("effort", "budget:N"), or None.
    """
    if isinstance(v, bool):
        return "enabled" if v else None
    if isinstance(v, str):
        if v.lower() in _FALSEY_THINKING:
            return None
        if v.lower() in _TRUTHY_THINKING:
            return "enabled"
        return v
    if isinstance(v, (int, float)):
        return "enabled" if v else None
    return None


def strip_internal(record: dict) -> dict:
    """Return a copy of record with INTERNAL_KEYS and null-valued fields removed."""
    return {k: v for k, v in record.items() if k not in INTERNAL_KEYS and v is not None}


_fp_ds_re = None

def parse_fingerprint(raw: str | None) -> dict:
    """Parse system_fingerprint into structured fields.

    Supports:
      vllm:    vllm-<version>[-tp<N>][-ep]-<hash>
      deepseek: fp_<hash>_<server>_<quant>[_<feat>...][_YYYYMMDD]
      ollama:   fp_ollama
      triton:   *triton* (case-insensitive)
    """
    if not raw:
        return {}
    r: dict = {}
    if raw.startswith("vllm-"):
        r["engine"] = "vllm"
        rest = raw[5:]
        import re as _re
        m = _re.match(r'^(.+?)(?:-tp(\d+))?(?:-ep)?-([a-f0-9]{7,40})$', rest)
        if m:
            r["engine_version"] = m.group(1)
            if m.group(2):
                r["tensor_parallel"] = int(m.group(2))
        else:
            m2 = _re.match(r'^(.+?)(?:-tp(\d+))?(?:-ep)?$', rest)
            if m2:
                r["engine_version"] = m2.group(1)
                if m2.group(2):
                    r["tensor_parallel"] = int(m2.group(2))
            else:
                r["engine_version"] = rest
    elif raw == "fp_ollama":
        r["engine"] = "ollama"
    elif raw.startswith("fp_"):
        import re as _re
        global _fp_ds_re
        if _fp_ds_re is None:
            _fp_ds_re = _re.compile(
                r'^fp_([0-9a-f]+)_([^_]+)_([^_]+)(?:_([^_\d][^_]*))*(?:_(\d{8}))?$'
            )
        m = _fp_ds_re.match(raw)
        if m:
            r["engine"] = "deepseek"
            r["fp_server"] = m.group(2)
            quant = m.group(3)
            if quant:
                r["quantization"] = quant
            remain = raw[m.end(3):]
            if remain:
                parts = [p for p in remain.split('_') if p and not p.isdigit() and p != quant]
                if parts:
                    r["fp_features"] = ",".join(parts)
        else:
            r["engine"] = "deepseek"
    elif "triton" in raw.lower():
        r["engine"] = "triton"
    return r


THINK_END = "\n\n\n\n\n\n\n\n\n\n\n\n"  # Qwen separates reasoning from content in the same delta with 12 newlines


def ensure_scheme(url: str) -> str:
    """Ensure a URL has an https:// scheme. Handles bare hostnames like 'api.example.com/v1'."""
    if not url:
        return url
    if "://" not in url.split("?")[0].split("#")[0]:
        return f"https://{url}"
    return url


# ── Optional dependencies ───────────────────────────────────────────────────

try:
    from watchfiles import awatch, Change
except ImportError:
    awatch = None
    Change = None

try:
    from pywebpush import webpush  # noqa: F401 - re-exported for push helpers
    push_available = True
except ImportError:
    push_available = False

try:
    from PIL import Image
    pillow_available = True
except ImportError:
    Image = None
    pillow_available = False
