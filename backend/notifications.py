"""Notification dispatch, degradation detection, and in-app notification history.

Push subscription routes and push internals live in backend.push_routes.
This module owns the single delivery filter (should_notify), metric
degradation/recovery detection (TPS/TTFT), and dispatch to push, webhooks,
in-app history, and WebSocket broadcast.
"""

import asyncio
import collections
import hmac
import time
from datetime import datetime, timezone, timedelta

import orjson
from fastapi.responses import JSONResponse

import backend.state as st
import backend.db as db
import backend.db_push as db_push
import backend.push_routes as push_routes
from backend.state import (
    c, log, log_error, push_available, utc_now_iso, parse_model_key,
    EVENT_LABELS, METRIC_LABELS,
)
from backend.push_routes import (
    _is_dead_push_sub, _sub_hash, _log_push_error, _remove_dead_sub,
)
from backend.websocket import ws_mgr
from backend.models import _registry_by_id
from backend.stats import tier_idx


# ── Shared helpers ───────────────────────────────────────────────────────────

_push_subs_cache: list[dict] | None = None
_push_subs_cache_time: float = 0.0


def _get_push_subs() -> list[dict]:
    """Get all push subscriptions, cached for the current dispatch cycle.

    1s TTL avoids redundant DB queries when push and webhooks both need the
    subscriber list within the same dispatch.
    """
    global _push_subs_cache, _push_subs_cache_time
    now = time.monotonic()
    if _push_subs_cache is None or (now - _push_subs_cache_time) > 1.0:
        _push_subs_cache = db_push.all_push_subs()
        _push_subs_cache_time = now
    return _push_subs_cache


def invalidate_push_subs_cache():
    """Invalidate the push subscriptions cache (call after dead sub removal)."""
    global _push_subs_cache
    _push_subs_cache = None


def _attach_degradation(target: dict, degradation: dict | None):
    """Copy degradation details into a target dict (webhook payload or history entry)."""
    if degradation:
        for k, v in degradation.items():
            if k != "event_type" and v is not None:
                target[k] = v


# ── Notification filtering ───────────────────────────────────────────────────

def _provider_matches(allowed_providers: list, model_key: str) -> bool:
    """Check if the model's provider is in the allowed list. Returns True if list is empty."""
    if not allowed_providers:
        return True
    provider, _ = parse_model_key(model_key)
    return not provider or provider in allowed_providers


def _tier_allows_notification(prefs: dict, event_type: str, degradation: dict | None) -> bool:
    if not degradation or event_type not in ("degraded_tps", "degraded_ttft", "recovered_tps", "recovered_ttft"):
        return True
    current_tier = degradation.get("current_tier")
    pref_tier = prefs.get(f"degraded_{event_type.split('_')[1]}_tier")
    if pref_tier is None:
        return True
    try:
        pref_tier = int(pref_tier)
    except (ValueError, TypeError):
        log.debug("Invalid pref_tier: %r", prefs.get(f"degraded_{event_type.split('_')[1]}_tier"))
        return True
    if event_type.startswith("degraded") and current_tier is not None and current_tier < pref_tier:
        log.debug("Tier filter: current=%d < pref=%d event=%s", current_tier, pref_tier, event_type)
        return False
    if event_type.startswith("recovered") and current_tier is not None:
        if current_tier >= pref_tier:
            log.debug("Recovery tier: current=%d >= pref=%d event=%s", current_tier, pref_tier, event_type)
            return False
        prev_tier = degradation.get("prev_tier")
        if prev_tier is not None and prev_tier < pref_tier:
            log.debug("Recovery prev: %d < pref=%d event=%s", prev_tier, pref_tier, event_type)
            return False
    return True


_DEGRADED_METRIC_EVENTS = frozenset({"degraded_tps", "degraded_ttft", "recovered_tps", "recovered_ttft"})

_RECOVERY_EVENT_MAP = {
    "recovered_offline": "recovered_offline",
    "recovered_degraded": "recovered_degraded",
    "partially_recovered": "recovered_degraded",
}


