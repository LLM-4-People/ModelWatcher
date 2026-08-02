"""Test scheduler, dual-tier dispatch (benchmark + health check), and provider test execution.

Runs an event-driven loop that dispatches due tests per model: health checks
first (fast, free provider slots), then benchmarks, then probes, then audits.
Concurrency is bounded by a global semaphore and per-provider semaphores.
Also owns retry logic, critical-tier degradation detection, auto-archiving of
long-offline models, and the BroadcastBatcher that coalesces WS result messages.
"""

import asyncio
import random
import time
import uuid

import backend.state as st
import backend.db as db
import backend.db_probe as db_probe
from backend.state import utc_now_iso, TEST_HEALTH, TEST_BENCHMARK, TEST_AUDIT, TEST_PROBE, parse_model_key
from backend.streaming import stream_test, strip_health_metrics
from backend.metrics import ensure_model
from backend.stats import find_critical_metrics, cached_range_scores, THRESHOLD_TO_RESULT_KEY
from backend.state import strip_internal
from backend.batch import PeriodicBatcher
from backend.notifications import (
    notify_status_change, check_tps_degradation, check_ttft_degradation,
)
from backend.websocket import ws_mgr
from backend.prompts import random_prompt
from backend.models import get_provider_for, get_provider_concurrency
from backend.security import scrub_pii, safe_internal_error, is_internal_error


# ── Concurrency control ────────────────────────────────────────────────────────

_global_sem: asyncio.Semaphore | None = None
_provider_sems: dict[str, asyncio.Semaphore] = {}


def _get_global_sem() -> asyncio.Semaphore:
    global _global_sem
    if _global_sem is None:
        _global_sem = asyncio.Semaphore(st.c.max_concurrent_tests)
    return _global_sem


def _get_provider_sem(provider_name: str) -> asyncio.Semaphore:
    if provider_name not in _provider_sems:
        _provider_sems[provider_name] = asyncio.Semaphore(get_provider_concurrency(provider_name))
    return _provider_sems[provider_name]


# ── Retry helpers ──────────────────────────────────────────────────────────────


def _mark_insufficient_as_degraded(result):
    """Mark a clean-success BENCHMARK result as degraded if output is below minimum thresholds.

    Uses AND logic (completion_tokens < min_tokens AND token_count < min_chunks)
    - intentionally strict so only truly insufficient output is flagged.
    Health checks are skipped: any tokens received means the model is alive.
    """
    if result.get("test_type") == TEST_HEALTH:
        return
    if not result.get("success") or result.get("degraded"):
        return
    ct = result.get("completion_tokens") or 0
    tc = result.get("token_count") or 0
    if ct < st.c.benchmark_min_tokens and tc < st.c.benchmark_min_chunks:
        result["degraded"] = True
        result["degraded_reason"] = "insufficient_output"
        result["error"] = (
            f"Insufficient output ({tc} chunks, {ct} tokens;"
            f" min {st.c.benchmark_min_chunks} chunks and {st.c.benchmark_min_tokens} tokens)"
        )


def _should_retry(result, attempt, total_attempts):
    """Determine whether to retry. Retries all failures except HTTP 4xx (auth/config issues),
    and all degraded results (insufficient_output, stream_error, critical_tier).
    Clean successes are never retried."""
    if attempt >= total_attempts - 1:
        st.log.debug(
            "_should_retry %s: no more attempts (attempt %d/%d)",
            result.get("error", "?")[:60], attempt + 1, total_attempts,
        )
        return False
    if not result.get("success"):
        should = not (result.get("error") or "").startswith("HTTP 4")
        st.log.debug(
            "_should_retry %s: attempt %d/%d, success=False, should_retry=%s",
            result.get("error", "?")[:60], attempt + 1, total_attempts, should,
        )
        return should
    if result.get("degraded"):
        st.log.debug(
            "_should_retry degraded (%s): attempt %d/%d, will retry",
            result.get("degraded_reason", "?"), attempt + 1, total_attempts,
        )
        return True
    st.log.debug("_should_retry: clean success, no retry (attempt %d/%d)", attempt + 1, total_attempts)
    return False


def _build_retry_record(result, attempt, total_attempts):
    """Build a standardized retry record for an intermediate attempt."""
    ts = time.time()
    return {
        "timestamp": utc_now_iso(),
        "_ts_epoch": ts,
        "ts_epoch": ts,
        "available": result.get("success", False),
        "retry_attempt": attempt,
        "retry_total": total_attempts,
        **result,
    }


def _log_retry(model_key, attempt, total, result):
    """Log a retry attempt with context about why."""
    label = "degraded" if result.get("degraded") else "failed"
    test_type = result.get("test_type", "benchmark")
    err = (result.get("error") or "unknown")[:100]
    st.log.warning("%s [%s]: attempt %d/%d %s (%s) - retrying", model_key, test_type, attempt, total, label, err)





