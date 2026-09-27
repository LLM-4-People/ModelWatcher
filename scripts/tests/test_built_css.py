"""Test: every producer and consumer of the built stylesheet agrees on its path.

Catches finding F3: backend/state.py defaulted MW_BUILT_CSS_PATH to the Docker
location, so following DEVELOPMENT.md (npm run build:css, then uvicorn) served no
stylesheet; the build output was not gitignored; a missing file surfaced only as
a bare error response. And F44: the missing file then logged a full traceback on
every page load.
"""
import json
import logging
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


def test_dockerfile_builds_and_points_at_the_same_file(dockerfile_stages):
    stages = dockerfile_stages
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


def test_missing_file_logs_path_and_build_command_once(monkeypatch, tmp_path, caplog):
    """F44: every page load requests the stylesheet; a missing build logs once, not per request."""
    missing = tmp_path / "absent.css"
    monkeypatch.setattr(st, "BUILT_CSS_PATH", missing)
    monkeypatch.setattr(st, "_failing_conditions", set())
    with caplog.at_level(logging.INFO, logger=st.log.name):
        responses = [routes.built_css() for _ in range(3)]
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        missing.write_bytes(b"body{}")
        assert routes.built_css().status_code == 200
        missing.unlink()
        routes.built_css()
    assert {r.status_code for r in responses} == {404}
    assert all(json.loads(r.body) == {"error": "Stylesheet not built"} for r in responses)
    assert len(errors) == 1, "one log line per outage, not per request"
    assert str(missing) in errors[0].getMessage() and st.BUILT_CSS_BUILD_CMD in errors[0].getMessage()
    assert errors[0].exc_info is None, "a missing file needs its path, not a traceback"
    levels = [r.levelno for r in caplog.records]
    assert levels == [logging.ERROR, logging.INFO, logging.ERROR], "logged again after it came back and went missing"


def test_served_file_and_version_hash_share_one_path(monkeypatch, tmp_path):
    css = tmp_path / "built.css"
    css.write_bytes(b"body{color:red}")
    monkeypatch.setattr(st, "BUILT_CSS_PATH", css)
    monkeypatch.setattr(routes, "_file_version_cache", {})
    assert routes.built_css().body == css.read_bytes()
    assert routes._file_version(st.BUILT_CSS_NAME) == routes._short_hash(css.read_bytes())
