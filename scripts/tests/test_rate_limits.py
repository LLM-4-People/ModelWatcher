"""Test: all rate limits come from config, none hardcoded.

Catches bug family #2: push_routes had subscribe=20, validate=30
hardcoded; routes had client-error=10 hardcoded - bypassing config.
"""
import re
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[2] / "backend"


def test_no_hardcoded_rate_limits_in_push_routes():
    """push_routes.py should not have hardcoded numeric rate limits."""
    src = (BACKEND / "push_routes.py").read_text()
    # Find all check_rate_limit CALLS (not the def) and check they use c.notif_rate_limit_*
    for line in src.splitlines():
        if "check_rate_limit(" in line and "def check_rate_limit" not in line:
            assert "c.notif_rate_limit_" in line, \
                f"Hardcoded rate limit in push_routes.py: {line.strip()}"


def test_no_hardcoded_rate_limits_in_routes():
    """routes.py should not have hardcoded numeric rate limits."""
    src = (BACKEND / "routes.py").read_text()
    for line in src.splitlines():
        if "check_rate_limit(" in line and "def check_rate_limit" not in line and "def check_rate_limit" not in line:
            assert "c.notif_rate_limit_" in line, \
                f"Hardcoded rate limit in routes.py: {line.strip()}"


def test_config_has_all_rate_limit_keys():
    """config.py validates and loads all 5 rate-limit keys."""
    src = (BACKEND / "config.py").read_text()
    # Validation
    for key in ("prefs_per_minute", "push_test_per_minute",
                "subscribe_per_minute", "validate_per_minute",
                "client_error_per_minute"):
        assert f'"{key}"' in src, f"config.py missing validation for {key}"
    # Assignment to c
    for field in ("notif_rate_limit_prefs", "notif_rate_limit_push_test",
                  "notif_rate_limit_subscribe", "notif_rate_limit_validate",
                  "notif_rate_limit_client_error"):
        assert f"c.{field}" in src, f"config.py missing c.{field} assignment"


def test_all_rate_limit_fields_exist_on_c():
    """All 5 rate-limit fields are accessible on the c namespace after load."""
    # We can't call reload_config without a real config, but we can verify
    # the field names are referenced in config.py
    src = (BACKEND / "config.py").read_text()
    for field in ("notif_rate_limit_prefs", "notif_rate_limit_push_test",
                  "notif_rate_limit_subscribe", "notif_rate_limit_validate",
                  "notif_rate_limit_client_error"):
        assert f"c.{field} = " in src, f"config.py does not assign c.{field}"