# ── Broadcast batcher: aggregates WS result messages ──────────────────────────

async def _dispatch_notifications(batch: dict):
    """Fire accumulated notifications (immediate, not batched). Shared by BroadcastBatcher and _broadcast_result."""
    for model_key, info in batch.items():
        changed = info.get("changed")
        if not changed:
            continue
        degradation = info.get("degradation") or {}
        uptime_pct = info["msg"].get("uptime_pct")
        if changed == "degraded":
            await notify_status_change(model_key, "degraded", uptime_pct, degradation=degradation)
        elif changed == "offline":
            await notify_status_change(model_key, changed, uptime_pct, degradation=degradation)
        else:
            notif_event = "partially_recovered" if degradation.get("new_status") == "degraded" else changed
            await notify_status_change(model_key, notif_event, uptime_pct, degradation=degradation)


class BroadcastBatcher(PeriodicBatcher):
    """Aggregates WebSocket result messages per model, flushes periodically.

    Instead of sending one WS message per test result, buffers them and sends
    a single result_batch message per flush with the latest state per model.
    Notifications remain immediate (they are low-volume and time-sensitive).
    """

    def __init__(self, flush_interval: float = 2.0):
        super().__init__(flush_interval)
        self._pending: dict[str, dict] = {}

    def add(self, model_key: str, msg: dict, changed: str | None = None,
            degradation: dict | None = None):
        """Buffer a WS result message for batched delivery.

        For duplicate model_keys within a flush window, the latest result
        wins (the client already got the intermediate cache updates).
        Notification-relevant data (changed, degradation) is accumulated
        last-writer-wins: a new changed != None overwrites previous change
        info; a new changed == None preserves the previous change info (a
        deferred notification from an earlier result). So a model that goes
        degraded->recovered within one flush window only triggers a
        "recovered" notification - the transient degradation resolved itself.
        """
        existing = self._pending.get(model_key)
        if existing:
            existing["msg"] = msg
            if changed:
                existing["changed"] = changed
                existing["degradation"] = degradation
        else:
            self._pending[model_key] = {
                "msg": msg,
                "changed": changed,
                "degradation": degradation,
            }

    async def flush(self):
        """Flush all pending results as a single WS broadcast."""
        if not self._pending:
            return
        batch = self._pending.copy()
        self._pending.clear()
        results = {mk: info["msg"] for mk, info in batch.items()}
        await ws_mgr.broadcast({"type": "result_batch", "results": results})
        await _dispatch_notifications(batch)


# Singleton - started/stopped by main.py lifespan
broadcast_batcher: BroadcastBatcher | None = None

async def _broadcast_result(model_key: str, record: dict, uptime_pct: float | None, changed: str | None = None, prev_status: str | None = None, degraded_since: float | None = None):
    """Broadcast a test result (final or retry) via WS and send notifications on status change.

    Retry records can temporarily set degraded until the final attempt resolves.
    When BroadcastBatcher is active, WS messages are buffered and sent as a
    result_batch; notifications remain immediate for status changes.
    """
    entry = st.model_cache.get(model_key, {})
    msg = {
        "type": "result",
        "model": model_key,
        "record": strip_internal(record),
        "uptime_pct": entry.get("uptime_pct") if changed is None else uptime_pct,
        "test_type": record.get("test_type", TEST_BENCHMARK),
        "status": entry.get("status"),
        "scores": cached_range_scores(entry),
        "trends": entry.get("trends", {}),
    }
    ds = entry.get("degraded_source")
    if ds is not None:
        msg["degraded_source"] = ds

    degradation = None
    if changed:
        new_status = entry.get("status")
        if changed == "degraded":
            degradation = {
                "degraded_reason": record.get("degraded_reason") or ("test_retry" if record.get("retry_attempt") is not None else None),
                "critical_metrics": record.get("critical_metrics"),
                "prev_status": prev_status,
                "new_status": new_status,
            }
            cm = record.get("critical_metrics") or []
            if cm:
                degradation["metric_values"] = {m: record.get(THRESHOLD_TO_RESULT_KEY.get(m, m)) for m in cm}
        elif changed == "offline":
            error = record.get("error", "")
            degradation = {"prev_status": prev_status, "new_status": new_status}
            if error:
                degradation["error"] = error
        else:
            degradation = {"prev_status": prev_status, "new_status": new_status}
            if degraded_since:
                degradation["degraded_since"] = degraded_since

    if broadcast_batcher is not None:
        broadcast_batcher.add(model_key, msg, changed=changed, degradation=degradation)
        return

    await ws_mgr.broadcast(msg)
    if not changed:
        return
    batch = {model_key: {"msg": msg, "changed": changed, "degradation": degradation}}
    await _dispatch_notifications(batch)


