"""Statistical computations: composite scores, trends, and bucketed history aggregation.

All scoring and trend logic lives here. tier_idx() is the single source of
truth for tier resolution - imported by streaming.py, metrics.py, and
notifications.py. API response builders (summary, chart, history) also live
here since they aggregate the same statistical primitives.
"""

import math
import time
from typing import Any

from backend.state import c, log, normalize_thinking


# ── Helpers ──────────────────────────────────────────────────────────────────

def _lightweight_audit(ar: dict | None) -> dict | None:
    """Strip suites from audit result for summary/polling responses.

    Cards only need top-level ``total``/``pass_rate``/``success``/``duration_ms``.
    The cache always stores the full result; this is only removed at the
    /api/metrics collection-response boundary to reduce polling payload.
    The modal lazy-loads full suites with evals via ``/api/audit?model=X``,
    which serves the ``latest`` from model_cache (no DB hit).
    """
    if not ar:
        return None
    if "suites" not in ar:
        return ar
    return {k: v for k, v in ar.items() if k != "suites"}


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def percentile(sorted_vals: list[float], p: float) -> float | None:
    """Compute the p-th percentile (0-100) using linear interpolation.

    Values must be pre-sorted ascending.
    Returns None for empty lists. For single values, returns the value.
    Shared by stats.py and streaming.py.
    """
    if not sorted_vals:
        return None
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    k = (p / 100) * (n - 1)
    lo = int(k)
    hi = lo + 1
    if hi >= n:
        return sorted_vals[lo]
    frac = k - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


# ── Tier resolution (shared by metrics.py + notifications.py) ───────────────

def tier_idx(value: float, thresholds: list, higher_is_better: bool) -> int:
    """Return the tier index for a value given thresholds and direction.

    Iterates thresholds; for higher_is_better, the first threshold where
    value >= threshold hits. For lower_is_better, the first where value < threshold.
    Falls through to len(thresholds) - 1 (worst tier) if nothing hits.
    Returns -1 for empty thresholds or None value.
    """
    if value is None or not thresholds:
        return -1
    for i, th in enumerate(thresholds):
        if higher_is_better and value >= th:
            return i
        if not higher_is_better and value < th:
            return i
    return len(thresholds) - 1


def tier_continuous_score(value: float, thresholds: list, higher_is_better: bool) -> float | None:
    """Continuous [0,1] score from piecewise linear interpolation over tier breakpoints.

    1.0 = best possible value, 0.0 = worst possible.
    Each threshold[i] maps to score = 1.0 - i/(n-1) for hib, or 1.0 - (i+1)/(n-1)
    for lib (since lib tier_idx at threshold[i] is i+1). Values between thresholds
    are linearly interpolated. Values beyond range are clamped.

    Boundary semantics match tier_idx: >= for hib, < for lib.
    For lower_is_better, a sentinel 0 in the last position (e.g. TTFT
    [1000,3000,5000,10000,0]) means the worst tier has no finite upper bound -
    values at or beyond the second-to-last threshold receive score 0.0.
    """
    if value is None or not thresholds or len(thresholds) < 2:
        return None

    n = len(thresholds)
    denom = n - 1

    def _lerp(lo_score, hi_score, frac):
        return round(lo_score + frac * (hi_score - lo_score), 4)

    if higher_is_better:
        if value >= thresholds[0]:
            return 1.0
        if value < thresholds[-1]:
            return 0.0
        for i in range(n - 1):
            upper = thresholds[i]
            lower = thresholds[i + 1]
            if upper >= value > lower:
                band = upper - lower
                if band == 0:
                    return round(1.0 - i / denom, 4)
                frac = (value - lower) / band
                return _lerp(1.0 - (i + 1) / denom, 1.0 - i / denom, frac)
    else:
        if value < thresholds[0]:
            return 1.0
        has_sentinel = n >= 2 and thresholds[-1] <= 0 < thresholds[-2]
        if has_sentinel:
            if value >= thresholds[-2]:
                return 0.0
            for i in range(n - 2):
                lo_th = thresholds[i]
                hi_th = thresholds[i + 1]
                if lo_th <= value < hi_th:
                    band = hi_th - lo_th
                    if band == 0:
                        return round(1.0 - (i + 1) / denom, 4)
                    frac = (value - lo_th) / band
                    return _lerp(1.0 - (i + 1) / denom, 1.0 - (i + 2) / denom, frac)
        else:
            if value >= thresholds[-1]:
                return 0.0
            for i in range(n - 1):
                lo_th = thresholds[i]
                hi_th = thresholds[i + 1]
                if lo_th <= value < hi_th:
                    band = hi_th - lo_th
                    if band == 0:
                        return round(1.0 - (i + 1) / denom, 4)
                    frac = (value - lo_th) / band
                    return _lerp(1.0 - (i + 1) / denom, 1.0 - (i + 2) / denom, frac)

    return round(1.0 - tier_idx(value, thresholds, higher_is_better) / denom, 4)


# ── Per-test stall position metrics ────────────────────────────────────────

def compute_stall_metrics(raw_itls: list[float], effective_stall_threshold: float,
                          chunk_count: int) -> dict:
    """Compute stall position and clustering metrics from raw ITLs.

    Returns dict with: stall_first_pct, stall_last_pct, stall_clusters, stall_ratio.
    """
    if not raw_itls or chunk_count < 2:
        return {"stall_first_pct": None, "stall_last_pct": None,
                "stall_clusters": 0, "stall_ratio": None}

    # Identify stall indices (0-based among ITLs, which = chunk gaps)
    stall_indices = [i for i, itl in enumerate(raw_itls) if itl > effective_stall_threshold]
    stall_count = len(stall_indices)

    if stall_count == 0:
        return {"stall_first_pct": None, "stall_last_pct": None,
                "stall_clusters": 0, "stall_ratio": 0.0}

    # Position percentages: where in the stream stalls occur
    # ITL index i = gap between chunk i and chunk i+1 (out of chunk_count-1 gaps)
    total_gaps = len(raw_itls)
    first_pct = round(stall_indices[0] / total_gaps * 100, 1)
    last_pct = round(stall_indices[-1] / total_gaps * 100, 1)

    # Clusters: consecutive stall gaps separated by non-stall gaps
    clusters = 1
    for k in range(1, len(stall_indices)):
        if stall_indices[k] != stall_indices[k - 1] + 1:
            clusters += 1

    # Stall ratio: fraction of total gaps that are stalls
    ratio = round(stall_count / total_gaps, 3)

    return {
        "stall_first_pct": first_pct,
        "stall_last_pct": last_pct,
        "stall_clusters": clusters,
        "stall_ratio": ratio,
    }


def empty_stall_metrics() -> dict:
    return {"stall_first_pct": None, "stall_last_pct": None,
            "stall_clusters": 0, "stall_ratio": None}


# ── Composite scores (0-100) ────────────────────────────────────────────────

def _weighted_tier_score(record: dict, metrics_map: dict[str, str],
                         weights: dict[str, float],
                         color_thresholds: dict | None = None) -> float | None:
    """Compute a weighted score from 0-100 using continuous tier interpolation.

    metrics_map: {result_key: threshold_metric_name}
    weights: {result_key: weight}
    Returns None if fewer than 2 components have valid data.
    """
    ct = color_thresholds or c.color_thresholds

    weighted_sum = 0.0
    weight_sum = 0.0
    valid = 0

    for rkey, metric_name in metrics_map.items():
        value = record.get(rkey)
        if value is None:
            continue
        w = weights.get(rkey, 0)
        if w == 0:
            continue
        cfg = ct.get(metric_name, {})
        thresholds = cfg.get("thresholds", [])
        hib = cfg.get("higher_is_better", True)
        score = tier_continuous_score(value, thresholds, hib)
        if score is None:
            continue
        weighted_sum += w * score
        weight_sum += w
        valid += 1

    if valid < 2:
        return None
    return round(weighted_sum / weight_sum * 100, 1)


