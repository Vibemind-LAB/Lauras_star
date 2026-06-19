"""Plan B / Task B3 — VlmBackend protocol + deterministic StubVlmBackend + vlm_available."""

from __future__ import annotations

from laura.analysis.transition_review import (
    StubVlmBackend,
    default_backend,
    vlm_available,
)


def test_stub_jump_cut_for_contiguous_same_source() -> None:
    v = StubVlmBackend().review([], {"same_source": True, "removed_gap_frames": 0, "k": 6})
    assert v.label == "jump_cut"
    assert v.suggested_fix.kind == "transition"
    assert v.suggested_fix.transition_style == "crossfade"
    assert v.suggested_fix.transition_frames == 6
    assert v.smoothness < 0.5


def test_stub_smooth_for_distinct_shots() -> None:
    v = StubVlmBackend().review([], {"same_source": False, "removed_gap_frames": 0, "k": 6})
    assert v.label == "smooth" and v.suggested_fix.kind == "none"


def test_stub_smooth_for_gapped_same_source() -> None:
    # same asset but a real source gap -> enumerate sets same_source=False -> not a jump cut
    v = StubVlmBackend().review([], {"same_source": False, "removed_gap_frames": 40, "k": 6})
    assert v.label == "smooth"


def test_stub_model_identity_is_stable() -> None:
    backend = StubVlmBackend()
    assert backend.available() is True
    assert backend.model_digest() == backend.model_digest()
    assert isinstance(backend.model_id(), str) and backend.model_id()


def test_vlm_unavailable_without_a_registered_model() -> None:
    assert default_backend() is None
    assert vlm_available() is False