async def _clear_testing_state(model_key: str, test_type: str,
                               invalidate: bool = True, broadcast: bool = True):
    """Clear testing flags in model_cache, optionally invalidate metrics and broadcast WS."""
    if model_key not in st.model_cache:
        return
    if test_type == TEST_HEALTH:
        st.model_cache[model_key]["testing_health"] = False
    elif test_type == TEST_AUDIT:
        st.model_cache[model_key]["testing_audit"] = False
    elif test_type == TEST_PROBE:
        st.model_cache[model_key]["testing_probe"] = False
    else:
        st.model_cache[model_key]["testing_benchmark"] = False
    if invalidate:
        st.invalidate_metrics_cache()
    if broadcast and test_type not in (TEST_HEALTH, TEST_AUDIT, TEST_PROBE):
        await ws_mgr.broadcast({"type": "testing", "model": model_key, "testing": False, "test_type": test_type})


async def run_test(model_key: str, test_type: str = TEST_BENCHMARK):
    """Execute a test for a model with retries, record results, and notify.

    test_type is TEST_BENCHMARK for full benchmarks or TEST_HEALTH for
    lightweight health checks. Concurrency is controlled by a global
    semaphore (max_concurrent_tests) and a per-provider semaphore
    (concurrent_models). The scheduler pre-adds the model to
    running_tests/running_health before dispatching; this function ensures
    it is present (defensive for direct calls) and removes it on completion.

    Resumes interrupted retry cycles from where they left off - if the model
    has recent retry records with no subsequent final result (e.g. server
    restarted mid-retry), the next attempt continues from that point rather
    than starting from attempt 0.
    """
    # Guard: reject if opposite test type is already running for this model
    if test_type not in (TEST_BENCHMARK, TEST_HEALTH):
        return {"error": f"run_test does not support {test_type}"}
    if test_type == TEST_HEALTH and model_key in st.running_tests:
        return {"error": "benchmark already running"}
    if test_type == TEST_BENCHMARK and model_key in st.running_health:
        return {"error": "health already running"}
    # Defensive add (scheduler pre-adds, but this handles direct calls)
    if test_type == TEST_HEALTH:
        st.running_health.add(model_key)
    else:
        st.running_tests.add(model_key)
    try:
        async with _get_global_sem():
            provider = get_provider_for(model_key)
            if not provider:
                st.log.warning("%s: provider not found, cannot test", model_key)
                return {"error": f"Model {model_key} not found"}

            _psem = _get_provider_sem(provider.get("name", ""))
            await _psem.acquire()
            try:
                ensure_model(model_key)
                # Consume pending-retry flag (set by scheduler for due-checking).
                st.model_cache[model_key].pop("_pending_retry", None)
                if test_type == TEST_HEALTH:
                    st.model_cache[model_key]["testing_health"] = True
                else:
                    st.model_cache[model_key]["testing_benchmark"] = True
                st.invalidate_metrics_cache()
                if test_type != TEST_HEALTH:
                    await ws_mgr.broadcast({"type": "testing", "model": model_key, "testing": True, "test_type": test_type})

                # Resume an interrupted retry cycle or start fresh.
                total_attempts = 1 + st.c.max_retries
                interval, _ = st.test_type_schedule(test_type)
                skip = await asyncio.to_thread(db.get_resume_attempt, model_key, interval, test_type)
                if skip > 0 and skip < total_attempts:
                    st.log.debug("%s: resuming retry cycle from attempt %d/%d", model_key, skip + 1, total_attempts)
                else:
                    skip = 0

                # Generate prompt based on test type.
                if test_type == TEST_HEALTH:
                    prompt = random.choice(st.c.health_prompts)
                else:
                    prompt = random_prompt()

                retry_count = skip
                result = None
                for attempt in range(skip, total_attempts):
                    if st._shutting_down:
                        break
                    result = await stream_test(provider, prompt, test_type=test_type)
                    st.log.debug(
                        "%s [%s]: attempt %d/%d result: success=%s degraded=%s error=%s",
                        model_key, test_type, attempt + 1, total_attempts,
                        result.get("success"), result.get("degraded"),
                        (result.get("error") or "")[:80],
                    )
                    _mark_insufficient_as_degraded(result)
                    # Strip meaningless metrics from health checks before recording.
                    if test_type == TEST_HEALTH:
                        strip_health_metrics(result)
                    if not _should_retry(result, attempt, total_attempts):
                        break
                    retry_count += 1
                    _log_retry(model_key, attempt + 1, total_attempts, result)
                    retry_record = _build_retry_record(result, attempt + 1, total_attempts)
                    uptime_pct, changed, prev_status, degraded_since = await db.record_retry_async(model_key, retry_record, st.model_cache)
                    await _broadcast_result(model_key, retry_record, uptime_pct, changed, prev_status, degraded_since)
                    await asyncio.sleep(st.c.retry_delay)

                if result is None:
                    await _clear_testing_state(model_key, test_type, invalidate=True, broadcast=False)
                    return {"error": "shutting down"}

                if retry_count > 0:
                    result["retry_count"] = retry_count

                # Critical-tier degradation check - benchmark only (health metrics
                # are too minimal for critical-tier detection).
                if test_type == TEST_BENCHMARK:
                    critical = find_critical_metrics(result)
                    if len(critical) >= 2:
                        result["degraded"] = True
                        result["degraded_reason"] = "critical_tier"
                        result["critical_metrics"] = critical

                available = result.get("available", result.get("success", False))
                ts = time.time()
                record = {
                    "timestamp": utc_now_iso(),
                    "_ts_epoch": ts,
                    "ts_epoch": ts,
                    "available": available,
                    "test_type": test_type,
                    **result,
                }

                uptime_pct, changed, prev_status, degraded_since = await db.record_result_async(model_key, record, available, st.model_cache)

                if uptime_pct is not None:
                    await _broadcast_result(model_key, record, uptime_pct, changed, prev_status, degraded_since)
                    if not changed and available:
                        # TPS/TTFT degradation - benchmark only (health checks
                        # produce too few tokens for reliable TPS, and health
                        # TTFT is stripped).
                        if test_type == TEST_BENCHMARK and result.get("tps"):
                            degraded_tps = check_tps_degradation(model_key, result["tps"])
                            if degraded_tps:
                                await notify_status_change(model_key, degraded_tps["event_type"], uptime_pct, degradation=degraded_tps)
                        # TTFT degradation - benchmark only (health TTFT is stripped).
                        if test_type == TEST_BENCHMARK and result.get("ttft_ms"):
                            degraded_ttft = check_ttft_degradation(model_key, result["ttft_ms"])
                            if degraded_ttft:
                                await notify_status_change(model_key, degraded_ttft["event_type"], uptime_pct, degradation=degraded_ttft)
                return record

            finally:
                _psem.release()

    except asyncio.CancelledError:
        await _clear_testing_state(model_key, test_type)
        st.log.debug("run_test cancelled %s type=%s", model_key, test_type)
        raise
    except Exception as e:
        if st._shutting_down:
            return {"error": "shutting down"}
        if is_internal_error(e):
            st.log_error(f"Internal error in test for {model_key}", e)
            await _clear_testing_state(model_key, test_type)
            return {"error": "Internal error"}
        # Legitimate model-availability error - record as failure (no DB record for internal errors).
        st.log.warning("Test failed for %s: %s", model_key, scrub_pii(str(e))[:200])
        record = {
            "timestamp": utc_now_iso(),
            "success": False,
            "available": False,
            "error": safe_internal_error(e),
            "_ts_epoch": time.time(),
            "test_type": test_type,
            "request_id": str(uuid.uuid4()),
        }
        uptime_pct, changed, prev_status, degraded_since = await db.record_result_async(model_key, record, False, st.model_cache)
        if uptime_pct is not None:
            await _broadcast_result(model_key, record, uptime_pct, changed, prev_status, degraded_since)
        return {"error": safe_internal_error(e)}
    finally:
        st.running_tests.discard(model_key)
        st.running_health.discard(model_key)
        await _clear_testing_state(model_key, test_type, invalidate=False, broadcast=False)
        if st._wake_event:
            st._wake_event.set()


