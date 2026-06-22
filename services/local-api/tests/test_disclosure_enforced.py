"""Compliance tests: reel disclosure_text is always non-empty (D2).

_effective_disclosure() is the enforcement point: any blank or None input
is coerced to the module-level default so the drawtext overlay always fires.
"""
from __future__ import annotations

import pytest

from laura.render import mp4


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_blank_disclosure_coerced_to_default(blank: str | None) -> None:
    assert mp4._effective_disclosure(blank) == mp4._DEFAULT_DISCLOSURE


def test_nonblank_disclosure_kept_verbatim() -> None:
    assert mp4._effective_disclosure("Mein Text") == "Mein Text"
