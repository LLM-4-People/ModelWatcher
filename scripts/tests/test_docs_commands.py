"""Test: commands in the docs and script docstrings work as written.

Catches finding F7: DEVELOPMENT.md installed Python dependencies into the system
interpreter, where Debian-patched Pythons fail to build http-ece; a virtualenv works.
Catches finding F1 (docs side): the seeder's usage still named its old location,
and running a scripts/ file by path cannot import backend. And F43: bare `pytest`
failed at conftest import (no module named backend); only `python -m pytest` worked.
"""
import re

import pytest

from backend.state import BASE_DIR

# The findings register quotes commands exactly as they were run when an issue was found
HISTORICAL_DOCS = {BASE_DIR / "docs" / "redesign" / "FINDINGS.md"}
DOC_FILES = sorted({
    *BASE_DIR.glob("*.md"), *(BASE_DIR / "docs").rglob("*.md"),
    *(BASE_DIR / "scripts").rglob("*.md"), *(BASE_DIR / ".github").rglob("*.md"),
} - HISTORICAL_DOCS)
SCRIPT_FILES = sorted((BASE_DIR / "scripts").rglob("*.py"))
REQUIREMENT_FILES = sorted(BASE_DIR.glob("requirements*.txt"))
FENCE_RE = re.compile(r"^```[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
FILE_FORM_RE = re.compile(r"\bpython3?\s+scripts/\S+\.py")
# The lookahead skips placeholders such as scripts.util.<name>
MODULE_FORM_RE = re.compile(r"\bpython3?\s+-m\s+(scripts(?:\.\w+)+)(?![\w.<])")


def _rel(path) -> str:
    return str(path.relative_to(BASE_DIR))


def _pip_installs_outside_venv(text: str) -> list[str]:
    """pip install lines not preceded, in the same fenced block, by venv creation and activation."""
    offenders = [line.strip() for line in FENCE_RE.sub("", text).splitlines() if "pip install" in line]
    for block in FENCE_RE.findall(text):
        lines = block.splitlines()
        for idx, line in enumerate(lines):
            before = lines[:idx]
            if "pip install" in line and not (any("-m venv" in b for b in before)
                                              and any("activate" in b for b in before)):
                offenders.append(line.strip())
    return offenders


@pytest.mark.parametrize("path", DOC_FILES + REQUIREMENT_FILES, ids=_rel)
def test_pip_installs_happen_in_a_virtualenv(path):
    offenders = _pip_installs_outside_venv(path.read_text())
    assert not offenders, f"create and activate a virtualenv before pip install: {offenders}"


@pytest.mark.parametrize("path", DOC_FILES + SCRIPT_FILES, ids=_rel)
def test_scripts_are_invoked_in_module_form(path):
    text = path.read_text()
    assert not FILE_FORM_RE.findall(text), "use python3 -m scripts.<pkg>.<module> so backend is importable"
    for module in MODULE_FORM_RE.findall(text):
        assert (BASE_DIR / (module.replace(".", "/") + ".py")).is_file(), f"{module} does not exist"


def test_virtualenv_dir_is_ignored(git_ignored):
    assert git_ignored(".venv")
    assert ".venv/" in (BASE_DIR / ".dockerignore").read_text().splitlines()


def test_bare_pytest_imports_the_project(run_python):
    """`pytest` puts neither the working directory nor the project on sys.path; pytest.ini must.

    -P leaves the working directory off sys.path as the `pytest` entry point does, where
    `python -m pytest` would add it and hide a missing setting.
    """
    code = "import sys, pytest; sys.exit(pytest.main(['--collect-only', '-q', 'scripts/tests/test_project_paths.py']))"
    proc = run_python("-P", "-c", code, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
