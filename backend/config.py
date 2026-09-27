"""YAML config loading, env var resolution, hot-reload, and config watcher."""

import asyncio
import math
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from backend.state import (
    c, app_cfg, models_cfg, model_registry, model_cache, log, log_error,
    apply_log_level, LOG_LEVELS,
    CONFIG_DIR, awatch, ensure_scheme, make_model_key,
)
from backend.models import build_model_registry
from backend.websocket import ws_mgr
import backend.state as st


_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "mo": 2592000}
_DURATION_RE = re.compile(r"^(\d+)\s*(s|m|h|d|w|mo)$", re.IGNORECASE)


def _parse_duration(value: str | int, *, raise_on_invalid: bool = True) -> int | None:
    """Parse a duration string (e.g. '2d', '3h', '1w', '1mo') to seconds.

    Units: s=seconds, m=minutes, h=hours, d=days, w=weeks, mo=30-day-months.
    Bare integers are treated as seconds.
    If raise_on_invalid=False, returns None instead of raising ValueError.
    """
    if isinstance(value, int):
        return value
    m = _DURATION_RE.match(str(value).strip())
    if not m:
        if raise_on_invalid:
            raise ValueError(f"Invalid duration '{value}' - use <number><s|m|h|d|w|mo> (e.g. 2d, 3h, 1w, 1mo)")
        return None
    return int(m.group(1)) * _DURATION_UNITS[m.group(2).lower()]


def _resolve_env_vars(obj):
    """Recursively resolve ${VAR} references in config values from env vars."""
    if isinstance(obj, str):
        return re.sub(
            r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}',
            lambda m: os.environ.get(m.group(1), m.group(0)),
            obj,
        )
    if isinstance(obj, dict):
        return {k: _resolve_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_vars(v) for v in obj]
    return obj


# Env var that replaces each config file: a name inside CONFIG_DIR or an absolute path
_CONFIG_ENV = {"app.yaml": "MW_APP_YAML", "models.yaml": "MW_MODELS_YAML", "audits.yaml": "MW_AUDITS_YAML"}


def config_path(name: str) -> Path:
    """The file a config name is read from, honouring its MW_*_YAML override.

    The one resolver for loading, watching and rewriting config files, so all three
    agree on which file is in use.
    """
    return CONFIG_DIR / (os.environ.get(_CONFIG_ENV[name]) or name)


def _load_yaml(name: str, *, required: bool = True) -> dict:
    """Load a YAML config file with env var resolution. Returns {} if not required and missing."""
    p = config_path(name)
    if not p.exists():
        if os.environ.get(_CONFIG_ENV[name]):
            raise FileNotFoundError(f"{_CONFIG_ENV[name]} points at a missing file: {p}")
        if not required:
            return {}
        raise FileNotFoundError(f"Required config file not found: {p}")
    with open(p) as f:
        data = yaml.safe_load(f) or {}
    return _resolve_env_vars(data)


def _validate_mapping(path: str, value, *, prefix: str = "app.yaml:"):
    if not isinstance(value, dict):
        raise ValueError(f"{prefix} {path} must be a mapping")


def _validate_string(path: str, value, *, allow_empty: bool = False, prefix: str = "app.yaml:"):
    if not isinstance(value, str):
        raise ValueError(f"{prefix} {path} must be a string")
    if not allow_empty and not value.strip():
        raise ValueError(f"{prefix} {path} must not be empty")


def _validate_bool(path: str, value, *, prefix: str = "app.yaml:"):
    if not isinstance(value, bool):
        raise ValueError(f"{prefix} {path} must be true or false")


def _validate_number(path: str, value, *, min_value: float | None = None, inclusive: bool = True, prefix: str = "app.yaml:"):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{prefix} {path} must be a finite number")
    if min_value is not None:
        valid = value >= min_value if inclusive else value > min_value
        if not valid:
            op = ">=" if inclusive else ">"
            raise ValueError(f"{prefix} {path} must be {op} {min_value:g} (got {value})")


def _validate_int(path: str, value, *, min_value: int | None = None, prefix: str = "app.yaml:"):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{prefix} {path} must be an integer")
    if min_value is not None and value < min_value:
        raise ValueError(f"{prefix} {path} must be >= {min_value} (got {value})")


def _validate_duration(path: str, value, *, prefix: str = "app.yaml:") -> int:
    """Validate a positive duration (e.g. 30m, 2d) and return it in seconds."""
    if isinstance(value, bool):
        raise ValueError(f"{prefix} {path} must be a duration, not a boolean")
    try:
        seconds = _parse_duration(value)
    except ValueError as e:
        raise ValueError(f"{prefix} {path} is invalid: {e}") from e
    if seconds <= 0:
        raise ValueError(f"{prefix} {path} must be > 0 seconds")
    return seconds


def _validate_string_list(path: str, value, *, min_len: int = 0, prefix: str = "app.yaml:"):
    if not isinstance(value, list):
        raise ValueError(f"{prefix} {path} must be a list")
    if len(value) < min_len:
        raise ValueError(f"{prefix} {path} must contain at least {min_len} item(s)")
    for idx, item in enumerate(value):
        _validate_string(f"{path}[{idx}]", item, prefix=prefix)


def _require_keys(path: str, value, keys, *, prefix: str = "app.yaml:"):
    """Validate that value is a mapping holding every key; the message names each missing key's path."""
    _validate_mapping(path or "root", value, prefix=prefix)
    missing = [f"{path}.{key}" if path else key for key in keys if key not in value]
    if missing:
        raise ValueError(f"{prefix} {', '.join(missing)} {'is' if len(missing) == 1 else 'are'} required")


def _validate_choice(path: str, value, choices, *, prefix: str = "app.yaml:"):
    if value not in choices:
        raise ValueError(f"{prefix} {path} must be one of {', '.join(map(str, choices))} (got {value!r})")