# ── Provider stagger ──────────────────────────────────────────────────────────


def _stagger_allowed_provider(undispatched: list[str] | None = None) -> str | None:
    """Return the single provider allowed to dispatch benchmarks under stagger.

    Returns None when stagger is disabled (all providers allowed) or when
    no provider is active and none are due.

    When stagger is enabled:
      1. If a provider has running benchmarks, finish its turn.
      2. Otherwise, pick the most overdue provider (lowest earliest epoch).
    """
    if not st.c.benchmark_stagger:
        return None
    for mk in st.running_tests:
        return parse_model_key(mk)[0]
    # No active provider - find the most overdue one
    interval, epoch_key = st.test_type_schedule(TEST_BENCHMARK)
    now = time.time()
    best_provider: str | None = None
    best_earliest: float | None = None
    for mk in (undispatched or _iter_undispatched_models()):
        cache = st.model_cache.get(mk, {})
        if not st.test_type_allows_status(TEST_BENCHMARK, cache.get("status")):
            continue
        pr = cache.get("_pending_retry")
        is_due = (
            (pr and pr.get("test_type") == TEST_BENCHMARK)
            or cache.get(epoch_key) is None
            or (now - cache.get(epoch_key, 0)) >= interval
        )
        if not is_due:
            continue
        provider = parse_model_key(mk)[0]
        ts = cache.get(epoch_key) or 0.0
        if best_earliest is None or ts < best_earliest:
            best_earliest = ts
            best_provider = provider
    return best_provider


# ── Due model detection ────────────────────────────────────────────────────────


def _iter_undispatched_models() -> list[str]:
    """Return model keys not currently in any running_* set, excluding archived models."""
    return [e["id"] for e in st.model_registry if e["id"] not in st.running_tests and e["id"] not in st.running_health and e["id"] not in st.running_audit and e["id"] not in st.running_probe and e["id"] not in st._archived_model_keys]