_CONSISTENCY_METRICS = {
    "stall_count": "stall_count",
    "effective_itl_tail_ratio": "effective_itl_tail_ratio",
    "chunk_token_ratio": "chunk_token_ratio",
    "burst_arrival_pct": "burst_arrival_pct",
}

_SPEED_METRICS = {
    "ttft_ms": "ttft",
    "tps": "tps",
}


def compute_consistency_score(record: dict, color_thresholds: dict | None = None) -> float | None:
    """Compute consistency score (0-100) for a test result.

    Returns None for failed/degraded results or <2 valid components.
    """
    if not record.get("success") or record.get("degraded"):
        return None
    weights = c.scores_consistency_weights
    score = _weighted_tier_score(record, _CONSISTENCY_METRICS, weights, color_thresholds)
    if score is not None:
        log.debug("consistency: stalls=%s tail=%.2f batch=%.2f burst=%s → %.1f",
                   record.get("stall_count"), record.get("effective_itl_tail_ratio") or 0,
                   record.get("chunk_token_ratio") or 0, record.get("burst_arrival_pct"), score)
    return score


def compute_speed_score(record: dict, color_thresholds: dict | None = None) -> float | None:
    """Compute speed score (0-100) for a test result.

    Returns None for failed results or <2 valid components.
    """
    if not record.get("success"):
        return None
    weights = c.scores_speed_weights
    score = _weighted_tier_score(record, _SPEED_METRICS, weights, color_thresholds)
    if score is not None:
        log.debug("speed: ttft=%.0f tps=%.1f → %.1f",
                   record.get("ttft_ms", 0), record.get("tps", 0), score)
    return score


def _reliability_formula(avail_score: float, quality: float) -> float:
    """Apply the shared reliability formula: avail_score × (avail_w + quality_w × quality) × 100."""
    avail_weight = c.scores_reliability_avail_weight
    quality_weight = c.scores_reliability_quality_weight
    reliability = avail_score * (avail_weight + quality_weight * quality) * 100
    return round(min(max(reliability, 0), 100), 1)


def compute_reliability_score(model_key: str, color_thresholds: dict | None = None) -> float | None:
    """Compute reliability score (0-100) for a model from its recent history.

    reliability = avail_score × (avail_w + quality_w × quality) × 100
    where avail_score is continuous interpolation of uptime within its tier band
    and quality = 1 - degradation_rate
    """
    from backend.state import model_cache
    ct = color_thresholds or c.color_thresholds
    entry = model_cache.get(model_key)
    if not entry:
        return None

    history = entry.get("recent_history", [])
    min_pts = c.min_data_points_score
    if len(history) < min_pts:
        return None

    uptime_pct = entry.get("uptime_pct")
    if uptime_pct is None:
        return None

    uptime_cfg = ct.get("uptime", {})
    thresholds = uptime_cfg.get("thresholds", [])
    hib = uptime_cfg.get("higher_is_better", True)
    avail_score = tier_continuous_score(uptime_pct, thresholds, hib)
    if avail_score is None:
        return None

    bench_history = bench_only(history)
    if not bench_history:
        return None
    degraded_count = sum(1 for r in bench_history if r.get("degraded"))
    quality = 1.0 - (degraded_count / len(bench_history))

    result = _reliability_formula(avail_score, quality)
    log.debug("reliability: %s uptime=%.1f%% avail=%.3f quality=%.3f degraded=%d/%d → %.1f",
              model_key, uptime_pct, avail_score, quality, degraded_count, len(bench_history), result)
    return result


def compute_range_reliability(records: list[dict], uptime_pct: float | None = None,
                              color_thresholds: dict | None = None) -> float | None:
    """Compute reliability score from an arbitrary list of records.

    Unlike compute_reliability_score (which reads from model_cache),
    this works with any record list - used for range-aware scoring.
    If uptime_pct is not provided, it's computed from the records.
    """
    ct = color_thresholds or c.color_thresholds
    min_pts = c.min_data_points_score
    if len(records) < min_pts:
        return None

    if uptime_pct is None:
        avail_count = sum(1 for r in records if r.get("available"))
        if not records:
            return None
        uptime_pct = round(avail_count / len(records) * 100, 1)
    if uptime_pct is None:
        return None

    uptime_cfg = ct.get("uptime", {})
    thresholds = uptime_cfg.get("thresholds", [])
    hib = uptime_cfg.get("higher_is_better", True)
    avail_score = tier_continuous_score(uptime_pct, thresholds, hib)
    if avail_score is None:
        return None

    bench_history = bench_only(records)
    if not bench_history:
        bench_history = records
    degraded_count = sum(1 for r in bench_history if r.get("degraded"))
    quality = 1.0 - (degraded_count / len(bench_history))

    return _reliability_formula(avail_score, quality)


# ── Trends (test-count-based) ───────────────────────────────────────────────

def bench_only(records: list[dict]) -> list[dict]:
    """Filter records to benchmark-only (excludes health checks)."""
    return [r for r in records if r.get("test_type", "benchmark") == "benchmark"]

_TREND_METRICS = [
    "tps", "ttft_ms", "stall_count", "raw_p99_itl_ms",
    "effective_itl_tail_ratio", "chunk_token_ratio",
    "consistency_score", "speed_score",
    "available", "reliability_score",
]

_TREND_HIB = {
    "tps": True, "ttft_ms": False, "stall_count": False,
    "raw_p99_itl_ms": False, "effective_itl_tail_ratio": False,
    "chunk_token_ratio": False, "consistency_score": True,
    "speed_score": True, "available": True, "reliability_score": True,
}

_TREND_UNITS = {
    "tps": "t/s", "ttft_ms": "ms", "stall_count": "",
    "raw_p99_itl_ms": "ms", "effective_itl_tail_ratio": "\u00d7",
    "chunk_token_ratio": "\u00d7", "consistency_score": "pts",
    "speed_score": "pts", "available": "pp", "reliability_score": "pts",
}

_TREND_THRESHOLDS = {
    "tps": 5.0, "ttft_ms": 500.0, "stall_count": 1.0,
    "raw_p99_itl_ms": 20.0, "effective_itl_tail_ratio": 0.5,
    "chunk_token_ratio": 0.5, "consistency_score": 5.0,
    "speed_score": 5.0, "available": 5.0, "reliability_score": 5.0,
}

_TREND_BINARY = frozenset({"available"})

_TREND_MIN_RECENT = 3


def _sliding_medianCompare(baseline: list[float], recent: list[float],
                            hib: bool, threshold: float) -> dict | None:
    """Compare medians of baseline (75% window) vs recent (25% window).

    Returns {direction, change} or None if insufficient data. change is the
    absolute delta (recent - baseline) in the metric's native unit. For
    lower_is_better metrics the sign is flipped so positive = improving.
    """
    bl_med = _median(baseline)
    rc_med = _median(recent)
    if bl_med is None or rc_med is None:
        return None

    delta = rc_med - bl_med
    if not hib:
        delta = -delta

    if abs(delta) < threshold:
        return {"direction": "stable", "change": round(abs(rc_med - bl_med), 2)}
    return {"direction": "improving" if delta > 0 else "degrading",
            "change": round(abs(rc_med - bl_med), 2)}


