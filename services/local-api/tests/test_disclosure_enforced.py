"""Compliance tests: reel disclosure_text is always non-empty (D2).

_effective_disclosure() is the enforcement point:
- None input → None (plain export; no overlay requested).
- Blank / whitespace input → default label (explicit blank cannot suppress a reel overlay).
- Non-blank input → stripped text passed through.
"""
from __future__ import annotations

import pytest

from laura.render import mp4


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_disclosure_coerced_to_default(blank: str) -> None:
    assert mp4._effective_disclosure(blank) == mp4._DEFAULT_DISCLOSURE


def test_none_disclosure_returns_none() -> None:
    """None means no overlay was requested (plain export, not a reel)."""
    assert mp4._effective_disclosure(None) is None


def test_nonblank_disclosure_kept_verbatim() -> None:
    assert mp4._effective_disclosure("Mein Text") == "Mein Text"
