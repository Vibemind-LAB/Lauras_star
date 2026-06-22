"""Pure decision logic for the transcript-edit auto-pipeline (spec §5).

Keeps the VO->lipsync coupling decision free of DB/ffmpeg so it is fully unit-testable;
the handler does I/O (probe, consent lookup, enqueue) and feeds the booleans in here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LipsyncPlan:
    should_enqueue: bool
    reason: str  # "ok" | "no_face" | "no_consent"
    audio_asset_id: str | None
    consent_id: str | None
    seq_in_frame: int
    seq_out_frame_exclusive: int


def plan_lipsync_after_voiceover(
    *,
    probe_face_detected: bool,
    probe_mouth_visible: bool,
    consent_id: str | None,
    audio_asset_id: str,
    seq_in_frame: int,
    seq_out_frame_exclusive: int,
) -> LipsyncPlan:
    """Decide whether a successful VO should auto-enqueue lipsync.

    Enqueue iff a face+mouth are present in the span AND a valid (non-revoked) consent
    id was resolved. Missing face -> skip silently (only VO stays). Face but no consent
    -> hold back (caller surfaces a one-time hint). The span/audio binding is passed
    through verbatim so the lipsync idempotency key derives from it.
    """
    if not (probe_face_detected and probe_mouth_visible):
        return LipsyncPlan(
            False, "no_face", None, None, seq_in_frame, seq_out_frame_exclusive
        )
    if not consent_id:
        return LipsyncPlan(
            False,
            "no_consent",
            audio_asset_id,
            None,
            seq_in_frame,
            seq_out_frame_exclusive,
        )
    return LipsyncPlan(
        True, "ok", audio_asset_id, consent_id, seq_in_frame, seq_out_frame_exclusive
    )
