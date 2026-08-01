"""Test: config has no defaults in code - config is the sole source of truth.

Catches bug family #1: getattr(c, "field", default) in stats.py hid drifted
values (reliability weights 0.5/0.5 vs config 0.75/0.25).
"""
import ast
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[2] / "backend"


def _python_files():
    return list(BACKEND.glob("*.py"))


def test_no_getattr_with_non_none_default():
    """No getattr(c, ..., non-None) anywhere in backend except config.py."""
    violations = []
    for f in _python_files():
        if f.name == "config.py":
            continue
        src = f.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if (isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "c"
                        and node.func.attr == "getattr"):
                    pass
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "getattr" and len(node.args) >= 3:
                    first = node.args[0]
                    if isinstance(first, ast.Name) and first.id == "c":
                        third = node.args[2]
                        if not (isinstance(third, ast.Constant) and third.value is None):
                            violations.append(f"{f.name}:{node.lineno}")
    assert not violations, f"getattr with non-None default found: {violations}"


def test_no_default_weight_dicts():
    """No _DEFAULT_*_WEIGHTS dicts in stats.py."""
    src = (BACKEND / "stats.py").read_text()
    assert "_DEFAULT_CONSISTENCY_WEIGHTS" not in src, "_DEFAULT_CONSISTENCY_WEIGHTS should be deleted"
    assert "_DEFAULT_SPEED_WEIGHTS" not in src, "_DEFAULT_SPEED_WEIGHTS should be deleted"


def test_stats_uses_direct_access():
    """stats.py accesses config via c.field, not getattr fallbacks."""
    src = (BACKEND / "stats.py").read_text()
    assert "getattr(c," not in src, "stats.py should not use getattr(c, ...)"
    assert "c.scores_consistency_weights" in src
    assert "c.scores_speed_weights" in src
    assert "c.scores_reliability_avail_weight" in src
    assert "c.scores_reliability_quality_weight" in src
    assert "c.min_data_points_score" in src
    assert "c.min_data_points_trend" in src


def test_config_validates_all_rate_limits():
    """config.py validates all 5 rate-limit keys."""
    src = (BACKEND / "config.py").read_text()
    for key in ("prefs_per_minute", "push_test_per_minute",
                "subscribe_per_minute", "validate_per_minute",
                "client_error_per_minute"):
        assert key in src, f"config.py missing rate limit key: {key}"


def test_config_loads_all_rate_limits():
    """config.py assigns all 5 rate-limit fields to c."""
    src = (BACKEND / "config.py").read_text()
    for field in ("notif_rate_limit_prefs", "notif_rate_limit_push_test",
                  "notif_rate_limit_subscribe", "notif_rate_limit_validate",
                  "notif_rate_limit_client_error"):
        assert f"c.{field}" in src, f"config.py missing c.{field} assignment"
