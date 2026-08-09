"""YAML config loading, env var resolution, hot-reload, and config watcher."""

import asyncio
import math
import os
import re
import time

import yaml

from backend.state import (
    c, app_cfg, models_cfg, model_registry, model_cache, log, log_error,
    apply_log_level,
    CONFIG_DIR, awatch, Change, ensure_scheme,
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


def _load_yaml(name: str, *, required: bool = True) -> dict:
    """Load a YAML config file with env var resolution. Returns {} if not required and missing."""
    env_map = {"models.yaml": "MW_MODELS_YAML", "app.yaml": "MW_APP_YAML", "audits.yaml": "MW_AUDITS_YAML"}
    env_name = os.environ.get(env_map.get(name, ""))
    if env_name:
        p = CONFIG_DIR / env_name
        if not p.exists():
            raise FileNotFoundError(f"Env override for {name} not found: {p}")
        with open(p) as f:
            data = yaml.safe_load(f) or {}
        return _resolve_env_vars(data)
    p = CONFIG_DIR / name
    if not p.exists():
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


def _validate_string_list(path: str, value, *, min_len: int = 0, prefix: str = "app.yaml:"):
    if not isinstance(value, list):
        raise ValueError(f"{prefix} {path} must be a list")
    if len(value) < min_len:
        raise ValueError(f"{prefix} {path} must contain at least {min_len} item(s)")
    for idx, item in enumerate(value):
        _validate_string(f"{path}[{idx}]", item, prefix=prefix)


def _validate_config(cfg: dict):
    """Validate app.yaml structure - key presence, value types, and ranges.

    Raises ValueError with a descriptive message on the first problem found.
    Called after loading so the app fails fast with a clear error.
    """
    _REQUIRED_SECTIONS = {
        "app", "testing", "metrics", "stalls", "server", "websocket",
        "notifications", "color_thresholds", "scores", "time_ranges",
        "auto_archive",
    }
    missing = _REQUIRED_SECTIONS - set(cfg)
    if missing:
        raise ValueError(f"app.yaml: missing required sections: {', '.join(sorted(missing))}")
    for section in _REQUIRED_SECTIONS - {"time_ranges"}:
        _validate_mapping(section, cfg[section])

    app = cfg["app"]
    for key in ("name", "site_url", "vapid_email", "log_level", "static_url_prefix", "description", "debug"):
        if key not in app:
            raise ValueError(f"app.yaml: app.{key} is required")
        if key in ("description", "static_url_prefix"):
            _validate_string(f"app.{key}", app[key], allow_empty=True)
        elif key == "debug":
            _validate_bool(f"app.{key}", app[key])
        else:
            _validate_string(f"app.{key}", app[key])

    testing = cfg["testing"]
    for key in ("max_retries", "initial_delay", "retry_delay", "stream_activity_timeout", "timeout", "max_concurrent_tests"):
        if key not in testing:
            raise ValueError(f"app.yaml: testing.{key} is required")
    _validate_int("testing.max_retries", testing["max_retries"], min_value=0)
    _validate_number("testing.initial_delay", testing["initial_delay"], min_value=0)
    _validate_number("testing.retry_delay", testing["retry_delay"], min_value=0)
    _validate_number("testing.stream_activity_timeout", testing["stream_activity_timeout"], min_value=0, inclusive=False)
    _validate_number("testing.timeout", testing["timeout"], min_value=10)
    _validate_int("testing.max_concurrent_tests", testing["max_concurrent_tests"], min_value=1)
    if "benchmark" not in testing:
        raise ValueError("app.yaml: testing.benchmark is required")
    if "health_check" not in testing:
        raise ValueError("app.yaml: testing.health_check is required")

    bench = testing["benchmark"]
    _validate_mapping("testing.benchmark", bench)
    for key in ("interval", "target_total_tokens", "min_tokens", "min_chunks", "stagger", "prompts"):
        if key not in bench:
            raise ValueError(f"app.yaml: testing.benchmark.{key} is required")
    _validate_int("testing.benchmark.interval", bench["interval"], min_value=60)
    _validate_int("testing.benchmark.target_total_tokens", bench["target_total_tokens"], min_value=1)
    _validate_int("testing.benchmark.min_tokens", bench["min_tokens"], min_value=0)
    _validate_int("testing.benchmark.min_chunks", bench["min_chunks"], min_value=0)
    _validate_bool("testing.benchmark.stagger", bench["stagger"])
    thinking_budget = bench.get("anthropic_thinking_budget")
    if thinking_budget is not None:
        _validate_int("testing.benchmark.anthropic_thinking_budget", thinking_budget, min_value=0)
    prompts = bench["prompts"]
    _validate_mapping("testing.benchmark.prompts", prompts)
    if "suffix" not in prompts:
        raise ValueError("app.yaml: testing.benchmark.prompts.suffix is required")
    _validate_string("testing.benchmark.prompts.suffix", prompts["suffix"], allow_empty=True)

    health = testing["health_check"]
    _validate_mapping("testing.health_check", health)
    for key in ("enabled", "interval", "max_tokens", "prompts"):
        if key not in health:
            raise ValueError(f"app.yaml: testing.health_check.{key} is required")
    _validate_bool("testing.health_check.enabled", health["enabled"])
    _validate_int("testing.health_check.interval", health["interval"], min_value=1)
    _validate_int("testing.health_check.max_tokens", health["max_tokens"], min_value=1)
    _validate_string_list("testing.health_check.prompts", health["prompts"], min_len=1)

    if "probe" not in testing:
        raise ValueError("app.yaml: testing.probe is required")
    probe = testing["probe"]
    _validate_mapping("testing.probe", probe)
    for key in ("enabled", "interval", "max_tokens"):
        if key not in probe:
            raise ValueError(f"app.yaml: testing.probe.{key} is required")
    _validate_bool("testing.probe.enabled", probe["enabled"])
    _validate_int("testing.probe.interval", probe["interval"], min_value=60)
    _validate_int("testing.probe.max_tokens", probe["max_tokens"], min_value=1)

    audit = testing.get("audit")
    if audit is not None:
        raise ValueError("app.yaml: testing.audit is no longer supported - move audit config to audits.yaml")

    metrics = cfg["metrics"]
    for key in (
        "retention_days", "uptime_window", "recent_history", "min_data_points_score",
        "min_data_points_trend", "history_query_limit", "provider_fetch_ttl",
        "cleanup_interval",
        "write_batch_interval", "write_batch_max_buffer",
    ):
        if key not in metrics:
            raise ValueError(f"app.yaml: metrics.{key} is required")
    _validate_int("metrics.retention_days", metrics["retention_days"], min_value=1)
    _validate_number("metrics.uptime_window", metrics["uptime_window"], min_value=0, inclusive=False)
    _validate_int("metrics.min_data_points_score", metrics["min_data_points_score"], min_value=1)
    _validate_int("metrics.min_data_points_trend", metrics["min_data_points_trend"], min_value=1)
    _validate_int("metrics.history_query_limit", metrics["history_query_limit"], min_value=1)
    _validate_number("metrics.provider_fetch_ttl", metrics["provider_fetch_ttl"], min_value=0)
    _validate_int("metrics.cleanup_interval", metrics["cleanup_interval"], min_value=60)
    _validate_number("metrics.write_batch_interval", metrics["write_batch_interval"], min_value=0.1)
    _validate_int("metrics.write_batch_max_buffer", metrics["write_batch_max_buffer"], min_value=1)
    if isinstance(metrics["recent_history"], bool):
        raise ValueError("app.yaml: metrics.recent_history must be a duration, not a boolean")
    try:
        recent_history_seconds = _parse_duration(metrics["recent_history"])
    except ValueError as e:
        raise ValueError(f"app.yaml: metrics.recent_history is invalid: {e}") from e
    if recent_history_seconds <= 0:
        raise ValueError("app.yaml: metrics.recent_history must be > 0 seconds")

    stalls = cfg["stalls"]
    for key in ("visible_threshold_ms", "hiccup_threshold_ms", "hiccup_multiplier", "batching_log_threshold"):
        if key not in stalls:
            raise ValueError(f"app.yaml: stalls.{key} is required")
        _validate_number(f"stalls.{key}", stalls[key], min_value=0, inclusive=False)

    aa = cfg["auto_archive"]
    _validate_mapping("auto_archive", aa)
    for key in ("enabled", "offline_duration"):
        if key not in aa:
            raise ValueError(f"app.yaml: auto_archive.{key} is required")
    _validate_bool("auto_archive.enabled", aa["enabled"])
    if isinstance(aa["offline_duration"], bool):
        raise ValueError("app.yaml: auto_archive.offline_duration must be a duration, not a boolean")
    try:
        aa_seconds = _parse_duration(aa["offline_duration"])
    except ValueError as e:
        raise ValueError(f"app.yaml: auto_archive.offline_duration is invalid: {e}") from e
    if aa_seconds <= 0:
        raise ValueError("app.yaml: auto_archive.offline_duration must be > 0 seconds")

    server = cfg["server"]
    for key in ("max_connections", "http_connect_timeout", "http_pool_max"):
        if key not in server:
            raise ValueError(f"app.yaml: server.{key} is required")
    _validate_int("server.max_connections", server["max_connections"], min_value=1)
    _validate_number("server.http_connect_timeout", server["http_connect_timeout"], min_value=0, inclusive=False)
    _validate_int("server.http_pool_max", server["http_pool_max"], min_value=1)

    ws = cfg["websocket"]
    if "allowed_origins" not in ws:
        raise ValueError("app.yaml: websocket.allowed_origins is required")
    _validate_string_list("websocket.allowed_origins", ws["allowed_origins"])

    notif = cfg["notifications"]
    for key in ("enabled", "webhook_timeout", "push_ttl", "events", "in_app", "rate_limits"):
        if key not in notif:
            raise ValueError(f"app.yaml: notifications.{key} is required")
    _validate_bool("notifications.enabled", notif["enabled"])
    _validate_number("notifications.webhook_timeout", notif["webhook_timeout"], min_value=0, inclusive=False)
    _validate_int("notifications.push_ttl", notif["push_ttl"], min_value=0)
    events = notif["events"]
    _validate_mapping("notifications.events", events)
    for key in ("offline", "recovered", "degraded", "degraded_tps", "degraded_ttft", "recovered_tps", "recovered_ttft", "provider_changed", "model_changed"):
        if key not in events:
            raise ValueError(f"app.yaml: notifications.events.{key} is required")
        _validate_bool(f"notifications.events.{key}", events[key])
    for key in ("degraded_tps_tier", "degraded_ttft_tier"):
        if key not in notif:
            raise ValueError(f"app.yaml: notifications.{key} is required")
        _validate_int(f"notifications.{key}", notif[key], min_value=0)
    in_app = notif["in_app"]
    _validate_mapping("notifications.in_app", in_app)
    for key in ("enabled", "toast_duration_ms", "history_size", "retention_days", "api_response_cap"):
        if key not in in_app:
            raise ValueError(f"app.yaml: notifications.in_app.{key} is required")
    _validate_bool("notifications.in_app.enabled", in_app["enabled"])
    _validate_int("notifications.in_app.toast_duration_ms", in_app["toast_duration_ms"], min_value=0)
    _validate_int("notifications.in_app.history_size", in_app["history_size"], min_value=1)
    _validate_int("notifications.in_app.retention_days", in_app["retention_days"], min_value=1)
    _validate_int("notifications.in_app.api_response_cap", in_app["api_response_cap"], min_value=1)
    rate_limits = notif["rate_limits"]
    _validate_mapping("notifications.rate_limits", rate_limits)
    for key in ("prefs_per_minute", "push_test_per_minute", "subscribe_per_minute", "validate_per_minute", "client_error_per_minute"):
        if key not in rate_limits:
            raise ValueError(f"app.yaml: notifications.rate_limits.{key} is required")
        _validate_int(f"notifications.rate_limits.{key}", rate_limits[key], min_value=1)
    if "webhooks" not in notif:
        raise ValueError("app.yaml: notifications.webhooks is required")
    webhooks = notif["webhooks"]
    if not isinstance(webhooks, list):
        raise ValueError("app.yaml: notifications.webhooks must be a list")
    for idx, webhook in enumerate(webhooks):
            _validate_mapping(f"notifications.webhooks[{idx}]", webhook)
            if "url" not in webhook:
                raise ValueError(f"app.yaml: notifications.webhooks[{idx}].url is required")
            _validate_string(f"notifications.webhooks[{idx}].url", webhook["url"])
            if "name" in webhook:
                _validate_string(f"notifications.webhooks[{idx}].name", webhook["name"])
            if "secret" in webhook:
                _validate_string(f"notifications.webhooks[{idx}].secret", webhook["secret"], allow_empty=True)

    ct = cfg["color_thresholds"]
    if "tiers" not in ct:
        raise ValueError("app.yaml: color_thresholds.tiers is required")
    tiers = ct["tiers"]
    if not isinstance(tiers, list) or len(tiers) < 2:
        raise ValueError("app.yaml: color_thresholds.tiers must contain at least 2 tiers")
    for idx, tier in enumerate(tiers):
        _validate_mapping(f"color_thresholds.tiers[{idx}]", tier)
        for key in ("label", "color"):
            if key not in tier:
                raise ValueError(f"app.yaml: color_thresholds.tiers[{idx}].{key} is required")
            _validate_string(f"color_thresholds.tiers[{idx}].{key}", tier[key])
    max_tier_idx = len(tiers) - 1
    if notif["degraded_tps_tier"] > max_tier_idx or notif["degraded_ttft_tier"] > max_tier_idx:
        raise ValueError(f"app.yaml: notification degraded tier indexes must be between 0 and {max_tier_idx}")
    for metric in (
        "uptime", "tps", "ttft", "stall_count", "raw_p99_itl_ms", "raw_median_itl_ms",
        "raw_max_itl_ms", "effective_itl_tail_ratio", "chunk_token_ratio", "burst_arrival_pct",
        "chunk_token_cv",
    ):
        if metric not in ct:
            raise ValueError(f"app.yaml: color_thresholds.{metric} is required")
        metric_cfg = ct[metric]
        _validate_mapping(f"color_thresholds.{metric}", metric_cfg)
        for key in ("higher_is_better", "thresholds"):
            if key not in metric_cfg:
                raise ValueError(f"app.yaml: color_thresholds.{metric}.{key} is required")
        _validate_bool(f"color_thresholds.{metric}.higher_is_better", metric_cfg["higher_is_better"])
        thresholds = metric_cfg["thresholds"]
        if not isinstance(thresholds, list) or len(thresholds) != len(tiers):
            raise ValueError(f"app.yaml: color_thresholds.{metric}.thresholds must have {len(tiers)} values")
        for idx, threshold in enumerate(thresholds):
            _validate_number(f"color_thresholds.{metric}.thresholds[{idx}]", threshold)

    scores = cfg["scores"]
    _validate_mapping("scores", scores)
    for section_name in ("consistency", "speed"):
        if section_name not in scores:
            raise ValueError(f"app.yaml: scores.{section_name} is required")
        section = scores[section_name]
        _validate_mapping(f"scores.{section_name}", section)
        if "weights" not in section:
            raise ValueError(f"app.yaml: scores.{section_name}.weights is required")
        weights = section["weights"]
        _validate_mapping(f"scores.{section_name}.weights", weights)
        if not weights:
            raise ValueError(f"app.yaml: scores.{section_name}.weights must not be empty")
        for key, weight in weights.items():
            _validate_string(f"scores.{section_name}.weights key", key)
            _validate_number(f"scores.{section_name}.weights.{key}", weight, min_value=0)
    if "reliability" not in scores:
        raise ValueError("app.yaml: scores.reliability is required")
    reliability = scores["reliability"]
    _validate_mapping("scores.reliability", reliability)
    for key in ("availability_weight", "quality_weight"):
        if key not in reliability:
            raise ValueError(f"app.yaml: scores.reliability.{key} is required")
        _validate_number(f"scores.reliability.{key}", reliability[key], min_value=0)
    if reliability["availability_weight"] + reliability["quality_weight"] <= 0:
        raise ValueError("app.yaml: scores.reliability weights must sum to more than 0")

    time_ranges = cfg["time_ranges"]
    if not isinstance(time_ranges, list) or not time_ranges:
        raise ValueError("app.yaml: time_ranges must be a non-empty list")
    for idx, entry in enumerate(time_ranges):
        _validate_mapping(f"time_ranges[{idx}]", entry)
        for key in ("key", "label"):
            if key not in entry:
                raise ValueError(f"app.yaml: time_ranges[{idx}].{key} is required")
            _validate_string(f"time_ranges[{idx}].{key}", entry[key])
        seconds = _parse_duration(entry["key"], raise_on_invalid=False)
        if seconds is None or seconds <= 0:
            raise ValueError(f"app.yaml: time_ranges[{idx}].key must be a positive duration")
        if "seconds" in entry and entry["seconds"] is not None:
            _validate_int(f"time_ranges[{idx}].seconds", entry["seconds"], min_value=1)


def _validate_audits_cfg(cfg: dict):
    """Validate audits.yaml structure."""
    if not cfg:
        return
    if not isinstance(cfg, dict):
        raise ValueError("audits.yaml: root must be a mapping")
    for key in ("enabled", "interval"):
        if key not in cfg:
            raise ValueError(f"audits.yaml: audit.{key} is required when audit config is present")
    _validate_bool("audits.audit.enabled", cfg["enabled"], prefix="audits.yaml:")
    _validate_int("audits.audit.interval", cfg["interval"], min_value=60, prefix="audits.yaml:")
    suites = cfg.get("suites")
    if suites is not None:
        _validate_mapping("audits.audit.suites", suites, prefix="audits.yaml:")
        for suite_name, suite_cfg in suites.items():
            _validate_mapping(f"audits.audit.suites.{suite_name}", suite_cfg, prefix="audits.yaml:")
            for key in ("enabled", "stream", "count", "url"):
                if key not in suite_cfg:
                    raise ValueError(f"audits.audit.suites.{suite_name}.{key} is required")
            _validate_bool(f"audits.audit.suites.{suite_name}.enabled", suite_cfg["enabled"], prefix="audits.yaml:")
            _validate_bool(f"audits.audit.suites.{suite_name}.stream", suite_cfg["stream"], prefix="audits.yaml:")
            _validate_int(f"audits.audit.suites.{suite_name}.count", suite_cfg["count"], min_value=1, prefix="audits.yaml:")
            _validate_string(f"audits.audit.suites.{suite_name}.url", suite_cfg["url"], prefix="audits.yaml:")
            if "skip_reasoning" in suite_cfg:
                _validate_bool(f"audits.audit.suites.{suite_name}.skip_reasoning", suite_cfg["skip_reasoning"], prefix="audits.yaml:")
            if "reasoning_effort" in suite_cfg and suite_cfg["reasoning_effort"] is not None:
                if suite_cfg["reasoning_effort"] not in ("low", "medium", "high"):
                    raise ValueError(f"audits.audit.suites.{suite_name}.reasoning_effort must be low/medium/high or null")
            if "only" in suite_cfg and suite_cfg["only"] is not None:
                _validate_string(f"audits.audit.suites.{suite_name}.only", suite_cfg["only"], prefix="audits.yaml:")


def _validate_models_cfg(cfg: dict):
    """Validate models.yaml structure - provider/model field presence, types, and uniqueness."""
    if not isinstance(cfg, dict):
        raise ValueError("models.yaml: root must be a mapping")
    providers = cfg.get("providers")
    if not isinstance(providers, list):
        raise ValueError("models.yaml: providers must be a list")
    if not providers:
        raise ValueError("models.yaml: providers must contain at least one provider")
    seen_provider_names: set[str] = set()
    for idx, provider in enumerate(providers):
        _validate_mapping(f"models.providers[{idx}]", provider, prefix="models.yaml:")
        for key in ("name", "api_url"):
            if key not in provider:
                raise ValueError(f"models.yaml: providers[{idx}].{key} is required")
            _validate_string(f"models.providers[{idx}].{key}", provider[key], prefix="models.yaml:")
        p_name = provider["name"]
        if p_name in seen_provider_names:
            raise ValueError(f"models.yaml: duplicate provider name {p_name!r} - provider names must be unique")
        seen_provider_names.add(p_name)
        if "api_key" in provider:
            _validate_string(f"models.providers[{idx}].api_key", provider["api_key"], allow_empty=True, prefix="models.yaml:")
        models = provider.get("models")
        if not isinstance(models, list) or not models:
            raise ValueError(f"models.yaml: providers[{idx}].models must be a non-empty list")
        seen_model_ids: set[str] = set()
        for midx, m in enumerate(models):
            _validate_mapping(f"models.providers[{idx}].models[{midx}]", m, prefix="models.yaml:")
            if "id" not in m:
                raise ValueError(f"models.yaml: providers[{idx}].models[{midx}].id is required")
            _validate_string(f"models.providers[{idx}].models[{midx}].id", m["id"], prefix="models.yaml:")
            m_id = m["id"]
            if m_id in seen_model_ids:
                raise ValueError(f"models.yaml: duplicate model id {m_id!r} in provider {p_name!r}")
            seen_model_ids.add(m_id)
            if "name" in m:
                _validate_string(f"models.providers[{idx}].models[{midx}].name", m["name"], prefix="models.yaml:")


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
                reset_keys.add(f"{p_name}::{m['id']}")
        for m in provider["models"]:
            if m.pop("reset_epoch", None) is True:
                reset_keys.add(f"{p_name}::{m['id']}")

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

    c.allowed_ws_origins = set(ws["allowed_origins"])

    notif = new_app_cfg["notifications"]
    c.notif_enabled = notif["enabled"]
    c.notif_webhook_timeout = notif["webhook_timeout"]
    c.notif_push_ttl = notif["push_ttl"]
    notif_events = notif["events"]
    c.notif_events = {
        "offline": notif_events["offline"],
        "recovered": notif_events["recovered"],
        "degraded": notif_events["degraded"],
        "degraded_tps": notif_events["degraded_tps"],
        "degraded_ttft": notif_events["degraded_ttft"],
        "recovered_tps": notif_events["recovered_tps"],
        "recovered_ttft": notif_events["recovered_ttft"],
        "provider_changed": notif_events["provider_changed"],
        "model_changed": notif_events["model_changed"],
    }
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
        _SKIP_DIFF = {"notif_events", "notif_webhooks", "allowed_ws_origins", "color_thresholds",
                      "benchmark_prompt_suffix", "health_prompts"}
        for k, new_v in c.__dict__.items():
            if k.startswith("_") or k in _SKIP_DIFF:
                continue
            old_v = old_c.get(k)
            try:
                changed = old_v != new_v
            except TypeError:
                continue
            if changed:
                parts.append(f"{k}: {old_v} → {new_v}")
        if parts:
            log.info("Config reloaded: %s", ", ".join(parts))
        else:
            log.info("Config reloaded: no runtime changes")

    current_provider_names = {e.get("name") for e in models_cfg.get("providers", [])}
    return {"added": list(added), "removed": list(stale), "old_provider_names": old_provider_names, "current_provider_names": current_provider_names, "old_model_names": old_model_names, "reset_keys": reset_keys}


def _yaml_filter(change: Change, path: str) -> bool:
    """watchfiles filter - only watch .yaml/.yml files."""
    return path.endswith((".yaml", ".yml"))


_RESET_EPOCH_RE = re.compile(r"^\s*reset_epoch:\s*true\s*(?:#.*)?$", re.MULTILINE)


def _strip_reset_epoch_from_yaml():
    """Remove reset_epoch lines from models.yaml in-place (preserves file permissions/inode)."""
    path = CONFIG_DIR / "models.yaml"
    with open(path, 'r+') as f:
        text = f.read()
        new_text = _RESET_EPOCH_RE.sub("", text)
        if new_text != text:
            f.seek(0)
            f.write(new_text)
            f.truncate()
            log.info("Stripped reset_epoch from models.yaml")


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

        # Reconcile archived state: the YAML directive controls MANUAL archives
        # (archived_by='manual'); auto-archive rows are runtime-managed and are
        # NOT cleared by config reloads.  Full archived set is loaded back into
        # memory afterwards.
        #   archived: true  → force archive (archived_by='manual')
        #   archived: false → force unarchive (any source - the escape hatch
        #                     for re-enabling auto-archived models)
        #   absent          → clear a MANUAL archive only, so removing the
        #                     directive re-enables the model and auto-archive
        #                     may take over again if it stays offline
        current = await asyncio.to_thread(db.load_all_archived)
        archive_true = {e["id"] for e in model_registry if e.get("archived") is True}
        archive_false = {e["id"] for e in model_registry if e.get("archived") is False}
        stale_manual = {k for k, src in current.items() if src == "manual"} - archive_true - archive_false
        if stale_manual:
            await asyncio.to_thread(db.set_archived, stale_manual, False)
        if archive_true:
            await asyncio.to_thread(db.set_archived, archive_true, True, "manual")
        if archive_false:
            await asyncio.to_thread(db.set_archived, archive_false, False)
        # Prune trailing failures for newly archived models only (skip
        # already-archived ones to avoid repeated no-op scans on every reload)
        newly_archived = archive_true - current.keys()
        if newly_archived:
            await asyncio.to_thread(db.prune_trailing_failures, newly_archived)
        loaded = await asyncio.to_thread(db.load_all_archived)
        st._archived_model_keys.clear()
        st._archived_model_keys.update(loaded)
        st.invalidate_metrics_cache()
        st.invalidate_providers_cache()
    except Exception as e:
        log_error("DB sync failed during config reload", e)

    if added:
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
    """Watch config/ directory for YAML changes, hot-reload, and broadcast WS update."""
    crash_count = 0
    while crash_count < _MAX_WATCHER_RESTARTS and not st._shutting_down:
        try:
            async for changes in awatch(str(CONFIG_DIR), watch_filter=_yaml_filter):
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
