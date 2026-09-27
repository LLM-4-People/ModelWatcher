"""Test: the scale-test seeder writes a DB and configs the server accepts.

Catches finding F1: the seeder computed its project root one level too high
(scripts/) after moving into scripts/util/, so it could not open its database.
Also guards the seeder against keeping its own copy of the schema, which had
drifted from backend/db.py (no archived flag, no fingerprint columns). F39: the seeder
wrote favicons into its own --data-dir while the server only read data/favicons; both
now take the data dir from MW_DATA_DIR. F38: app.yaml overrides were checked after
minutes of seeding, a nested unknown key raised a bare KeyError, and a failure left a
DB and models YAML without an app YAML.
"""
import json
import sqlite3

import pytest
import yaml

from backend.config import _validate_config, _validate_models_cfg
from backend.state import CONFIG_DIR, TEST_BENCHMARK, TEST_HEALTH
from scripts.tests.app_child import result
from scripts.util.scale_test_db import API_KEY_ENV

PROVIDERS = 2
MODELS_PER = 3
MONTHS = 0.05
BENCH_INTERVAL_S = 5 * 3600
HEALTH_INTERVAL_S = 15 * 60
APP_BENCH_INTERVAL_S = 3600
APP_HEARTBEAT_S = 7
APP_RECONNECT_MAX_S = 42
HISTORY_S = MONTHS * 30 * 86400
N_BENCH = int(HISTORY_S / BENCH_INTERVAL_S)
N_HEALTH = int(HISTORY_S / HEALTH_INTERVAL_S)
N_MODELS = PROVIDERS * MODELS_PER


SEED = ("-m", "scripts.util.scale_test_db", "--app-template", CONFIG_DIR / "app.yaml.example")


@pytest.fixture(scope="module")
def seeded(tmp_path_factory, run_python):
    """Run the seeder in module form, the documented way, into a scratch root."""
    root = tmp_path_factory.mktemp("seed")
    data_dir = root / "missing" / "data"
    config_dir = root / "missing" / "config"
    run_python(
        *SEED, "--providers", PROVIDERS, "--models-per", MODELS_PER, "--months", MONTHS,
        "--bench-interval", BENCH_INTERVAL_S, "--health-interval", HEALTH_INTERVAL_S,
        "--config-dir", config_dir, "--server-env", root / "server-env.json",
        "--app-bench-interval", APP_BENCH_INTERVAL_S,
        "--app-set", f"websocket.heartbeat_interval={APP_HEARTBEAT_S}",
        "--app-set", f"websocket.reconnect.max_delay={APP_RECONNECT_MAX_S}",
        env={"MW_DATA_DIR": str(data_dir)},
    )
    return {"root": root, "data": data_dir, "config": config_dir, "db": data_dir / "metrics-scale-test.db",
            "server_env": json.loads((root / "server-env.json").read_text())}


def _query(db_path, sql):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(sql).fetchall()