def should_notify(prefs: dict | None, event_type: str, model_key: str, degradation: dict | None = None) -> bool:
    """Single filter for ALL delivery channels (push, WS, in-app history).

    Returns True if this subscriber should receive this notification. None
    prefs (not yet synced from client) means accept all - the client filters
    as defense-in-depth. Applies master toggle, per-event-type toggles,
    provider filters, tier-based degradation filtering, and recovery grounding.
    """
    if prefs is None:
        return True
    if not prefs.get("enabled", True):
        log.debug("should_notify=False: enabled=false event=%s model=%s", event_type, model_key)
        return False
    if event_type in _DEGRADED_METRIC_EVENTS and not prefs.get("degraded", True):
        log.debug("should_notify=False: degraded parent gate event=%s model=%s", event_type, model_key)
        return False
    mapped = _RECOVERY_EVENT_MAP.get(event_type)
    if mapped:
        pref_val = prefs.get(mapped)
        if pref_val is None:
            pref_val = prefs.get("recovered", True)
        if not pref_val:
            log.debug("should_notify=False: %s/recovered disabled event=%s model=%s", mapped, event_type, model_key)
            return False
    else:
        if not prefs.get(event_type, True):
            log.debug("should_notify=False: event_type=%s disabled model=%s", event_type, model_key)
            return False
    providers = prefs.get("providers", [])
    if not isinstance(providers, list):
        providers = []
    if providers and not _provider_matches(providers, model_key):
        log.debug("should_notify=False: provider filter event=%s model=%s", event_type, model_key)
        return False
    if not _tier_allows_notification(prefs, event_type, degradation):
        log.debug("should_notify=False: tier filter event=%s model=%s", event_type, model_key)
        return False
    if event_type in ("recovered", "recovered_offline", "recovered_degraded", "partially_recovered", "recovered_tps", "recovered_ttft"):
        degraded_since = (degradation or {}).get("degraded_since")
        enabled_at = prefs.get("enabled_at")
        if degraded_since and enabled_at:
            try:
                enabled_epoch = datetime.fromisoformat(enabled_at.replace("Z", "+00:00")).timestamp()
                if enabled_epoch > degraded_since:
                    log.debug("should_notify=False: recovery grounding enabled_at=%s > degraded_since=%s event=%s model=%s", enabled_at, degraded_since, event_type, model_key)
                    return False
            except (ValueError, TypeError):
                log.debug("Recovery grounding: invalid enabled_at=%s for model=%s", enabled_at, model_key)
    return True


def effective_degraded_tier(metric: str) -> int:
    """Server-configured degradation threshold tier for a metric.

    Returns the server-level tier index only; per-subscriber filtering is
    applied by should_notify() at delivery time.
    """
    return c.notif_degraded_tps_tier if metric == "tps" else c.notif_degraded_ttft_tier


def should_notify_model(webhook_cfg: dict, model_key: str) -> bool:
    """Check if a webhook's provider/model filters match the given model key."""
    filters = webhook_cfg.get("filters", {})
    if not _provider_matches(filters.get("providers", []), model_key):
        return False
    models = filters.get("models", [])
    if models and model_key not in models:
        return False
    return True


# ── Push sending ─────────────────────────────────────────────────────────────

def _send_push_sync(title: str, body: str, tag: str = "mw-event", event_type: str | None = None, model_key: str | None = None, degradation: dict | None = None):
    """Send a push notification to subscribers whose prefs match. Blocking."""
    if not push_available or not push_routes.vapid_private:
        return
    from backend.state import webpush
    subs = _get_push_subs()
    if not subs:
        return
    payload = orjson.dumps({"title": title, "body": body, "tag": tag, "url": c.site_url}).decode()
    sent = 0
    skipped = 0
    failed = 0
    dead = []
    for sub in subs:
        prefs = sub.get("prefs")
        # Fall back to created_at when enabled_at is absent so recovery grounding still works.
        if prefs and "enabled_at" not in prefs and sub.get("created_at"):
            try:
                prefs = {**prefs, "enabled_at": datetime.fromtimestamp(sub["created_at"], tz=timezone.utc).isoformat()}
            except (ValueError, TypeError, OSError):
                log.debug("Recovery grounding: invalid created_at for fallback enabled_at")
        if not should_notify(prefs, event_type, model_key, degradation):
            skipped += 1
            log.debug("Push skipped for %s: event=%s model=%s",
                      _sub_hash(sub["endpoint"]), event_type, model_key)
            continue
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=push_routes.vapid_private,
                vapid_claims={"sub": c.vapid_email},
                ttl=c.notif_push_ttl,
                timeout=30,
            )
            sent += 1
        except Exception as e:
            failed += 1
            _log_push_error(sub["endpoint"], e)
            if _is_dead_push_sub(e):
                dead.append(sub["endpoint"])
    if dead:
        for ep in dead:
            _remove_dead_sub(ep)
        invalidate_push_subs_cache()
        log.info("Removed %d expired push subscription(s)", len(dead))
    log.debug("Push: %d sent, %d skipped, %d failed of %d subs event=%s model=%s",
              sent, skipped, failed, len(subs),
              event_type, model_key)


