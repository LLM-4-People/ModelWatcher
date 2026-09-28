"""Test: scores, trends, chart buckets and status derivation mean what their labels say.

Catches the data findings of the analyst round:
- F59: a failed last benchmark vanished at the next health success (status back to plain online).
- F60: newest-first rows from the SQL path inverted every modal trend.
- F61: the card Health view plotted benchmark TTFT.
- F62: every card trend rested on 3 samples (a 75/25 split with a code-constant minimum).
- F63: provider trends voted a direction and averaged magnitudes across opposite moves; provider
  scores were unrounded.
- F64: SQL-path chart buckets labelled MIN/MAX as P10/P90 next to real percentiles in memory.
- F65: lower-is-better metrics scored one tier band harsher, and a list without the 0 sentinel
  scored below 0.
"""
import copy
import itertools

import pytest
import yaml

import backend.state as st
from backend import stats
from backend.config import _validate_config
from backend.db import _derive_status_from_result

APP_EXAMPLE = yaml.safe_load((st.CONFIG_DIR / "app.yaml.example").read_text())
CT = APP_EXAMPLE["color_thresholds"]
WINDOW = 12 * 3600
HOUR = 3600
NOW = 1_800_000_000


@pytest.fixture(autouse=True)
def example_scoring(monkeypatch):
    """The example's scoring and trend settings on the live config namespace, as reload_config sets them."""
    metrics = APP_EXAMPLE["metrics"]
    scores = APP_EXAMPLE["scores"]
    for name, value in {
        "color_thresholds": CT,
        "trend_window": WINDOW,
        "min_data_points_trend": metrics["min_data_points_trend"],
        "trend_deadbands": metrics["trend_deadbands"],
        "min_data_points_score": metrics["min_data_points_score"],
        "scores_consistency_weights": scores["consistency"]["weights"],
        "scores_speed_weights": scores["speed"]["weights"],
        "scores_reliability_avail_weight": scores["reliability"]["availability_weight"],
        "scores_reliability_quality_weight": scores["reliability"]["quality_weight"],
    }.items():
        monkeypatch.setattr(st.c, name, value, raising=False)


# ── F65: one score mapping for both directions ──────────────────────────────

HIB = [100, 50, 30, 15, 0]
LIB = [1000, 3000, 5000, 10000, 0]


def _grid(thresholds):
    top = max(thresholds) * 3
    return [top * i / 400 for i in range(401)]


@pytest.mark.parametrize("metric", [m for m in CT if m != "tiers"])
def test_scores_stay_in_range_and_follow_the_value(metric):
    cfg = CT[metric]
    values = _grid(cfg["thresholds"])
    scores = [stats.tier_continuous_score(v, cfg["thresholds"], cfg["higher_is_better"]) for v in values]
    assert all(0.0 <= s <= 1.0 for s in scores), metric
    ordered = scores if cfg["higher_is_better"] else scores[::-1]
    assert all(a <= b for a, b in itertools.pairwise(ordered)), f"{metric} scores are not monotonic"


def test_a_boundary_scores_the_same_in_both_directions():
    n = len(HIB)
    for i in range(n - 1):
        expected = round(1 - i / (n - 1), 4)
        assert stats.tier_continuous_score(HIB[i], HIB, True) == expected
        assert stats.tier_continuous_score(LIB[i], LIB, False) == expected


def test_scores_are_continuous_across_boundaries():
    for i in range(len(LIB) - 1):
        below = stats.tier_continuous_score(LIB[i] - 0.001, LIB, False)
        at = stats.tier_continuous_score(LIB[i], LIB, False)
        assert abs(below - at) < 0.001, f"TTFT jumps at {LIB[i]} ms: {below} -> {at}"
    # The F65 reproduction: 999 ms scored 1.0 and 1000 ms 0.75 while TPS 100 scored 1.0
    assert stats.tier_continuous_score(1000, LIB, False) == stats.tier_continuous_score(100, HIB, True) == 1.0


def test_open_ended_worst_tier_reaches_zero_without_going_below():
    assert stats.tier_continuous_score(10000, LIB, False) == 0.25
    assert stats.tier_continuous_score(15000, LIB, False) == 0.0
    assert stats.tier_continuous_score(10 ** 9, LIB, False) == 0.0


@pytest.mark.parametrize("metric, thresholds, hib, message", [
    ("stall_count", [1, 2, 3, 15, 4], False, "must end with 0"),
    ("stall_count", [1, 3, 2, 15, 0], False, "strictly increasing"),
    ("tps", [100, 30, 50, 15, 0], True, "strictly decreasing"),
])
def test_config_rejects_out_of_order_thresholds(metric, thresholds, hib, message):
    cfg = copy.deepcopy(APP_EXAMPLE)
    cfg["color_thresholds"][metric] = {"higher_is_better": hib, "thresholds": thresholds}
    with pytest.raises(ValueError, match=message):
        _validate_config(cfg)


# ── F60, F62: trends over time windows, in any record order ─────────────────

def _bench(ts, tps, ttft=900, available=True, **extra):
    return {"ts_epoch": ts, "test_type": "benchmark", "tps": tps, "ttft_ms": ttft, "available": available,
            "success": available, "degraded": False, **extra}


