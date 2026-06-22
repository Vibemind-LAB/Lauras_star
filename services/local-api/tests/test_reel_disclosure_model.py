"""Compliance tests: ReelRenderRequest.disclosure_text is always non-blank (D3).

The field validator on ReelRenderRequest coerces None / empty / whitespace-only
values to the default "KI · synthetisch" so a client literally cannot request a
reel without the AI label — enforced at the request model layer (before the
endpoint forwards options to the renderer).
"""
from __future__ import annotations

import pytest

from laura.api.models import ReelRenderRequest


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_blank_disclosure_becomes_default(raw: str | None) -> None:
    req = ReelRenderRequest(disclosure_text=raw)
    assert req.disclosure_text == "KI · synthetisch"


def test_custom_disclosure_is_preserved() -> None:
    req = ReelRenderRequest(disclosure_text="Synthetische Stimme")
    assert req.disclosure_text == "Synthetische Stimme"