async def send_push(title: str, body: str, tag: str = "mw-event", event_type: str | None = None, model_key: str | None = None, degradation: dict | None = None):
    """Send a push notification to subscribers whose prefs match (non-blocking wrapper)."""
    await asyncio.to_thread(_send_push_sync, title, body, tag, event_type, model_key, degradation)


# ── Webhook sending ──────────────────────────────────────────────────────────

async def send_webhook(webhook_cfg: dict, payload: dict):
    """Send a notification to a single webhook endpoint. Fire-and-forget with error logging."""
    from backend.state import get_http_client
    try:
        url = webhook_cfg.get("url", "")
        if not url:
            return
        headers = {"Content-Type": "application/json"}
        custom_headers = webhook_cfg.get("headers", {})
        if isinstance(custom_headers, dict):
            headers.update(custom_headers)
        body_bytes = orjson.dumps(payload)
        secret = webhook_cfg.get("secret", "")
        if secret:
            sig = hmac.new(secret.encode(), body_bytes, "sha256").hexdigest()
            headers["X-ModelWatcher-Signature"] = f"sha256={sig}"
        client = get_http_client()
        resp = await client.post(url, content=body_bytes, headers=headers, timeout=float(c.notif_webhook_timeout))
        if resp.status_code >= 400:
            log.warning("Webhook %s returned %d: %s", webhook_cfg.get("name", url[:40]), resp.status_code, resp.text[:200])
        else:
            log.info("Webhook %s delivered (%d)", webhook_cfg.get("name", url[:40]), resp.status_code)
    except Exception as e:
        log.warning("Webhook %s failed: %s", webhook_cfg.get("name", "?"), e)


# ── Degradation detection ───────────────────────────────────────────────────

def _detect_transition(current_tier: int | None, prev_tier: int | None, degraded_tier: int) -> str | None:
    if current_tier is None or current_tier < 0 or prev_tier is None or prev_tier < 0:
        if current_tier is not None and current_tier >= degraded_tier:
            return "initial"
        return None
    if prev_tier >= degraded_tier and current_tier < degraded_tier:
        return "recovery"
    if current_tier >= degraded_tier:
        if prev_tier < degraded_tier:
            return "initial"
        if current_tier > prev_tier:
            return "further"
    return None


# 5-min cooldown for repeated "initial" degradation notifications (per model+metric).
# "further" and "recovery" transitions always fire - only "initial" is rate-limited.
_METRIC_NOTIF_COOLDOWN: dict[str, float] = {}
_METRIC_NOTIF_COOLDOWN_SECS = 300.0
# 2-min cooldown for repeated status-change notifications per model.
_STATUS_NOTIF_COOLDOWN: dict[str, float] = {}
_STATUS_NOTIF_COOLDOWN_SECS = 120.0


