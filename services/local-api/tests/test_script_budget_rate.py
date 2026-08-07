"""production_tools: ``script_budget`` prefers a MEASURED speech rate over the language
heuristic when this board's own last voice synthesis still matches the current script.

Live finding 2026-08-04: a production wrote 95 words against a "93 words ~ 60s" budget (the
German heuristic assumes ~1.724 words/s after headroom) and the render came out at only 50s —
the ACTUAL ElevenLabs rate on this board's own text was ~1.9 words/s, well outside the
heuristic's advertised +/-20% spread. ``budget_words_for`` now accepts a ``measured_rate_wps``
override (``int(usable_seconds * measured_rate_wps)``, no heuristic headroom — the headroom
existed to buffer a per-language GUESS, and a rate measured on this board's own last synthesis
is not a guess); ``script_budget`` computes one from ``voice_s`` / word count whenever the
board's ``voice`` artifact was synthesized from the CURRENT ``script`` (checked the same way
``build_cutlist`` checks it — by re-hashing the script in storyline/played order and comparing
against ``voice.script_hash``), and reports which source it used as ``rate_source``. The
under-budget gate in ``save_script_chapter`` threads the same rate through, so the number in a
gate rejection can never diverge from the number ``script_budget`` just told the author to
write to.

Board/DB fixture mirrors ``tests/test_production_tools_grounding.py``'s ``_seed``/``_board``/
``_review`` (the budget gate needs a real scene+review geometry, not just a bare board) plus
the ``_specs``/direct-artifact-save pattern from ``tests/test_production_tools_contact_sheet.py``
for seeding artifacts straight onto the board without a real TTS backend.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.short_creator.board import Board
from laura.short_creator.board_models import (
    BestWindow,
    BoardMeta,
    SceneReview,
    Script,
    Storyline,
    VoiceArtifact,
    lines_in_storyline_order,
    script_hash,
    script_text,
)
from laura.short_creator.production_tools import budget_words_for, build_production_tool_specs

FPS = 30


def _seed(tmp_path: Path, *, scene_frames: int) -> tuple[Database, str]:
    """Project + asset + succeeded analysis run + rough cut with one scene."""
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
            "end_sample": scene_frames * 1600,
            "start_frame": 0,
            "end_frame": scene_frames,
            "text": "echte rede " * 90,
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
        src_out_frame_exclusive=scene_frames,
        seq_in_frame=0,
        seq_out_frame_exclusive=scene_frames,
        lane=0,
        role="base",
    )
    repos.replace_scenes(db, project["id"], timeline["id"], [(0, scene_frames)])
    return db, str(asset["id"])


def _board(tmp_path: Path, asset_id: str, *, target_seconds: float) -> Board:
    meta = BoardMeta(
        session_id="s1",
        asset_id=asset_id,
        created_utc="2026-08-04T00:00:00Z",
        language="German",
        task="overview short",
        target_seconds=target_seconds,
    )
    return Board.create(tmp_path / "board", meta)


def _review(board: Board, *, scene_frames: int) -> None:
    board.save_scene_review(
        SceneReview(
            scene_number=1,
            src_start_frame=0,
            src_end_frame_exclusive=scene_frames,
            description="d",
            whats_happening="h",
            hook_score=5,
            best_window=BestWindow(offset_s=0.0, duration_s=3.0),
        )
    )


def _sixty_second_fixture(tmp_path: Path) -> tuple[Database, str, Board, dict[str, Any]]:
    """One 60s scene, 60s target — the live geometry: a 93-word heuristic chapter allocation."""
    db, asset_id = _seed(tmp_path, scene_frames=1800)
    board = _board(tmp_path, asset_id, target_seconds=60.0)
    _review(board, scene_frames=1800)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}
    saved = specs["save_storyline"].func(
        red_thread="r",
        chapters=[
            {
                "chapter": 1,
                "role": "hook",
                "message": "m",
                "scene_numbers": [1],
                "target_seconds": 60.0,
            }
        ],
    )
    assert saved["ok"] is True, saved
    return db, asset_id, board, specs


def _save_matching_voice(board: Board, *, rate_wps: float) -> int:
    """Save a ``voice`` artifact whose ``script_hash`` matches the board's CURRENT script, at
    exactly ``rate_wps`` words/second. Returns the script's total word count."""
    script = board.load("script")
    storyline = board.load("storyline")
    assert isinstance(script, Script) and isinstance(storyline, Storyline)
    ordered = lines_in_storyline_order(script, storyline)
    total_words = len(script_text(ordered).split())
    voice = VoiceArtifact(
        script_hash=script_hash(ordered),
        mp3_path="voice.mp3",
        voice_s=total_words / rate_wps,
    )
    board.save("voice", voice)
    return total_words


# --- budget_words_for: the pure calculation ---------------------------------------------------


def test_budget_words_for_uses_measured_rate_when_given() -> None:
    """Brief's calibration case: a 1.9 w/s measured rate over a 60s budget, no heuristic
    headroom (the headroom buffers a per-language GUESS; a measured rate is not one)."""
    assert budget_words_for(60.0, measured_rate_wps=1.9) == 114


def test_budget_words_for_falls_back_to_the_language_heuristic_without_a_measured_rate() -> None:
    """Unchanged from before this task: the German heuristic's usual 93-word result for the
    live 60s/60s geometry."""
    assert budget_words_for(60.0, "German") == 93


