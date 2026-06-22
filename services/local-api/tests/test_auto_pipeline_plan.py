"""Pure decision logic for the VO->lipsync auto-coupling (no DB, no ffmpeg)."""

from __future__ import annotations

from laura.ai.auto_pipeline import LipsyncPlan, plan_lipsync_after_voiceover


def _call(**kw: object) -> LipsyncPlan:
    base: dict[str, object] = dict(
        probe_face_detected=True,
        probe_mouth_visible=True,
        consent_id="c1",
        audio_asset_id="a1",
        seq_in_frame=10,
        seq_out_frame_exclusive=40,
    )
    base.update(kw)
    return plan_lipsync_after_voiceover(**base)  # type: ignore[arg-type]


def test_face_and_consent_enqueues_with_bound_span_and_audio() -> None:
    plan = _call()
    assert plan.should_enqueue is True
    assert plan.reason == "ok"
    assert plan.audio_asset_id == "a1"
    assert plan.consent_id == "c1"
    assert plan.seq_in_frame == 10
    assert plan.seq_out_frame_exclusive == 40


def test_no_face_skips_silently() -> None:
    plan = _call(probe_face_detected=False)
    assert plan.should_enqueue is False
    assert plan.reason == "no_face"


def test_no_mouth_skips_silently() -> None:
    plan = _call(probe_mouth_visible=False)
    assert plan.should_enqueue is False
    assert plan.reason == "no_face"


def test_face_but_no_consent_holds_back() -> None:
    plan = _call(consent_id=None)
    assert plan.should_enqueue is False
    assert plan.reason == "no_consent"