def _check_metric_degradation(
    metric: str,
    model_key: str,
    current_value: float,
    degraded_event: str,
    recovered_event: str,
) -> dict | None:
    """Detect degradation/recovery transitions for a metric (TPS or TTFT).

    Only considers benchmark records when looking up the previous metric
    value - health TTFT is excluded from the comparison baseline. The search
    starts at index len(history)-2 because the current result has already
    been appended to recent_history by record_result_async() before this
    is called. When no previous benchmark record exists, prev_tier defaults
    to 0 (best tier) so first-ever degradation is always detected.
    Returns dict with event info if a transition is detected, else None.
    """
    if not c.notif_events.get(degraded_event, True) and not c.notif_events.get(recovered_event, True):
        return None
    degraded_tier = effective_degraded_tier(metric)
    ct = c.color_thresholds
    metric_cfg = ct.get(metric, {})
    thresholds = metric_cfg.get("thresholds", [])
    higher_is_better = metric_cfg.get("higher_is_better", metric != "ttft")
    if not thresholds or degraded_tier >= len(thresholds):
        return None

    current_tier = tier_idx(current_value, thresholds, higher_is_better)

    mc = st.model_cache.get(model_key, {})
    history = mc.get("recent_history", [])
    metric_key = "ttft_ms" if metric == "ttft" else "tps"

    prev_record = None
    for i in range(len(history) - 2, -1, -1):
        candidate = history[i]
        if not candidate or not candidate.get("success", False):
            continue
        if candidate.get("retry_attempt") is not None:
            continue
        tt = candidate.get("test_type", "benchmark")
        if tt != "benchmark":
            continue
        v = candidate.get(metric_key)
        if v is not None and v > 0:
            prev_record = candidate
            break

    prev_val = prev_record.get(metric_key) if prev_record else None
    prev_tier = tier_idx(prev_val, thresholds, higher_is_better) if prev_record else 0

    base_result = {"current_value": current_value, "current_tier": current_tier, "threshold": thresholds[degraded_tier], "prev_value": prev_val, "prev_tier": prev_tier, "degraded_tier": degraded_tier}

    transition = _detect_transition(current_tier, prev_tier, degraded_tier)
    now = time.time()
    if transition == "recovery" and c.notif_events.get(recovered_event, True):
        degraded_since_key = f"{metric}_degraded_since"
        result = {**base_result, "event_type": recovered_event}
        degraded_since_val = mc.get(degraded_since_key)
        if degraded_since_val:
            result["degraded_since"] = degraded_since_val
            mc[degraded_since_key] = None
        _METRIC_NOTIF_COOLDOWN[f"{model_key}:{metric}"] = now
        return result
    if transition == "initial" and c.notif_events.get(degraded_event, True):
        if len(_METRIC_NOTIF_COOLDOWN) > 100:
            stale = [k for k, v in _METRIC_NOTIF_COOLDOWN.items() if now - v >= _METRIC_NOTIF_COOLDOWN_SECS]
            for k in stale:
                del _METRIC_NOTIF_COOLDOWN[k]
        ck = f"{model_key}:{metric}"
        last = _METRIC_NOTIF_COOLDOWN.get(ck, 0)
        if now - last < _METRIC_NOTIF_COOLDOWN_SECS:
            mc[f"{metric}_degraded_since"] = now
            return None
        mc[f"{metric}_degraded_since"] = now
        _METRIC_NOTIF_COOLDOWN[ck] = now
        return {**base_result, "event_type": degraded_event}
    if transition == "further" and c.notif_events.get(degraded_event, True):
        return {**base_result, "event_type": degraded_event}
    return None


def check_tps_degradation(model_key: str, current_tps: float) -> dict | None:
    """Check TPS degradation/recovery transitions."""
    return _check_metric_degradation("tps", model_key, current_tps, "degraded_tps", "recovered_tps")


def check_ttft_degradation(model_key: str, current_ttft_ms: float) -> dict | None:
    """Check TTFT degradation/recovery transitions."""
    return _check_metric_degradation("ttft", model_key, current_ttft_ms, "degraded_ttft", "recovered_ttft")


# ── Notification dispatch ────────────────────────────────────────────────────

_notif_history: collections.deque[dict] = collections.deque()
_notif_id_counter: int = 0

_EVENT_LABELS = EVENT_LABELS
_METRIC_LABELS = METRIC_LABELS


def _fmt_tps(val):
    return f"{val:.1f} t/s"


def _fmt_ttft(ms):
    if ms is None:
        return None
    return f"{ms/1000:.1f}s" if ms >= 1000 else f"{ms:.0f}ms"