# ── Auto-archive ───────────────────────────────────────────────────────────────


def _model_offline_for(key: str, threshold: float, now: float) -> bool:
    """Check if a model has been in error status for >= threshold seconds."""
    ce = st.model_cache.get(key)
    if not ce or ce.get("status") != "error":
        return False
    ds = ce.get("degraded_since")
    return ds is not None and (now - ds) >= threshold


async def apply_auto_archive():
    """Archive models/providers offline (error status) for >= offline_duration.

    Persists archived state to SQLite via db.set_archived so it survives
    server restarts and config reloads. Models/providers with
    auto_archive: false in models.yaml are exempt. Disabling the feature
    via auto_archive.enabled: false stops new auto-archiving but does NOT
    unarchive models that were previously auto-archived - use archived: false
    in models.yaml to explicitly unarchive.
    """
    if not st.c.auto_archive_enabled:
        return
    threshold = st.c.auto_archive_offline_duration
    now = time.time()

    # Group non-archived, non-opted-out registry models by provider
    provider_models: dict[str, list[str]] = {}
    for entry in st.model_registry:
        if entry["id"] in st._archived_model_keys:
            continue
        if entry.get("auto_archive") is False:
            continue
        provider_models.setdefault(entry["provider"], []).append(entry["id"])

    newly_archived: list[str] = []
    for provider, keys in provider_models.items():
        # If ALL models in a provider are offline for >= threshold, archive them all
        if keys and all(_model_offline_for(k, threshold, now) for k in keys):
            st._archived_model_keys.update(keys)
            newly_archived.extend(keys)
            st.log.info(
                "Auto-archived provider %s: all %d model(s) offline for >= %ds",
                provider, len(keys), threshold,
            )
        else:
            for key in keys:
                if _model_offline_for(key, threshold, now):
                    st._archived_model_keys.add(key)
                    newly_archived.append(key)
                    st.log.info(
                        "Auto-archived model %s: offline for >= %ds",
                        key, threshold,
                    )

    if newly_archived:
        await asyncio.to_thread(db.set_archived, set(newly_archived), True)
        await asyncio.to_thread(db.prune_trailing_failures, set(newly_archived))
        st.invalidate_metrics_cache()
        st.invalidate_providers_cache()


def _due_tests(test_type: str = TEST_BENCHMARK, undispatched: list[str] | None = None) -> list[str]:
    """Return model keys that are past their test interval for the given test_type.

    When stagger is enabled, benchmarks are restricted to a single provider
    at a time - either the one currently running or the first due provider
    in sorted order.
    """
    interval, epoch_key = st.test_type_schedule(test_type)
    now = time.time()
    stagger_provider = (
        _stagger_allowed_provider(undispatched)
        if test_type == TEST_BENCHMARK and st.c.benchmark_stagger
        else None
    )
    due = []
    for mk in (undispatched or _iter_undispatched_models()):
        if stagger_provider is not None and parse_model_key(mk)[0] != stagger_provider:
            continue
        cache = st.model_cache.get(mk, {})
        if not st.test_type_allows_status(test_type, cache.get("status")):
            continue
        pr = cache.get("_pending_retry")
        if pr and pr.get("test_type") == test_type:
            due.append(mk)
            continue
        ts = cache.get(epoch_key)
        if ts is None or (now - ts) >= interval:
            due.append(mk)
    return due


def _next_due_in(test_type: str = TEST_BENCHMARK, undispatched: list[str] | None = None) -> float:
    """Seconds until the soonest undispatched model becomes due for the given test_type."""
    interval, epoch_key = st.test_type_schedule(test_type)
    now = time.time()
    stagger_provider = (
        _stagger_allowed_provider(undispatched)
        if test_type == TEST_BENCHMARK and st.c.benchmark_stagger
        else None
    )
    waits = []
    for mk in (undispatched or _iter_undispatched_models()):
        if stagger_provider is not None and parse_model_key(mk)[0] != stagger_provider:
            continue
        cache = st.model_cache.get(mk, {})
        if not st.test_type_allows_status(test_type, cache.get("status")):
            continue
        ts = cache.get(epoch_key)
        if ts is not None:
            waits.append(max(interval - (now - ts), 0))
    if not waits:
        return 5.0 if (st.running_tests or st.running_health or st.running_audit or st.running_probe) else interval
    return max(min(waits), 0)


# ── Dispatch ─────────────────────────────────────────────────────────────────


def _provider_inflight() -> dict[str, int]:
    """Count in-flight tests per provider from running_tests + running_health + running_audit.

    All test types hit the same provider API, so all count toward
    per-provider concurrency at the dispatch level.  This prevents
    overloading a provider with more concurrent requests than its
    concurrent_models limit allows - regardless of test type.
    """
    counts: dict[str, int] = {}
    for mk in st.running_tests | st.running_health | st.running_audit | st.running_probe:
        provider = parse_model_key(mk)[0]
        counts[provider] = counts.get(provider, 0) + 1
    return counts


