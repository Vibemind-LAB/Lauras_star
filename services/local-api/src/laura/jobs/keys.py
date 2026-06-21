"""Deterministic idempotency-key builder for all job kinds.

Single source of truth for key construction.  The produced strings are
**byte-identical** to what each enqueue site built inline in Task 1 — the
refactor is purely structural, no semantic change.

Usage::

    from laura.jobs.keys import idempotency_key_for

    key = idempotency_key_for(kind, payload)   # str | None
    enqueue(db, ..., idempotency_key=key)

Kinds covered
-------------
``export.render``
    ``render:{export_id}``

``ai.lipsync``
    ``ai.lipsync:{sha256( timeline_id | seq_in_frame | seq_out_frame_exclusive
                          | audio_asset_id | consent_id )}``

``ai.reenact``
    ``ai.reenact:{sha256( timeline_id | seq_in_frame | seq_out_frame_exclusive
                          | portrait_asset_id | consent_id )}``

``ai.voiceover``
    ``ai.voiceover:{sha256( timeline_id | seq_in_frame | seq_out_frame_exclusive
                            | segment_id | text* | voice_id | mix_mode
                            | ducking_percent )}``
    *text is included only when segment_id is absent/None (matches Task-1 logic).

All other kinds return ``None`` — no dedup key.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _sha256_of(parts: dict[str, object]) -> str:
    """SHA-256 hex digest of a JSON-serialised dict with sorted keys."""
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def idempotency_key_for(kind: str, payload: dict[str, Any]) -> str | None:
    """Return the deterministic idempotency key for *kind* + *payload*, or None.

    The produced strings are byte-identical to what the individual enqueue sites
    built inline before this refactor.  Do NOT change the key strings without a
    matching migration / cache-bust.
    """
    if kind == "export.render":
        export_id = payload.get("export_id")
        if export_id is None:
            return None
        return f"render:{export_id}"

    if kind == "ai.lipsync":
        parts: dict[str, object] = {
            "timeline_id": payload.get("timeline_id"),
            "seq_in_frame": payload.get("seq_in_frame"),
            "seq_out_frame_exclusive": payload.get("seq_out_frame_exclusive"),
            "audio_asset_id": payload.get("audio_asset_id"),
            "consent_id": payload.get("consent_id"),
        }
        return f"ai.lipsync:{_sha256_of(parts)}"

    if kind == "ai.reenact":
        parts = {
            "timeline_id": payload.get("timeline_id"),
            "seq_in_frame": payload.get("seq_in_frame"),
            "seq_out_frame_exclusive": payload.get("seq_out_frame_exclusive"),
            "portrait_asset_id": payload.get("portrait_asset_id"),
            "consent_id": payload.get("consent_id"),
        }
        return f"ai.reenact:{_sha256_of(parts)}"

    if kind == "ai.voiceover":
        segment_id = payload.get("segment_id")
        parts = {
            "timeline_id": payload.get("timeline_id"),
            "seq_in_frame": payload.get("seq_in_frame"),
            "seq_out_frame_exclusive": payload.get("seq_out_frame_exclusive"),
            "segment_id": segment_id,
            # text is included only when there is no segment_id — mirrors Task-1 logic:
            # body.text if body.segment_id is None else None
            "text": payload.get("text") if segment_id is None else None,
            "voice_id": payload.get("voice_id"),
            "mix_mode": payload.get("mix_mode"),
            "ducking_percent": payload.get("ducking_percent"),
        }
        return f"ai.voiceover:{_sha256_of(parts)}"

    return None
