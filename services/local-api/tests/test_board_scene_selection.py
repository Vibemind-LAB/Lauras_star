"""Gate-S board mechanics: scene_selection as gate-dependent chain root."""

from pathlib import Path

from laura.short_creator.board import Board, downstream_of
from laura.short_creator.board_models import (
    BoardMeta,
    Chapter,
    SceneCandidate,
    SceneSelection,
    Storyline,
)


def _meta(scene_gate: bool) -> BoardMeta:
    return BoardMeta(
        session_id="s1",
        asset_id="a1",
        created_utc="2026-08-06T00:00:00Z",
        task="test",
        target_seconds=60.0,
        script_gate=True,
        scene_gate=scene_gate,
    )


def _selection(confirmed: bool) -> SceneSelection:
    return SceneSelection(
        candidates=[
            SceneCandidate(
                scene_number=2,
                src_start_frame=0,
                src_end_frame_exclusive=300,
                thumb_frame=150,
                description="screen recording of n8n",
                transcript_snippet="wir bauen den flow",
                rationale="starker hook",
                recommended=True,
            )
        ],
        selected_scene_numbers=[2] if confirmed else [],
        confirmed_utc="2026-08-06T00:01:00Z" if confirmed else None,
    )


def test_downstream_of_scene_selection_is_whole_rest() -> None:
    assert downstream_of("scene_selection") == (
        "storyline", "script", "voice", "cutlist",
        "contact_sheet", "render_report", "qa_report",
    )


def test_resume_point_gate_on_requires_selection(tmp_path: Path) -> None:
    board = Board.create(tmp_path / "b", _meta(scene_gate=True))
    assert board.resume_point([]) == "scene_selection"
    board.save("scene_selection", _selection(confirmed=False))
    # present but UNCONFIRMED still parks the run at the gate
    assert board.resume_point([]) == "scene_selection"
    board.save("scene_selection", _selection(confirmed=True))
    assert board.resume_point([]) == "storyline"


def test_resume_point_gate_off_skips_scene_selection(tmp_path: Path) -> None:
    board = Board.create(tmp_path / "b", _meta(scene_gate=False))
    assert board.resume_point([]) == "storyline"


def test_save_selection_invalidates_storyline(tmp_path: Path) -> None:
    board = Board.create(tmp_path / "b", _meta(scene_gate=True))
    board.save("scene_selection", _selection(confirmed=True))
    board.save(
        "storyline",
        Storyline(
            red_thread="x",
            arc=[Chapter(chapter=1, role="hook", message="m",
                         scene_numbers=[2], target_seconds=10.0)],
        ),
    )
    board.save("scene_selection", _selection(confirmed=True).model_copy(
        update={"selected_scene_numbers": [2], "confirmed_utc": "2026-08-06T00:09:00Z"}
    ))
    assert board.load("storyline") is None


def test_restore_brings_back_storyline_matching_selection(tmp_path: Path) -> None:
    from laura.short_creator.board_models import content_hash

    board = Board.create(tmp_path / "b", _meta(scene_gate=True))
    selection = _selection(confirmed=True)
    board.save("scene_selection", selection)
    saved_selection = board.load("scene_selection")
    assert saved_selection is not None
    board.save(
        "storyline",
        Storyline(
            red_thread="x",
            arc=[Chapter(chapter=1, role="hook", message="m",
                         scene_numbers=[2], target_seconds=10.0)],
            parents={"scene_selection": content_hash(saved_selection)},
        ),
    )
    board.invalidate("scene_selection")  # archives + removes storyline, selection stays
    assert board.load("storyline") is None
    restored = board.restore_coherent_suffix()
    assert "storyline" in restored


def test_status_reports_scene_gate_block(tmp_path: Path) -> None:
    board = Board.create(tmp_path / "b", _meta(scene_gate=True))
    status = board.status()
    assert status["scene_gate"] == {"enabled": True, "pending": False, "confirmed": False}
    board.save("scene_selection", _selection(confirmed=False))
    status = board.status()
    assert status["scene_gate"]["pending"] is True
    assert status["scene_gate"]["candidates"][0]["scene_number"] == 2
    assert status["scene_gate"]["recommended"] == [2]
    board.save("scene_selection", _selection(confirmed=True))
    status = board.status()
    assert status["scene_gate"]["confirmed"] is True
    assert status["scene_gate"]["selected"] == [2]