def _dispatch_due(undispatched: list[str] | None = None) -> int:
    """Dispatch due tests: health checks first, then benchmarks, then probes, then audits.

    Health checks get priority - they finish fast, freeing provider slots
    quickly for the longer-running benchmarks. All test types count toward
    per-provider concurrency at dispatch time (they hit the same API).

    When stagger is enabled, _due_tests() already filters benchmarks to a
    single provider at a time.
    """
    und = undispatched or _iter_undispatched_models()
    bench_due = _due_tests(TEST_BENCHMARK, und)
    health_due = _due_tests(TEST_HEALTH, und) if st.c.health_enabled else []
    from backend.audit import audit_available
    audit_due = _due_tests(TEST_AUDIT, und) if audit_available() else []
    probe_due = _due_tests(TEST_PROBE, und) if st.c.probe_enabled else []
    due_providers = {mk: parse_model_key(mk)[0] for mk in bench_due + health_due + audit_due + probe_due}
    inflight = _provider_inflight()
    bench_count = 0
    health_count = 0
    audit_count = 0
    probe_count = 0
    global_slots = max(st.c.max_concurrent_tests - len(st.running_tests | st.running_health | st.running_audit | st.running_probe), 0)
    if global_slots <= 0:
        return 0

    # Priority 1: Health checks (dispatch first - finish fast, free the slot)
    for mk in health_due:
        if global_slots <= 0:
            break
        if mk in st.running_tests or mk in st.running_health or mk in st.running_audit or mk in st.running_probe:
            continue
        provider = due_providers[mk]
        capacity = get_provider_concurrency(provider)
        if inflight.get(provider, 0) < capacity:
            st.running_health.add(mk)
            st.create_task(_run_test_managed(mk, test_type=TEST_HEALTH), name=f"health:{mk}")
            inflight[provider] = inflight.get(provider, 0) + 1
            global_slots -= 1
            health_count += 1

    # Priority 2: Benchmarks (already stagger-filtered by _due_tests)
    for mk in bench_due:
        if global_slots <= 0:
            break
        if mk in st.running_tests or mk in st.running_health or mk in st.running_audit or mk in st.running_probe:
            continue
        provider = due_providers[mk]
        capacity = get_provider_concurrency(provider)
        if inflight.get(provider, 0) < capacity:
            st.running_tests.add(mk)
            st.create_task(_run_test_managed(mk, test_type=TEST_BENCHMARK), name=f"benchmark:{mk}")
            inflight[provider] = inflight.get(provider, 0) + 1
            global_slots -= 1
            bench_count += 1

    # Priority 3: Probe tests (lightweight capability detection)
    for mk in probe_due:
        if global_slots <= 0:
            break
        if mk in st.running_tests or mk in st.running_health or mk in st.running_audit or mk in st.running_probe:
            continue
        provider = due_providers[mk]
        capacity = get_provider_concurrency(provider)
        if inflight.get(provider, 0) < capacity:
            st.running_probe.add(mk)
            st.create_task(_run_probe_managed(mk), name=f"probe:{mk}")
            inflight[provider] = inflight.get(provider, 0) + 1
            global_slots -= 1
            probe_count += 1

    # Priority 4: Audit tests
    for mk in audit_due:
        if global_slots <= 0:
            break
        if mk in st.running_tests or mk in st.running_health or mk in st.running_audit or mk in st.running_probe:
            continue
        provider = due_providers[mk]
        capacity = get_provider_concurrency(provider)
        if inflight.get(provider, 0) < capacity:
            st.running_audit.add(mk)
            st.create_task(_run_audit_managed(mk), name=f"audit:{mk}")
            inflight[provider] = inflight.get(provider, 0) + 1
            global_slots -= 1
            audit_count += 1

    if bench_count or health_count or audit_count or probe_count:
        suffix = ""
        if st.c.benchmark_stagger and bench_due:
            suffix = f" (stagger: {due_providers[bench_due[0]]})"
        st.log.info("Dispatched %d/%d health, %d/%d bench, %d/%d probe, %d/%d audit%s",
                     health_count, len(health_due), bench_count, len(bench_due),
                     probe_count, len(probe_due), audit_count, len(audit_due), suffix)

    return bench_count + health_count + audit_count + probe_count


async def _run_test_managed(model_key: str, test_type: str = TEST_BENCHMARK):
    """Fire-and-forget wrapper with top-level error handling."""
    try:
        await run_test(model_key, test_type=test_type)
    except asyncio.CancelledError:
        st.log.debug("run_test_managed cancelled %s type=%s", model_key, test_type)
    except Exception as e:
        st.log_error(f"Uncaught exception in managed test for {model_key}", e)


