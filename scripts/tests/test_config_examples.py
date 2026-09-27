"""Test: the shipped config templates load, validate, and are fully documented.

Catches drift like finding F2's new testing.benchmark.token_encoding key: a key
the backend requires but the templates, the local configs or CONFIGURATION.md
do not carry fails here instead of at the next server start.
"""
import copy

import pytest
import yaml

from backend.config import _validate_audits_cfg, _validate_config, _validate_models_cfg
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


def test_examples_load_end_to_end(run_python):
    code = (
        "from backend.config import reload_config\n"
        "import backend.state as st\n"
        "reload_config()\n"
        "print(st.c.benchmark_token_encoding, st.c.benchmark_token_encoding_retry)\n"
    )
    out = run_python("-c", code, env={
        "MW_APP_YAML": "app.yaml.example", "MW_MODELS_YAML": "models.yaml.example",
        "MW_AUDITS_YAML": "audits.yaml.example",
    }).stdout.split()
    bench = APP_EXAMPLE["testing"]["benchmark"]
    assert out[-2] == bench["token_encoding"]
    assert int(out[-1]) > 0


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


@pytest.mark.parametrize("key", ["token_encoding", "token_encoding_retry"])
def test_token_encoding_keys_are_required(key):
    cfg = copy.deepcopy(APP_EXAMPLE)
    del cfg["testing"]["benchmark"][key]
    with pytest.raises(ValueError, match=f"testing.benchmark.{key} is required"):
        _validate_config(cfg)


_WS_KEYS = ("allowed_origins", "heartbeat_interval", "stale_after", "reconnect", "unreachable",
            "max_message_bytes", "sync_prefs_per_minute")


@pytest.mark.parametrize("key", _WS_KEYS)
def test_websocket_keys_are_required(key):
    cfg = copy.deepcopy(APP_EXAMPLE)
    del cfg["websocket"][key]
    with pytest.raises(ValueError, match=f"websocket.{key} is required"):
        _validate_config(cfg)


@pytest.mark.parametrize("path", [("reconnect", "min_delay"), ("reconnect", "max_delay"),
                                  ("unreachable", "after_failures"), ("unreachable", "retry_interval")])
def test_websocket_nested_keys_are_required(path):
    cfg = copy.deepcopy(APP_EXAMPLE)
    del cfg["websocket"][path[0]][path[1]]
    with pytest.raises(ValueError, match=f"websocket.{'.'.join(path)} is required"):
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
])
def test_websocket_values_are_validated(path, bad_value):
    cfg = copy.deepcopy(APP_EXAMPLE)
    node = cfg["websocket"]
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = bad_value
    with pytest.raises(ValueError, match=f"websocket.{'.'.join(path)}"):
        _validate_config(cfg)
