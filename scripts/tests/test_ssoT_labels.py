"""Test: single source of truth for labels - state.py owns, /api/config exposes.

Catches bug family #9: _METRIC_LABELS drifted between backend ("P99 ITL")
and frontend ("P99 ITL (raw)") because they were maintained independently.

Catches bug family #10: _EVENT_LABELS (backend) vs _TYPE_LABELS (frontend)
were maintained independently with no contract test.

F76, F77, F80: status, capability and chart view labels had frontend copies with drifting
spellings ("Offline"/"Errors", "Thinking"/"Reasoning", chart views keyed by label); they are
backend tables sent in /api/config, and test_frontend_rules.py keeps copies out of the frontend.
"""
import pytest

from backend.state import (
    EVENT_LABELS, METRIC_LABELS, STATUS_VALUES, TEST_TYPES, TEST_TYPE_LABELS, CHART_VIEWS, BACKEND_DIR, FRONTEND_DIR,
    STATUS_LABELS, CHART_VIEW_LABELS, CAPABILITIES, METRIC_SHORT_LABELS, MODEL_INFO_FIELDS,
)


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
        "chunk_token_cv", "consistency_score", "speed_score", "reliability", "tpot_ms", "uptime",
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
    src = BACKEND_DIR / "notifications.py"
    text = src.read_text()
    assert "from backend.state import" in text
    assert "EVENT_LABELS" in text or "_EVENT_LABELS = EVENT_LABELS" in text
    assert "_METRIC_LABELS = METRIC_LABELS" in text or "METRIC_LABELS" in text
    assert "_EVENT_LABELS = {" not in text, "notifications.py should not define its own _EVENT_LABELS dict"


def test_frontend_does_not_define_type_labels():
    """frontend notifications.js should not define _TYPE_LABELS dict."""
    src = FRONTEND_DIR / "js" / "notifications.js"
    text = src.read_text()
    assert "const _TYPE_LABELS = {" not in text, \
        "frontend should not define _TYPE_LABELS (should use state.eventLabels from /api/config)"


def test_frontend_does_not_define_metric_labels():
    """frontend format.js should not define METRIC_LABELS dict."""
    src = FRONTEND_DIR / "js" / "format.js"
    text = src.read_text()
    assert "const METRIC_LABELS = {" not in text, \
        "frontend should not define METRIC_LABELS (should use state.metricLabels from /api/config)"


def test_test_type_labels_cover_every_test_type():
    """TEST_TYPE_LABELS has a full and a short label for each test type (the check line shows both)."""
    assert set(TEST_TYPE_LABELS) == set(TEST_TYPES)
    for key, labels in TEST_TYPE_LABELS.items():
        assert set(labels) == {"full", "short"}, key
        assert all(isinstance(v, str) and v for v in labels.values()), key
    shorts = [labels["short"] for labels in TEST_TYPE_LABELS.values()]
    assert len(set(shorts)) == len(shorts), "short labels must tell the test types apart"


def test_config_endpoint_exposes_test_type_labels():
    """routes.py sends TEST_TYPE_LABELS in /api/config and state.js applies it."""
    assert '"test_type_labels": st.TEST_TYPE_LABELS' in (BACKEND_DIR / "routes.py").read_text()
    assert "state.testTypeLabels = cfg.test_type_labels" in (FRONTEND_DIR / "js" / "state.js").read_text()


@pytest.mark.parametrize("table, keys", [
    (STATUS_LABELS, STATUS_VALUES),
    (CHART_VIEW_LABELS, CHART_VIEWS),
    (METRIC_SHORT_LABELS, tuple(METRIC_LABELS)),
], ids=["status", "chart_view", "metric_short"])
def test_label_tables_cover_their_keys(table, keys):
    assert set(table) == set(keys)
    assert all(isinstance(v, str) and v for v in table.values())
    assert len(set(table.values())) == len(table), "labels must tell the keys apart"


def test_capabilities_are_model_info_fields_with_one_label_each():
    keys = [c["key"] for c in CAPABILITIES]
    assert len(set(keys)) == len(keys)
    assert set(keys) <= set(MODEL_INFO_FIELDS), "a capability is a model info field"
    for cap in CAPABILITIES:
        assert set(cap) == {"key", "label", "desc"} and all(cap.values()), cap


@pytest.mark.parametrize("config_key, table, state_field", [
    ("status_labels", "STATUS_LABELS", "statusLabels"),
    ("chart_view_labels", "CHART_VIEW_LABELS", "chartViewLabels"),
    ("capabilities", "CAPABILITIES", "capabilities"),
    ("metric_short_labels", "METRIC_SHORT_LABELS", "metricShortLabels"),
])
def test_config_endpoint_exposes_label_tables(config_key, table, state_field):
    """routes.py sends each table in /api/config and state.js applies it."""
    assert f'"{config_key}": st.{table}' in (BACKEND_DIR / "routes.py").read_text()
    assert f"state.{state_field} = cfg.{config_key}" in (FRONTEND_DIR / "js" / "state.js").read_text()