async def _run_audit_managed(model_key: str):
    """Run audit tests for a model. Simpler than run_test - no retries, no status changes."""
    from backend.audit import run_audit_test
    try:
        if model_key not in st.model_cache:
            return
        st.model_cache[model_key]["testing_audit"] = True
        st.invalidate_metrics_cache()

        result = await run_audit_test(model_key)
        if result is None:
            entry = st.model_cache.get(model_key, {})
            entry["last_audit_epoch"] = time.time()
            entry["last_audit_result"] = None
            st.invalidate_metrics_cache()
            st.log.info("Audit %s: no result (suite unavailable?)", model_key)
            return

        ts = time.time()
        result["ts_epoch"] = ts
        result["model_key"] = model_key

        from backend.db import insert_audit_result
        await asyncio.to_thread(insert_audit_result, model_key, result)

        entry = st.model_cache.get(model_key, {})
        entry["last_audit_epoch"] = ts
        audit_stripped = strip_internal(result)
        audit_stripped.pop("model_key", None)
        entry["last_audit_result"] = audit_stripped
        st.invalidate_metrics_cache()

        st.log.info("Audit %s: %d/%d passed (%.1f%%) in %.1fs%s",
                     model_key, result.get("passed", 0), result.get("total", 0),
                     result.get("pass_rate", 0) * 100, result.get("duration_ms", 0) / 1000,
                     " - FAILED" if not result.get("success") else "")

        from backend.stats import _lightweight_audit
        await ws_mgr.broadcast({
            "type": "audit_result",
            "model": model_key,
            "result": _lightweight_audit(audit_stripped),
        })
    except asyncio.CancelledError:
        st.log.debug("audit cancelled %s", model_key)
        _ae = st.model_cache.get(model_key)
        if _ae:
            _ae["last_audit_epoch"] = time.time()
            _ae["last_audit_result"] = None
    except Exception as e:
        st.log_error(f"Uncaught exception in audit for {model_key}", e)
        _ae = st.model_cache.get(model_key)
        if _ae:
            _ae["last_audit_epoch"] = time.time()
            _ae["last_audit_result"] = None
    finally:
        st.running_audit.discard(model_key)
        _ae = st.model_cache.get(model_key)
        if _ae:
            _ae["testing_audit"] = False
        st.invalidate_metrics_cache()
        if st._wake_event:
            st._wake_event.set()


async def _run_probe_managed(model_key: str):
    """Run a capability probe for a model. Like audit - no status changes, no retries."""
    from backend.probe import run_probe_test
    import backend.db as db; import backend.db_probe as db_probe
    try:
        entry = st.model_cache.get(model_key)
        if not entry:
            return
        entry["testing_probe"] = True
        st.invalidate_metrics_cache()

        result = await run_probe_test(model_key)
        ts = time.time()
        result["ts_epoch"] = ts
        result["model_key"] = model_key

        await asyncio.to_thread(db_probe.insert_probe_result, model_key, result)

        if result.get("success"):
            info_updates = {}
            for field in ("supports_vision", "supports_tools", "supports_structured_output",
                         "supports_cache", "thinking", "served_by",
                         "engine_version", "tensor_parallel", "served_model",
                         "quantization", "fp_server", "fp_features"):
                v = result.get(field)
                if v is not None:
                    if field == "thinking" and isinstance(v, bool):
                        v = "enabled" if v else None
                    if v is not None:
                        info_updates[field] = v
            if result.get("system_fingerprint"):
                info_updates["fingerprint"] = result["system_fingerprint"]

            if info_updates:
                await asyncio.to_thread(db.update_model_info, model_key, info_updates, True)
                st.model_info_cache.setdefault(model_key, {}).update(info_updates)
                st.invalidate_providers_cache()
                st.invalidate_model_info_response_cache()

            cap_str = ", ".join(
                f"{k}={'✓' if v else '✗'}" if isinstance(v, bool) else f"{k}={v}"
                for k, v in info_updates.items()
            )
            st.log.info("Probe %s: %s (%.1fs)", model_key, cap_str or "no changes",
                        result.get("duration_ms", 0) / 1000)
        else:
            st.log.info("Probe %s: failed - %s", model_key, result.get("error", "unknown"))

        entry["last_probe_epoch"] = ts
        stripped = st.strip_internal(result)
        stripped.pop("model_key", None)
        entry["last_probe_result"] = stripped
        st.invalidate_metrics_cache()
        st.invalidate_model_info_response_cache()

        await ws_mgr.broadcast({
            "type": "probe_result",
            "model": model_key,
            "result": stripped,
        })
    except asyncio.CancelledError:
        st.log.debug("probe cancelled %s", model_key)
        _entry = st.model_cache.get(model_key)
        if _entry:
            _entry["last_probe_epoch"] = time.time()
    except Exception as e:
        st.log_error(f"Uncaught exception in probe for {model_key}", e)
        _entry = st.model_cache.get(model_key)
        if _entry:
            _entry["last_probe_epoch"] = time.time()
    finally:
        st.running_probe.discard(model_key)
        _entry = st.model_cache.get(model_key)
        if _entry:
            _entry["testing_probe"] = False
        st.invalidate_metrics_cache()
        st.invalidate_model_info_response_cache()
        if st._wake_event:
            st._wake_event.set()


