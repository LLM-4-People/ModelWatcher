"""In-memory model cache initialization.

All scoring, tier resolution, trend computation, and API response
building live in backend.stats. This module only provides
ensure_model() and make_cache_entry() for cache entry construction.
"""

from backend.state import model_cache, invalidate_metrics_cache


def make_cache_entry(**overrides) -> dict:
    """Create a model cache entry with all schema fields defaulted.

    The single factory for cache entries - pass keyword overrides to set
    specific fields at construction. Internal underscore-prefixed fields
    (_scores_version, _card_buckets, etc.) are cache-layer bookkeeping.
    """
    entry = {
        "status": "unknown",
        "degraded_source": None,
        "degraded_since": None,
        "tps_degraded_since": None,
        "ttft_degraded_since": None,
        "testing_health": False,
        "testing_benchmark": False,
        "testing_audit": False,
        "testing_probe": False,
        "uptime_pct": None,
        "last_test": None,
        "last_success_test": None,
        "last_success_epoch": None,
        "last_benchmark_epoch": None,
        "last_health_epoch": None,
        "last_health_success": None,
        "last_health_error": None,
        "last_health_ttft_ms": None,
        "last_health_request_id": None,
        "last_health_success_epoch": None,
        "last_audit_epoch": None,
        "last_audit_result": None,
        "last_probe_epoch": None,
        "last_probe_result": None,
        "first_ts_epoch": None,
        "total_tests": 0,
        "total_success": 0,
        "recent_history": [],
        "reliability_score": None,
        "trends": {},
        "_scores_version": 0,
        "_cached_scores": None,
        "_cached_scores_version": -1,
        "_card_buckets": None,
        "_card_buckets_version": -1,
    }
    entry.update(overrides)
    return entry


def ensure_model(key: str):
    """Create a model cache entry for key if it does not already exist."""
    if key not in model_cache:
        model_cache[key] = make_cache_entry()
        invalidate_metrics_cache()
