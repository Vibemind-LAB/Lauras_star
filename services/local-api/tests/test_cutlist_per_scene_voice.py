"""build_cutlist sizing segments to their OWN voice clip (VS3, spec 2026-08-06 §5.2).

VS2 made ``synthesize_script_voice`` construct the board's single continuous track from
independently-synthesized per-line clips and record them as ``VoiceArtifact.segments`` (in
STORYLINE playback order). This module covers the consumer side: when ``segments`` is present,
each cutlist segment's length comes from ITS OWN clip's ``duration_s`` (+ the one shared
inter-scene gap, dropped after the very last segment) instead of the legacy
``chapter_audio_windows`` proportional-scaling path — plus the honest refusals when the voice
and the storyline disagree on how many lines there are, or a line does not fit its scene.

DB fixture mirrors ``tests/test_production_tools_cutlist.py``'s ``_seed_two_scenes`` (same
10.0s-at-30fps-per-scene rough cut; no analysis run / transcript needed — ``build_cutlist`` only
reads each scene's SOURCE frame range), extended to THREE scenes so the count-drift test can
reference a third distinct scene instead of a second window of an already-used one (Storyline
itself rejects the same (scene, window) pair twice anywhere in the arc).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.short_creator.board import Board
from laura.short_creator.board_models import (
    BoardMeta,
    Chapter,
    Cutlist,
    Script,
    ScriptLine,
    Storyline,
    VoiceArtifact,
    VoiceSegment,
    lines_in_storyline_order,
)
from laura.short_creator.production_tools import (
    build_production_tool_specs,
    script_hash,
)
from laura.short_creator.voice_concat import INTER_SCENE_GAP_S

FPS = 30
SCENE_FRAMES = 300  # 300 frames @ 30fps = 10.0s per scene
N_SCENES = 3


def _seed_two_scenes(tmp_path: Path) -> tuple[Database, str]:
    """Project + asset + a rough cut with ``N_SCENES`` equal-length scenes. One lane-0 clip
    spans all of them 1:1 (SOURCE == SEQUENCE, speed 1): scene i is source frames
    [(i-1)*SCENE_FRAMES, i*SCENE_FRAMES). Most tests here only ever reference scenes 1 and 2 —
    the third exists solely so the count-drift test has a distinct scene to reference instead of
    a second window of an already-used one."""
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
        src_out_frame_exclusive=SCENE_FRAMES * N_SCENES,
        seq_in_frame=0,
        seq_out_frame_exclusive=SCENE_FRAMES * N_SCENES,
        lane=0,
        role="base",
    )
    repos.replace_scenes(
        db,
        project["id"],
        timeline["id"],
        [(i * SCENE_FRAMES, (i + 1) * SCENE_FRAMES) for i in range(N_SCENES)],
    )
    return db, str(asset["id"])


def _board(tmp_path: Path, asset_id: str) -> Board:
    meta = BoardMeta(
        session_id="s1",
        asset_id=asset_id,
        created_utc="2026-08-06T00:00:00Z",
        task="overview short",
        target_seconds=20.0,
    )
    return Board.create(tmp_path / "board", meta)


def _storyline(*, arc: list[Chapter] | None = None) -> Storyline:
    return Storyline(
        red_thread="stop scrolling",
        arc=arc
        if arc is not None
        else [
            Chapter(
                chapter=1,
                role="hook",
                message="stop scrolling",
                scene_numbers=[1, 2],
                target_seconds=4.0,
            )
        ],
    )


def _script(*, lines: list[ScriptLine] | None = None) -> Script:
    return Script(
        language="de",
        lines=lines
        if lines is not None
        else [
            ScriptLine(chapter=1, scene_number=1, text="Stopp dein Team"),
            ScriptLine(chapter=1, scene_number=2, text="Ein Klick genuegt"),
        ],
    )


def _save_voice_with_segments(
    board: Board,
    tmp_path: Path,
    durations: list[float],
    *,
    n_lines: int | None = None,
) -> VoiceArtifact:
    """Seed the board's voice artifact with PER-LINE segments (VS2 contract), one
    ``VoiceSegment`` per entry in ``durations``, walked in the SAME storyline-ordered iteration
    ``build_cutlist`` itself uses (``lines_in_storyline_order``) so ``segments[i]`` lines up
    with the storyline's i-th scene entry across the whole arc — exactly the invariant
    ``build_cutlist``'s ``order`` slicing depends on.

    ``n_lines`` lets a drift test build FEWER (or more) segments than the storyline actually has
    lines for, without also having to fabricate a mismatched script — it only controls how many
    of ``lines_in_storyline_order``'s lines get a segment; the ``script_hash`` stamp always
    covers the REAL ordered lines, so the earlier voice/script agreement check still passes and
    the NEW count-drift guard is the one that fires.
    """
    script = board.load("script")
    storyline = board.load("storyline")
    assert isinstance(script, Script), "seed the script before the voice"
    assert isinstance(storyline, Storyline), "seed the storyline before the voice"
    lines = lines_in_storyline_order(script, storyline)
    take = n_lines if n_lines is not None else len(lines)
    assert take <= len(lines)
    assert len(durations) == take

    segments: list[VoiceSegment] = []
    offset = 0.0
    for line, dur in zip(lines[:take], durations, strict=True):
        segments.append(
            VoiceSegment(
                scene_number=line.scene_number,
                chapter=line.chapter,
                line_hash=f"hash-{line.chapter}-{line.scene_number}",
                mp3_path=str(tmp_path / f"{line.chapter}-{line.scene_number}.mp3"),
                duration_s=dur,
                offset_s=offset,
            )
        )
        offset += dur + INTER_SCENE_GAP_S

    artifact = VoiceArtifact(
        script_hash=script_hash(lines),
        mp3_path=str(tmp_path / "voice.mp3"),
        timings_path=None,
        segments=segments,
    )
    board.save("voice", artifact)
    return artifact


def test_segments_sized_to_their_clips(tmp_path: Path) -> None:
    """The core VS3 contract: segment i's length == segments[i].duration_s + gap, except the
    LAST segment which gets no gap — video total matches audio total (offsets construction)."""
    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    board.save("storyline", _storyline())
    board.save("script", _script())
    _save_voice_with_segments(board, tmp_path, [1.2, 0.8])
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["build_cutlist"].func()

    assert out["ok"] is True, out
    cutlist = board.load("cutlist")
    assert isinstance(cutlist, Cutlist)
    seg0, seg1 = cutlist.segments

    d0 = (seg0.end_frame_exclusive - seg0.start_frame) / FPS
    d1 = (seg1.end_frame_exclusive - seg1.start_frame) / FPS
    assert d0 == pytest.approx(1.2 + INTER_SCENE_GAP_S, abs=1 / FPS)
    assert d1 == pytest.approx(0.8, abs=1 / FPS)  # last segment: no gap
    # sync invariant: video total == audio total (offsets construction, n-1 gaps)
    assert d0 + d1 == pytest.approx(1.2 + INTER_SCENE_GAP_S + 0.8, abs=2 / FPS)


def test_segment_count_drift_rejected(tmp_path: Path) -> None:
    """Storyline references THREE distinct (chapter, scene) entries but the voice on the board
    only carries two line clips — a stale voice (script edited/re-ordered without a re-run of
    synthesize_script_voice) must refuse loudly, naming the fix, not silently misalign. The
    guard fires purely on COUNT, before any per-chapter window resolution, so all three entries
    use plain (reviewless) scene numbers rather than exercising the window machinery."""
    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    board.save(
        "storyline",
        _storyline(
            arc=[
                Chapter(
                    chapter=1,
                    role="hook",
                    message="c1",
                    scene_numbers=[1],
                    target_seconds=2.0,
                ),
                Chapter(
                    chapter=2,
                    role="payoff_cta",
                    message="c2",
                    scene_numbers=[2, 3],
                    target_seconds=2.0,
                ),
            ]
        ),
    )
    board.save(
        "script",
        _script(
            lines=[
                ScriptLine(chapter=1, scene_number=1, text="Erste Zeile"),
                ScriptLine(chapter=2, scene_number=2, text="Zweite Zeile"),
                ScriptLine(chapter=2, scene_number=3, text="Dritte Zeile"),
            ]
        ),
    )
    # storyline has 3 scene entries (1 + 2 + 3) across its two chapters; only 2 line clips seeded.
    _save_voice_with_segments(board, tmp_path, [1.0, 1.0], n_lines=2)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["build_cutlist"].func()

    assert out["ok"] is False
    assert "synthesize_script_voice" in out["reason"]
    assert board.load("cutlist") is None


def test_clip_longer_than_scene_rejected_with_scene_name(tmp_path: Path) -> None:
    """A per-line clip that simply cannot fit inside its scene's SOURCE range (the scene is only
    ``SCENE_FRAMES`` / 30fps = 10.0s of source, and the guard uses the plain scene length, not a
    window-narrowed capacity) must refuse with the offending scene named, not silently overrun
    the source or clamp away the mismatch."""
    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    board.save("storyline", _storyline())
    board.save("script", _script())
    # scene 1's line "speaks" 20.0s but the whole scene is only 10.0s of source.
    _save_voice_with_segments(board, tmp_path, [20.0, 0.8])
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["build_cutlist"].func()

    assert out["ok"] is False
    assert "scene 1" in out["reason"]
    assert board.load("cutlist") is None


# --- legacy path (segments=None) stays byte-identical -------------------------------------------
#
# No NEW test is written for this — it would only duplicate coverage. build_cutlist's legacy,
# chapter_audio_windows-scaled duration path is already exercised end-to-end, unchanged, by the
# EXISTING tests in tests/test_production_tools_cutlist.py, all of which seed a VoiceArtifact via
# that file's ``_save_voice`` helper (constructs VoiceArtifact WITHOUT the ``segments`` kwarg, so
# it defaults to None):
#   test_build_cutlist_deterministic_segments_and_zoom
#   test_build_cutlist_clamps_inside_scene
#   test_build_cutlist_couples_segment_durations_to_chapter_audio
#   test_build_cutlist_floors_when_chapter_audio_shorter_than_scenes
#   test_build_cutlist_stretch_stops_at_scene_end_keeping_offset
#   test_build_cutlist_uses_referenced_window
#   test_build_cutlist_same_scene_twice_with_different_windows
#   test_build_cutlist_rejects_out_of_range_window_ref
#   test_build_cutlist_stamps_storyline_script_and_voice_parents
#   test_build_cutlist_refuses_a_voice_from_a_different_script
#   test_build_cutlist_gives_short_and_long_windows_equal_time
#   test_build_cutlist_zoom_off_drops_all_rois_and_zoom
#   test_build_cutlist_zoom_off_overrides_window_refs
# All of these stay green, unmodified, after this task's change to build_cutlist — that IS the
# "legacy path byte-identical" assertion, verified by running the whole file (see the module
# docstring's regression scope / the task report for the actual run).