def compute_trends(records: list[dict], color_thresholds: dict | None = None) -> dict[str, dict]:
    """Compute trend direction and absolute change using 75/25 sliding median.

    Splits records: first 75% = baseline (stable reference), last 25% = recent
    (current state). Compares median of each window per metric.

    Returns {metric: {"direction": "improving"|"degrading"|"stable",
                       "change": float, "unit": str, "data_points": int}}
    Plus "since_ts" = timestamp of first record in the range.
    """
    min_pts = c.min_data_points_trend
    n = len(records)
    if n < min_pts:
        return {}

    first_ts = (records[0].get("_ts_epoch") or records[0].get("ts_epoch")) if records else None
    since_ts = first_ts

    split = max(1, int(n * 0.75))
    baseline_recs = records[:split]
    recent_recs = records[split:]

    if len(recent_recs) < _TREND_MIN_RECENT:
        return {}

    log.debug("trends: n=%d split=%d/%d since=%s", n, len(baseline_recs), len(recent_recs),
              since_ts and time.strftime("%Y-%m-%d %H:%M", time.gmtime(since_ts)) or "?")

    trends = {}

    for metric in _TREND_METRICS:
        threshold = _TREND_THRESHOLDS.get(metric, 5.0)
        hib = _TREND_HIB.get(metric, True)
        unit = _TREND_UNITS.get(metric, "")

        if metric in _TREND_BINARY:
            bl_vals = [bool(r.get("available")) for r in baseline_recs if r.get("available") is not None]
            rc_vals = [bool(r.get("available")) for r in recent_recs if r.get("available") is not None]
            if not bl_vals or not rc_vals:
                continue
            bl_rate = sum(bl_vals) / len(bl_vals) * 100
            rc_rate = sum(rc_vals) / len(rc_vals) * 100
            delta = rc_rate - bl_rate
            eff_delta = delta if hib else -delta
            direction = "stable"
            if abs(delta) >= threshold:
                direction = "improving" if eff_delta > 0 else "degrading"
            trends[metric] = {
                "direction": direction,
                "change": round(abs(delta), 1),
                "unit": unit,
                "data_points": len(rc_vals),
            }
            continue

        if metric == "reliability_score":
            bl_r = compute_range_reliability(baseline_recs, color_thresholds=color_thresholds)
            rc_r = compute_range_reliability(recent_recs, color_thresholds=color_thresholds)
            if bl_r is None or rc_r is None:
                continue
            delta = rc_r - bl_r
            direction = "stable"
            if abs(delta) >= threshold:
                direction = "improving" if delta > 0 else "degrading"
            trends[metric] = {
                "direction": direction,
                "change": round(abs(delta), 1),
                "unit": unit,
                "data_points": len(recent_recs),
            }
            continue

        bl_vals = [r[metric] for r in baseline_recs if r.get(metric) is not None]
        rc_vals = [r[metric] for r in recent_recs if r.get(metric) is not None]
        if len(bl_vals) < min_pts or len(rc_vals) < _TREND_MIN_RECENT:
            continue

        result = _sliding_medianCompare(bl_vals, rc_vals, hib, threshold)
        if result is None:
            continue

        trends[metric] = {
            "direction": result["direction"],
            "change": result["change"],
            "unit": unit,
            "data_points": len(rc_vals),
        }

    if since_ts is not None:
        trends["since_ts"] = since_ts

    log.debug("trends: computed %d metrics: %s", len({k: v for k, v in trends.items() if k != "since_ts"}),
              ", ".join(f"{k}={v['direction']}" for k, v in sorted(trends.items()) if k != "since_ts"))

    return trends


# ── Bucketed history aggregation ───────────────────────────────────────────

CARD_VIEW_METRICS = {
    "speed": ["tps", "ttft_ms"],
    "consistency": ["raw_p99_itl_ms", "chunk_token_ratio"],
    "scores": ["consistency_score", "speed_score"],
    "health": ["ttft_ms"],
}


def _aggregate_card_bucket(records: list[dict], bucket_start: float,
                            view: str = "speed") -> dict:
    """Flat metrics for card mini-charts, view-aware.

    When view="all", returns shared fields plus all view-specific fields
    in a single dict (for dedup multi-view precomputation).
    Otherwise returns only shared + the requested view's fields.
    """
    n = len(records)
    avail_count = sum(1 for r in records if r.get("available"))
    degraded_count = sum(1 for r in records if r.get("degraded"))
    mean_ts = _mean_ts(records) or bucket_start

    shared: dict = {
        "ts": mean_ts,
        "available_rate": round(avail_count / n, 3) if n else 0,
        "degraded_rate": round(degraded_count / n, 3) if n else 0,
        "count": n,
    }

    ar = shared["available_rate"] if shared["available_rate"] is not None else 0
    dr = shared["degraded_rate"] if shared["degraded_rate"] is not None else 0

    views = CARD_VIEW_METRICS if view == "all" else {view: CARD_VIEW_METRICS.get(view, CARD_VIEW_METRICS["speed"])}
    per_view: dict[str, dict] = {}
    for vname, fields in views.items():
        vdata: dict = {}
        for field in fields:
            vals = [r[field] for r in records if r.get(field) is not None]
            vdata[field] = _median(vals) if vals else None
            if len(vals) > 1 and vname != "scores":
                sv = sorted(vals)
                vdata[field + "_p10"] = round(percentile(sv, 10), 1)
                vdata[field + "_p90"] = round(percentile(sv, 90), 1)
        if vname == "scores":
            vdata["reliability_score"] = _bucket_reliability(ar, dr)
        per_view[vname] = vdata

    if view == "all":
        result = dict(shared)
        for vname, vdata in per_view.items():
            result[vname] = vdata
        return result

    result = dict(shared)
    result.update(per_view.get(view, {}))
    return result


def _bucket_reliability(available_rate: float, degraded_rate: float,
                        color_thresholds: dict | None = None) -> float | None:
    """Compute reliability score from pre-aggregated bucket rates.

    Same formula as compute_range_reliability but works with rates
    instead of record lists - for bucket-level aggregation.
    """
    ct = color_thresholds or c.color_thresholds
    uptime_pct = available_rate * 100
    uptime_cfg = ct.get("uptime", {})
    thresholds = uptime_cfg.get("thresholds", [])
    hib = uptime_cfg.get("higher_is_better", True)
    avail_score = tier_continuous_score(uptime_pct, thresholds, hib)
    if avail_score is None:
        return None
    quality = 1.0 - degraded_rate
    return _reliability_formula(avail_score, quality)


def _aggregate_modal_bucket(records: list[dict], bucket_start: float,
                            view: str = "speed") -> dict:
    """Nested metrics with avg/p10/p90 for modal full charts."""
    def _pstats(key):
        vals = [r[key] for r in records if r.get(key) is not None]
        if not vals:
            return None
        sv = sorted(vals)
        return {"avg": round(_mean(vals), 1), "p10": round(percentile(sv, 10), 1), "p90": round(percentile(sv, 90), 1)}

    avail_count = sum(1 for r in records if r.get("available"))
    degraded_count = sum(1 for r in records if r.get("degraded"))
    n = len(records) or 1
    mean_ts = _mean_ts(records) or bucket_start

    base: dict = {
        "ts": mean_ts,
        "available_rate": round(avail_count / n, 3) if records else 0,
        "degraded_rate": round(degraded_count / n, 3) if records else 0,
        "count": len(records),
    }

    base["tps"] = _pstats("tps")
    base["ttft_ms"] = _pstats("ttft_ms")
    base["raw_p99_itl_ms"] = _pstats("raw_p99_itl_ms")
    base["chunk_token_ratio"] = _pstats("chunk_token_ratio")
    cs_vals = [r["consistency_score"] for r in records if r.get("consistency_score") is not None]
    ss_vals = [r["speed_score"] for r in records if r.get("speed_score") is not None]
    base["consistency_score"] = {"avg": round(_mean(cs_vals), 1)} if cs_vals else None
    base["speed_score"] = {"avg": round(_mean(ss_vals), 1)} if ss_vals else None
    ar = base["available_rate"] if base["available_rate"] is not None else 0
    dr = base["degraded_rate"] if base["degraded_rate"] is not None else 0
    base["reliability_score"] = {"avg": _bucket_reliability(ar, dr)} if records else None

    return base