def _validate_host_patterns(path: str, value):
    """server.allowed_hosts: host names, '*.example.com' for subdomains, or '*' for any host."""
    _validate_string_list(path, value)
    for idx, pattern in enumerate(value):
        name = pattern.removeprefix("*.")
        if pattern != "*" and ("*" in name or "/" in name or ":" in name):
            raise ValueError(f"app.yaml: {path}[{idx}] must be a host name, '*.domain' or '*' (got {pattern!r})")


_APP_SECTIONS = (
    "app", "testing", "metrics", "stalls", "server", "websocket",
    "notifications", "color_thresholds", "scores", "time_ranges", "auto_archive",
)
_NOTIFICATION_EVENTS = (
    "offline", "recovered", "degraded", "degraded_tps", "degraded_ttft",
    "recovered_tps", "recovered_ttft", "provider_changed", "model_changed",
)
_THRESHOLD_METRICS = (
    "uptime", "tps", "ttft", "stall_count", "raw_p99_itl_ms", "raw_median_itl_ms",
    "raw_max_itl_ms", "effective_itl_tail_ratio", "chunk_token_ratio", "burst_arrival_pct",
    "chunk_token_cv",
)


def _validate_config(cfg: dict):
    """Validate app.yaml structure - key presence, value types, and ranges.

    Raises ValueError with a descriptive message on the first problem found.
    Called after loading so the app fails fast with a clear error.
    """
    _require_keys("", cfg, _APP_SECTIONS)

    app = cfg["app"]
    _require_keys("app", app, ("name", "site_url", "vapid_email", "log_level", "static_url_prefix", "description", "debug"))
    for key in ("name", "site_url", "vapid_email"):
        _validate_string(f"app.{key}", app[key])
    try:
        site_host = urlsplit(ensure_scheme(app["site_url"])).hostname
    except ValueError as e:
        raise ValueError(f"app.yaml: app.site_url is not a URL: {e}") from e
    if not site_host:
        raise ValueError(f"app.yaml: app.site_url has no host (got {app['site_url']!r})")
    for key in ("description", "static_url_prefix"):
        _validate_string(f"app.{key}", app[key], allow_empty=True)
    _validate_choice("app.log_level", app["log_level"], LOG_LEVELS)
    _validate_bool("app.debug", app["debug"])

    testing = cfg["testing"]
    _require_keys("testing", testing, ("max_retries", "initial_delay", "retry_delay", "stream_activity_timeout",
                                       "timeout", "max_concurrent_tests", "benchmark", "health_check", "probe"))
    _validate_int("testing.max_retries", testing["max_retries"], min_value=0)
    _validate_number("testing.initial_delay", testing["initial_delay"], min_value=0)
    _validate_number("testing.retry_delay", testing["retry_delay"], min_value=0)
    _validate_number("testing.stream_activity_timeout", testing["stream_activity_timeout"], min_value=0, inclusive=False)
    _validate_number("testing.timeout", testing["timeout"], min_value=10)
    _validate_int("testing.max_concurrent_tests", testing["max_concurrent_tests"], min_value=1)

    bench = testing["benchmark"]
    _require_keys("testing.benchmark", bench, ("interval", "target_total_tokens", "min_tokens", "min_chunks", "stagger",
                                               "prompts", "token_encoding", "token_encoding_retry"))
    _validate_int("testing.benchmark.interval", bench["interval"], min_value=60)
    _validate_int("testing.benchmark.target_total_tokens", bench["target_total_tokens"], min_value=1)
    _validate_int("testing.benchmark.min_tokens", bench["min_tokens"], min_value=0)
    _validate_int("testing.benchmark.min_chunks", bench["min_chunks"], min_value=0)
    _validate_bool("testing.benchmark.stagger", bench["stagger"])
    _validate_string("testing.benchmark.token_encoding", bench["token_encoding"])
    _validate_duration("testing.benchmark.token_encoding_retry", bench["token_encoding_retry"])
    thinking_budget = bench.get("anthropic_thinking_budget")
    if thinking_budget is not None:
        _validate_int("testing.benchmark.anthropic_thinking_budget", thinking_budget, min_value=0)
    _require_keys("testing.benchmark.prompts", bench["prompts"], ("suffix",))
    _validate_string("testing.benchmark.prompts.suffix", bench["prompts"]["suffix"], allow_empty=True)

    health = testing["health_check"]
    _require_keys("testing.health_check", health, ("enabled", "interval", "max_tokens", "prompts"))
    _validate_bool("testing.health_check.enabled", health["enabled"])
    _validate_int("testing.health_check.interval", health["interval"], min_value=1)
    _validate_int("testing.health_check.max_tokens", health["max_tokens"], min_value=1)
    _validate_string_list("testing.health_check.prompts", health["prompts"], min_len=1)

    probe = testing["probe"]
    _require_keys("testing.probe", probe, ("enabled", "interval", "max_tokens"))
    _validate_bool("testing.probe.enabled", probe["enabled"])
    _validate_int("testing.probe.interval", probe["interval"], min_value=60)
    _validate_int("testing.probe.max_tokens", probe["max_tokens"], min_value=1)

    if testing.get("audit") is not None:
        raise ValueError("app.yaml: testing.audit is no longer supported - move audit config to audits.yaml")

    metrics = cfg["metrics"]
    _require_keys("metrics", metrics, ("retention_days", "uptime_window", "recent_history", "min_data_points_score",
                                       "min_data_points_trend", "history_query_limit", "provider_fetch_ttl",
                                       "cleanup_interval", "write_batch_interval", "write_batch_max_buffer"))
    _validate_int("metrics.retention_days", metrics["retention_days"], min_value=1)
    _validate_number("metrics.uptime_window", metrics["uptime_window"], min_value=0, inclusive=False)
    _validate_int("metrics.min_data_points_score", metrics["min_data_points_score"], min_value=1)
    _validate_int("metrics.min_data_points_trend", metrics["min_data_points_trend"], min_value=1)
    _validate_int("metrics.history_query_limit", metrics["history_query_limit"], min_value=1)
    _validate_number("metrics.provider_fetch_ttl", metrics["provider_fetch_ttl"], min_value=0)
    _validate_int("metrics.cleanup_interval", metrics["cleanup_interval"], min_value=60)
    _validate_number("metrics.write_batch_interval", metrics["write_batch_interval"], min_value=0.1)
    _validate_int("metrics.write_batch_max_buffer", metrics["write_batch_max_buffer"], min_value=1)
    _validate_duration("metrics.recent_history", metrics["recent_history"])

    stalls = cfg["stalls"]
    stall_keys = ("visible_threshold_ms", "hiccup_threshold_ms", "hiccup_multiplier", "batching_log_threshold")
    _require_keys("stalls", stalls, stall_keys)
    for key in stall_keys:
        _validate_number(f"stalls.{key}", stalls[key], min_value=0, inclusive=False)

    aa = cfg["auto_archive"]
    _require_keys("auto_archive", aa, ("enabled", "offline_duration"))
    _validate_bool("auto_archive.enabled", aa["enabled"])
    _validate_duration("auto_archive.offline_duration", aa["offline_duration"])

    server = cfg["server"]
    _require_keys("server", server, ("max_connections", "http_connect_timeout", "http_pool_max", "allowed_hosts"))
    _validate_int("server.max_connections", server["max_connections"], min_value=1)
    _validate_number("server.http_connect_timeout", server["http_connect_timeout"], min_value=0, inclusive=False)
    _validate_int("server.http_pool_max", server["http_pool_max"], min_value=1)
    _validate_host_patterns("server.allowed_hosts", server["allowed_hosts"])

    ws = cfg["websocket"]
    _require_keys("websocket", ws, ("allowed_origins", "heartbeat_interval", "stale_after", "reconnect", "unreachable",
                                    "max_message_bytes", "sync_prefs_per_minute", "ping_interval", "ping_timeout"))
    _validate_string_list("websocket.allowed_origins", ws["allowed_origins"])
    _validate_number("websocket.heartbeat_interval", ws["heartbeat_interval"], min_value=0, inclusive=False)
    # Clients count silence from their last message; a window no longer than the heartbeat
    # interval would tear down healthy sockets on a quiet server
    _validate_number("websocket.stale_after", ws["stale_after"], min_value=ws["heartbeat_interval"], inclusive=False)
    reconnect = ws["reconnect"]
    _require_keys("websocket.reconnect", reconnect, ("min_delay", "max_delay"))
    _validate_number("websocket.reconnect.min_delay", reconnect["min_delay"], min_value=0, inclusive=False)
    _validate_number("websocket.reconnect.max_delay", reconnect["max_delay"], min_value=reconnect["min_delay"])
    unreachable = ws["unreachable"]
    _require_keys("websocket.unreachable", unreachable, ("after_failures", "retry_interval"))
    _validate_int("websocket.unreachable.after_failures", unreachable["after_failures"], min_value=1)
    _validate_number("websocket.unreachable.retry_interval", unreachable["retry_interval"], min_value=0, inclusive=False)
    _validate_int("websocket.max_message_bytes", ws["max_message_bytes"], min_value=1)
    _validate_int("websocket.sync_prefs_per_minute", ws["sync_prefs_per_minute"], min_value=1)
    _validate_number("websocket.ping_interval", ws["ping_interval"], min_value=0, inclusive=False)
    _validate_number("websocket.ping_timeout", ws["ping_timeout"], min_value=0, inclusive=False)

    notif = cfg["notifications"]
    _require_keys("notifications", notif, ("enabled", "webhook_timeout", "push_ttl", "events", "degraded_tps_tier",
                                           "degraded_ttft_tier", "in_app", "rate_limits", "webhooks"))
    _validate_bool("notifications.enabled", notif["enabled"])
    _validate_number("notifications.webhook_timeout", notif["webhook_timeout"], min_value=0, inclusive=False)
    _validate_int("notifications.push_ttl", notif["push_ttl"], min_value=0)
    _require_keys("notifications.events", notif["events"], _NOTIFICATION_EVENTS)
    for key in _NOTIFICATION_EVENTS:
        _validate_bool(f"notifications.events.{key}", notif["events"][key])
    for key in ("degraded_tps_tier", "degraded_ttft_tier"):
        _validate_int(f"notifications.{key}", notif[key], min_value=0)
    in_app = notif["in_app"]
    _require_keys("notifications.in_app", in_app, ("enabled", "toast_duration_ms", "history_size", "retention_days",
                                                   "api_response_cap"))
    _validate_bool("notifications.in_app.enabled", in_app["enabled"])
    _validate_int("notifications.in_app.toast_duration_ms", in_app["toast_duration_ms"], min_value=0)
    _validate_int("notifications.in_app.history_size", in_app["history_size"], min_value=1)
    _validate_int("notifications.in_app.retention_days", in_app["retention_days"], min_value=1)
    _validate_int("notifications.in_app.api_response_cap", in_app["api_response_cap"], min_value=1)
    rate_limits = notif["rate_limits"]
    limit_keys = ("prefs_per_minute", "push_test_per_minute", "subscribe_per_minute", "validate_per_minute",
                  "client_error_per_minute")
    _require_keys("notifications.rate_limits", rate_limits, limit_keys)
    for key in limit_keys:
        _validate_int(f"notifications.rate_limits.{key}", rate_limits[key], min_value=1)
    webhooks = notif["webhooks"]
    if not isinstance(webhooks, list):
        raise ValueError("app.yaml: notifications.webhooks must be a list")
    for idx, webhook in enumerate(webhooks):
        _require_keys(f"notifications.webhooks[{idx}]", webhook, ("url",))
        _validate_string(f"notifications.webhooks[{idx}].url", webhook["url"])
        if "name" in webhook:
            _validate_string(f"notifications.webhooks[{idx}].name", webhook["name"])
        if "secret" in webhook:
            _validate_string(f"notifications.webhooks[{idx}].secret", webhook["secret"], allow_empty=True)

    ct = cfg["color_thresholds"]
    _require_keys("color_thresholds", ct, ("tiers", *_THRESHOLD_METRICS))
    tiers = ct["tiers"]
    if not isinstance(tiers, list) or len(tiers) < 2:
        raise ValueError("app.yaml: color_thresholds.tiers must contain at least 2 tiers")
    for idx, tier in enumerate(tiers):
        _require_keys(f"color_thresholds.tiers[{idx}]", tier, ("label", "color"))
        for key in ("label", "color"):
            _validate_string(f"color_thresholds.tiers[{idx}].{key}", tier[key])
    max_tier_idx = len(tiers) - 1
    if notif["degraded_tps_tier"] > max_tier_idx or notif["degraded_ttft_tier"] > max_tier_idx:
        raise ValueError(f"app.yaml: notification degraded tier indexes must be between 0 and {max_tier_idx}")
    for metric in _THRESHOLD_METRICS:
        metric_cfg = ct[metric]
        _require_keys(f"color_thresholds.{metric}", metric_cfg, ("higher_is_better", "thresholds"))
        _validate_bool(f"color_thresholds.{metric}.higher_is_better", metric_cfg["higher_is_better"])
        thresholds = metric_cfg["thresholds"]
        if not isinstance(thresholds, list) or len(thresholds) != len(tiers):
            raise ValueError(f"app.yaml: color_thresholds.{metric}.thresholds must have {len(tiers)} values")
        for idx, threshold in enumerate(thresholds):
            _validate_number(f"color_thresholds.{metric}.thresholds[{idx}]", threshold)

    scores = cfg["scores"]
    _require_keys("scores", scores, ("consistency", "speed", "reliability"))
    for section_name in ("consistency", "speed"):
        _require_keys(f"scores.{section_name}", scores[section_name], ("weights",))
        weights = scores[section_name]["weights"]
        _validate_mapping(f"scores.{section_name}.weights", weights)
        if not weights:
            raise ValueError(f"app.yaml: scores.{section_name}.weights must not be empty")
        for key, weight in weights.items():
            _validate_string(f"scores.{section_name}.weights key", key)
            _validate_number(f"scores.{section_name}.weights.{key}", weight, min_value=0)
    reliability = scores["reliability"]
    _require_keys("scores.reliability", reliability, ("availability_weight", "quality_weight"))
    for key in ("availability_weight", "quality_weight"):
        _validate_number(f"scores.reliability.{key}", reliability[key], min_value=0)
    if reliability["availability_weight"] + reliability["quality_weight"] <= 0:
        raise ValueError("app.yaml: scores.reliability weights must sum to more than 0")

    time_ranges = cfg["time_ranges"]
    if not isinstance(time_ranges, list) or not time_ranges:
        raise ValueError("app.yaml: time_ranges must be a non-empty list")
    for idx, entry in enumerate(time_ranges):
        _require_keys(f"time_ranges[{idx}]", entry, ("key", "label"))
        for key in ("key", "label"):
            _validate_string(f"time_ranges[{idx}].{key}", entry[key])
        seconds = _parse_duration(entry["key"], raise_on_invalid=False)
        if seconds is None or seconds <= 0:
            raise ValueError(f"app.yaml: time_ranges[{idx}].key must be a positive duration")
        if "seconds" in entry and entry["seconds"] is not None:
            _validate_int(f"time_ranges[{idx}].seconds", entry["seconds"], min_value=1)


