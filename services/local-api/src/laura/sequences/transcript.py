"""Transcript projection for nested project sequences."""

from __future__ import annotations

from typing import Any

from ..db import repos
from ..db.database import Database
from .flatten import flatten_sequence


def sequence_transcript_blocks(db: Database, sequence_id: str) -> list[dict[str, Any]]:
    """Return transcript blocks projected onto a flattened sequence timeline.

    Source transcript rows stay canonical in asset frame space. This function only
    creates a read model for the UI: every segment/word that overlaps a flattened
    base clip receives a sequence-frame projection for the visible overlap.
    """
    blocks: list[dict[str, Any]] = []
    transcript_cache: dict[str, list[dict[str, Any]]] = {}

    for clip in flatten_sequence(db, sequence_id):
        if clip.get("role", "base") != "base" or int(clip.get("lane", 0)) != 0:
            continue
        asset_id = str(clip["asset_id"])
        src_in = int(clip["src_in_frame"])
        src_out = int(clip["src_out_frame_exclusive"])
        seq_in = int(clip["seq_in_frame"])

        if asset_id not in transcript_cache:
            run = repos.get_latest_analysis_run(db, asset_id)
            transcript_cache[asset_id] = (
                repos.get_transcript(db, asset_id, run["id"]) if run is not None else []
            )

        for seg in transcript_cache[asset_id]:
            seg_start = int(seg["start_frame"])
            seg_end = int(seg["end_frame"])
            overlap_start = max(seg_start, src_in)
            overlap_end = min(seg_end, src_out)
            if overlap_start >= overlap_end:
                continue

            words: list[dict[str, Any]] = []
            for word in seg["words"]:
                word_start = int(word["start_frame"])
                word_end = int(word["end_frame"])
                word_overlap_start = max(word_start, src_in)
                word_overlap_end = min(word_end, src_out)
                if word_overlap_start >= word_overlap_end:
                    continue
                words.append(
                    {
                        "id": word["id"],
                        "idx": word["idx"],
                        "segment_id": seg["id"],
                        "asset_id": asset_id,
                        "source_start_frame": word_overlap_start,
                        "source_end_frame": word_overlap_end,
                        "seq_in_frame": seq_in + (word_overlap_start - src_in),
                        "seq_out_frame_exclusive": seq_in + (word_overlap_end - src_in),
                        "text": word["text"],
                        "confidence": word.get("confidence"),
                        "is_punctuation": bool(word.get("is_punctuation", False)),
                    }
                )

            blocks.append(
                {
                    "segment_id": seg["id"],
                    "asset_id": asset_id,
                    "speaker_label": seg.get("speaker_label"),
                    "source_start_frame": overlap_start,
                    "source_end_frame": overlap_end,
                    "seq_in_frame": seq_in + (overlap_start - src_in),
                    "seq_out_frame_exclusive": seq_in + (overlap_end - src_in),
                    "text": seg["text"],
                    "alignment_status": seg.get("alignment_status", "aligned"),
                    "alignment_job_id": seg.get("alignment_job_id"),
                    "alignment_language": seg.get("alignment_language"),
                    "alignment_error": seg.get("alignment_error"),
                    "alignment_updated_at": seg.get("alignment_updated_at"),
                    "words": words,
                }
            )

    blocks.sort(key=lambda b: (int(b["seq_in_frame"]), int(b["seq_out_frame_exclusive"])))
    return blocks
