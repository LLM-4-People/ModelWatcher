"""Test: config hot reload works with MW_DISABLE_TESTS, on the files actually in use, without provider fetches.

Catches finding F46: the config watcher sat in the table of test tasks, so with
MW_DISABLE_TESTS (the mode DEVELOPMENT.md recommends) a config edit never reached the
dashboard. And F97: the watcher watched config/ and the reset_epoch rewrite edited
config/models.yaml even when MW_APP_YAML/MW_MODELS_YAML pointed at other files, so an
override outside config/ never hot-reloaded and its reset_epoch was re-applied forever.
"""
import copy

import yaml

from backend.config import config_path
from backend.state import CONFIG_DIR
from scripts.tests.app_child import result

APP_EXAMPLE = yaml.safe_load((CONFIG_DIR / "app.yaml.example").read_text())
MODELS_EXAMPLE = yaml.safe_load((CONFIG_DIR / "models.yaml.example").read_text())
RELOAD_DEADLINE_S = 20

_CHILD = """
from pathlib import Path
import yaml
from starlette.testclient import TestClient
import backend.state as st
from backend import favicons, model_info
from backend.main import app
from scripts.tests.app_child import emit, receive_until

app_yaml = Path({app_yaml!r})
with TestClient(app, base_url='http://localhost') as client:
    out = {{'stripped': 'reset_epoch' not in Path({models_yaml!r}).read_text()}}
    with client.websocket_connect('ws://localhost' + st.WS_PATH, headers={{'origin': 'http://localhost'}}) as ws:
        assert ws.receive_json()['type'] == 'hello'
        cfg = yaml.safe_load(app_yaml.read_text())
        cfg['app']['name'] = 'Reloaded'
        app_yaml.write_text(yaml.safe_dump(cfg))
        frames = receive_until(ws, lambda f: f and f[-1]['type'] == 'config_updated', {deadline})
    out.update(frames=[f['type'] for f in frames], name=st.c.app_name,
               provider_fetches=[t for t in (favicons._favicon_task, model_info._model_info_task) if t is not None] != [])
emit(out)
"""


def test_reload_with_tests_disabled_outside_config_dir(run_python, example_config_env, tmp_path):
    cfg = copy.deepcopy(APP_EXAMPLE)
    cfg["websocket"]["heartbeat_interval"] = 0.2
    cfg["websocket"]["stale_after"] = 1
    app_yaml = tmp_path / "app.yaml"
    app_yaml.write_text(yaml.safe_dump(cfg))
    models = copy.deepcopy(MODELS_EXAMPLE)
    models["providers"][0]["reset_epoch"] = True
    models_yaml = tmp_path / "models.yaml"
    models_yaml.write_text(yaml.safe_dump(models))

    proc = run_python("-c", _CHILD.format(app_yaml=str(app_yaml), models_yaml=str(models_yaml),
                                          deadline=RELOAD_DEADLINE_S), env={
        **example_config_env, "MW_APP_YAML": str(app_yaml), "MW_MODELS_YAML": str(models_yaml), "MW_DISABLE_TESTS": "1",
    })
    out = result(proc)
    assert out["frames"][-1:] == ["config_updated"], \
        f"no reload within {RELOAD_DEADLINE_S}s ({len(out['frames'])} frames: {sorted(set(out['frames']))})"
    assert out["name"] == "Reloaded"
    assert out["stripped"], "reset_epoch must be stripped from the models file in use"
    assert not out["provider_fetches"], "MW_DISABLE_TESTS: a reload must not start favicon or model-info fetches"
    assert "config watcher" not in proc.stderr.split("not starting:", 1)[1].splitlines()[0]


def test_config_path_follows_overrides(monkeypatch, tmp_path):
    monkeypatch.delenv("MW_MODELS_YAML", raising=False)
    assert config_path("models.yaml") == CONFIG_DIR / "models.yaml"
    monkeypatch.setenv("MW_MODELS_YAML", "")
    assert config_path("models.yaml") == CONFIG_DIR / "models.yaml", "an empty override means no override"
    monkeypatch.setenv("MW_MODELS_YAML", "models-scale-test.yaml")
    assert config_path("models.yaml") == CONFIG_DIR / "models-scale-test.yaml"
    monkeypatch.setenv("MW_MODELS_YAML", str(tmp_path / "m.yaml"))
    assert config_path("models.yaml") == tmp_path / "m.yaml"
