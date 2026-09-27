"""Test: the image carries only what it should, and the server starts one way with one set of settings.

Catches finding F9: `COPY config/ config/` baked the build host's local config/*.yaml
(gitignored, may hold inline keys) into an image layer, and nothing kept them out of the
build context. And F16: the WebSocket protocol ping interval and timeout were hardcoded
twice (main.py's __main__ and the Dockerfile CMD) and quoted in DEPLOYMENT.md; they are
now websocket.ping_interval/ping_timeout, applied by `python -m backend.main`, the one
entry point every launch uses.
"""
import copy
import fnmatch
import json
import re
import shlex

import pytest
import yaml

from backend.state import BACKEND_DIR, BASE_DIR, CONFIG_DIR

APP_EXAMPLE = yaml.safe_load((CONFIG_DIR / "app.yaml.example").read_text())
ENTRY_POINT = ["python", "-m", "backend.main"]


def _dockerignored(rel_path: str) -> bool:
    """Docker's .dockerignore rule, simplified: a pattern matches a path or one of its parent
    directories, `!` re-includes, and the last matching pattern wins."""
    patterns = [line.strip() for line in (BASE_DIR / ".dockerignore").read_text().splitlines()
                if line.strip() and not line.startswith("#")]
    parts = rel_path.split("/")
    candidates = ["/".join(parts[:n]) for n in range(1, len(parts) + 1)]
    ignored = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        glob = pattern.lstrip("!").strip("/")
        if any(fnmatch.fnmatchcase(c, glob) for c in candidates):
            ignored = not negated
    return ignored


def test_image_copies_only_example_configs(dockerfile_stages):
    sources = [src for instr, args in dockerfile_stages["final"] if instr == "COPY" and not args.startswith("--from")
               for src in shlex.split(args)[:-1]]
    assert "." not in sources and "./" not in sources, "never COPY the whole build context"
    config_sources = [src for src in sources if src.startswith("config")]
    assert config_sources and all(src.endswith(".example") for src in config_sources), config_sources


@pytest.mark.parametrize("path, ignored", [
    ("config/app.yaml", True), ("config/models.yaml", True), ("config/audits.yaml", True),
    ("config/app-scale-test.yaml", True), ("config/local.yml", True),
    (".env", True), (".env.modelwatcher", True), ("data/metrics.db", True),
    ("config/app.yaml.example", False), ("config/models.yaml.example", False),
    ("backend/main.py", False), ("frontend/index.html", False), ("requirements.txt", False),
])
def test_build_context_leaves_out_local_secrets(path, ignored):
    assert _dockerignored(path) is ignored


def test_image_starts_through_the_entry_point(dockerfile_stages):
    cmd = [args for instr, args in dockerfile_stages["final"] if instr == "CMD"]
    assert len(cmd) == 1 and json.loads(cmd[0]) == ENTRY_POINT


# Files that start the server or describe how; FINDINGS.md keeps the history on purpose
_LAUNCH_SITES = [
    BASE_DIR / "Dockerfile", BASE_DIR / "compose.example.yaml", BASE_DIR / "package.json",
    BASE_DIR / "README.md", BASE_DIR / "CONTRIBUTING.md",
    *(BASE_DIR / "docs").glob("*.md"), BASE_DIR / "docs" / "redesign" / "WORKLOG.md",
    *(BASE_DIR / "tests").rglob("*.mjs"), *(BASE_DIR / "scripts").rglob("*.py"),
    # config.py loads the ping keys and main.py applies them; no other module may know them
    *CONFIG_DIR.glob("*.example"), *(p for p in BACKEND_DIR.glob("*.py") if p.name not in ("config.py", "main.py")),
]


@pytest.mark.parametrize("path", _LAUNCH_SITES, ids=lambda p: p.relative_to(BASE_DIR).as_posix())
def test_no_second_launch_path_or_ping_setting(path):
    text = path.read_text()
    if path.name == "test_deployment.py":
        return
    assert not re.search(r"ws[-_]ping", text), "the ping settings live in websocket.ping_interval/ping_timeout"
    assert not re.search(r"uvicorn\s+backend\.main:app|-m['\",\s]+uvicorn", text), \
        "start the server with `python -m backend.main`"


def _server_options(run_python, tmp_path, debug: bool) -> dict:
    cfg = copy.deepcopy(APP_EXAMPLE)
    cfg["app"]["debug"] = debug
    app_yaml = tmp_path / "app.yaml"
    app_yaml.write_text(yaml.safe_dump(cfg))
    code = "import json\nfrom backend.main import server_options\nprint('RESULT ' + json.dumps(server_options()))\n"
    proc = run_python("-c", code, env={
        "MW_APP_YAML": str(app_yaml), "MW_MODELS_YAML": "models.yaml.example", "MW_AUDITS_YAML": "audits.yaml.example",
        "MW_DB_NAME": str(tmp_path / "metrics.db"), "HOST": "127.0.0.1", "PORT": "8099",
    })
    return json.loads(proc.stdout.split("RESULT ", 1)[1])


@pytest.mark.parametrize("debug", [True, False])
def test_server_options_come_from_config_and_env(run_python, tmp_path, debug):
    options = _server_options(run_python, tmp_path, debug)
    ws = APP_EXAMPLE["websocket"]
    assert options["ws_ping_interval"] == ws["ping_interval"]
    assert options["ws_ping_timeout"] == ws["ping_timeout"]
    assert (options["host"], options["port"]) == ("127.0.0.1", 8099)
    assert options["reload"] is debug
    assert options.get("reload_dirs") == ([str(BACKEND_DIR)] if debug else None)