def _mean_ts(records: list[dict]) -> float | None:
    """Mean timestamp of records in a bucket (for more accurate bucket positioning)."""
    if not records:
        return None
    ts_vals = [r.get("_ts_epoch") or r.get("ts_epoch") for r in records]
    ts_vals = [t for t in ts_vals if t]
    return _mean(ts_vals) if ts_vals else None


def _bucket_params(data_start: float, data_end: float, num_buckets: int) -> tuple[float, float, float]:
    """Compute chart_start, chart_end, bucket_width from data endpoints.

    Charts fit exactly to data with no padding - the first data point
    sits at the left edge and the last at the right edge.
    Degenerate single-point data gets a minimum 1s range.
    """
    data_range = data_end - data_start
    if data_range <= 0:
        data_range = 1
    bucket_width = data_range / num_buckets if num_buckets > 0 else 1
    return data_start, data_end, bucket_width


def _trim_empty_buckets(buckets: list[dict]) -> list[dict]:
    """Remove leading and trailing empty buckets to avoid wasted chart space."""
    if not buckets:
        return buckets
    first_data = None
    last_data = None
    for i, b in enumerate(buckets):
        if b.get("count"):
            if first_data is None:
                first_data = i
            last_data = i
    if first_data is None:
        return buckets  # All empty
    return buckets[first_data:last_data + 1]


_VIEW_PRIMARY_FIELD = {
    "speed": "tps",
    "consistency": "raw_p99_itl_ms",
    "health": "ttft_ms",
}


def _filter_no_primary(bucket_data: list[dict], view: str) -> list[dict]:
    """Remove buckets that lack the primary metric for the chart view.

    Each view has a required metric - if a bucket has no real data point for it,
    the bucket is meaningless for that view and should not appear in the chart.
    - speed: tps OR ttft_ms (TTFT-only buckets from health data are kept)
    - consistency: p99_itl_ms (requires at least 1 ITL measurement)
    - health: ttft_ms (requires at least 1 health TTFT)
    - scores: no filter (derived from available_rate/degraded_rate)
    """
    primary = _VIEW_PRIMARY_FIELD.get(view)
    if primary:
        return [b for b in bucket_data if b.get(primary) is not None]
    return bucket_data


def _filter_no_primary_all(bucket_data: list[dict]) -> list[dict]:
    """Remove buckets lacking ANY view's primary metric (all-views combined format).

    A bucket is kept if at least one view has its primary metric present.
    Empty buckets (count=0 with no view data) are removed entirely.
    """
    return [b for b in bucket_data
            if b.get("count") or any(b.get(v, {}).get(f) is not None
                                     for v, f in _VIEW_PRIMARY_FIELD.items())]


def _attach_markers(bucket_data: list[dict], markers: list[dict],
                    prefix: str, bucket_width: float) -> None:
    """Merge cross-type marker counts into chart buckets in-place.

    Adjusts count, available_rate, and degraded_rate to include cross-type
    failures/degraded results so the frontend computes correct marker counts
    from the standard fields (no extra_* needed).
    """
    if not markers:
        return
    marker_map = {m["bucket_ts"]: m for m in markers}
    for b in bucket_data:
        bts = math.floor(b["ts"] / bucket_width) * bucket_width
        m = marker_map.get(bts)
        if not m:
            continue
        extra_fail = m["failure_count"]
        extra_degr = m["degraded_count"]
        if extra_fail <= 0 and extra_degr <= 0:
            continue
        old_count = b.get("count") or 0
        old_avail = b.get("available_rate")
        old_degr_rate = b.get("degraded_rate") or 0.0
        if old_count == 0:
            old_avail = 1.0
        elif old_avail is None:
            old_avail = 1.0
        old_success = round(old_avail * old_count)
        old_degraded = round(old_degr_rate * old_count)
        new_count = old_count + extra_fail + extra_degr
        new_success = old_success + extra_degr  # degraded results ARE available
        new_degraded = old_degraded + extra_degr
        b["count"] = new_count
        b["available_rate"] = min(new_success / new_count, 1.0) if new_count > 0 else 0.0
        b["degraded_rate"] = min(new_degraded / new_count, 1.0) if new_count > 0 else 0.0


def _attach_cross_ttft(bucket_data: list[dict], model_key: str,
                       since: float, until: float, bucket_width: float,
                       detail: str, cross_type: str, db) -> None:
    """Merge cross-type TTFT into primary ttft_ms.

    Where primary ttft_ms is null, fills from cross-type TTFT (gap-fill).
    Where primary ttft_ms exists, expands p10/p90 range to include cross-type
    TTFT values - TTFT is TTFT regardless of source. P10/P90 from cross-type
    data with few points per bucket approximates min/max, which is acceptable
    for cross-type overlay.

    cross_type: "benchmark" -> query benchmark TTFT (for health view);
                "health"    -> query health TTFT (for speed view).
    """
    if not bucket_data or bucket_width <= 0:
        return
    if cross_type == "benchmark":
        cross_buckets = db.query_bucketed_history(model_key, since, bucket_width,
                                                   "benchmark", detail, "speed")
    else:
        cross_buckets = db.query_bucketed_health(model_key, since, bucket_width,
                                                   flat=(detail == "card"), until=until)
    if not cross_buckets:
        return
    cross_map: dict[float, Any] = {}
    for cb in cross_buckets:
        bts = cb.get("bucket_ts", math.floor(cb["ts"] / bucket_width) * bucket_width)
        ttft = cb.get("ttft_ms")
        if ttft is not None:
            # ttft_ms may be a nested dict (modal) or a scalar (card).
            if isinstance(ttft, dict):
                c_p10 = ttft.get("p10")
                c_p90 = ttft.get("p90")
                c_avg = ttft.get("avg")
            else:
                c_p10 = cb.get("ttft_ms_p10", ttft)
                c_p90 = cb.get("ttft_ms_p90", ttft)
                c_avg = ttft
            cross_map[bts] = {
                "ttft_ms": c_avg if isinstance(ttft, dict) else ttft,
                "ttft_ms_p10": c_p10,
                "ttft_ms_p90": c_p90,
            }
    for b in bucket_data:
        bts = math.floor(b["ts"] / bucket_width) * bucket_width
        cross = cross_map.get(bts)
        if cross is None:
            continue

        if b.get("ttft_ms") is None:
            # Gap-fill: no primary TTFT - use cross-type.
            c_avg = cross["ttft_ms"]
            c_p10 = cross.get("ttft_ms_p10", c_avg)
            c_p90 = cross.get("ttft_ms_p90", c_avg)
            if detail == "modal":
                b["ttft_ms"] = {"avg": c_avg, "p10": c_p10, "p90": c_p90}
            else:
                b["ttft_ms"] = c_avg
                b["ttft_ms_p10"] = c_p10
                b["ttft_ms_p90"] = c_p90
        elif detail == "card":
            # Range-enhance: expand existing p10/p90 with cross-type range.
            c_p10 = cross.get("ttft_ms_p10")
            c_p90 = cross.get("ttft_ms_p90")
            if c_p10 is not None:
                existing_p10 = b.get("ttft_ms_p10")
                if existing_p10 is None:
                    b["ttft_ms_p10"] = c_p10
                else:
                    b["ttft_ms_p10"] = round(min(existing_p10, c_p10), 1)
            if c_p90 is not None:
                existing_p90 = b.get("ttft_ms_p90")
                if existing_p90 is None:
                    b["ttft_ms_p90"] = c_p90
                else:
                    b["ttft_ms_p90"] = round(max(existing_p90, c_p90), 1)
        elif detail == "modal":
            existing = b.get("ttft_ms")
            if isinstance(existing, dict):
                c_p10 = cross.get("ttft_ms_p10")
                c_p90 = cross.get("ttft_ms_p90")
                if c_p10 is not None:
                    e_p10 = existing.get("p10")
                    existing["p10"] = round(min(e_p10, c_p10), 1) if e_p10 is not None else c_p10
                if c_p90 is not None:
                    e_p90 = existing.get("p90")
                    existing["p90"] = round(max(e_p90, c_p90), 1) if e_p90 is not None else c_p90