def _fmt_metric_value(metric: str, value) -> str:
    """Format a metric name and its value for notification body text."""
    label = _METRIC_LABELS.get(metric, metric)
    if metric == "tps":
        return f"{label}: {_fmt_tps(value)}"
    if metric == "ttft":
        formatted = _fmt_ttft(value)
        return f"{label}: {formatted}" if formatted else label
    if metric in ("effective_itl_tail_ratio", "chunk_token_ratio", "burst_arrival_pct"):
        return f"{label}: {value:.1f}×"
    if metric.endswith("_ms"):
        formatted = _fmt_ttft(value)
        return f"{label}: {formatted}" if formatted else label
    if metric == "stall_count":
        return f"{label}: {int(value)}"
    return f"{label}: {value}"


_STATUS_LABELS = {
    "online": "Online",
    "degraded": "Degraded",
    "error": "Offline",
    "unknown": "Unknown",
}


def _fmt_duration(seconds: float) -> str:
    """Format elapsed seconds as compact human-readable duration (e.g. '2h 15m', '1d 3h')."""
    s = max(0, int(seconds))
    if s < 60:
        return "<1m"
    m, _ = divmod(s, 60)
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h {m}m" if m else f"{h}h"
    d, h = divmod(h, 24)
    if d < 7:
        return f"{d}d {h}h" if h else f"{d}d"
    w, d = divmod(d, 7)
    if w < 4:
        return f"{w}w {d}d" if d else f"{w}w"
    mo, w = divmod(w, 4)
    return f"{mo}mo {w}w" if w else f"{mo}mo"


def _fmt_transition(before: str, after: str) -> str:
    """Format a before→after transition for notification bodies."""
    if before and after:
        return f"Before: {before} → Current: {after}"
    if after:
        return f"Current: {after}"
    return ""


def _build_status_body(degradation: dict | None) -> str:
    """Build notification body for a status transition event (recovered/offline/degraded)."""
    deg = degradation or {}
    prev_raw = deg.get("prev_status") or ""
    cur_raw = deg.get("new_status") or ""
    prev = _STATUS_LABELS.get(prev_raw, prev_raw)
    cur = _STATUS_LABELS.get(cur_raw, cur_raw)
    since = deg.get("degraded_since")
    if since and prev:
        elapsed = _fmt_duration(time.time() - since)
        prev = f"{prev} ({elapsed})"
    return _fmt_transition(prev, cur)



def _build_notif_title(provider_name: str, model_name: str, event_type: str) -> str:
    """Standardized notification title: 'Provider - ModelName - ShortDesc'."""
    label = _EVENT_LABELS.get(event_type, event_type)
    parts = [p for p in (provider_name, model_name) if p]
    model_str = " - ".join(parts) or "Unknown"
    return f"{model_str} - {label}"


def _build_degradation_body(degradation: dict | None, fmt_val, tier_labels: list[str]) -> str:
    """Build notification body for a metric degradation/recovery event.

    fmt_val formats current/previous values (e.g. _fmt_tps or _fmt_ttft).
    tier_labels are the configured tier label strings
    (e.g. ['Excellent', 'Good', 'OK', 'Bad', 'Critical']).
    """
    if not degradation:
        return ""

    def _format_val(val, tier_idx):
        f = fmt_val(val) if val is not None else None
        lbl = tier_labels[tier_idx] if tier_idx is not None and tier_idx < len(tier_labels) else None
        return f"{f} ({lbl})" if f and lbl else (f or "")

    prev_str = _format_val(degradation.get("prev_value"), degradation.get("prev_tier"))
    cur_str = _format_val(degradation.get("current_value"), degradation.get("current_tier"))
    return _fmt_transition(prev_str, cur_str)