def _validate_audits_cfg(cfg: dict):
    """Validate the audit section of audits.yaml."""
    if not cfg:
        return
    prefix = "audits.yaml:"
    _require_keys("audit", cfg, ("enabled", "interval"), prefix=prefix)
    _validate_bool("audit.enabled", cfg["enabled"], prefix=prefix)
    _validate_int("audit.interval", cfg["interval"], min_value=60, prefix=prefix)
    suites = cfg.get("suites")
    if suites is None:
        return
    _validate_mapping("audit.suites", suites, prefix=prefix)
    for suite_name, suite_cfg in suites.items():
        path = f"audit.suites.{suite_name}"
        _require_keys(path, suite_cfg, ("enabled", "stream", "count", "url"), prefix=prefix)
        _validate_bool(f"{path}.enabled", suite_cfg["enabled"], prefix=prefix)
        _validate_bool(f"{path}.stream", suite_cfg["stream"], prefix=prefix)
        _validate_int(f"{path}.count", suite_cfg["count"], min_value=1, prefix=prefix)
        _validate_string(f"{path}.url", suite_cfg["url"], prefix=prefix)
        if "skip_reasoning" in suite_cfg:
            _validate_bool(f"{path}.skip_reasoning", suite_cfg["skip_reasoning"], prefix=prefix)
        if suite_cfg.get("reasoning_effort") is not None:
            _validate_choice(f"{path}.reasoning_effort", suite_cfg["reasoning_effort"], ("low", "medium", "high"), prefix=prefix)
        if suite_cfg.get("only") is not None:
            _validate_string(f"{path}.only", suite_cfg["only"], prefix=prefix)


