"""Store tests: lifecycle, scene-review versioning."""

from pathlib import Path

import pytest

from laura.short_creator.board import Board, downstream_of
from laura.short_creator.board_models import BestWindow, BoardMeta, Roi, SceneReview


def _meta() -> BoardMeta:
    return BoardMeta(session_id="s1", asset_id="a1", created_utc="2026-07-13T12:00:00Z",
                     task="overview short", target_seconds=60.0)


def _review(n: int = 1, desc: str = "dashboard") -> SceneReview:
    return SceneReview(
        scene_number=n, src_start_frame=0, src_end_frame_exclusive=120,
        description=desc, whats_happening="scrolling", hook_score=5,
        best_window=BestWindow(offset_s=0.5, duration_s=3.0),
        roi=Roi(x=0.1, y=0.1, w=0.5, h=0.5),
    )


def test_create_open_meta_roundtrip(tmp_path: Path) -> None:
    Board.create(tmp_path / "board", _meta())
    again = Board.open(tmp_path / "board")
    assert again.meta().session_id == "s1"
    assert (tmp_path / "board" / "scene_reviews").is_dir()
    assert (tmp_path / "board" / "versions").is_dir()


def test_open_missing_board_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Board.open(tmp_path / "nope")


def test_scene_review_save_versions_and_archives(tmp_path: Path) -> None:
    board = Board.create(tmp_path / "board", _meta())
    assert board.save_scene_review(_review(1, "first look")) == 1
    assert board.save_scene_review(_review(1, "second look")) == 2
    reviews = board.scene_reviews()
    assert len(reviews) == 1
    assert reviews[0].description == "second look" and reviews[0].version == 2
    archived = tmp_path / "board" / "versions" / "scene_1.v1.json"
    assert archived.is_file() and "first look" in archived.read_text(encoding="utf-8")


def test_scene_reviews_sorted_by_number(tmp_path: Path) -> None:
    board = Board.create(tmp_path / "board", _meta())
    board.save_scene_review(_review(10))
    board.save_scene_review(_review(2))
    assert [r.scene_number for r in board.scene_reviews()] == [2, 10]


def test_downstream_of() -> None:
    assert downstream_of("scene_reviews") == (
        "storyline", "script", "voice", "cutlist", "render_report", "qa_report")
    assert downstream_of("cutlist") == ("render_report", "qa_report")
    assert downstream_of("qa_report") == ()