async def scheduler():
    """Event-driven scheduler: dispatches on test completion, config change, or timer.

    Dispatch order per cycle: health checks first (finish fast, free provider
    slots), then benchmarks, then probes, then audits. Dispatches up to each
    provider's concurrent_models capacity (greedy dispatch) to prevent
    semaphore queue buildup and keep benchmarks close to their scheduled time.
    """
    if st.scheduler_running:
        return
    st.scheduler_running = True
    st._wake_event = asyncio.Event()
    st.config_changed = asyncio.Event()
    stagger_msg = ", stagger: 1 provider at a time" if st.c.benchmark_stagger else ""
    audit_msg = f"{st.c.audit_interval}s" if st.c.audit_interval else "disabled"
    archive_msg = f", auto-archive: {st.c.auto_archive_offline_duration}s" if st.c.auto_archive_enabled else ""
    st.log.info(
        "Scheduler started - benchmark: %ss, health: %ss, audit: %s, probe: %ss, max concurrent: %d%s%s (first run in %ds)",
        st.c.benchmark_interval, st.c.health_interval, audit_msg, st.c.probe_interval, st.c.max_concurrent_tests, stagger_msg, archive_msg, st.c.initial_delay,
    )
    await asyncio.sleep(st.c.initial_delay)
    _last_cleanup = time.monotonic()
    while True:
        try:
            await apply_auto_archive()
            undispatched = _iter_undispatched_models()
            dispatched = _dispatch_due(undispatched)
            if dispatched:
                st.last_run_time = time.monotonic()

            # Periodic DB cleanup: delete old results and orphaned entries
            now = time.monotonic()
            if now - _last_cleanup >= st.c.cleanup_interval:
                _last_cleanup = now
                try:
                    import backend.db as db; import backend.db_probe as db_probe
                    cutoff = time.time() - st.c.retention_days * 86400
                    deleted = await asyncio.to_thread(db.delete_old_results, cutoff)
                    if deleted:
                        st.log.info("DB cleanup: deleted %d old results (retention=%dd)", deleted, st.c.retention_days)
                    audit_deleted = await asyncio.to_thread(db_probe.delete_old_audit_results, cutoff)
                    if audit_deleted:
                        st.log.info("DB cleanup: deleted %d old audit results (retention=%dd)", audit_deleted, st.c.retention_days)
                    probe_deleted = await asyncio.to_thread(db_probe.delete_old_probe_results, cutoff)
                    if probe_deleted:
                        st.log.info("DB cleanup: deleted %d old probe results (retention=%dd)", probe_deleted, st.c.retention_days)
                    reg_keys = {e["id"] for e in st.model_registry}
                    reg_providers = {e["provider"] for e in st.model_registry}
                    removed = await asyncio.to_thread(db.delete_removed_entries, reg_keys, reg_providers)
                    if removed["models"] or removed["providers"]:
                        st.log.info("DB cleanup: removed %d orphaned models (%d results), %d orphaned providers",
                                    removed["models"], removed["results"], removed["providers"])
                        for mk in list(st.model_cache.keys()):
                            if mk not in reg_keys:
                                st.model_cache.pop(mk, None)
                        st.invalidate_metrics_cache()
                        st.invalidate_providers_cache()
                        st.invalidate_model_info_response_cache()
                except Exception as e:
                    st.log_error("DB cleanup error", e)

            # Sleep until next due test, config change, or test completion
            health_wait = _next_due_in(TEST_HEALTH, undispatched) if st.c.health_enabled else float("inf")
            bench_wait = _next_due_in(TEST_BENCHMARK, undispatched)
            audit_wait = _next_due_in(TEST_AUDIT, undispatched) if st.c.audit_enabled else float("inf")
            probe_wait = _next_due_in(TEST_PROBE, undispatched) if st.c.probe_enabled else float("inf")
            sleep_time = min(health_wait, bench_wait, audit_wait, probe_wait)

            if (st.running_tests or st.running_health or st.running_audit or st.running_probe) and sleep_time < 1.0:
                sleep_time = 1.0

            st.next_run_time = time.monotonic() + sleep_time
            if sleep_time > 0:
                try:
                    await asyncio.wait_for(st._wake_event.wait(), timeout=sleep_time)
                except asyncio.TimeoutError:
                    pass
                st._wake_event.clear()
                st.config_changed.clear()
        except asyncio.CancelledError:
            st.scheduler_running = False
            st.log.info("Scheduler stopped")
            break
        except Exception as e:
            st.log_error("Scheduler error", e)
            await asyncio.sleep(5.0)