def _validate_models_cfg(cfg: dict):
    """Validate models.yaml structure - provider/model field presence, types, and uniqueness."""
    prefix = "models.yaml:"
    _require_keys("", cfg, ("providers",), prefix=prefix)
    providers = cfg["providers"]
    if not isinstance(providers, list):
        raise ValueError("models.yaml: providers must be a list")
    if not providers:
        raise ValueError("models.yaml: providers must contain at least one provider")
    seen_provider_names: set[str] = set()
    for idx, provider in enumerate(providers):
        path = f"providers[{idx}]"
        _require_keys(path, provider, ("name", "api_url", "models"), prefix=prefix)
        for key in ("name", "api_url"):
            _validate_string(f"{path}.{key}", provider[key], prefix=prefix)
        p_name = provider["name"]
        if p_name in seen_provider_names:
            raise ValueError(f"models.yaml: duplicate provider name {p_name!r} - provider names must be unique")
        seen_provider_names.add(p_name)
        if "api_key" in provider:
            _validate_string(f"{path}.api_key", provider["api_key"], allow_empty=True, prefix=prefix)
        models = provider["models"]
        if not isinstance(models, list) or not models:
            raise ValueError(f"models.yaml: {path}.models must be a non-empty list")
        seen_model_ids: set[str] = set()
        for midx, m in enumerate(models):
            _require_keys(f"{path}.models[{midx}]", m, ("id",), prefix=prefix)
            _validate_string(f"{path}.models[{midx}].id", m["id"], prefix=prefix)
            m_id = m["id"]
            if m_id in seen_model_ids:
                raise ValueError(f"models.yaml: duplicate model id {m_id!r} in provider {p_name!r}")
            seen_model_ids.add(m_id)
            if "name" in m:
                _validate_string(f"{path}.models[{midx}].name", m["name"], prefix=prefix)