def _schema(db_path) -> dict[str, list[str]]:
    tables = [r[0] for r in _query(db_path, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {t: [c[1] for c in _query(db_path, f"PRAGMA table_info({t})")] for t in tables}


def test_creates_missing_output_dirs_and_db(seeded):
    assert seeded["db"].is_file()


def test_result_rows_per_test_type(seeded):
    counts = dict(_query(seeded["db"], "SELECT test_type, COUNT(*) FROM test_results GROUP BY test_type"))
    assert counts == {TEST_BENCHMARK: N_MODELS * N_BENCH, TEST_HEALTH: N_MODELS * N_HEALTH}


def test_model_state_and_providers(seeded):
    rows = _query(seeded["db"], "SELECT COUNT(*), SUM(total_tests) FROM model_state")[0]
    assert rows == (N_MODELS, N_MODELS * (N_BENCH + N_HEALTH))
    assert _query(seeded["db"], "SELECT COUNT(*) FROM providers")[0][0] == PROVIDERS


def test_schema_matches_backend(seeded, run_python, tmp_path):
    reference = tmp_path / "reference.db"
    run_python("-c", "import sys, pathlib, backend.db as db; db.init(pathlib.Path(sys.argv[1])); db.close()", reference)
    assert _schema(seeded["db"]) == _schema(reference)


def test_models_yaml_is_valid(seeded):
    cfg = yaml.safe_load((seeded["config"] / "models-scale-test.yaml").read_text())
    _validate_models_cfg(cfg)
    assert len(cfg["providers"]) == PROVIDERS
    assert all(len(p["models"]) == MODELS_PER for p in cfg["providers"])
    assert {p["api_key"] for p in cfg["providers"]} == {f"${{{API_KEY_ENV}}}"}


def test_app_yaml_is_valid(seeded):
    cfg = yaml.safe_load((seeded["config"] / "app-scale-test.yaml").read_text())
    _validate_config(cfg)
    assert cfg["testing"]["benchmark"]["interval"] == APP_BENCH_INTERVAL_S
    assert cfg["testing"]["benchmark"]["stagger"] is False
    assert cfg["websocket"]["heartbeat_interval"] == APP_HEARTBEAT_S
    assert cfg["websocket"]["reconnect"]["max_delay"] == APP_RECONNECT_MAX_S


@pytest.mark.parametrize("override, message", [
    ("websocket.heartbeat_intervall=1", "no key websocket.heartbeat_intervall in app.yaml.example"),
    ("nosuch.key=1", "no key nosuch in app.yaml.example"),
    ("websocket.stale_after.deeper=1", "no key websocket.stale_after.deeper in app.yaml.example"),
    ("websocket.stale_after=abc", "websocket.stale_after must be a finite number"),
])
def test_bad_app_override_fails_before_any_output(run_python, tmp_path, override, message):
    """A bad --app-set is a usage error (exit 2) found before seeding, so nothing is written."""
    proc = run_python(*SEED, "--providers", 1, "--models-per", 1, "--months", MONTHS, "--config-dir", tmp_path / "config",
                      "--app-set", override, env={"MW_DATA_DIR": str(tmp_path / "data")}, check=False)
    assert proc.returncode == 2, proc.stderr
    assert message in proc.stderr.splitlines()[-1]
    assert "Traceback" not in proc.stderr
    assert not (tmp_path / "config").exists()
    assert not list((tmp_path / "data").glob("*.db"))


def test_favicon_per_provider(seeded):
    assert len(list((seeded["data"] / "favicons").iterdir())) == PROVIDERS


def test_server_env_points_the_server_at_the_output(seeded):
    """The env the seeder hands the server (and the browser tests) names the seeded files and data dir."""
    env = seeded["server_env"]
    assert env["MW_DATA_DIR"] == str(seeded["data"])
    assert (seeded["data"] / env["MW_DB_NAME"]) == seeded["db"]
    assert env["MW_MODELS_YAML"] == str(seeded["config"] / "models-scale-test.yaml")
    assert env["MW_APP_YAML"] == str(seeded["config"] / "app-scale-test.yaml")
    assert env[API_KEY_ENV] == "dummy"


def test_server_on_the_seeded_env_serves_every_logo(seeded, run_python):
    """F39: an out-of-tree seed had no provider logos, because the server read only data/favicons."""
    code = (
        "from starlette.testclient import TestClient\n"
        "from backend.main import app\n"
        "from scripts.tests.app_child import emit\n"
        "with TestClient(app, base_url='http://localhost') as client:\n"
        "    emit({name: p['logo'] for name, p in client.get('/api/providers').json().items()})\n"
    )
    logos = result(run_python("-c", code, env={**seeded["server_env"], "MW_DISABLE_TESTS": "1"}))
    assert len(logos) == PROVIDERS
    assert all(logo and logo.startswith("data:image/") for logo in logos.values()), logos