def _build_notif_body(event_type: str, degradation: dict | None, tier_labels: list[str]) -> str:
    """Build notification body text for any event type."""
    deg = degradation or {}
    if event_type == "degraded":
        transition = _build_status_body(deg)
        reason = deg.get("degraded_reason") or ""
        if "critical_tier" in reason:
            critical_metrics = deg.get("critical_metrics") or []
            metric_values = deg.get("metric_values") or {}
            parts = [_fmt_metric_value(m, metric_values.get(m)) for m in critical_metrics if metric_values.get(m) is not None]
            detail = ("Critical metrics: " + ", ".join(parts)) if parts else "Critical metrics reached."
        elif "stream_error" in reason:
            detail = "Stream interrupted after tokens were received. Metrics are computed from partial output."
        elif "insufficient_output" in reason:
            detail = "Output below minimum threshold for reliable metrics. Too few tokens or chunks received."
        elif "test_retry" in reason:
            detail = "Test failed, retrying."
        else:
            detail = "Performance below acceptable thresholds."
        return f"{transition}. {detail}" if transition else detail

    if event_type in ("degraded_tps", "recovered_tps", "degraded_ttft", "recovered_ttft"):
        fmt_fn = _fmt_tps if "tps" in event_type else _fmt_ttft
        return _build_degradation_body(degradation, fmt_fn, tier_labels)

    if event_type == "offline":
        transition = _build_status_body(deg)
        err = deg.get("error") or ""
        detail = err[:200] if err else "Failed its most recent test."
        return f"{transition}. {detail}" if transition else detail

    if event_type in ("recovered", "recovered_offline", "recovered_degraded", "partially_recovered"):
        return _build_status_body(deg)

    if event_type == "provider_changed":
        action = deg.get("action", "added")
        models = deg.get("models") or []
        if action == "removed":
            return "Provider removed." + (f" Models: {', '.join(models)}" if models else "")
        if models:
            return "Models: " + ", ".join(models)
        return "New provider configured."

    if event_type == "model_changed":
        action = deg.get("action", "added")
        model_name = deg.get("model_name") or ""
        if action == "removed":
            return f"Model removed: {model_name}" if model_name else "Model removed."
        return f"New model: {model_name}" if model_name else "New model added."

    return ""


def _dispatch_webhooks(event_type: str, model_key: str, provider_name: str, model_name: str, uptime_pct: float | None, degradation: dict | None):
    payload = {
        "event": event_type,
        "model_key": model_key,
        "model_name": model_name,
        "provider": provider_name,
        "timestamp": utc_now_iso(),
        "uptime_pct": uptime_pct,
        "url": c.site_url,
    }
    _attach_degradation(payload, degradation)
    for wh in c.notif_webhooks:
        wh_events = wh.get("events", {})
        if not wh_events.get(event_type, True):
            continue
        if not should_notify_model(wh, model_key):
            continue
        st.create_task(send_webhook(wh, payload), name=f"webhook:{wh.get('name','?')}")


def _record_in_app_history(notif_id: str, event_type: str, model_key: str, title: str, body: str, degradation: dict | None) -> dict:
    entry = {
        "id": notif_id,
        "timestamp": utc_now_iso(),
        "model_key": model_key,
        "event_type": event_type,
        "message": title,
        "body": body,
    }
    _attach_degradation(entry, degradation)
    _notif_history.append(entry)
    while len(_notif_history) > c.notif_in_app_history_size:
        _notif_history.popleft()
    return entry


async def _broadcast_ws_notification(event_type: str, model_key: str, degradation: dict | None, entry: dict):
    if not c.notif_in_app_enabled:
        return
    def notif_filter(prefs):
        return should_notify(prefs, event_type, model_key, degradation)
    await ws_mgr.broadcast({"type": "notification", "notification": entry}, filter_fn=notif_filter)