def _normalize_audit_suites(suites: dict) -> dict:
    """Normalize suite configs: set defaults, strip nulls."""
    result = {}
    for name, cfg in suites.items():
        result[name] = {
            "enabled": cfg["enabled"],
            "stream": cfg["stream"],
            "count": cfg["count"],
            "url": cfg["url"],
            "skip_reasoning": cfg.get("skip_reasoning"),
            "reasoning_effort": cfg.get("reasoning_effort"),
            "only": cfg.get("only"),
        }
    return result


def reload_config(log_changes: bool = False) -> dict:
    """Load YAML config, validate, populate the runtime namespace, and rebuild the registry.

    Returns a dict with added/removed model keys, provider names, and reset_epoch keys
    for apply_db_changes() to sync SQLite and dispatch notifications. Mutates app_cfg,
    models_cfg, and model_registry in place so all holders see the update.
    """

    new_app_cfg = _load_yaml("app.yaml")
    new_models_cfg = _load_yaml("models.yaml")
    _validate_config(new_app_cfg)
    _validate_models_cfg(new_models_cfg)

    # Scan reset_epoch directives before in-memory config is updated.
    # Side effects (epoch reset, YAML rewrite) happen in apply_db_changes()
    # which runs at both startup and hot-reload.
    reset_keys: set[str] = set()
    for provider in new_models_cfg["providers"]:
        p_name = provider["name"]
        if provider.pop("reset_epoch", None) is True:
            for m in provider["models"]:
                reset_keys.add(make_model_key(p_name, m["id"]))
        for m in provider["models"]:
            if m.pop("reset_epoch", None) is True:
                reset_keys.add(make_model_key(p_name, m["id"]))

    # Snapshot current values for change detection
    old_c = {k: v for k, v in c.__dict__.items() if not k.startswith("_")} if log_changes else None
    old_recent_history_seconds = getattr(c, 'recent_history_seconds', None)
    old_model_ids = {e["id"] for e in model_registry} if model_registry else set()
    old_model_names = {e["id"]: e["name"] for e in model_registry} if model_registry else {}
    old_provider_names = {e.get("name") for e in models_cfg.get("providers", [])} if models_cfg else set()

    app_cfg.clear()
    app_cfg.update(new_app_cfg)
    models_cfg.clear()
    models_cfg.update(new_models_cfg)

    # Normalize provider api_urls - ensure all have a scheme (https://)
    for provider in models_cfg.get("providers", []):
        raw_url = provider.get("api_url", "")
        normalized = ensure_scheme(raw_url)
        if normalized != raw_url:
            provider["api_url"] = normalized
            if log_changes:
                log.info("Normalized provider URL: %s → %s", raw_url, normalized)
        # Also normalize provider_url if specified
        raw_purl = provider.get("provider_url")
        if raw_purl:
            normalized_purl = ensure_scheme(raw_purl)
            if normalized_purl != raw_purl:
                provider["provider_url"] = normalized_purl
                if log_changes:
                    log.info("Normalized provider_url: %s → %s", raw_purl, normalized_purl)
        # Normalize per-model api_url overrides
        for m in provider.get("models", []):
            raw_murl = m.get("api_url")
            if raw_murl:
                normalized_murl = ensure_scheme(raw_murl)
                if normalized_murl != raw_murl:
                    m["api_url"] = normalized_murl
                    if log_changes:
                        log.info("Normalized model %s api_url: %s → %s", m.get("id", "?"), raw_murl, normalized_murl)

    # Apply app.yaml values to the runtime config namespace - no fallbacks; YAML is the sole source of truth
    app_section = new_app_cfg["app"]
    testing = new_app_cfg["testing"]
    benchmark = testing["benchmark"]
    health = testing["health_check"]
    metrics_cfg = new_app_cfg["metrics"]
    stalls = new_app_cfg["stalls"]
    server = new_app_cfg["server"]
    ws = new_app_cfg["websocket"]

    c.static_url_prefix = app_section["static_url_prefix"]
    c.app_name = app_section["name"]
    c.app_description = app_section["description"]
    c.debug = app_section["debug"]
    c.site_url = app_section["site_url"]
    c.site_host = urlsplit(ensure_scheme(c.site_url)).hostname or ""
    c.vapid_email = app_section["vapid_email"]
    c.log_level = app_section["log_level"]
    apply_log_level(c.log_level)

    # Shared testing settings
    c.max_retries = testing["max_retries"]
    c.initial_delay = testing["initial_delay"]
    c.retry_delay = testing["retry_delay"]
    c.stream_activity_timeout = testing["stream_activity_timeout"]
    c.test_timeout = testing["timeout"]
    c.max_concurrent_tests = testing["max_concurrent_tests"]

    # Benchmark settings
    c.benchmark_interval = benchmark["interval"]
    c.benchmark_target_tokens = benchmark["target_total_tokens"]
    c.benchmark_min_tokens = benchmark["min_tokens"]
    c.benchmark_min_chunks = benchmark["min_chunks"]
    c.anthropic_thinking_budget = benchmark.get("anthropic_thinking_budget")
    c.benchmark_prompt_suffix = benchmark["prompts"]["suffix"]
    c.benchmark_stagger = benchmark["stagger"]
    c.benchmark_token_encoding = benchmark["token_encoding"]
    c.benchmark_token_encoding_retry = _parse_duration(benchmark["token_encoding_retry"])

    # Health check settings
    c.health_enabled = health["enabled"]
    c.health_interval = health["interval"]
    c.health_max_tokens = health["max_tokens"]
    c.health_prompts = health["prompts"]

    # Audit settings - from audits.yaml (optional file)
    try:
        _audits_raw = _load_yaml("audits.yaml", required=False)
        audits_cfg = _audits_raw.get("audit", {}) if _audits_raw else {}
        _validate_audits_cfg(audits_cfg)
        if audits_cfg:
            c.audit_enabled = audits_cfg["enabled"]
            c.audit_interval = audits_cfg["interval"]
            c.audit_suites = _normalize_audit_suites(audits_cfg.get("suites") or {})
        else:
            c.audit_enabled = False
            c.audit_interval = None
            c.audit_suites = {}
    except Exception as e:
        log_error("Failed to load audits.yaml - audit disabled", e)
        c.audit_enabled = False
        c.audit_interval = None
        c.audit_suites = {}

    # Probe settings - from testing.probe
    probe_cfg = testing["probe"]
    c.probe_enabled = probe_cfg["enabled"]
    c.probe_interval = probe_cfg["interval"]
    c.probe_max_tokens = probe_cfg["max_tokens"]

    c.retention_days = metrics_cfg["retention_days"]
    c.uptime_window = metrics_cfg["uptime_window"]
    rh_cfg = metrics_cfg["recent_history"]
    c.recent_history_seconds = _parse_duration(rh_cfg)
    if c.recent_history_seconds < c.uptime_window:
        log.warning("recent_history (%ds) < uptime_window (%ds) - uptime may be inaccurate for early data",
                     c.recent_history_seconds, c.uptime_window)
    c.history_query_limit = metrics_cfg["history_query_limit"]
    c.provider_fetch_ttl = metrics_cfg["provider_fetch_ttl"]
    c.cleanup_interval = metrics_cfg["cleanup_interval"]
    c.write_batch_interval = metrics_cfg["write_batch_interval"]
    c.write_batch_max_buffer = metrics_cfg["write_batch_max_buffer"]
    c.min_data_points_score = metrics_cfg["min_data_points_score"]
    c.min_data_points_trend = metrics_cfg["min_data_points_trend"]

    c.stall_visible_ms = stalls["visible_threshold_ms"]
    c.stall_hiccup_ms = stalls["hiccup_threshold_ms"]
    c.hiccup_multiplier = stalls["hiccup_multiplier"]
    c.batching_log_threshold = stalls["batching_log_threshold"]

    auto_archive_cfg = new_app_cfg["auto_archive"]
    c.auto_archive_enabled = auto_archive_cfg["enabled"]
    c.auto_archive_offline_duration = _parse_duration(auto_archive_cfg["offline_duration"])

    c.max_connections = server["max_connections"]
    c.http_connect_timeout = server["http_connect_timeout"]
    c.http_pool_max = server["http_pool_max"]
    c.allowed_hosts = tuple(h.lower() for h in server["allowed_hosts"])

    c.allowed_ws_origins = set(ws["allowed_origins"])
    c.ws_heartbeat_interval = ws["heartbeat_interval"]
    c.ws_stale_after = ws["stale_after"]
    c.ws_reconnect = dict(ws["reconnect"])
    c.ws_unreachable = dict(ws["unreachable"])
    c.ws_max_message_bytes = ws["max_message_bytes"]
    c.ws_sync_prefs_per_minute = ws["sync_prefs_per_minute"]
    # Read by main.server_options() when uvicorn starts; a change needs a restart
    c.ws_ping_interval = ws["ping_interval"]
    c.ws_ping_timeout = ws["ping_timeout"]

    notif = new_app_cfg["notifications"]
    c.notif_enabled = notif["enabled"]
    c.notif_webhook_timeout = notif["webhook_timeout"]
    c.notif_push_ttl = notif["push_ttl"]
    c.notif_events = {key: notif["events"][key] for key in _NOTIFICATION_EVENTS}
    c.notif_degraded_tps_tier = notif["degraded_tps_tier"]
    c.notif_degraded_ttft_tier = notif["degraded_ttft_tier"]
    in_app = notif["in_app"]
    c.notif_in_app_enabled = in_app["enabled"]
    c.notif_in_app_toast_ms = in_app["toast_duration_ms"]
    c.notif_in_app_history_size = in_app["history_size"]
    c.notif_in_app_retention_days = in_app["retention_days"]
    c.notif_in_app_api_response_cap = in_app["api_response_cap"]
    rate_limits = notif["rate_limits"]
    c.notif_rate_limit_prefs = rate_limits["prefs_per_minute"]
    c.notif_rate_limit_push_test = rate_limits["push_test_per_minute"]
    c.notif_rate_limit_subscribe = rate_limits["subscribe_per_minute"]
    c.notif_rate_limit_validate = rate_limits["validate_per_minute"]
    c.notif_rate_limit_client_error = rate_limits["client_error_per_minute"]
    c.notif_webhooks = notif["webhooks"]

    c.color_thresholds = new_app_cfg["color_thresholds"]

    # Composite scores config (required - validated by _validate_config)
    scores = new_app_cfg["scores"]
    consistency = scores["consistency"]
    c.scores_consistency_weights = consistency["weights"]
    speed = scores["speed"]
    c.scores_speed_weights = speed["weights"]
    reliability = scores["reliability"]
    c.scores_reliability_avail_weight = reliability["availability_weight"]
    c.scores_reliability_quality_weight = reliability["quality_weight"]

    # Time ranges for modal chart views - auto-compute seconds from key
    raw_ranges = new_app_cfg["time_ranges"]
    c.time_ranges = []
    for r in raw_ranges:
        entry = dict(r)
        if 'seconds' not in entry or entry.get('seconds') is None:
            entry['seconds'] = _parse_duration(entry['key'], raise_on_invalid=False)
        c.time_ranges.append(entry)

    model_registry.clear()
    model_registry.extend(build_model_registry())
    from backend.models import _rebuild_registry_index
    _rebuild_registry_index()
    st.invalidate_providers_cache()
    st.invalidate_metrics_cache()
    st.invalidate_model_info_response_cache()

    # Cross-validate stagger: each model needs >= 2.5 min (150s) in the interval
    if c.benchmark_stagger and model_registry:
        n_models = len(model_registry)
        min_interval = n_models * 150
        if c.benchmark_interval < min_interval:
            raise ValueError(
                f"app.yaml: benchmark interval ({c.benchmark_interval}s) too short for stagger "
                f"with {n_models} models - need >= {min_interval}s "
                f"({n_models} models × 2.5 min each). "
                f"Increase testing.benchmark.interval or disable stagger."
            )

    # Reset scheduler semaphores so new concurrency limits take effect
    import backend.scheduler as _sched
    _sched._global_sem = None
    _sched._provider_sems.clear()
    st.reset_http_client()

    new_model_ids = {e["id"] for e in model_registry}
    added = new_model_ids - old_model_ids

    # Detect removed models for cache cleanup + notifications. Two sources:
    #  - registry diff (old_model_ids - new_model_ids): catches ALL removals,
    #    including models removed between server runs (model_cache is empty at
    #    startup, so cache-only detection would miss them and leave orphaned
    #    SQLite data that resurfaces with old stats if the model is re-added).
    #  - cache sweep: defensively catches leftover model_cache entries if a
    #    prior reload crashed after rebuilding the registry but before popping.
    stale = (old_model_ids - new_model_ids) | {k for k in model_cache if k not in new_model_ids}
    if stale:
        for k in stale:
            model_cache.pop(k, None)  # no-op if not in cache
        st.update_healthy_model_count()
        st.invalidate_metrics_cache()
        st.invalidate_providers_cache()
        st.invalidate_model_info_response_cache()
        if log_changes or not old_model_ids:
            log.info("Cleaned up metrics for removed models: %s", stale)

    # Invalidate per-model score caches when history retention changes
    if c.recent_history_seconds != old_recent_history_seconds and model_cache:
        from backend.db import _effective_history_cap
        from backend.stats import compute_trends, bench_only
        cap = _effective_history_cap()
        cutoff = time.time() - c.recent_history_seconds
        for entry in model_cache.values():
            entry["_scores_version"] = entry.get("_scores_version", 0) + 1
            entry["_cached_scores"] = None
            rh = entry.get("recent_history")
            if rh:
                if cap and len(rh) > cap:
                    rh = rh[-cap:]
                # Trim records older than the configured window
                rh = [r for r in rh if (r.get("ts_epoch") or r.get("_ts_epoch") or 0) >= cutoff]
                entry["recent_history"] = rh
                # Recompute trends from the trimmed benchmark data
                bench_rh = bench_only(rh)
                if bench_rh:
                    entry["trends"] = compute_trends(bench_rh)
        st.invalidate_metrics_cache()

    if log_changes:
        parts = []
        if added:
            parts.append(f"+models: {added}")
        if stale:
            parts.append(f"-models: {stale}")
        # Diff all c attributes for changed values
        _SKIP_DIFF = {"notif_events", "notif_webhooks", "allowed_ws_origins", "allowed_hosts", "color_thresholds",
                      "benchmark_prompt_suffix", "health_prompts"}
        for k, new_v in c.__dict__.items():
            if k.startswith("_") or k in _SKIP_DIFF:
                continue
            old_v = old_c.get(k)
            if old_v != new_v:
                parts.append(f"{k}: {old_v} → {new_v}")
        if parts:
            log.info("Config reloaded: %s", ", ".join(parts))
        else:
            log.info("Config reloaded: no runtime changes")

    current_provider_names = {e.get("name") for e in models_cfg.get("providers", [])}
    return {"added": list(added), "removed": list(stale), "old_provider_names": old_provider_names, "current_provider_names": current_provider_names, "old_model_names": old_model_names, "reset_keys": reset_keys}


