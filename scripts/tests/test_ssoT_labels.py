"""Test: single source of truth for labels - state.py owns, /api/config exposes.

Catches bug family #9: _METRIC_LABELS drifted between backend ("P99 ITL")
and frontend ("P99 ITL (raw)") because they were maintained independently.

Catches bug family #10: _EVENT_LABELS (backend) vs _TYPE_LABELS (frontend)
were maintained independently with no contract test.
"""
import pytest

from backend.state import EVENT_LABELS, METRIC_LABELS, STATUS_VALUES, TEST_TYPES, CHART_VIEWS


def test_event_labels_is_complete():
    """EVENT_LABELS covers all notification event types."""
    expected = {
        "offline", "recovered", "recovered_offline", "recovered_degraded",
        "partially_recovered", "degraded", "degraded_tps", "recovered_tps",
        "degraded_ttft", "recovered_ttft", "provider_changed", "model_changed",
    }
    assert set(EVENT_LABELS.keys()) == expected


def test_metric_labels_is_complete():
    """METRIC_LABELS covers all metric keys used in critical_metrics + tooltips."""
    expected = {
        "tps", "ttft", "stall_count", "raw_p99_itl_ms", "raw_median_itl_ms",
        "raw_avg_itl_ms", "raw_max_itl_ms", "effective_median_itl_ms",
        "effective_avg_itl_ms", "effective_p99_itl_ms", "effective_itl_tail_ratio",
        "chunk_token_ratio", "network_jitter_ms", "burst_arrival_pct",
        "chunk_token_cv", "consistency_score", "speed_score", "reliability", "tpot_ms",
    }
    assert set(METRIC_LABELS.keys()) == expected


def test_event_labels_values_are_strings():
    """All label values are non-empty strings."""
    for key, val in EVENT_LABELS.items():
        assert isinstance(val, str) and val, f"EVENT_LABELS[{key}] is not a non-empty string"


def test_metric_labels_values_are_strings():
    """All label values are non-empty strings."""
    for key, val in METRIC_LABELS.items():
        assert isinstance(val, str) and val, f"METRIC_LABELS[{key}] is not a non-empty string"


def test_status_values():
    """STATUS_VALUES has exactly the 4 status values."""
    assert STATUS_VALUES == ("online", "degraded", "error", "unknown")


def test_test_types():
    """TEST_TYPES has exactly the 4 test types."""
    assert TEST_TYPES == ("benchmark", "health", "audit", "probe")


def test_chart_views():
    """CHART_VIEWS has exactly the 4 chart view keys."""
    assert CHART_VIEWS == ("speed", "consistency", "scores", "health")


def test_notifications_py_uses_canonical_labels():
    """notifications.py imports from state.py, not defining its own."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[2] / "backend" / "notifications.py"
    text = src.read_text()
    assert "from backend.state import" in text
    assert "EVENT_LABELS" in text or "_EVENT_LABELS = EVENT_LABELS" in text
    assert "_METRIC_LABELS = METRIC_LABELS" in text or "METRIC_LABELS" in text
    assert "_EVENT_LABELS = {" not in text, "notifications.py should not define its own _EVENT_LABELS dict"


def test_frontend_does_not_define_type_labels():
    """frontend notifications.js should not define _TYPE_LABELS dict."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "js" / "notifications.js"
    text = src.read_text()
    assert "const _TYPE_LABELS = {" not in text, \
        "frontend should not define _TYPE_LABELS (should use state.eventLabels from /api/config)"


def test_frontend_does_not_define_metric_labels():
    """frontend format.js should not define METRIC_LABELS dict."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "js" / "format.js"
    text = src.read_text()
    assert "const METRIC_LABELS = {" not in text, \
        "frontend should not define METRIC_LABELS (should use state.metricLabels from /api/config)"