async def notify_status_change(model_key: str, event_type: str, uptime_pct: float | None = None, degradation: dict | None = None):
    """Central notification dispatcher for status changes.

    Handles push, webhooks, in-app history, and WS broadcast. Splits
    "recovered" into recovered_offline / recovered_degraded for per-subscriber
    prefs while the server config uses a single "recovered" toggle. Applies
    status-change cooldown (except for offline events). Enriches degradation
    with degraded_since for recovery grounding.
    """
    global _notif_id_counter
    try:
        if not c.notif_enabled:
            log.info("Notification for %s (%s) skipped: notifications disabled", model_key, event_type)
            return

        if event_type in ("recovered", "partially_recovered"):
            prev_status = (degradation or {}).get("prev_status")
            if prev_status == "error":
                split_event = "recovered_offline"
            else:
                split_event = "recovered_degraded"
        else:
            split_event = event_type

        # Server config uses a single "recovered" toggle for all recovery
        # variants; per-subscriber prefs support the split
        # (recovered_offline/recovered_degraded).
        config_event = "recovered" if split_event in ("recovered_offline", "recovered_degraded") else split_event
        if not c.notif_events.get(config_event, True):
            log.info("Notification for %s (%s) skipped: event type disabled in config", model_key, event_type)
            return

        if event_type != "offline":
            now = time.time()
            if len(_STATUS_NOTIF_COOLDOWN) > 100:
                stale = [k for k, v in _STATUS_NOTIF_COOLDOWN.items() if now - v >= _STATUS_NOTIF_COOLDOWN_SECS]
                for k in stale:
                    del _STATUS_NOTIF_COOLDOWN[k]
            last = _STATUS_NOTIF_COOLDOWN.get(model_key, 0)
            if now - last < _STATUS_NOTIF_COOLDOWN_SECS:
                log.info("Notification for %s (%s) skipped: status cooldown", model_key, event_type)
                return
            _STATUS_NOTIF_COOLDOWN[model_key] = now

        if split_event in ("recovered", "recovered_offline", "recovered_degraded", "partially_recovered", "recovered_tps", "recovered_ttft"):
            mc = st.model_cache.get(model_key, {})
            degraded_since = mc.get("degraded_since") or mc.get("tps_degraded_since") or mc.get("ttft_degraded_since")
            if degraded_since:
                if degradation is None:
                    degradation = {}
                degradation.setdefault("degraded_since", degraded_since)

        _notif_id_counter += 1
        notif_id = f"n{_notif_id_counter}"
        provider_name, model_id = parse_model_key(model_key)
        reg_entry = _registry_by_id.get(model_key)
        model_name = reg_entry["name"] if reg_entry else model_id

        log.info("Notification: %s (%s) %s (push_subs=%d)", split_event, event_type, model_key, len(_get_push_subs()))

        tier_labels = [t.get("label", str(i)) for i, t in enumerate(c.color_thresholds.get("tiers", []))]
        title = _build_notif_title(provider_name, model_name, split_event)
        body = _build_notif_body(split_event, degradation, tier_labels)

        await send_push(title, body, tag=f"mw-{model_key}", event_type=split_event, model_key=model_key, degradation=degradation)
        _dispatch_webhooks(split_event, model_key, provider_name, model_name, uptime_pct, degradation)
        entry = _record_in_app_history(notif_id, split_event, model_key, title, body, degradation)
        await _broadcast_ws_notification(split_event, model_key, degradation, entry)
    except Exception as e:
        log_error(f"Notification dispatch failed for {model_key}", e)


async def _dispatch_registry_notification(
    event_type: str,
    model_key: str,
    provider: str,
    model_name: str,
    degradation: dict,
):
    """Shared dispatch for registry change notifications (push + webhooks + history + WS)."""
    global _notif_id_counter
    if not c.notif_events.get(event_type, True):
        return
    _notif_id_counter += 1
    notif_id = f"n{_notif_id_counter}"
    tier_labels = [t.get("label", str(i)) for i, t in enumerate(c.color_thresholds.get("tiers", []))]
    action = degradation.get("action", "added")
    action_label = action.capitalize()
    if event_type == "provider_changed":
        title = f"{provider} - Provider {action_label}"
    else:
        title = f"{provider} - {model_name} - Model {action_label}"
    body = _build_notif_body(event_type, degradation, tier_labels)
    tag = f"mw-{model_key}-{action}"
    log.info("Notification: %s(%s) %s", event_type, action, model_key)
    await send_push(title, body, tag=tag, event_type=event_type, model_key=model_key, degradation=degradation)
    _dispatch_webhooks(event_type, model_key, provider, model_name, None, degradation)
    entry = _record_in_app_history(notif_id, event_type, model_key, title, body, degradation)
    await _broadcast_ws_notification(event_type, model_key, degradation, entry)