def _series(n_before, n_recent, before_tps, recent_tps):
    """n_before results spread over the day before the window, n_recent inside it, oldest first."""
    old = [_bench(NOW - WINDOW - HOUR * (n_before - i), before_tps) for i in range(n_before)]
    new = [_bench(NOW - WINDOW + HOUR * (i + 1) * 11 / max(n_recent, 1), recent_tps) for i in range(n_recent)]
    return old + new


def test_trends_do_not_depend_on_record_order():
    records = _series(8, 6, before_tps=80, recent_tps=96)
    forward = stats.compute_trends(records)
    assert forward["tps"]["direction"] == "improving"
    assert forward["tps"]["delta"] == 16
    assert stats.compute_trends(records[::-1]) == forward


def test_a_window_below_the_minimum_has_no_trend():
    min_pts = APP_EXAMPLE["metrics"]["min_data_points_trend"]
    few_recent = stats.compute_trends(_series(10, min_pts - 1, 80, 20))
    assert "tps" not in few_recent
    enough = stats.compute_trends(_series(10, min_pts, 80, 20))
    assert enough["tps"]["direction"] == "degrading"
    assert enough["tps"]["data_points"] == min_pts


def test_a_move_inside_the_deadband_is_stable():
    deadband = APP_EXAMPLE["metrics"]["trend_deadbands"]["tps"]
    trend = stats.compute_trends(_series(8, 6, 80, 80 + deadband / 2))["tps"]
    assert trend["direction"] == "stable"


# ── F63: provider trends and scores ─────────────────────────────────────────

def _entry(consistency, trend_delta, status="online"):
    return {"status": status, "trends": {"consistency_score": {"direction": "x", "change": abs(trend_delta), "delta": trend_delta, "unit": "pts"}}}, \
        {"consistency": consistency, "speed": None, "reliability": None}


def test_opposite_model_trends_cancel_in_the_provider_trend():
    summary = stats.compute_provider_summaries(pre_accumulated={"P": [_entry(70.15, 15), _entry(54.55, -15)]})["P"]
    trend = summary["trends"]["consistency_score"]
    assert trend["direction"] == "stable"
    assert trend["change"] == 0
    assert trend["models"] == 2
    assert summary["scores"]["consistency"] == 62.4


def test_provider_direction_uses_the_model_deadband():
    deadband = APP_EXAMPLE["metrics"]["trend_deadbands"]["consistency_score"]
    small = stats.compute_provider_summaries(pre_accumulated={"P": [_entry(60, deadband - 1), _entry(60, deadband - 1)]})["P"]
    assert small["trends"]["consistency_score"]["direction"] == "stable"


# ── F61: the card Health view plots health checks ───────────────────────────

def test_card_health_view_follows_health_records():
    history = []
    for i in range(20):
        history.append(_bench(NOW - HOUR * (20 - i), 80, ttft=500))
        history.append({"ts_epoch": NOW - HOUR * (20 - i) + 60, "test_type": "health", "ttft_ms": 3000 + i,
                        "available": True, "success": True, "degraded": False})
    entry = {"recent_history": history, "_scores_version": 1}
    cb = stats.cached_card_buckets(entry)
    health = [b["ttft_ms"] for b in cb["health"] if b.get("ttft_ms") is not None]
    speed = [b["ttft_ms"] for b in cb["speed"] if b.get("ttft_ms") is not None]
    assert health and all(v >= 3000 for v in health), "Health view shows health-check TTFT"
    assert speed and all(v == 500 for v in speed), "Speed view shows benchmark TTFT"


# ── F64: the same statistics from memory and from the database ─────────────

@pytest.mark.parametrize("detail", ["card", "modal"])
def test_ram_and_sql_paths_aggregate_alike(detail):
    tps = [52.5, 60.1, 91.9, 95.0, 110.3, 123.15]
    records = [_bench(NOW + i * 60, v) for i, v in enumerate(tps)]
    ram = stats.compute_bucketed_history(records, 1, detail, "speed")
    sql = stats.bucket_rows(records, 86400, detail, "speed")
    assert len(ram) == len(sql) == 1
    key = "tps"
    if detail == "card":
        got = {k: sql[0][k] for k in ("tps", "tps_p10", "tps_p90")}
        assert got == {k: ram[0][k] for k in got}
        assert got["tps_p10"] > min(tps) and got["tps_p90"] < max(tps), "P10/P90 are percentiles, not MIN/MAX"
    else:
        assert sql[0][key] == ram[0][key]
        assert sql[0][key]["p10"] > min(tps) and sql[0][key]["p90"] < max(tps)


# ── F59: a failed benchmark owns the state until the next benchmark ─────────

def _replay(results):
    status, source = "unknown", None
    for test_type, available in results:
        status, source = _derive_status_from_result(status, source, test_type, available, False)
    return status, source


def test_failed_benchmark_survives_health_successes():
    assert _replay([("benchmark", True), ("benchmark", False)]) == ("error", "benchmark")
    assert _replay([("benchmark", True), ("benchmark", False), ("health", True), ("health", True)]) == ("degraded", "benchmark")
    assert _replay([("benchmark", False), ("health", True), ("benchmark", True)]) == ("online", None)


def test_health_failures_stay_health_owned():
    assert _replay([("benchmark", True), ("health", False)]) == ("error", None)
    assert _replay([("benchmark", True), ("health", False), ("health", True)]) == ("online", None)
