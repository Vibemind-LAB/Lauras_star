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

import json
from pathlib import Path
from typing import Any

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.short_creator.board import Board
from laura.short_creator.board_models import (
    BestWindow,
    BoardMeta,
    Chapter,
    Cutlist,
    Roi,
    SceneReview,
    SceneWindowRef,
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


def _review(
    board: Board,
    scene_number: int,
    *,
    best_window: BestWindow | None = None,
    roi: Roi | None = None,
    windows: list[BestWindow] | None = None,
) -> None:
    """Write a minimal valid SceneReview straight to the board (same shape as
    ``test_production_tools_cutlist.py``'s ``_review`` — only needed here for the window-reuse
    test, which requires a scene with >=2 windows to get PAST the window-resolution guards
    before the identity-pairing guard under test can fire)."""
    board.save_scene_review(
        SceneReview(
            scene_number=scene_number,
            src_start_frame=(scene_number - 1) * SCENE_FRAMES,
            src_end_frame_exclusive=scene_number * SCENE_FRAMES,
            description="d",
            whats_happening="h",
            hook_score=5,
            best_window=best_window
            if best_window is not None
            else BestWindow(offset_s=0.0, duration_s=2.0),
            windows=windows if windows is not None else [],
            roi=roi,
        )
    )


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
    words: list[dict[str, Any]] | None = None,
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

    ``words`` optionally writes a real timings sidecar (same shape as
    ``test_production_tools_cutlist.py``'s ``_save_voice`` helper) so ``build_cutlist``'s
    ``line_starts``-derived zoom timing has something to read — needed only by the zoom-anchor
    regression test; every other test here leaves ``timings_path=None`` (no zoom scheduled).
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

    timings_path: str | None = None
    if words is not None:
        wp = tmp_path / "voice.mp3.timings.json"
        wp.write_text(json.dumps({"words": words}), encoding="utf-8")
        timings_path = str(wp)

    artifact = VoiceArtifact(
        script_hash=script_hash(lines),
        mp3_path=str(tmp_path / "voice.mp3"),
        timings_path=timings_path,
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
    only carries two line clips — a stale voice (script edited without a re-run of
    synthesize_script_voice) must refuse loudly, naming the fix, not silently misalign.

    Identity-pairing (the controller's design correction over the original positional-slice
    design) means this manifests as the per-entry "no own narration line" refusal for the
    specific entry missing its clip (scene 3 here), not a separate count check — there is no
    upfront raw-count guard anymore. All three entries use plain (reviewless) scene numbers so
    the window machinery never has a chance to fire first."""
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
    assert "scene 3" in out["reason"]  # the specific entry with no clip of its own
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


# --- identity pairing (controller design correction over the original positional slice) ---------


def test_window_reuse_with_one_line_rejected_naming_scene_and_window(tmp_path: Path) -> None:
    """A chapter references ONE scene through TWO different review windows, but the script (and
    therefore the voice) has only ONE line for that scene — ``lines_in_storyline_order`` collapses
    a scene repeated WITHIN one chapter to its first occurrence, so the voice has exactly one clip
    for it. The first window entry claims that clip; the second finds nothing of its own and must
    refuse naming BOTH the scene and the WINDOW (not the generic leftover/count wording — a caller
    fixing this needs to know it's window 1 specifically that has no line, not that the whole
    voice is stale)."""
    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    w0 = BestWindow(offset_s=0.0, duration_s=2.0)
    w1 = BestWindow(offset_s=5.0, duration_s=2.0)
    _review(board, 1, best_window=w0, windows=[w0, w1])
    board.save(
        "storyline",
        _storyline(
            arc=[
                Chapter(
                    chapter=1,
                    role="hook",
                    message="m",
                    scene_numbers=[1, SceneWindowRef(scene=1, window=1)],
                    target_seconds=4.0,
                )
            ]
        ),
    )
    board.save(
        "script", _script(lines=[ScriptLine(chapter=1, scene_number=1, text="Nur eine Zeile")])
    )
    _save_voice_with_segments(board, tmp_path, [1.0])
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["build_cutlist"].func()

    assert out["ok"] is False
    assert "scene 1" in out["reason"]
    assert "window 1" in out["reason"]
    assert "has no own narration line" in out["reason"]
    assert "no longer references" not in out["reason"]  # distinct from the leftover refusal
    assert board.load("cutlist") is None


def test_two_lines_same_scene_merge_into_one_segment(tmp_path: Path) -> None:
    """A scene spoken by TWO script lines (same chapter + scene_number — e.g. a beat split into
    two sentences): both clips are consecutive in the constructed track and pair to the SAME
    storyline entry, landing in ONE cutlist segment sized to their sum plus the ONE inner gap
    between them (+ the usual trailing gap to the next entry, since this scene is not last) — the
    reason identity-pairing groups by key instead of a 1:1 positional slice."""
    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    board.save(
        "storyline",
        _storyline(
            arc=[
                Chapter(
                    chapter=1, role="hook", message="m", scene_numbers=[1, 2], target_seconds=4.0
                )
            ]
        ),
    )
    board.save(
        "script",
        _script(
            lines=[
                ScriptLine(chapter=1, scene_number=1, text="Erster Satz"),
                ScriptLine(chapter=1, scene_number=1, text="Zweiter Satz"),
                ScriptLine(chapter=1, scene_number=2, text="Andere Szene"),
            ]
        ),
    )
    _save_voice_with_segments(board, tmp_path, [1.0, 0.5, 0.8])
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["build_cutlist"].func()

    assert out["ok"] is True, out
    assert out["segments"] == 2  # one cutlist segment per STORYLINE entry, not per script line
    cutlist = board.load("cutlist")
    assert isinstance(cutlist, Cutlist)
    seg0, seg1 = cutlist.segments

    d0 = (seg0.end_frame_exclusive - seg0.start_frame) / FPS
    d1 = (seg1.end_frame_exclusive - seg1.start_frame) / FPS
    # scene 1's segment = both its clips (1.0 + 0.5) + ONE inner gap between them + ONE trailing
    # gap to scene 2's entry.
    assert d0 == pytest.approx(1.0 + 0.5 + 2 * INTER_SCENE_GAP_S, abs=1 / FPS)
    assert d1 == pytest.approx(0.8, abs=1 / FPS)  # last entry: no trailing gap
    # sync invariant: video total == audio total (3 clips -> n-1 = 2 gaps total, VS1's rule).
    total_audio = 1.0 + 0.5 + 0.8 + 2 * INTER_SCENE_GAP_S
    assert d0 + d1 == pytest.approx(total_audio, abs=2 / FPS)


def test_merged_scene_zoom_anchors_to_first_lines_start(tmp_path: Path) -> None:
    """Regression (review round 2): ``line_starts`` must keep the FIRST line's word start for a
    ``(chapter, scene_number)`` key shared by multiple lines (the same two-line-merge shape as
    ``test_two_lines_same_scene_merge_into_one_segment``, here WITH a review roi so a zoom is
    actually scheduled, and real word timings). An unconditional last-write-wins ``line_starts``
    would anchor the zoom to the SECOND line's spoken moment instead of where the scene's
    narration actually begins — here that bug would push the candidate outside the
    ``candidate < actual_dur_s - 0.7`` window and drop the zoom entirely, an even more visible
    symptom than a merely-wrong timestamp."""
    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(
        board,
        1,
        best_window=BestWindow(offset_s=0.0, duration_s=2.0),
        roi=Roi(x=0.1, y=0.1, w=0.2, h=0.2),
    )
    board.save(
        "storyline",
        _storyline(
            arc=[
                Chapter(
                    chapter=1, role="hook", message="m", scene_numbers=[1, 2], target_seconds=4.0
                )
            ]
        ),
    )
    board.save(
        "script",
        _script(
            lines=[
                ScriptLine(chapter=1, scene_number=1, text="Erster Satz"),
                ScriptLine(chapter=1, scene_number=1, text="Zweiter Satz"),
                ScriptLine(chapter=1, scene_number=2, text="Andere Szene"),
            ]
        ),
    )
    # Word starts for the 6 whitespace tokens of "Erster Satz Zweiter Satz Andere Szene": the
    # FIRST line ("Erster Satz") starts at 0.0s, the SECOND ("Zweiter Satz") at 1.55s — chosen so
    # a buggy last-write-wins line_starts (anchoring to 1.55s) pushes the zoom candidate to 1.95s,
    # outside this segment's `candidate < actual_dur_s - 0.7` window (actual_dur_s == 2.2s here),
    # dropping the zoom to None — a difference no float tolerance could paper over.
    _save_voice_with_segments(
        board,
        tmp_path,
        [1.0, 0.5, 0.8],
        words=[
            {"text": "Erster", "start_s": 0.0, "end_s": 0.3},
            {"text": "Satz", "start_s": 0.3, "end_s": 0.6},
            {"text": "Zweiter", "start_s": 1.55, "end_s": 1.85},
            {"text": "Satz", "start_s": 1.85, "end_s": 2.1},
            {"text": "Andere", "start_s": 2.4, "end_s": 2.7},
            {"text": "Szene", "start_s": 2.7, "end_s": 3.0},
        ],
    )
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["build_cutlist"].func()

    assert out["ok"] is True, out
    cutlist = board.load("cutlist")
    assert isinstance(cutlist, Cutlist)
    seg0 = cutlist.segments[0]
    assert seg0.scene_number == 1
    assert seg0.zoom_start_s is not None
    # video_start_s is 0.0 for the first segment; transition_lead_s defaults to 0.4 -> zoom =
    # max(0.0, line_start - 0.0 + 0.4). The FIRST line's start (0.0) gives 0.4.
    assert seg0.zoom_start_s == pytest.approx(0.4, abs=1 / FPS)


def test_unresolved_scene_rejected_in_per_scene_path(tmp_path: Path) -> None:
    """A storyline entry referencing a scene number that does not exist in this asset's rough cut
    (an error state ``save_storyline``'s own tool-level checks would normally catch — this
    fixture bypasses it, same pattern as
    ``test_build_cutlist_rejects_out_of_range_window_ref``) must refuse naming the scene. The
    legacy path silently ``continue``s past an unresolved scene; the per-scene-voice path cannot,
    because that silent skip would desync the identity pairing — the voice's own clip for the
    missing scene would surface as a misleading "no longer referenced" leftover instead of
    pointing at the real problem."""
    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    board.save(
        "storyline",
        _storyline(
            arc=[
                Chapter(chapter=1, role="hook", message="m", scene_numbers=[99], target_seconds=2.0)
            ]
        ),
    )
    board.save(
        "script", _script(lines=[ScriptLine(chapter=1, scene_number=99, text="Geisterszene")])
    )
    _save_voice_with_segments(board, tmp_path, [1.0])
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["build_cutlist"].func()

    assert out["ok"] is False
    assert "scene 99" in out["reason"]
    assert board.load("cutlist") is None


def test_leftover_unconsumed_clips_after_storyline_drops_a_scene(tmp_path: Path) -> None:
    """The script still carries a line for a scene NO chapter's storyline references anymore
    (``lines_in_storyline_order`` never drops such a line — the voice must contain every line the
    author wrote, per its own docstring) — after the whole arc is walked, that clip's group is
    still sitting in ``clips_by_key``, unconsumed. Must refuse instead of silently building a cut
    that is short one scene's worth of narration (or, worse, one whose voice mp3 runs longer than
    the picture it was cut to)."""
    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    board.save("storyline", _storyline())  # default: chapter 1, scene_numbers=[1, 2]
    board.save(
        "script",
        _script(
            lines=[
                ScriptLine(chapter=1, scene_number=1, text="Stopp dein Team"),
                ScriptLine(chapter=1, scene_number=2, text="Ein Klick genuegt"),
                # storyline never references scene 3 in any chapter's scene_numbers.
                ScriptLine(chapter=1, scene_number=3, text="Verwaiste Zeile"),
            ]
        ),
    )
    _save_voice_with_segments(board, tmp_path, [1.2, 0.8, 0.5])
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["build_cutlist"].func()

    assert out["ok"] is False
    assert "no longer references" in out["reason"]
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