def compute_bucketed_history(records: list[dict], buckets: int,
                              detail: str = "card", view: str = "speed") -> list[dict]:
    """Aggregate records into time buckets from RAM (recent_history).

    detail: "card" (flat metrics) or "modal" (nested avg/p10/p90).
    view: "speed", "consistency", "scores", or "health" - controls which
    metrics are aggregated. Returns buckets trimmed of leading/trailing empties.
    """
    if not records or buckets <= 0:
        return []

    ts_vals = [r.get("_ts_epoch", r.get("ts_epoch", 0)) for r in records
                if r.get("_ts_epoch") or r.get("ts_epoch")]
    if not ts_vals:
        return []

    chart_start, _, bucket_width = _bucket_params(min(ts_vals), max(ts_vals), buckets)
    if bucket_width <= 0:
        return []

    # Group records by bucket
    bucket_map: dict[int, list[dict]] = {}
    for r in records:
        ts = r.get("_ts_epoch") or r.get("ts_epoch") or 0
        if ts < chart_start:
            continue
        idx = int((ts - chart_start) / bucket_width)
        idx = min(idx, buckets - 1)
        bucket_map.setdefault(idx, []).append(r)

    agg_fn = _aggregate_card_bucket if detail == "card" else _aggregate_modal_bucket
    is_all = detail == "card" and view == "all"
    fields = CARD_VIEW_METRICS if is_all else {view: CARD_VIEW_METRICS.get(view, CARD_VIEW_METRICS["speed"])}
    result = []
    first_data = min(bucket_map.keys()) if bucket_map else 0
    last_data = max(bucket_map.keys()) if bucket_map else 0
    for i in range(buckets):
        bucket_start = round(chart_start + i * bucket_width, 1)
        recs = bucket_map.get(i, [])
        if recs:
            result.append(agg_fn(recs, bucket_start, view))
        elif first_data <= i <= last_data:
            # Internal empty bucket (between data points) - emit for x-axis continuity
            empty: dict = {"ts": bucket_start, "count": 0,
                           "available_rate": None, "degraded_rate": None}
            if detail == "modal":
                for field in ("tps", "ttft_ms", "raw_p99_itl_ms",
                              "chunk_token_ratio", "consistency_score",
                              "speed_score", "reliability_score"):
                    empty[field] = None
            elif is_all:
                for vname, vfields in fields.items():
                    vempty: dict = {}
                    for field in vfields:
                        vempty[field] = None
                        if vname != "scores":
                            vempty[field + "_p10"] = None
                            vempty[field + "_p90"] = None
                    if vname == "scores":
                        vempty["reliability_score"] = None
                    empty[vname] = vempty
            else:
                for vname, vfields in fields.items():
                    for field in vfields:
                        empty[field] = None
                        if vname != "scores":
                            empty[field + "_p10"] = None
                            empty[field + "_p90"] = None
                    if vname == "scores":
                        empty["reliability_score"] = None
            result.append(empty)

    return _trim_empty_buckets(result)


# ── Score assembly helpers ──────────────────────────────────────────────────

def range_scores(entry: dict, records: list[dict]) -> dict:
    """Compute scores from a range-specific set of records.

    consistency/speed come from median of per-test scores in range records.
    reliability is computed from the provided records.
    """
    c_vals = [r["consistency_score"] for r in records if r.get("consistency_score") is not None] if records else []
    s_vals = [r["speed_score"] for r in records if r.get("speed_score") is not None] if records else []
    consistency = round(_median(c_vals), 1) if c_vals else None
    speed = round(_median(s_vals), 1) if s_vals else None
    uptime_pct = entry.get("uptime_pct")
    reliability = compute_range_reliability(records, uptime_pct=uptime_pct) if records else None
    return {
        "consistency": consistency,
        "speed": speed,
        "reliability": reliability,
    }


def cached_range_scores(entry: dict) -> dict:
    """range_scores from recent_history with version-based caching.

    Returns cached scores when _scores_version matches, otherwise recomputes.
    """
    cached = entry.get("_cached_scores")
    if cached is not None and entry.get("_cached_scores_version") == entry.get("_scores_version"):
        return cached
    rh = bench_only(entry.get("recent_history", []))
    scores = range_scores(entry, rh)
    entry["_cached_scores"] = scores
    entry["_cached_scores_version"] = entry.get("_scores_version", 0)
    return scores


_SHARED_KEYS = frozenset(("ts", "available_rate", "degraded_rate", "count"))
_CARD_BUCKET_VIEWS = ("speed", "consistency", "scores", "health")


def _split_card_buckets(combined: list[dict]) -> dict:
    """Split all-views bucket list into deduplicated {shared, speed, ...} structure.

    Each combined bucket has shared keys at top level + per-view dicts under
    view-name keys. This extracts shared once, per-view metrics separately.
    """
    if not combined:
        return {}
    shared_list: list[dict] = []
    view_lists: dict[str, list[dict]] = {v: [] for v in _CARD_BUCKET_VIEWS}
    for bucket in combined:
        shared_item: dict = {}
        for k in _SHARED_KEYS:
            if k in bucket:
                shared_item[k] = bucket[k]
        shared_list.append(shared_item)
        for vname in _CARD_BUCKET_VIEWS:
            vdata = bucket.get(vname)
            view_lists[vname].append(vdata if vdata else {})
    return {"shared": shared_list, **view_lists}


def cached_card_buckets(entry: dict) -> dict:
    """Card chart buckets with version-based caching.

    Returns deduplicated {shared: [...], speed: [...], consistency: [...],
    scores: [...], health: [...]} for all four card chart views.
    Shared fields (ts, available_rate, degraded_rate, count) appear once
    in the "shared" array; per-view metrics appear under each view key.
    """
    cached = entry.get("_card_buckets")
    if cached is not None and entry.get("_card_buckets_version") == entry.get("_scores_version"):
        return cached
    history = entry.get("recent_history", [])
    bench = bench_only(history) if history else []
    if bench:
        combined = compute_bucketed_history(bench, 20, "card", "all")
        combined = _filter_no_primary_all(combined)
        cb = _split_card_buckets(combined)
    else:
        cb = {}
    entry["_card_buckets"] = cb
    entry["_card_buckets_version"] = entry.get("_scores_version", 0)
    return cb


# ── Critical metrics detection ──────────────────────────────────────────────