async def notify_registry_changes(
    added_model_keys: list[str],
    removed_model_keys: list[str],
    old_provider_names: set[str],
    current_provider_names: set[str],
    old_model_names: dict[str, str],
):
    """Dispatch notifications for provider/model additions and removals.

    - New provider (not in old_provider_names): provider_changed action=added
    - Removed provider (in old but not current): provider_changed action=removed
    - Model added to existing provider: model_changed action=added
    - Model removed from existing provider: model_changed action=removed
    """
    if not c.notif_enabled or not st.scheduler_running:
        log.debug("Registry notification skipped: enabled=%s scheduler=%s added=%d removed=%d", c.notif_enabled, st.scheduler_running, len(added_model_keys), len(removed_model_keys))
        return
    if not added_model_keys and not removed_model_keys:
        return
    log.info("Registry changes: %d added, %d removed", len(added_model_keys), len(removed_model_keys))
    removed_by_provider: dict[str, list[tuple[str, str]]] = {}
    new_provider_models: dict[str, list[str]] = {}

    for mk in removed_model_keys:
        provider, model_id = parse_model_key(mk)
        removed_by_provider.setdefault(provider, []).append((mk, old_model_names.get(mk, model_id)))

    fully_removed_providers = old_provider_names - current_provider_names

    for provider in fully_removed_providers:
        try:
            model_names = [name for _, name in removed_by_provider.get(provider, [])]
            await _dispatch_registry_notification(
                "provider_changed", f"{provider}::", provider, "",
                {"provider": provider, "models": model_names, "action": "removed"},
            )
        except Exception as e:
            log_error(f"Registry notification failed for {provider}", e)

    for mk in added_model_keys:
        provider, model_id = parse_model_key(mk)
        reg_entry = _registry_by_id.get(mk)
        model_name = reg_entry["name"] if reg_entry else model_id
        is_new_provider = provider not in old_provider_names
        try:
            if is_new_provider:
                new_provider_models.setdefault(provider, []).append(model_name)
            else:
                await _dispatch_registry_notification(
                    "model_changed", mk, provider, model_name,
                    {"provider": provider, "model_name": model_name, "action": "added"},
                )
        except Exception as e:
            log_error(f"Registry notification failed for {mk}", e)

    for provider, model_names in new_provider_models.items():
        try:
            await _dispatch_registry_notification(
                "provider_changed", f"{provider}::", provider, "",
                {"provider": provider, "models": model_names, "action": "added"},
            )
        except Exception as e:
            log_error(f"Registry notification failed for {provider}", e)

    for provider, models in removed_by_provider.items():
        if provider in fully_removed_providers:
            continue
        for mk, model_name in models:
            try:
                await _dispatch_registry_notification(
                    "model_changed", mk, provider, model_name,
                    {"provider": provider, "model_name": model_name, "action": "removed"},
                )
            except Exception as e:
                log_error(f"Registry notification failed for {mk}", e)


def handle_get_notifications(since: str | None = None, client_id: str | None = None):
    """GET /api/notifications - Return notification config + history.

    History is scoped by the subscriber's created_at (transparent - new
    subscribers only see notifications from after they subscribed) and by
    the optional since param (ISO 8601). Server-side config is stripped to
    app_name, enabled, and in_app settings to prevent information leakage.
    """
    from backend.routes import orjson_response, error_response
    if client_id and (len(client_id) > st.MAX_CLIENT_ID_LEN or "\x00" in client_id):
        return error_response("Invalid client_id")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=c.notif_in_app_retention_days)).isoformat()
    min_ts = cutoff
    if since:
        try:
            datetime.fromisoformat(since)
            min_ts = max(since, cutoff)
        except (ValueError, TypeError):
            return error_response("Invalid 'since' parameter - expected ISO 8601 datetime")
    client_sub = db_push.get_push_sub_by_client(client_id) if client_id else None
    if client_sub:
        created = client_sub.get("created_at", 0)
        if created:
            created_ts = datetime.fromtimestamp(created, tz=timezone.utc).isoformat()
            min_ts = max(min_ts, created_ts)
    history = [n for n in _notif_history if n.get("timestamp", "") > min_ts][-c.notif_in_app_api_response_cap:] if min_ts else []
    prefs = client_sub.get("prefs") if client_sub else None
    if prefs:
        def _history_filter(n):
            _std_keys = {"id", "timestamp", "model_key", "event_type", "message", "body"}
            deg = {k: v for k, v in n.items() if k not in _std_keys and v is not None} or None
            return should_notify(prefs, n.get("event_type", ""), n.get("model_key", ""), deg)
        history = [n for n in history if _history_filter(n)]
    return orjson_response({
        "app_name": c.app_name,
        "enabled": c.notif_enabled,
        "in_app": {
            "enabled": c.notif_in_app_enabled,
            "toast_duration_ms": c.notif_in_app_toast_ms,
            "history_size": c.notif_in_app_history_size,
        },
        "history": history,
    })
