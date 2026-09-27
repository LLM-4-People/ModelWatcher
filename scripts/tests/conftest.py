"""Shared fixtures for scripts/tests.

Project paths come from backend.state (the single source); fixtures here run
subprocesses and git checks the same way for every test module.
"""
import os
import shutil
import subprocess
import sys

import pytest

from backend.state import BASE_DIR

# Dev-environment overrides that would mask the default behaviour under test
# (PYTHONPATH can carry a sitecustomize shim that patches tiktoken).
_SCRUBBED_ENV = ("PYTHONPATH", "MW_BUILT_CSS_PATH", "TIKTOKEN_CACHE_DIR")
_SUBPROCESS_TIMEOUT_S = 120


@pytest.fixture(scope="session")
def run_python():
    """Run the current interpreter from the project root with a scrubbed env.

    Returns the CompletedProcess; raises with captured output when check=True
    and the command fails, so failures explain themselves.
    """
    def _run(*args, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
        clean = {k: v for k, v in os.environ.items() if k not in _SCRUBBED_ENV}
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
def git_ignored():
    """Return a predicate telling whether git ignores a project-relative path."""
    if shutil.which("git") is None or not (BASE_DIR / ".git").exists():
        pytest.skip("not a git checkout")

    def _ignored(rel_path: str) -> bool:
        proc = subprocess.run(["git", "check-ignore", "-q", rel_path], cwd=BASE_DIR, timeout=_SUBPROCESS_TIMEOUT_S)
        return proc.returncode == 0
    return _ignored