_RESET_EPOCH_RE = re.compile(r"^\s*reset_epoch:\s*true\s*(?:#.*)?$", re.MULTILINE)


def _strip_reset_epoch_from_yaml():
    """Remove reset_epoch lines from the loaded models file in-place (preserves file permissions/inode)."""
    path = config_path("models.yaml")
    with open(path, 'r+') as f:
        text = f.read()
        new_text = _RESET_EPOCH_RE.sub("", text)
        if new_text != text:
            f.seek(0)
            f.write(new_text)
            f.truncate()
            log.info("Stripped reset_epoch from %s", path)


def apply_reset_epochs(reset_keys: set[str]):
    """Reset benchmark/health/audit/probe epochs for the given model keys, forcing immediate retest.

    Called both during hot-reload (from apply_db_changes) and startup
    (from _startup, after model_cache is populated).
    """
    if not reset_keys:
        return
    for mk in reset_keys:
        entry = model_cache.get(mk)
        if entry:
            entry["last_benchmark_epoch"] = None
            entry["last_health_epoch"] = None
            entry["last_audit_epoch"] = None
            entry["last_probe_epoch"] = None
    st.invalidate_metrics_cache()
    log.info("reset_epoch: forcing retest for %d model(s): %s", len(reset_keys), reset_keys)


