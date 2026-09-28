"""Test: text colour tokens meet WCAG AA against the card surface in both themes.

Catches finding F71: in the light theme 14 of 23 text elements on a card were below AA (tier values
3.30:1, the TPS label 1.81:1, units 2.56:1), because the light tier tokens, the faint text and the
series-coloured labels were never checked against --color-raised; in the dark theme Bad and
Critical were near-identical reds. The tokens are read from frontend/input.css, their one home
(finding F82), so the test guards whatever palette the redesign brings.
"""
import re

import pytest

from backend.routes import theme_tokens
from backend.state import FRONTEND_DIR

AA_TEXT = 4.5
# Critical must stand apart from Bad by lightness too, not by hue alone (it also gets .critical-mark)
BAD_CRITICAL_MIN = 1.3

# Tokens used for text on cards, tiles, badges, labels and tables
TEXT_TOKENS = (
    "--color-text-primary", "--color-text-secondary", "--color-text-muted", "--color-text-faint",
    "--color-tier-accent", "--color-tier-success", "--color-tier-warn", "--color-tier-danger",
    "--color-tier-danger-dark", "--color-tier-teal",
    "--color-status-online", "--color-status-degraded", "--color-status-error", "--color-status-testing",
    "--chart-label-cc-tps", "--chart-label-cc-ttft", "--chart-label-cc-uptime", "--chart-label-cc-tails",
    "--chart-label-cc-batching",
)
SURFACES = ("--color-raised", "--color-overlay")


def _rgb(token_value: str) -> tuple[float, float, float]:
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", token_value.strip())
    assert m, f"text tokens are #rrggbb in input.css (got {token_value!r})"
    return tuple(int(m.group(1)[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _luminance(rgb) -> float:
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(_rgb(a)), _luminance(_rgb(b))), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _resolved(scheme: str) -> dict[str, str]:
    """A scheme's tokens: the dark block overrides the light (:root) one."""
    tokens = theme_tokens()
    return {**tokens["light"], **tokens[scheme]} if scheme == "dark" else tokens["light"]


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("surface", SURFACES)
def test_text_tokens_meet_aa_on_card_surfaces(scheme, surface):
    tokens = _resolved(scheme)
    low = {t: round(contrast(tokens[t], tokens[surface]), 2) for t in TEXT_TOKENS
           if contrast(tokens[t], tokens[surface]) < AA_TEXT}
    assert not low, f"{scheme}: below {AA_TEXT}:1 on {surface}: {low}"


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_critical_is_told_apart_from_bad(scheme):
    tokens = _resolved(scheme)
    ratio = contrast(tokens["--color-tier-danger"], tokens["--color-tier-danger-dark"])
    assert ratio >= BAD_CRITICAL_MIN, f"{scheme}: Bad and Critical differ by {ratio:.2f}:1 only"
    assert ".critical-mark" in (FRONTEND_DIR / "index.html").read_text()


def test_contrast_helper():
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)
