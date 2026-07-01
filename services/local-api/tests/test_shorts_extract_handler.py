"""S5b — handle_shorts_extract: generate → score → QA → flatten → persist.

Seeds an in-memory DB with a project, a 30 fps video asset, a *succeeded* analysis
run, transcript words carrying sentence-end punctuation + a speaker change (so
``semantic`` yields legal boundary frames), and a couple of shots. Then drives the
pure pipeline through the handler and asserts the persisted, flattened, ranked rows.

Uses a short ``max_duration_s`` so a small transcript yields in-range candidates
(default 15-60 s would need a minutes-long transcript at 30 fps).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from laura.analysis.embeddings_store import FrameEmbedding, SqliteVectorStore
from laura.analysis.shorts_handlers import handle_shorts_extract
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.jobs.runner import JobContext

# 30 fps: 1 s == 30 frames. Words are 15 frames (0.5 s) each, abutting.
_WORD_FRAMES = 15


def _make_db(tmp_path: Path) -> SqliteDatabase:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    return db


def _ctx(db: SqliteDatabase, payload: dict[str, Any]) -> JobContext:
    return JobContext(
        job_id="job-1",
        kind="shorts.extract",
        queue="analysis.scene",
        payload=payload,
        db=db,
    )


def _seed_succeeded_run_with_transcript(
    db: SqliteDatabase,
) -> tuple[str, str, str]:
    """Project + 30 fps asset + succeeded run + words (2 speakers, sentence ends) + shots.

    Returns (project_id, asset_id, run_id).
    """
    proot = "/tmp/p"
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root=proot
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a",
        source_path="/tmp/a.mp4",
    )
    # Give the asset a frame rate + duration so total_frames clamping is exercised.
    with db.transaction() as conn:
        conn.execute(
            "UPDATE media_assets SET rate_num=30, rate_den=1, duration_frames=? WHERE id=?",
            (40 * _WORD_FRAMES, asset["id"]),
        )

    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="t", config={}
    )
    repos.start_analysis_run(db, run["id"])

    # Two speakers so a speaker turn appears mid-stream.
    spk_a = repos.insert_speaker(db, asset_id=asset["id"], run_id=run["id"], label="A")
    spk_b = repos.insert_speaker(db, asset_id=asset["id"], run_id=run["id"], label="B")

    # Build 30 abutting words. Every 5th word ends a sentence ("word."); the speaker
    # flips to B at word 15 (a speaker-turn boundary). End-exclusive frames.
    def _words(start_idx: int, count: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for j in range(count):
            i = start_idx + j
            sf = i * _WORD_FRAMES
            ef = sf + _WORD_FRAMES
            ends = (i + 1) % 5 == 0
            out.append(
                {
                    "idx": j,
                    "start_sample": sf * 1600,
                    "end_sample": ef * 1600,
                    "start_frame": sf,
                    "end_frame": ef,
                    "text": f"word{i}." if ends else f"word{i}",
                    "confidence": 1.0,
                    "is_punctuation": False,
                }
            )
        return out

    # Segment for speaker A (words 0..14), segment for speaker B (words 15..29).
    repos.insert_segment_with_words(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        speaker_id=spk_a,
        segment={
            "start_sample": 0,
            "end_sample": 15 * _WORD_FRAMES * 1600,
            "start_frame": 0,
            "end_frame": 15 * _WORD_FRAMES,
            "text": "a",
            "confidence": 1.0,
        },
        words=_words(0, 15),
    )
    repos.insert_segment_with_words(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        speaker_id=spk_b,
        segment={
            "start_sample": 15 * _WORD_FRAMES * 1600,
            "end_sample": 30 * _WORD_FRAMES * 1600,
            "start_frame": 15 * _WORD_FRAMES,
            "end_frame": 30 * _WORD_FRAMES,
            "text": "b",
            "confidence": 1.0,
        },
        words=_words(15, 15),
    )

    repos.insert_shots(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        shots=[
            {"src_in_frame": 0, "src_out_frame_exclusive": 225, "method": "test"},
            {"src_in_frame": 225, "src_out_frame_exclusive": 450, "method": "test"},
        ],
    )

    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})
    return project["id"], asset["id"], run["id"]


def test_extract_persists_ranked_flattened_candidates(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    _project_id, asset_id, run_id = _seed_succeeded_run_with_transcript(db)

    # Short window so the small transcript (15 s total) yields in-range candidates.
    result = handle_shorts_extract(
        _ctx(db, {"asset_id": asset_id, "min_duration_s": 1.0, "max_duration_s": 8.0})
    )

    assert result["analysis_run_id"] == run_id
    assert result["source_timeline_id"] == asset_id  # no timeline yet -> asset id fallback
    assert result["candidates"] > 0

    rows = repos.list_shorts_candidates_by_asset(db, asset_id)
    assert len(rows) == result["candidates"]

    # Flattening contract: score is a real float (never -inf), breakdown is a dict,
    # qa_issues is a list, no rejected rows persisted, ordered by score desc.
    scores: list[float] = []
    for r in rows:
        assert isinstance(r["score"], float)
        assert r["score"] != float("-inf")
        assert r["rejected"] is False
        assert r["reject_reason"] is None
        assert isinstance(r["score_breakdown"], dict)
        assert isinstance(r["qa_issues"], list)
        assert isinstance(r["qa_passed"], bool)
        # End-exclusive, integer-frame invariant.
        assert isinstance(r["start_frame"], int)
        assert isinstance(r["end_frame_exclusive"], int)
        assert r["end_frame_exclusive"] > r["start_frame"]
        scores.append(r["score"])

    assert scores == sorted(scores, reverse=True)
    assert result["kept"] == sum(1 for r in rows if r["qa_passed"])


def test_extract_uses_frame_embeddings_when_present(tmp_path: Path) -> None:
    """VE4: seeded frame embeddings flow into the persisted score_breakdown.

    With distinct embeddings across the asset's timeline, at least one candidate's
    breakdown must carry a non-zero visual_shift / visual_continuity (the visual
    columns are no longer all-zero). All three VE4 keys must be present on every row.
    """
    db = _make_db(tmp_path)
    _project_id, asset_id, run_id = _seed_succeeded_run_with_transcript(db)

    # Dense 2-D embeddings across the 450-frame asset whose direction rotates with
    # frame index. Samples sit at frames 10,25,40,... (offset from the candidate cut
    # frames, which are multiples of 75) so cuts land mid-rotation → non-zero
    # visual_shift, and different candidate windows see different interior sequences
    # → distinct visual_continuity / segment reprs (so robust-z is not flattened).
    store = SqliteVectorStore(db)
    items: list[FrameEmbedding] = []
    for frame in range(10, 460, 15):
        theta = (frame / 450.0) * (np.pi / 2.0)  # 0 → 90° sweep across the asset
        vec = np.array([np.cos(theta), np.sin(theta)], dtype=np.float32)
        items.append(
            FrameEmbedding(
                asset_id=asset_id,
                analysis_run_id=run_id,
                frame=frame,
                model="test-clip",
                vector=vec,
            )
        )
    store.replace_frame_embeddings(asset_id, run_id, items)

    result = handle_shorts_extract(
        _ctx(db, {"asset_id": asset_id, "min_duration_s": 1.0, "max_duration_s": 8.0})
    )
    assert result["candidates"] > 0

    rows = repos.list_shorts_candidates_by_asset(db, asset_id)
    assert rows

    for r in rows:
        bd = r["score_breakdown"]
        assert "visual_shift" in bd
        assert "visual_continuity" in bd
        assert "duplicate_penalty" in bd

    # At least one candidate sees a non-zero visual signal (post-z, so it may be
    # negative; what matters is that the column is not uniformly zero).
    visual_values = [
        r["score_breakdown"]["visual_shift"] for r in rows
    ] + [r["score_breakdown"]["visual_continuity"] for r in rows]
    assert any(abs(v) > 1e-9 for v in visual_values), (
        "embeddings present but every visual breakdown value is zero"
    )


def test_extract_no_succeeded_run_raises(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a",
        source_path="/tmp/a.mp4",
    )
    # A run exists but is still running (not succeeded).
    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="t", config={}
    )
    repos.start_analysis_run(db, run["id"])

    with pytest.raises(ValueError, match="no succeeded analysis run"):
        handle_shorts_extract(_ctx(db, {"asset_id": asset["id"]}))


def test_extract_empty_transcript_persists_empty(tmp_path: Path) -> None:
    """0 words → 0 candidates persisted, summary reports zero (graceful)."""
    db = _make_db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a",
        source_path="/tmp/a.mp4",
    )
    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="t", config={}
    )
    repos.start_analysis_run(db, run["id"])
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})

    result = handle_shorts_extract(_ctx(db, {"asset_id": asset["id"]}))

    assert result["candidates"] == 0
    assert result["kept"] == 0
    assert repos.list_shorts_candidates_by_asset(db, asset["id"]) == []
