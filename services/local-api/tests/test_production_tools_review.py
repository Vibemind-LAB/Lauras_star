"""production_tools: board infra + board-read tools + VLM `review_scene` (Slice 3, Task 3).

DB fixture mirrors ``tests/test_tool_build_roughcut.py``'s seeding pattern (project + asset +
succeeded analysis run + transcript) plus a hand-built rough cut (``tests/conftest.py``'s
``seeded_rough_cut`` shape), but with ``kind="rough_cut"`` + ``created_from=asset_id`` — the key
``repos.get_or_create_asset_rough_cut`` looks up (see ``context.scene_transcripts`` usage).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.short_creator.board import Board
from laura.short_creator.board_models import BestWindow, BoardMeta
from laura.short_creator.production_tools import (
    ProductionDeps,
    _clamp_windows,
    build_production_tool_specs,
)

FPS = 30
SCENE_FRAMES = 150  # 150 frames @ 30fps = 5.0s
SCENE_DURATION_S = SCENE_FRAMES / FPS
DEFAULT_TEXT = "hallo welt schauen wir uns das dashboard an"

_GOOD_REPLY = (
    '{"description": "agent dashboard", "whats_happening": "list scrolls", '
    '"hook_score": 8, "best_window": {"offset_s": 1.0, "duration_s": 3.0}, '
    '"roi": {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.4}, "legibility_notes": "small text"}'
)

_MULTI_REPLY = (
    '{"description": "agent dashboard", "whats_happening": "list scrolls", '
    '"hook_score": 8, "windows": ['
    '{"offset_s": 0.5, "duration_s": 2.0, "roi": {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.4}}, '
    '{"offset_s": 3.0, "duration_s": 1.5, "roi": null}, '
    '{"offset_s": 0.0, "duration_s": 1.0}], '
    '"legibility_notes": ""}'
)


class _Vlm:
    """Fake DescribeBackend that returns a fixed reply and asserts it got 3 frames."""

    def __init__(self, reply: str) -> None:
        self._reply = reply

    def available(self) -> bool:
        return True

    def describe(self, frames: list[bytes], prompt: str) -> str:
        assert len(frames) == 3
        return self._reply


def _seed_scene(tmp_path: Path, *, text: str = DEFAULT_TEXT) -> tuple[Database, str]:
    """Project + asset + succeeded analysis run w/ transcript + a ONE-scene rough cut.

    Returns ``(db, asset_id)``. The rough cut has a single lane-0 clip covering
    ``[0, SCENE_FRAMES)`` 1:1 (src == seq, speed 1) and one scene spanning the whole clip — so
    scene 1's SOURCE range is exactly ``[0, SCENE_FRAMES)`` and its transcript text is ``text``.
    """
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db: Database = SqliteDatabase(settings.db_path)
    db.migrate()
    project = repos.create_project(
        db,
        name="p",
        rate_num=FPS,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(tmp_path / "ws" / "proj"),
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a.mp4",
        source_path=str(tmp_path / "a.mp4"),
    )
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="t", config={})
    repos.start_analysis_run(db, run["id"])
    repos.insert_segment_with_words(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        speaker_id=None,
        segment={
            "start_sample": 0,
            "end_sample": 96_000,
            "start_frame": 0,
            "end_frame": SCENE_FRAMES,
            "text": text,
            "confidence": 1.0,
        },
        words=[],
    )
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})
    timeline = repos.create_timeline(
        db,
        project_id=project["id"],
        name="Rough Cut",
        kind="rough_cut",
        created_from=asset["id"],
    )
    repos.add_timeline_clip(
        db,
        timeline_id=timeline["id"],
        asset_id=asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=SCENE_FRAMES,
        seq_in_frame=0,
        seq_out_frame_exclusive=SCENE_FRAMES,
        lane=0,
        role="base",
    )
    repos.replace_scenes(db, project["id"], timeline["id"], [(0, SCENE_FRAMES)])
    return db, str(asset["id"])


def _board(tmp_path: Path, asset_id: str) -> Board:
    meta = BoardMeta(
        session_id="s1",
        asset_id=asset_id,
        created_utc="2026-07-13T00:00:00Z",
        task="overview short",
        target_seconds=20.0,
    )
    return Board.create(tmp_path / "board", meta)


def _extract_stub(_db: Database, _asset_id: str, frames: list[int]) -> list[bytes]:
    return [b"jpg"] * len(frames)


def test_review_scene_writes_validated_review(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    deps = ProductionDeps(describe_backend=_Vlm(_GOOD_REPLY), frame_extract=_extract_stub)
    specs = {
        s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    }

    out = specs["review_scene"].func(scene_number=1)

    assert out["ok"] and out["degraded"] is False and out["hook_score"] == 8
    assert out["roi"] == {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.4}
    reviews = board.scene_reviews()
    assert reviews[0].roi is not None and reviews[0].roi.w == 0.5
    assert reviews[0].description == "agent dashboard"
    assert reviews[0].whats_happening == "list scrolls"
    assert reviews[0].best_window.offset_s == 1.0 and reviews[0].best_window.duration_s == 3.0
    assert reviews[0].src_start_frame == 0
    assert reviews[0].src_end_frame_exclusive == SCENE_FRAMES
    assert reviews[0].version == 1


def test_review_scene_degrades_without_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Guard against a host env that has a VLM configured (LAURA_VLM_MODEL etc.) — the tool
    # resolves a real backend lazily when deps.describe_backend is None (mirrors
    # context.describe_moment), so the test must force "no backend configured" deterministically.
    monkeypatch.delenv("LAURA_VLM_MODEL", raising=False)
    monkeypatch.delenv("LAURA_VLM", raising=False)
    monkeypatch.delenv("LAURA_VLM_PROVIDER", raising=False)
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    deps = ProductionDeps(describe_backend=None, frame_extract=lambda _db, _a, _frames: [])
    specs = {
        s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    }

    out = specs["review_scene"].func(scene_number=1)

    assert out["ok"] and out["degraded"] is True
    assert out["roi"] is None
    assert out["hook_score"] == 5
    review = board.scene_reviews()[0]
    assert review.degraded is True and review.roi is None
    assert review.hook_score == 5
    assert review.best_window.offset_s == 0.0
    assert review.best_window.duration_s == 4.0  # min(4.0, 5.0s scene)
    assert review.windows == [review.best_window]
    assert review.description == DEFAULT_TEXT  # transcript snippet (<=300 chars)


def test_review_scene_degrades_on_garbage_reply(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    deps = ProductionDeps(describe_backend=_Vlm("not json {"), frame_extract=_extract_stub)
    specs = {
        s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    }

    out = specs["review_scene"].func(scene_number=1)

    assert out["ok"] and out["degraded"] is True
    review = board.scene_reviews()[0]
    assert review.degraded is True
    assert review.roi is None
    assert review.hook_score == 5


def test_review_scene_clamps_out_of_range_values(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    reply = (
        '{"description": "d", "whats_happening": "h", "hook_score": 99, '
        '"best_window": {"offset_s": 999.0, "duration_s": 2.0}, '
        '"roi": {"x": 0.7, "y": 0.1, "w": 0.5, "h": 0.2}, "legibility_notes": ""}'
    )
    deps = ProductionDeps(describe_backend=_Vlm(reply), frame_extract=_extract_stub)
    specs = {
        s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    }

    out = specs["review_scene"].func(scene_number=1)

    assert out["ok"] and out["degraded"] is False
    assert out["hook_score"] == 10  # 99 clamped to the [0, 10] range
    assert out["roi"] is None  # x=0.7 + w=0.5 > 1 -> invalid even after per-axis clamping
    review = board.scene_reviews()[0]
    assert review.roi is None
    # best_window: offset was far beyond the scene -> repositioned to still fit inside it,
    # keeping the requested 2.0s length (SCENE_DURATION_S == 5.0s).
    assert 0.0 <= review.best_window.offset_s < SCENE_DURATION_S
    assert review.best_window.duration_s == pytest.approx(2.0)
    assert review.best_window.offset_s + review.best_window.duration_s <= SCENE_DURATION_S + 1e-9


def test_review_scene_unknown_scene(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["review_scene"].func(scene_number=99)

    assert out == {"ok": False, "reason": "unknown scene"}


def test_board_status_and_get_reviews(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    deps = ProductionDeps(describe_backend=_Vlm(_GOOD_REPLY), frame_extract=_extract_stub)
    specs = {
        s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    }

    before = specs["board_status"].func()
    assert before["ok"] is True
    assert before["expected_scenes"] == [1]
    assert before["resume_point"] == "scene_reviews:1"
    assert before["scene_reviews"] == {
        "count": 0,
        "scenes": [],
        "degraded_count": 0,
        "degraded_scenes": [],
    }
    assert "artifacts" in before

    specs["review_scene"].func(scene_number=1)

    after = specs["board_status"].func()
    assert after["resume_point"] == "storyline"  # next artifact in the chain
    assert after["scene_reviews"] == {
        "count": 1,
        "scenes": [1],
        "degraded_count": 0,
        "degraded_scenes": [],
    }

    reviews = specs["get_reviews"].func()
    assert reviews["ok"] is True
    assert reviews["reviews"] == [
        {
            "scene_number": 1,
            "hook_score": 8,
            "degraded": False,
            "has_roi": True,
            "windows": [{"window": 0, "offset_s": 1.0, "duration_s": 3.0, "has_roi": False}],
            "description": "agent dashboard",
        }
    ]


def test_get_scene_context_returns_transcript_and_range(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path, text="wir zeigen das dashboard")
    board = _board(tmp_path, asset_id)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["get_scene_context"].func(scene_number=1)

    assert out["ok"] is True
    assert out["scene_number"] == 1
    assert out["src_start_frame"] == 0
    assert out["src_end_frame_exclusive"] == SCENE_FRAMES
    assert out["text"] == "wir zeigen das dashboard"
    assert out["duration_s"] == pytest.approx(SCENE_DURATION_S)

    missing = specs["get_scene_context"].func(scene_number=42)
    assert missing == {"ok": False, "reason": "unknown scene"}


def test_review_scene_parses_multiple_windows(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    deps = ProductionDeps(describe_backend=_Vlm(_MULTI_REPLY), frame_extract=_extract_stub)
    specs = {
        s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    }

    out = specs["review_scene"].func(scene_number=1)

    assert out["ok"] and out["degraded"] is False
    assert out["windows"] == 2  # third window overlaps the first (strongest) -> discarded
    review = board.scene_reviews()[0]
    assert [w.offset_s for w in review.windows] == [0.5, 3.0]
    assert review.best_window == review.windows[0]
    assert review.windows[0].roi is not None and review.windows[0].roi.w == 0.5
    assert review.windows[1].roi is None
    assert review.roi is None  # no scene-level roi in the reply

    reviews = specs["get_reviews"].func()
    entry = reviews["reviews"][0]
    assert entry["has_roi"] is True  # window 0 carries one
    assert entry["windows"] == [
        {"window": 0, "offset_s": 0.5, "duration_s": 2.0, "has_roi": True},
        {"window": 1, "offset_s": 3.0, "duration_s": 1.5, "has_roi": False},
    ]


def test_review_scene_legacy_best_window_reply_still_parses(tmp_path: Path) -> None:
    """A VLM that answers in the old single-`best_window` shape (no `windows` list) must
    still produce a valid one-window review with the top-level roi kept."""
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    deps = ProductionDeps(describe_backend=_Vlm(_GOOD_REPLY), frame_extract=_extract_stub)
    specs = {
        s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    }

    out = specs["review_scene"].func(scene_number=1)

    assert out["ok"] and out["windows"] == 1
    review = board.scene_reviews()[0]
    assert review.windows == [review.best_window]
    assert review.best_window.offset_s == 1.0 and review.best_window.duration_s == 3.0
    assert review.best_window.roi is None
    assert review.roi is not None and review.roi.w == 0.5  # top-level roi kept


def test_clamp_windows_clamps_discards_and_caps() -> None:
    # 5 proposals: [0] fine, [1] overlaps [0] -> discarded, [2] runs past the scene end ->
    # offset pulled back (still non-overlapping), [3] garbage -> skipped, [4] fine.
    raw: list[object] = [
        {"offset_s": 0.0, "duration_s": 2.0},
        {"offset_s": 1.0, "duration_s": 1.0},
        {"offset_s": 4.5, "duration_s": 1.0, "roi": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}},
        "garbage",
        {"offset_s": 2.5, "duration_s": 1.0},
    ]
    windows = _clamp_windows(raw, 5.0)
    assert [(w.offset_s, w.duration_s) for w in windows] == [(0.0, 2.0), (4.0, 1.0), (2.5, 1.0)]
    assert windows[1].roi is not None

    default = [BestWindow(offset_s=0.0, duration_s=4.0)]
    assert _clamp_windows(None, 5.0) == default
    assert _clamp_windows([], 5.0) == default
    assert _clamp_windows("prose", 5.0) == default

    five: list[object] = [{"offset_s": float(i * 2), "duration_s": 1.0} for i in range(5)]
    assert len(_clamp_windows(five, 20.0)) == 4  # hard cap at 4 accepted windows


# --- the re-review cap: a VLM is a stochastic oracle, not a measurement --------------------
# Live finding (run be23992c): every scene was reviewed SIX times — 36 VLM calls in 24 minutes
# — and exactly ONE of the 30 re-reviews fixed a degraded review. The other 29 re-asked a
# healthy review and got a randomly different answer each time (scene 6's window count went
# 4>3>1>3>3>2). The orchestrator read variance as progress, the turn budget died in the review
# phase, and no storyline was ever saved. Asking again yields a DIFFERENT answer, not a better
# one — so after one refinement of a healthy review, the tool refuses and points forward.
#
# Degraded reviews stay retryable without limit: fixing degradation is the one case where a
# re-review has a real target.


def test_a_healthy_review_allows_exactly_one_refinement(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    deps = ProductionDeps(describe_backend=_Vlm(_GOOD_REPLY), frame_extract=_extract_stub)
    specs = {
        s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    }

    assert specs["review_scene"].func(scene_number=1)["ok"] is True
    assert specs["review_scene"].func(scene_number=1)["ok"] is True  # one refinement

    third = specs["review_scene"].func(scene_number=1)

    assert third["ok"] is False
    assert "save_storyline" in third["reason"], "the refusal must point forward, not just block"
    assert board.scene_reviews()[0].version == 2, "the third call must not have written"


def test_the_cap_counts_per_scene_not_per_run(tmp_path: Path) -> None:
    """Refining scene 1 must not use up scene 2's refinement."""
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    deps = ProductionDeps(describe_backend=_Vlm(_GOOD_REPLY), frame_extract=_extract_stub)
    specs = {
        s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    }
    specs["review_scene"].func(scene_number=1)
    specs["review_scene"].func(scene_number=1)
    assert specs["review_scene"].func(scene_number=1)["ok"] is False

    # Scene 2 does not exist in this one-scene fixture, so prove the point on scene 1's
    # counter staying isolated: a fresh toolset (fresh run) starts a fresh budget.
    fresh = {
        s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    }
    assert fresh["review_scene"].func(scene_number=1)["ok"] is True


def test_degraded_reviews_stay_retryable(tmp_path: Path) -> None:
    """Fixing degradation is the one re-review with a real target — never cap it."""
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    deps = ProductionDeps(describe_backend=_Vlm("not json {"), frame_extract=_extract_stub)
    specs = {
        s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    }

    for _ in range(4):
        out = specs["review_scene"].func(scene_number=1)
        assert out["ok"] is True and out["degraded"] is True

    assert board.scene_reviews()[0].version == 4


def test_the_review_rubric_does_not_punish_held_screens() -> None:
    """Pins the hook rubric against reel logic.

    Live finding (commit 592e771's era, still measurable later): the reviewer handed held
    screens one-second windows and low hook scores — motion scored, stillness did not. That
    is reel logic; this product's footage is screen recordings, where a held, readable frame
    IS the content. The prompt must say so, or the VLM defaults to its social-video prior.
    """
    from laura.short_creator.production_tools import _REVIEW_PROMPT

    assert "held" in _REVIEW_PROMPT or "static" in _REVIEW_PROMPT
    assert "not a penalty" in _REVIEW_PROMPT or "not lower the score" in _REVIEW_PROMPT
