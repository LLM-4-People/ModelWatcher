"""Test: the shipped config templates load, validate, and are fully documented.

Catches drift like finding F2's new testing.benchmark.token_encoding key: a key
the backend requires but the templates, the local configs or CONFIGURATION.md
do not carry fails here instead of at the next server start. Also pins finding
F21: every required key is checked by the one _require_keys() helper, so leaving
any key out gives the same "<path> is required" message.
"""
import copy
import re

import pytest
import yaml

from backend.config import _require_keys, _validate_audits_cfg, _validate_config, _validate_models_cfg
from backend.state import BASE_DIR, CONFIG_DIR

APP_EXAMPLE = yaml.safe_load((CONFIG_DIR / "app.yaml.example").read_text())
CONFIG_DOC = (BASE_DIR / "docs" / "CONFIGURATION.md").read_text()


def _leaf_paths(node: dict, prefix: tuple = ()):
    for key, value in node.items():
        path = prefix + (str(key),)
        if isinstance(value, dict) and value:
            yield from _leaf_paths(value, path)
        else:
            yield path


def test_examples_pass_validation():
    _validate_config(copy.deepcopy(APP_EXAMPLE))
    _validate_models_cfg(yaml.safe_load((CONFIG_DIR / "models.yaml.example").read_text()))
    _validate_audits_cfg(yaml.safe_load((CONFIG_DIR / "audits.yaml.example").read_text())["audit"])


def test_examples_load_end_to_end(run_python, example_config_env):
    code = (
        "from backend.config import reload_config\n"
        "import backend.state as st\n"
        "reload_config()\n"
        "print(st.c.benchmark_token_encoding, st.c.benchmark_token_encoding_retry)\n"
    )
    out = run_python("-c", code, env=example_config_env).stdout.split()
    bench = APP_EXAMPLE["testing"]["benchmark"]
    assert out[-2] == bench["token_encoding"]
    assert int(out[-1]) > 0


# Keys the backend treats as optional, and mappings whose keys the operator chooses
_OPTIONAL_PATHS = {("testing", "benchmark", "anthropic_thinking_budget")}
_FREE_FORM = {("scores", "consistency", "weights"), ("scores", "speed", "weights")}


def _key_paths(node: dict, prefix: tuple = ()):
    """Every key path through nested mappings; lists and free-form mappings are leaves."""
    for key, value in node.items():
        path = prefix + (str(key),)
        yield path
        if isinstance(value, dict) and path not in _FREE_FORM:
            yield from _key_paths(value, path)


REQUIRED_PATHS = [path for path in _key_paths(APP_EXAMPLE) if path not in _OPTIONAL_PATHS]


@pytest.mark.parametrize("path", REQUIRED_PATHS, ids=".".join)
def test_every_example_key_is_required(path):
    cfg = copy.deepcopy(APP_EXAMPLE)
    parent = cfg
    for key in path[:-1]:
        parent = parent[key]
    del parent[path[-1]]
    with pytest.raises(ValueError, match=f"^app\\.yaml: {re.escape('.'.join(path))} is required$"):
        _validate_config(cfg)


def test_optional_keys_may_be_left_out():
    cfg = copy.deepcopy(APP_EXAMPLE)
    for path in _OPTIONAL_PATHS:
        parent = cfg
        for key in path[:-1]:
            parent = parent[key]
        del parent[path[-1]]
    _validate_config(cfg)


@pytest.mark.parametrize("value, keys, message", [
    ({"a": 1}, ("a", "b"), "app.yaml: sec.b is required"),
    ({}, ("a", "b"), "app.yaml: sec.a, sec.b are required"),
    ([], ("a",), "app.yaml: sec must be a mapping"),
])
def test_require_keys_messages(value, keys, message):
    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        _require_keys("sec", value, keys)


def test_every_app_key_is_documented():
    undocumented = [
        ".".join(path) for path in _leaf_paths(APP_EXAMPLE)
        if not any(f"`{'.'.join(path[i:])}`" in CONFIG_DOC for i in range(len(path)))
    ]
    assert not undocumented, f"document these app.yaml keys in docs/CONFIGURATION.md: {undocumented}"


@pytest.mark.parametrize("key, bad_value", [
    ("token_encoding", ""),
    ("token_encoding", None),
    ("token_encoding_retry", "soon"),
    ("token_encoding_retry", True),
    ("token_encoding_retry", 0),
])
def test_token_encoding_keys_are_validated(key, bad_value):
    cfg = copy.deepcopy(APP_EXAMPLE)
    cfg["testing"]["benchmark"][key] = bad_value
    with pytest.raises(ValueError, match=f"testing.benchmark.{key}"):
        _validate_config(cfg)


@pytest.mark.parametrize("path, bad_value", [
    (("heartbeat_interval",), 0),
    (("heartbeat_interval",), "30s"),
    # A stale window no longer than the heartbeat tears down healthy sockets on a quiet server
    (("stale_after",), APP_EXAMPLE["websocket"]["heartbeat_interval"]),
    (("reconnect", "min_delay"), 0),
    (("reconnect", "max_delay"), APP_EXAMPLE["websocket"]["reconnect"]["min_delay"] - 1),
    (("unreachable", "after_failures"), 0),
    (("unreachable", "after_failures"), 2.5),
    (("unreachable", "retry_interval"), -1),
    (("max_message_bytes",), 0),
    (("sync_prefs_per_minute",), True),
    (("ping_interval",), 0),
    (("ping_timeout",), "90s"),
])
def test_websocket_values_are_validated(path, bad_value):
    cfg = copy.deepcopy(APP_EXAMPLE)
    node = cfg["websocket"]
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = bad_value
    with pytest.raises(ValueError, match=f"websocket.{'.'.join(path)}"):
        _validate_config(cfg)


@pytest.mark.parametrize("path, bad_value", [
    (("app", "log_level"), "verbose"),
    # Its host is always an accepted Host header (F48), so it must have one
    (("app", "site_url"), "https://["),
    (("app", "site_url"), "https://"),
    (("server", "allowed_hosts"), "localhost"),
    (("server", "allowed_hosts"), ["https://example.com"]),
    (("server", "allowed_hosts"), ["example.com:8080"]),
    (("server", "allowed_hosts"), ["api.*.com"]),
    (("server", "allowed_hosts"), [""]),
])
def test_app_values_are_validated(path, bad_value):
    cfg = copy.deepcopy(APP_EXAMPLE)
    cfg[path[0]][path[1]] = bad_value
    with pytest.raises(ValueError, match=re.escape(".".join(path))):
        _validate_config(cfg)