async def apply_db_changes(result: dict):
    """Apply pending db changes from reload_config() via asyncio.to_thread().

    Must be called after reload_config() from async context to ensure
    all SQLite writes happen on the thread executor, not the event loop.

    Also triggers targeted model-info fetch for newly added models.
    """
    import backend.db as db
    from backend.model_info import clear_hf_cache
    added = result.get("added", [])
    removed = result.get("removed", [])

    if added or removed:
        clear_hf_cache()

    try:
        # Comprehensive cleanup: delete ALL data for models/providers not in
        # the current registry.  Catches both hot-reload removals and startup
        # orphans (models removed between server runs whose SQLite data was
        # never cleaned because model_cache was empty at startup).  Runs even
        # when reload_config() detected no diff, so orphans are always purged.
        reg_keys = {e["id"] for e in model_registry}
        reg_providers = {p.get("name") for p in models_cfg.get("providers", [])}
        await asyncio.to_thread(db.delete_removed_entries, reg_keys, reg_providers)
        if model_registry:
            await asyncio.to_thread(db.batch_sync_registry, list(model_registry), models_cfg.get("providers", []))

        # Reconcile archived state: YAML directives are force-applied to DB,
        # then the full archived set is loaded back into memory.
        #   archived: true  → force archive in DB
        #   archived: false → force unarchive in DB
        #   absent          → preserve existing DB state
        archive_true = {e["id"] for e in model_registry if e.get("archived") is True}
        archive_false = {e["id"] for e in model_registry if e.get("archived") is False}
        if archive_true:
            await asyncio.to_thread(db.set_archived, archive_true, True)
        if archive_false:
            await asyncio.to_thread(db.set_archived, archive_false, False)
        # Prune trailing failures for newly archived models only (skip
        # already-archived ones to avoid repeated no-op scans on every reload)
        newly_archived = archive_true - st._archived_model_keys
        if newly_archived:
            await asyncio.to_thread(db.prune_trailing_failures, newly_archived)
        loaded = await asyncio.to_thread(db.load_all_archived)
        st._archived_model_keys.clear()
        st._archived_model_keys.update(loaded)
        st.invalidate_metrics_cache()
        st.invalidate_providers_cache()
    except Exception as e:
        log_error("DB sync failed during config reload", e)

    if added and not st.TESTS_DISABLED:
        try:
            from backend import model_info
            st.create_task(
                model_info.fetch_model_info_for_keys(added),
                name="model_info_new_models"
            )
        except Exception as e:
            log_error("Model-info fetch start failed", e)

    if added or removed:
        try:
            from backend.notifications import notify_registry_changes
            old_provider_names = result.get("old_provider_names", set())
            current_provider_names = result.get("current_provider_names", set())
            old_model_names = result.get("old_model_names", {})
            await notify_registry_changes(added, removed, old_provider_names, current_provider_names, old_model_names)
        except Exception as e:
            log_error("Registry change notification failed", e)

    reset_keys = result.get("reset_keys", set())
    if reset_keys:
        apply_reset_epochs(reset_keys)
        _strip_reset_epoch_from_yaml()
        if st._wake_event:
            st._wake_event.set()


