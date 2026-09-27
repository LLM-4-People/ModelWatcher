"""Test: every producer and consumer of the built stylesheet agrees on its path.

Catches finding F3: backend/state.py defaulted MW_BUILT_CSS_PATH to the Docker
location, so following DEVELOPMENT.md (npm run build:css, then uvicorn) served no
stylesheet; the build output was not gitignored; a missing file surfaced only as
a bare error response.
"""
import json
import logging
import re
import shlex
from pathlib import PurePosixPath

import pytest

import backend.routes as routes
import backend.state as st

BUILD_SCRIPT = st.BUILT_CSS_BUILD_CMD.removeprefix("npm run ")
BUILT_CSS_REL = st.FRONTEND_DIR.joinpath(st.BUILT_CSS_NAME).relative_to(st.BASE_DIR).as_posix()


def _npm_script_args(name: str) -> list[str]:
    scripts = json.loads((st.BASE_DIR / "package.json").read_text())["scripts"]
    return shlex.split(scripts[name])


def _flag(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


def _dockerfile_stages() -> dict[str, list[tuple[str, str]]]:
    """Map each stage alias (or 'final') to its (INSTRUCTION, arguments) list."""
    text = re.sub(r"\\\n", " ", (st.BASE_DIR / "Dockerfile").read_text())
    stages: dict[str, list[tuple[str, str]]] = {}
    current: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        instr, _, args = line.partition(" ")
        instr = instr.upper()
        if instr == "FROM":
            parts = args.split()
            current = stages.setdefault(parts[2] if len(parts) > 2 and parts[1].upper() == "AS" else "final", [])
        current.append((instr, args.strip()))
    return stages


@pytest.mark.parametrize("script", [BUILD_SCRIPT, "watch:css"])
def test_npm_scripts_write_the_served_path(script):
    args = _npm_script_args(script)
    assert _flag(args, "-o") == BUILT_CSS_REL
    assert (st.BASE_DIR / _flag(args, "-i")).is_file()


def test_default_path_is_the_npm_output(run_python):
    out = run_python("-c", "import backend.state as st; print(st.BUILT_CSS_PATH)").stdout.strip()
    assert out == str(st.FRONTEND_DIR / st.BUILT_CSS_NAME)


def test_env_overrides_path(run_python, tmp_path):
    override = tmp_path / "built.css"
    out = run_python("-c", "import backend.state as st; print(st.BUILT_CSS_PATH)",
                     env={"MW_BUILT_CSS_PATH": str(override)}).stdout.strip()
    assert out == str(override)


def test_dockerfile_builds_and_points_at_the_same_file():
    stages = _dockerfile_stages()
    builder = stages["css-builder"]
    workdir = [a for i, a in builder if i == "WORKDIR"][-1]
    assert any(i == "RUN" and st.BUILT_CSS_BUILD_CMD in a for i, a in builder)

    final = stages["final"]
    env_idx, env_value = next((n, a.split("=", 1)[1]) for n, (i, a) in enumerate(final)
                              if i == "ENV" and a.startswith("MW_BUILT_CSS_PATH="))
    copy_idx, copy_args = next((n, a.split()) for n, (i, a) in enumerate(final)
                               if i == "COPY" and a.startswith("--from=css-builder"))
    src, dest = copy_args[1], copy_args[2]
    assert src == str(PurePosixPath(workdir) / BUILT_CSS_REL)
    assert env_idx < copy_idx, "ENV must be declared before the COPY that uses it"
    assert dest in ("${MW_BUILT_CSS_PATH}", "$MW_BUILT_CSS_PATH", env_value)


def test_index_links_the_served_name():
    html = (st.FRONTEND_DIR / "index.html").read_text()
    assert f'href="__STATIC_PREFIX__/{st.BUILT_CSS_NAME}"' in html


def test_build_output_is_ignored(git_ignored):
    assert git_ignored(BUILT_CSS_REL)
    assert BUILT_CSS_REL in (st.BASE_DIR / ".dockerignore").read_text().splitlines()


def test_missing_file_logs_path_and_build_command(monkeypatch, tmp_path, caplog):
    missing = tmp_path / "absent.css"
    monkeypatch.setattr(st, "BUILT_CSS_PATH", missing)
    with caplog.at_level(logging.ERROR, logger=st.log.name):
        resp = routes.built_css()
    assert resp.status_code == 404
    assert json.loads(resp.body) == {"error": "Stylesheet not built"}
    message = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)
    assert str(missing) in message and st.BUILT_CSS_BUILD_CMD in message


def test_served_file_and_version_hash_share_one_path(monkeypatch, tmp_path):
    css = tmp_path / "built.css"
    css.write_bytes(b"body{color:red}")
    monkeypatch.setattr(st, "BUILT_CSS_PATH", css)
    monkeypatch.setattr(routes, "_file_version_cache", {})
    assert routes.built_css().body == css.read_bytes()
    assert routes._file_version(st.BUILT_CSS_NAME) == routes._short_hash(css.read_bytes())
