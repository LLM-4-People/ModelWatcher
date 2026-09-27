"""Test: every tracked text file is stored and checked out with LF line endings.

Catches finding F11: eight files were committed with CRLF or mixed endings, so any
tool that writes LF turned a small edit into a whole-file diff. `.gitattributes`
normalises text files to LF (`* text=auto eol=lf`); this fails when the attribute
is dropped, when `.gitattributes` itself is ignored, or when a CR sneaks back in.
"""
import pytest

from backend.state import BASE_DIR

NORMALISED_ATTR = "text=auto eol=lf"
# git ls-files --eol: "lf" or "none" (no line breaks) for text, "-text" for binary
_CLEAN_EOL = {"lf", "none", "-text", ""}


@pytest.fixture(scope="module")
def eol_rows(git) -> list[tuple[str, str, str, str]]:
    """(index eol, worktree eol, attributes, path) for every tracked file."""
    rows = []
    for entry in git("ls-files", "--eol", "-z").stdout.split("\0"):
        if not entry:
            continue
        info, path = entry.split("\t", 1)
        index_eol, work_eol, *attr = info.split()
        rows.append((index_eol.removeprefix("i/"), work_eol.removeprefix("w/"),
                     " ".join(attr).removeprefix("attr/"), path))
    assert rows, "git ls-files returned nothing"
    return rows


def test_gitattributes_is_tracked(git_ignored):
    assert (BASE_DIR / ".gitattributes").is_file()
    assert not git_ignored(".gitattributes")


def test_every_file_is_normalised_to_lf(eol_rows):
    wrong = [path for _, _, attr, path in eol_rows if attr != NORMALISED_ATTR]
    assert not wrong, f"not covered by '{NORMALISED_ATTR}': {wrong}"


@pytest.mark.parametrize("column", ["index", "worktree"])
def test_no_crlf_in_tracked_files(eol_rows, column):
    pick = 0 if column == "index" else 1
    bad = [f"{row[pick]} {row[3]}" for row in eol_rows
           if row[pick] not in _CLEAN_EOL and (BASE_DIR / row[3]).exists()]
    assert not bad, f"{column} has CRLF or mixed endings: {bad}"
