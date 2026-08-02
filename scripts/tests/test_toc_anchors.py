"""Mechanical tests for documentation TOC anchor validity.

Parses all markdown files and verifies that every internal anchor link
(#section-name) points to an existing heading in the same file.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _anchor_from_heading(h):
    h = h.strip().lower()
    h = re.sub(r"[^\w\s-]", "", h)
    h = re.sub(r"[\s]+", "-", h)
    return h


def _extract_broken_anchors(path):
    content = path.read_text()
    headings = set()
    for m in re.finditer(r"^#+\s+(.+)$", content, re.M):
        headings.add(_anchor_from_heading(m.group(1)))
    broken = []
    for m in re.finditer(r"\[([^\]]+)\]\(#([^)]+)\)", content):
        anchor = m.group(2)
        if anchor not in headings:
            broken.append((anchor, m.group(1)))
    return broken


MARKDOWN_FILES = sorted(
    p for p in REPO_ROOT.rglob("*.md")
    if not any(part.startswith(".") or part == "node_modules" for part in p.parts)
    and "AGENTS.md" not in p.name
)


@pytest.mark.parametrize("md_file", MARKDOWN_FILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_toc_anchors_valid(md_file):
    """Every internal anchor link must point to an existing heading."""
    broken = _extract_broken_anchors(md_file)
    assert not broken, f"{md_file.name}: broken TOC anchors: {broken}"