RESULT_KEY_TO_THRESHOLD = {
    "tps": "tps",
    "ttft_ms": "ttft",
    "stall_count": "stall_count",
    "raw_p99_itl_ms": "raw_p99_itl_ms",
    "raw_median_itl_ms": "raw_median_itl_ms",
    "raw_max_itl_ms": "raw_max_itl_ms",
    "effective_itl_tail_ratio": "effective_itl_tail_ratio",
    "chunk_token_ratio": "chunk_token_ratio",
    "burst_arrival_pct": "burst_arrival_pct",
    "chunk_token_cv": "chunk_token_cv",
}

_DEFAULT_HIB: dict[str, bool] = {
    "tps": True,
    "ttft": False,
    "stall_count": False,
    "raw_p99_itl_ms": False,
    "raw_median_itl_ms": False,
    "raw_max_itl_ms": False,
    "effective_itl_tail_ratio": False,
    "chunk_token_ratio": False,
    "burst_arrival_pct": False,
    "chunk_token_cv": False,
}

THRESHOLD_TO_RESULT_KEY = {v: k for k, v in RESULT_KEY_TO_THRESHOLD.items()}


def find_critical_metrics(result: dict, color_thresholds: dict | None = None) -> list[str]:
    """Return the threshold metric names at the Critical (last) tier.

    Only checks successful results that are not already degraded -
    critical-tier results represent real measurements, not transient errors.
    Returns a list of metric names (e.g., ["tps", "stall_count"]) at the
    last tier index; empty list means no critical metrics.
    """
    if not result.get("success") or result.get("degraded"):
        return []
    ct = color_thresholds or c.color_thresholds
    if not ct:
        return []
    critical = []
    for rkey, metric_name in RESULT_KEY_TO_THRESHOLD.items():
        value = result.get(rkey)
        if value is None:
            continue
        metric_cfg = ct.get(metric_name, {})
        thresholds = metric_cfg.get("thresholds", [])
        if not thresholds:
            continue
        hib = metric_cfg.get("higher_is_better", _DEFAULT_HIB.get(metric_name, True))
        idx = tier_idx(value, thresholds, hib)
        if idx == len(thresholds) - 1:
            critical.append(metric_name)
    return critical


def _median_trend(trends: list[dict]) -> dict | None:
    """Aggregate per-model trend dicts into a single median trend.

    Direction is by majority vote; change is averaged.
    """
    if not trends:
        return None
    improving = degrading = 0
    total_change = 0.0
    valid = 0
    unit = trends[0].get("unit", "") if trends[0] else ""
    for t in trends:
        if not t.get("direction"):
            continue
        if t.get("unit") and t["unit"] != unit:
            continue
        if t["direction"] == "improving":
            improving += 1
        elif t["direction"] == "degrading":
            degrading += 1
        total_change += t.get("change") or 0
        valid += 1
    if not valid:
        return None
    is_int = unit in ("pts", "pp")
    avg = total_change / valid
    avg_change = round(avg) if is_int else round(avg, 1)
    if improving > degrading:
        direction = "improving"
    elif degrading > improving:
        direction = "degrading"
    else:
        direction = "stable"
    return {"direction": direction, "change": avg_change, "unit": unit, "data_points": valid}


_PROVIDER_SCORE_TRENDS = ("consistency_score", "speed_score", "reliability_score")


def compute_provider_summaries(
    providers: list[str] | None = None,
    pre_accumulated: dict[str, list[tuple[dict, dict | None]]] | None = None,
) -> dict[str, dict]:
    """Compute per-provider aggregated summaries from model_cache.

    Returns {provider_name: {scores, trends, since_ts, counts, total}}.
    scores are the median of per-model scores; trends are the median of
    per-model score trends (consistency/speed/reliability only); since_ts
    is the earliest trend window start across models; counts are
    {online, degraded, error}; total is the model count.

    pre_accumulated: if provided (from build_summary_response's main loop),
    skip iterating model_cache and use this data instead.
    """
    if pre_accumulated is not None:
        grouped = pre_accumulated
    else:
        from backend.state import model_cache, _archived_model_keys

        grouped = {}
        for k, entry in model_cache.items():
            pname = k.split("::")[0]
            if providers and pname not in providers:
                continue
            if k in _archived_model_keys:
                continue
            grouped.setdefault(pname, []).append((entry, cached_range_scores(entry)))

    result = {}
    for pname, entries in grouped.items():
        cs, ss, rs = [], [], []
        ct, st_t, rt = [], [], []
        online = degraded = error = testing = 0
        since_ts = None

        for entry, scores in entries:
            status = entry.get("status", "unknown")
            if status == "online":
                online += 1
            elif status == "degraded":
                degraded += 1
            elif status == "error":
                error += 1
            if entry.get("testing_benchmark"):
                testing += 1
            if scores:
                if scores.get("consistency") is not None:
                    cs.append(scores["consistency"])
                if scores.get("speed") is not None:
                    ss.append(scores["speed"])
                if scores.get("reliability") is not None:
                    rs.append(scores["reliability"])
            trends = entry.get("trends") or {}
            for key, arr in (("consistency_score", ct), ("speed_score", st_t), ("reliability_score", rt)):
                t = trends.get(key)
                if t and t.get("direction"):
                    arr.append(t)
            ts = trends.get("since_ts")
            if ts is not None and (since_ts is None or ts < since_ts):
                since_ts = ts

        agg_scores = {}
        if cs:
            agg_scores["consistency"] = _median(cs)
        if ss:
            agg_scores["speed"] = _median(ss)
        if rs:
            agg_scores["reliability"] = _median(rs)

        agg_trends = {}
        for key, arr in zip(_PROVIDER_SCORE_TRENDS, (ct, st_t, rt)):
            mt = _median_trend(arr)
            if mt:
                agg_trends[key] = mt

        summary: dict = {"counts": {"online": online, "degraded": degraded, "error": error, "testing": testing}, "total": len(entries)}
        if agg_scores:
            summary["scores"] = agg_scores
        if agg_trends:
            summary["trends"] = agg_trends
        if since_ts is not None:
            summary["since_ts"] = since_ts
        result[pname] = summary

    return result


# ── API response builders ───────────────────────────────────────────────────

def _compute_available_ranges(entry: dict) -> list[str]:
    """Compute which time ranges have data for this model.

    Returns a list of range keys from c.time_ranges that are eligible:
    - Range duration >= 4× benchmark_interval (meaningful resolution)
    - Data spans at least the range duration (data_start_epoch check)
    - Range window reaches the last data point (last_benchmark_epoch check)
    - At least 2 benchmark records in the window (for ranges within recent_history)
    """
    ranges = c.time_ranges
    if not ranges:
        return []
    data_start = entry.get("first_ts_epoch")
    if not data_start:
        return []
    min_sec = c.benchmark_interval * 4
    now = time.time()
    last_bench = entry.get("last_benchmark_epoch")
    data_age = now - data_start
    rh = entry.get("recent_history", [])
    rh_secs = c.recent_history_seconds
    result = []
    for r in ranges:
        secs = r.get("seconds", 0)
        if secs < min_sec:
            continue
        if data_age < secs:
            continue
        if last_bench and (now - last_bench) >= secs:
            continue
        if rh and rh_secs and secs <= rh_secs:
            range_start = now - secs
            bench_count = sum(1 for rec in rh
                              if rec.get("test_type", "benchmark") == "benchmark"
                              and rec.get("ts_epoch", 0) >= range_start)
            if bench_count < 2:
                continue
        result.append(r["key"])
    return result