def test_budget_words_for_ignores_a_non_positive_measured_rate() -> None:
    """A zero or negative rate is not a valid measurement (e.g. a malformed voice_s of 0) —
    falls back to the heuristic rather than dividing by (near) zero or returning nonsense."""
    assert budget_words_for(60.0, "German", measured_rate_wps=0.0) == 93
    assert budget_words_for(60.0, "German", measured_rate_wps=-1.0) == 93


# --- script_budget: rate_source and the measured number ------------------------------------


def test_script_budget_is_heuristic_without_a_voice_on_the_board(tmp_path: Path) -> None:
    _db, _asset_id, _board, specs = _sixty_second_fixture(tmp_path)

    out = specs["script_budget"].func()

    assert out["ok"] is True, out
    assert out["rate_source"] == "heuristic"
    assert out["words"] == 93
    assert out["per_chapter"][0]["words"] == 93


def test_script_budget_prefers_the_measured_rate_when_the_voice_matches_the_script(
    tmp_path: Path,
) -> None:
    """A voice synthesized from the CURRENT script measures 1.9 w/s — script_budget's numbers
    must come from that rate, not the German heuristic's ~1.72 w/s."""
    _db, _asset_id, board, specs = _sixty_second_fixture(tmp_path)
    saved = specs["save_script_chapter"].func(
        chapter=1, lines=[{"scene_number": 1, "text": " ".join(["wort"] * 85)}]
    )
    assert saved["ok"] is True, saved
    total_words = _save_matching_voice(board, rate_wps=1.9)
    assert total_words == 85

    out = specs["script_budget"].func()

    assert out["ok"] is True, out
    assert out["rate_source"] == "measured"
    # usable_seconds is 60.0 here (material == target); no heuristic headroom on the
    # measured path — the raw brief formula, int(usable_seconds * measured_rate_wps).
    assert out["usable_seconds"] == 60.0
    assert out["words"] == 114
    assert out["per_chapter"][0]["words"] == 114
    assert out["seconds_per_word"] == 1.0 / 1.9


def test_script_budget_falls_back_to_heuristic_when_the_voice_predates_the_script(
    tmp_path: Path,
) -> None:
    """A voice recorded a DIFFERENT script hash than the one currently on the board (the
    script was edited since the last synthesis) — the measurement would describe words that
    are no longer what will be spoken, so it must be ignored, not smuggled into the budget."""
    _db, _asset_id, board, specs = _sixty_second_fixture(tmp_path)
    saved = specs["save_script_chapter"].func(
        chapter=1, lines=[{"scene_number": 1, "text": " ".join(["wort"] * 85)}]
    )
    assert saved["ok"] is True, saved
    board.save(
        "voice",
        VoiceArtifact(
            script_hash="stale-hash-from-an-earlier-script", mp3_path="v.mp3", voice_s=44.7
        ),
    )

    out = specs["script_budget"].func()

    assert out["ok"] is True, out
    assert out["rate_source"] == "heuristic"
    assert out["words"] == 93


def test_script_budget_falls_back_to_heuristic_without_a_measurable_voice_s(
    tmp_path: Path,
) -> None:
    """A matching script_hash but no (or zero) voice_s — nothing to divide by, so this must
    read as "nothing measured yet", not crash or fabricate a rate."""
    _db, _asset_id, board, specs = _sixty_second_fixture(tmp_path)
    saved = specs["save_script_chapter"].func(
        chapter=1, lines=[{"scene_number": 1, "text": " ".join(["wort"] * 85)}]
    )
    assert saved["ok"] is True, saved
    script = board.load("script")
    storyline = board.load("storyline")
    assert isinstance(script, Script) and isinstance(storyline, Storyline)
    ordered = lines_in_storyline_order(script, storyline)
    board.save(
        "voice",
        VoiceArtifact(script_hash=script_hash(ordered), mp3_path="v.mp3", voice_s=None),
    )

    out = specs["script_budget"].func()

    assert out["ok"] is True, out
    assert out["rate_source"] == "heuristic"


# --- save_script_chapter's under-budget gate: same rate, never a diverging number ----------


def test_save_script_chapter_gate_uses_the_same_measured_rate_as_script_budget(
    tmp_path: Path,
) -> None:
    """70 words is ABOVE the heuristic's 93-word-budget gate threshold (0.7*93 = 65.1, so 70
    would pass silently) but BELOW the measured rate's 114-word threshold (0.7*114 = 79.8) —
    proving the gate computed its ``budget_words`` from the measured rate, the same number
    script_budget would report, not the heuristic it would otherwise have used."""
    _db, _asset_id, board, specs = _sixty_second_fixture(tmp_path)
    first = specs["save_script_chapter"].func(
        chapter=1, lines=[{"scene_number": 1, "text": " ".join(["wort"] * 85)}]
    )
    assert first["ok"] is True, first
    _save_matching_voice(board, rate_wps=1.9)
    budget = specs["script_budget"].func()
    assert budget["rate_source"] == "measured"
    assert budget["per_chapter"][0]["words"] == 114, "fixture geometry must reproduce"

    out = specs["save_script_chapter"].func(
        chapter=1, lines=[{"scene_number": 1, "text": " ".join(["wort"] * 70)}]
    )

    assert out["ok"] is False, out
    assert "70" in out["reason"] and "114" in out["reason"]
    assert "93" not in out["reason"], "must not fall back to the heuristic's number"