_MAX_WATCHER_RESTARTS = 5


async def config_watcher():
    """Watch the loaded config files, hot-reload, and broadcast a WS update.

    Runs with MW_DISABLE_TESTS too (config reloads are not tests); only the provider
    fetches a reload triggers are skipped then.
    """
    files = {config_path(name).resolve() for name in _CONFIG_ENV}
    crash_count = 0
    while crash_count < _MAX_WATCHER_RESTARTS and not st._shutting_down:
        try:
            async for changes in awatch(*sorted({str(f.parent) for f in files}),
                                        watch_filter=lambda _change, path: Path(path).resolve() in files):
                changed_files = [os.path.basename(p) for _, p in changes]
                log.info("Config files changed: %s", changed_files)
                try:
                    result = reload_config(log_changes=True)
                    await apply_db_changes(result)
                    from backend.routes import _config_cache
                    _config_cache["expires"] = 0
                    await ws_mgr.broadcast({"type": "config_updated"})
                    if st.config_changed:
                        st.config_changed.set()
                    if st._wake_event:
                        st._wake_event.set()
                    if not st.TESTS_DISABLED:
                        from backend import favicons
                        favicons.start_favicon_fetch()
                        from backend import model_info as _mi
                        _mi.start_model_info_fetch()
                except ValueError as e:
                    log_error("Config validation failed during hot-reload - shutting down", e)
                    raise SystemExit(f"FATAL: invalid config: {e}")
            # awatch exited cleanly (shouldn't happen in normal operation)
            log.warning("Config watcher: awatch exited unexpectedly, restarting")
            crash_count = 0
        except Exception as e:
            crash_count += 1
            log_error(f"Config watcher crashed (attempt {crash_count}/{_MAX_WATCHER_RESTARTS})", e)
            if crash_count < _MAX_WATCHER_RESTARTS and not st._shutting_down:
                await asyncio.sleep(min(30, 2 ** crash_count))
    if crash_count >= _MAX_WATCHER_RESTARTS:
        log_error("Config watcher terminated - exceeded %d restarts" % _MAX_WATCHER_RESTARTS)