def build_summary_response(providers: list[str] | None = None, detail_providers: list[str] | None = None, include_card_buckets: bool = False) -> dict:
    """Build collection-mode metrics response (no history arrays).

    Returns {model_key: {status, testing, testing_type, scores, trends,
    last_test, card_buckets, ...}, "providers": {name: {scores, trends,
    since_ts, counts, total}}}, filtered by provider names if specified.

    providers controls which provider summaries are returned (and per-model
    data when detail_providers is not set). detail_providers, if set (even
    empty), limits per-model data to those providers so collapsed/deferred
    providers get headers/scores without the per-model payload; pass
    detail_providers=[] for summaries-only (no per-model data at all).
    last_success_test is excluded - not used by cards or modal.
    """
    from backend.state import model_cache, _archived_model_keys, TEST_HEALTH, TEST_BENCHMARK, TEST_AUDIT, TEST_PROBE

    safe = {}
    model_filter = detail_providers if detail_providers is not None else providers
    skip_models = detail_providers is not None and not detail_providers
    provider_accum: dict[str, list[tuple[dict, dict | None]]] = {}
    for k, entry in list(model_cache.items()):
        pname = k.split("::")[0]
        is_archived = k in _archived_model_keys
        in_provider_filter = not providers or pname in providers
        in_model_filter = not model_filter or pname in model_filter
        scores = cached_range_scores(entry) if (in_model_filter and not skip_models) or in_provider_filter else None
        if in_provider_filter and not is_archived:
            provider_accum.setdefault(pname, []).append((entry, scores))
        if skip_models or not in_model_filter:
            continue
        lt = entry.get("last_test") or {}
        if is_archived:
            lt = {k: v for k, v in lt.items() if k != "degraded"}
        d = {
            "status": "unknown" if is_archived else entry.get("status", "unknown"),
            "testing": False if is_archived else bool(entry.get("testing_benchmark") or entry.get("testing_health") or entry.get("testing_audit") or entry.get("testing_probe")),
            "uptime_pct": entry.get("uptime_pct"),
            "last_benchmark_epoch": entry.get("last_benchmark_epoch"),
            "last_success_epoch": entry.get("last_success_epoch"),
            "data_start_epoch": entry.get("first_ts_epoch"),
            "available_ranges": _compute_available_ranges(entry),
            "health_enabled": c.health_enabled,
            "health_ts_epoch": entry.get("last_health_epoch"),
            "health_success": entry.get("last_health_success"),
            "health_ttft_ms": entry.get("last_health_ttft_ms"),
            "health_request_id": entry.get("last_health_request_id"),
            "health_success_epoch": entry.get("last_health_success_epoch"),
            "scores": scores,
            "trends": entry.get("trends", {}),
            "last_test": lt,
            "last_audit_result": _lightweight_audit(entry.get("last_audit_result")),
            "last_audit_epoch": entry.get("last_audit_epoch"),
        }
        if is_archived:
            d["archived"] = True
        he = entry.get("last_health_error")
        if he is not None and not is_archived:
            d["health_error"] = he
        ds = entry.get("degraded_source")
        if ds is not None and not is_archived:
            d["degraded_source"] = ds
        dgs = entry.get("degraded_since")
        if dgs is not None and not is_archived:
            d["degraded_since"] = dgs
        tds = entry.get("tps_degraded_since")
        if tds is not None and not is_archived:
            d["tps_degraded_since"] = tds
        ttftds = entry.get("ttft_degraded_since")
        if ttftds is not None and not is_archived:
            d["ttft_degraded_since"] = ttftds
        tt = TEST_BENCHMARK if entry.get("testing_benchmark") else TEST_HEALTH if entry.get("testing_health") else TEST_AUDIT if entry.get("testing_audit") else TEST_PROBE if entry.get("testing_probe") else None
        if tt is not None and not is_archived:
            d["testing_type"] = tt
        if include_card_buckets:
            d["card_buckets"] = cached_card_buckets(entry)
        safe[k] = d
    safe["providers"] = compute_provider_summaries(providers, pre_accumulated=provider_accum)
    return safe


def build_chart_response(model_key: str, since: float | None, buckets: int,
                         detail: str = "card", test_type: str = "benchmark",
                         view: str = "speed", until: float | None = None) -> dict:
    """Build single-model chart data response with bucketed aggregation.

    Uses RAM-based bucketing for ranges within recent_history and SQL-based
    bucketing for ranges that exceed it. view determines which metrics are
    aggregated ("speed", "consistency", "scores", "health"). until is an
    optional upper bound on ts_epoch for date-range filtering.
    """
    from backend.state import model_cache
    from backend import db

    entry = model_cache.get(model_key)
    if not entry:
        return {"error": "Model not found"}, 404

    now = time.time()
    data_start = entry.get("first_ts_epoch") or 0
    if since is None or since <= 0:
        # Card charts use recent_history window (capped by recent_history_seconds)
        # Modal charts can use the full data range (first_ts_epoch)
        if detail == "card":
            rh = entry.get("recent_history", [])
            if rh:
                since = rh[0].get("ts_epoch", rh[0].get("_ts_epoch", now))
            else:
                since = now - 86400
        else:
            since = data_start
    if since <= 0:
        since = now - 86400  # fallback: last 24h

    # Decide: RAM (recent_history covers the range) or SQL (range exceeds it)?
    history = entry.get("recent_history", [])
    history_covers = False
    range_records = []

    if history:
        oldest = history[0].get("ts_epoch", history[0].get("_ts_epoch", now))
        if oldest <= since:
            history_covers = True
            range_records = bench_only([r for r in history
                             if (r.get("_ts_epoch") or r.get("ts_epoch") or 0) >= since
                             and (until is None or (r.get("_ts_epoch") or r.get("ts_epoch") or 0) <= until)])

    is_health_view = test_type == "health" or (view == "health" and test_type != "benchmark")
    data_until = 0  # Max timestamp in chart data, for bounding cross-type markers
    bucket_width = 0

    if is_health_view:
        # Health data is not in recent_history - always query SQL.
        health_range = db.query_time_range(model_key, "health", since, until)
        _, _, bucket_width = _bucket_params(*health_range, buckets) if health_range else (0, 0, 1)
        bucket_data = db.query_bucketed_health(model_key, since, bucket_width, flat=(detail == "card"), until=until)
        bucket_data = _trim_empty_buckets(bucket_data)
        scores = cached_range_scores(entry)
        trends = entry.get("trends", {})
        if health_range:
            data_until = health_range[1]
    elif history_covers:
        # RAM-based bucketing (data-range-aware)
        ts_vals = [r.get("_ts_epoch") or r.get("ts_epoch") or 0 for r in range_records
                   if r.get("_ts_epoch") or r.get("ts_epoch")]
        # Don't create more buckets than we have data points
        effective_buckets = min(buckets, max(len(range_records), 1))
        if ts_vals:
            _, _, bucket_width = _bucket_params(min(ts_vals), max(ts_vals), effective_buckets)
            data_until = max(ts_vals)
        bucket_data = compute_bucketed_history(range_records, effective_buckets, detail, view)
        scores = range_scores(entry, range_records)
        trends = compute_trends(range_records) if detail == "modal" and range_records else entry.get("trends", {})
    else:
        # SQL-based bucketing - compute bucket width from actual data range
        bench_range = db.query_time_range(model_key, test_type or "benchmark", since, until)
        _, _, bucket_width = _bucket_params(*bench_range, buckets) if bench_range else (0, 0, 1)
        bucket_data = db.query_bucketed_history(model_key, since, bucket_width, test_type, detail, view, until=until)
        bucket_data = _trim_empty_buckets(bucket_data)
        # Add reliability_score (SQL doesn't compute it)
        if detail == "card":
            for b in bucket_data:
                ar = b.get("available_rate", 0)
                dr = b.get("degraded_rate", 0)
                b["reliability_score"] = _bucket_reliability(ar, dr) if ar is not None else None
        elif detail == "modal":
            for b in bucket_data:
                ar = b.get("available_rate", 0)
                dr = b.get("degraded_rate", 0)
                rv = _bucket_reliability(ar, dr) if ar is not None else None
                b["reliability_score"] = {"avg": rv} if rv is not None else None
        # Also load raw records for range-aware scoring
        cap = c.history_query_limit
        range_records = db.get_model_history(model_key, cap, test_type="benchmark", since=since, until=until)
        scores = range_scores(entry, range_records)
        trends = compute_trends(range_records) if detail == "modal" and range_records else entry.get("trends", {})
        if bench_range:
            data_until = bench_range[1]

    # Attach cross-type overlay data (bounded to chart data range)
    if bucket_data and data_until and bucket_width > 0:
        since_val = since if since else 0
        if is_health_view:
            bench_markers = db.query_markers_bucketed(model_key, "benchmark", since_val, data_until, bucket_width)
            _attach_markers(bucket_data, bench_markers, "bench", bucket_width)
            _attach_cross_ttft(bucket_data, model_key, since_val, data_until, bucket_width, detail, "benchmark", db)
        elif view == "speed":
            _attach_cross_ttft(bucket_data, model_key, since_val, data_until, bucket_width, detail, "health", db)

    # Remove buckets without the primary metric for this view
    bucket_data = _filter_no_primary(bucket_data, view if not is_health_view else "health")

    # Strip internal bucket fields before sending to client
    for b in bucket_data:
        b.pop("bucket_ts", None)

    result = {
        "model_key": model_key,
        "status": entry.get("status", "unknown"),
        "uptime_pct": entry.get("uptime_pct"),
        "scores": scores,
        "range_start": since,
        "buckets": bucket_data,
    }
    ds = entry.get("degraded_source")
    if ds is not None:
        result["degraded_source"] = ds
    dgs = entry.get("degraded_since")
    if dgs is not None:
        result["degraded_since"] = dgs
    tds = entry.get("tps_degraded_since")
    if tds is not None:
        result["tps_degraded_since"] = tds
    ttftds = entry.get("ttft_degraded_since")
    if ttftds is not None:
        result["ttft_degraded_since"] = ttftds
    if detail == "modal":
        result["trends"] = trends
    return result


