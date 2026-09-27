"""Shared fixtures for scripts/tests.

Project paths come from backend.state (the single source); fixtures here run
subprocesses and git checks the same way for every test module. Code that runs the
real app in a child uses the helpers in app_child.py.
"""
import os
import re
import shutil
import subprocess
import sys

import pytest

from backend.config import _CONFIG_ENV
from backend.state import BASE_DIR

# Dev-environment overrides that would mask the default behaviour under test
# (PYTHONPATH can carry a sitecustomize shim that patches tiktoken). Every child gets a
# private MW_DATA_DIR instead, so no test writes the checkout's data/ (F40).
_SCRUBBED_ENV = ("PYTHONPATH", "MW_BUILT_CSS_PATH", "TIKTOKEN_CACHE_DIR", "MW_DATA_DIR")
_SUBPROCESS_TIMEOUT_S = 120


@pytest.fixture(scope="session")
def run_python(tmp_path_factory):
    """Run the current interpreter from the project root with a scrubbed env and its own data dir.

    Returns the CompletedProcess; raises with captured output when check=True
    and the command fails, so failures explain themselves. `env` entries win,
    MW_DATA_DIR included (an empty value means the default data/).
    """
    def _run(*args, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
        clean = {k: v for k, v in os.environ.items() if k not in _SCRUBBED_ENV}
        clean["MW_DATA_DIR"] = str(tmp_path_factory.mktemp("data"))
        clean.update(env or {})
        proc = subprocess.run(
            [sys.executable, *map(str, args)], cwd=BASE_DIR, env=clean,
            capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_S,
        )
        if check and proc.returncode != 0:
            pytest.fail(f"{args} exited {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
        return proc
    return _run


@pytest.fixture(scope="session")
def example_config_env() -> dict[str, str]:
    """MW_*_YAML overrides that load the app on the shipped config/*.example files."""
    return {env: f"{name}.example" for name, env in _CONFIG_ENV.items()}


@pytest.fixture(scope="session")
def git():
    """Run git in the project root and return the CompletedProcess (text output).

    Skips the requesting test outside a git checkout.
    """
    if shutil.which("git") is None or not (BASE_DIR / ".git").exists():
        pytest.skip("not a git checkout")

    def _git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=BASE_DIR, capture_output=True, text=True,
                              timeout=_SUBPROCESS_TIMEOUT_S)
    return _git


@pytest.fixture(scope="session")
def git_ignored(git):
    """Return a predicate telling whether git ignores a project-relative path."""
    return lambda rel_path: git("check-ignore", "-q", rel_path).returncode == 0


@pytest.fixture(scope="session")
def dockerfile_stages() -> dict[str, list[tuple[str, str]]]:
    """The Dockerfile as {stage alias, or 'final': [(INSTRUCTION, arguments), ...]}."""
    text = re.sub(r"\\\n", " ", (BASE_DIR / "Dockerfile").read_text())
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