def build_history_response(model_key: str, before: float | None = None,
                           limit: int = 50, test_type: str = "benchmark",
                           since: float | None = None, until: float | None = None,
                           sort: str | None = None) -> dict:
    """Build raw history rows response for the detail table.

    Returns newest rows first with cursor-based pagination via before.
    since/until bound ts_epoch for date-range filtering. sort applies
    server-side ordering (dash-prefix format). Fetches cap+1 rows to detect
    has_more without a second query.
    """
    from backend.state import model_cache
    from backend import db

    entry = model_cache.get(model_key)
    if not entry:
        return {"error": "Model not found"}, 404

    cap = min(limit, c.history_query_limit)
    rows = db.get_model_history(model_key, cap + 1, test_type=test_type, before=before,
                                since=since, until=until, sort=sort,
                                columns=db._HISTORY_TABLE_COLS)
    has_more = len(rows) > cap
    history = rows[:cap]

    provider_name = None
    for row in history:
        if provider_name is None:
            provider_name = row.pop("provider", None)
        else:
            row.pop("provider", None)
        row.pop("model_key", None)

    return {
        "provider": provider_name,
        "has_more": has_more,
        "history": history,
    }


_MODEL_INFO_SUMMARY_FIELDS = (
    "supports_vision", "supports_tools", "supports_structured_output",
    "supports_cache", "thinking", "quantization",
    "context_window", "output_context", "param_count", "num_experts",
)


def _canonical_thinking(model_info_thinking, probe_thinking):
    """Merge thinking values from model_info and probe into a canonical string.

    model_info stores thinking as a rich string ("enabled", "effort",
    "budget:N", etc.) or None. Probe stores it as a bool (True = reasoning
    detected). A live negative probe (thinking=False) vetoes a stale
    model_info claim; otherwise the richer source wins.
    """
    if probe_thinking is False:
        return None
    if probe_thinking is True:
        return "enabled"
    return normalize_thinking(model_info_thinking)


def build_model_info_summary() -> dict:
    """Build lightweight capability summary for all models.

    Returns a dict keyed by model_key with fields needed for card capability
    badges and served-by info. Merges model_info_cache with the latest probe
    results from model_cache. Used by GET /api/model-info (no model param),
    ETag-cached.
    """
    from backend.state import model_info_cache, model_cache

    _PROBE_BOOL_FIELDS = frozenset(("supports_vision", "supports_tools", "supports_structured_output", "supports_cache"))
    _PROBE_STRING_FIELDS = frozenset(("served_by", "quantization", "engine_version", "served_model", "fp_server", "fp_features"))

    seen = set()
    result = {}

    def _merge_entry(mk, mi, probe):
        entry = {}
        for f in _MODEL_INFO_SUMMARY_FIELDS:
            v = mi.get(f) if mi else None
            if v is not None:
                if f in _PROBE_BOOL_FIELDS:
                    entry[f] = bool(v)
                elif f == "thinking":
                    continue
                else:
                    entry[f] = v
        probe_thinking = probe.get("thinking") if probe else None
        canonical = _canonical_thinking(mi.get("thinking") if mi else None, probe_thinking)
        if canonical is not None:
            entry["thinking"] = canonical
        if probe:
            for f in _PROBE_BOOL_FIELDS:
                pv = probe.get(f)
                if pv is not None:
                    entry[f] = bool(pv)
            for f in _PROBE_STRING_FIELDS:
                pv = probe.get(f)
                if pv is not None and f not in entry:
                    entry[f] = pv
            tp = probe.get("tensor_parallel")
            if tp is not None and "tensor_parallel" not in entry:
                entry["tensor_parallel"] = tp
        return entry

    for mk, mi in model_info_cache.items():
        seen.add(mk)
        mc = model_cache.get(mk)
        probe = mc.get("last_probe_result") if mc else None
        entry = _merge_entry(mk, mi, probe)
        if entry:
            result[mk] = entry

    for mk, mc in model_cache.items():
        if mk in seen:
            continue
        probe = mc.get("last_probe_result")
        if not probe:
            continue
        entry = _merge_entry(mk, None, probe)
        if entry:
            result[mk] = entry

    return result


def build_model_info_detail(model_key: str) -> dict | None:
    """Build full model_info detail for a single model.

    Returns a dict with all model_info fields plus the merged probe result,
    or None if the model has no model_info and no probe data. Used by GET
    /api/model-info?model=X - NOT cached (fresh per request). Probe history
    is fetched separately in the route handler when history=1.
    """
    from backend.state import model_info_cache, model_cache, strip_internal

    mi = model_info_cache.get(model_key, {})
    mc = model_cache.get(model_key)
    probe = mc.get("last_probe_result") if mc else None

    if not mi and not probe:
        return None

    latest = dict(mi)

    canonical = _canonical_thinking(mi.get("thinking"), probe.get("thinking") if probe else None)
    if canonical is not None:
        latest["thinking"] = canonical
    elif "thinking" in latest:
        del latest["thinking"]

    for f in ("supports_vision", "supports_tools", "supports_structured_output", "supports_cache"):
        pv = probe.get(f) if probe else None
        if pv is not None:
            latest[f] = bool(pv)
        elif f in latest and latest[f] is not None:
            latest[f] = bool(latest[f])

    if probe:
        for f in ("reasoning_field", "system_fingerprint", "served_by"):
            pv = probe.get(f)
            if pv is not None and f not in latest:
                latest[f] = pv

    return strip_internal(latest)
