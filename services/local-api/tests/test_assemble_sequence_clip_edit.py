"""Per-clip lane-0 drag edits on the AssembleView ("Zusammenfügen") sequence.

ROOT CAUSE (was a 422; now fixed by routing lane-0 sequence edits to the scene timeline):

    The AssembleView timeline (``apps/desktop/src/components/AssembleView.tsx``) hands
    ``<TimelineBar>`` a timeline shaped ``{ id: sequence.timeline_id, clips: seqClips }`` where
    ``seqClips`` come from ``GET /sequences/{seq_id}/flattened`` (``flatten_sequence``).  For a
    ``kind="sequence"`` timeline the flattened LANE-0 clips are resolved from each scene's *own*
    ``scene_timeline_id`` and re-offset by the running cumulative scene length — they DO NOT live
    on the sequence timeline's ``timeline_clips`` at all (that table holds only lane-≥1 overlays).

    Every TimelineBar lane-0 drag (move / trim / split / set_audio_offset / set_speed) fires
    ``POST /timelines/{seq_id}/operations`` with ``at_seq_frame`` taken from a *flattened* clip.
    Before the fix ``apply_operation`` loaded ``list_timeline_clips(seq_id)`` (zero lane-0 clips)
    and matched on exact ``seq_in_frame`` — no clip matched → ``ValueError`` → HTTP 422.

THE FIX (``laura.api.timelines._route_lane0_seq_op_to_scene`` + ``_apply_lane0_scene_op``, reusing
``laura.sequences.flatten.sequence_scene_windows``): a lane-0 single-clip op on a
``kind="sequence"`` timeline whose ``at_seq_frame`` falls into a flattened scene window is routed
to that scene's own
timeline, translated to scene-local frames, applied there, and the freshly flattened sequence is
returned.  A *cross-scene* ``move`` (a scene-reorder concern) is rejected with a specific 422 rather
than silently corrupting the cut.  Lane-≥1 overlays still apply to the sequence timeline unchanged.

Fixture pattern mirrors ``tests/test_flatten_sequence.py`` / ``tests/test_place_clip_api.py``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app
from laura.timebase.sampling import frame_to_sample

# Audio constants for the set_audio_offset (L/J) routing test: 48 kHz / 30 fps == 1600 samples/frame.
_SAMPLE_RATE = 48_000
_RATE_NUM, _RATE_DEN = 30, 1
_SPF = frame_to_sample(1, _SAMPLE_RATE, _RATE_NUM, _RATE_DEN)  # 1600 samples per frame


def _scene_timeline(
    db: SqliteDatabase,
    project_id: str,
    asset_id: str,
    clips: list[tuple[int, int]],
) -> dict[str, Any]:
    """A materialized scene timeline with the given lane-0 ``(src_in, src_out)`` clips, packed
    back-to-back from seq 0 (so a scene can hold one OR several lane-0 clips)."""
    tl = repos.create_timeline(db, project_id=project_id, name="s", kind="scene")
    rows: list[dict[str, Any]] = []
    seq = 0
    for src_in, src_out in clips:
        length = src_out - src_in
        rows.append(
            {
                "asset_id": asset_id,
                "src_in_frame": src_in,
                "src_out_frame_exclusive": src_out,
                "seq_in_frame": seq,
                "seq_out_frame_exclusive": seq + length,
                "lane": 0,
                "speed_num": 1,
                "speed_den": 1,
            }
        )
        seq += length
    repos.replace_timeline_clips(db, tl["id"], rows)
    return tl


def _assemble_sequence(
    tmp_path: Path,
    *,
    scene_clips: list[list[tuple[int, int]]],
    audio_sample_rate: int | None = None,
) -> tuple[TestClient, SqliteDatabase, str, list[dict[str, Any]]]:
    """Return (client, db, sequence_timeline_id, flattened_clips) for a sequence of scenes.

    ``scene_clips[i]`` is scene *i*'s list of lane-0 ``(src_in, src_out)`` clips.  Reproduces the
    AssembleView data wiring: scenes materialized to their own timelines, arranged into the project
    sequence; the sequence is then read back *flattened* (exactly what TimelineBar renders).
    """
    settings = Settings(workspace_root=tmp_path, start_runner=False, token=None)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    client = TestClient(create_app(settings))
    client.__enter__()

    pid: str = client.post(
        "/projects",
        json={"name": "p", "sequence_rate_num": _RATE_NUM, "sequence_rate_den": _RATE_DEN},
    ).json()["id"]

    a = repos.create_asset(
        db, project_id=pid, type="video", display_name="a", source_path="/tmp/a.mp4"
    )
    if audio_sample_rate is not None:
        repos.update_asset_probe(
            db, a["id"], type="video", duration_frames=10_000,
            rate_num=_RATE_NUM, rate_den=_RATE_DEN, audio_sample_rate=audio_sample_rate,
            start_timecode=None, width=1920, height=1080,
            codec_video="h264", codec_audio="aac", is_vfr=False, sha256=None,
        )

    n_scenes = len(scene_clips)
    rc = repos.create_timeline(db, project_id=pid, name="rc", kind="rough_cut")
    bounds = [(i * 30, i * 30 + 30) for i in range(n_scenes)]
    repos.replace_scenes(db, pid, rc["id"], bounds)
    scenes = repos.list_scenes(db, rc["id"])
    scene_ids: list[str] = []
    for i, sc in enumerate(scenes):
        t = _scene_timeline(db, pid, a["id"], scene_clips[i])
        repos.set_scene_timeline(db, sc["id"], t["id"])
        scene_ids.append(sc["id"])

    seq_id: str = client.get(f"/projects/{pid}/sequence").json()["timeline_id"]
    client.put(f"/sequences/{seq_id}/scenes", json={"scene_ids": scene_ids})

    flattened: list[dict[str, Any]] = client.get(f"/sequences/{seq_id}/flattened").json()
    return client, db, seq_id, flattened


def _lane0(flattened: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted((c for c in flattened if c["lane"] == 0), key=lambda c: c["seq_in_frame"])


# --- a default 2-scene sequence, one 30f clip each (the original repro shape) ----------------
def _two_single(tmp_path: Path) -> tuple[TestClient, SqliteDatabase, str, list[dict[str, Any]]]:
    return _assemble_sequence(
        tmp_path, scene_clips=[[(100, 130)], [(200, 230)]]
    )


def test_flattened_lane0_clips_absent_from_sequence_timeline(tmp_path: Path) -> None:
    """Diagnostic (still true): the flattened lane-0 clips do NOT exist on the sequence timeline
    that ``apply_operation`` targets — this is the exact divergence the routing fix bridges."""
    _client, db, seq_id, flattened = _two_single(tmp_path)

    lane0 = _lane0(flattened)
    # Two 30-frame scenes, flattened contiguously on lane 0.
    assert [(c["seq_in_frame"], c["seq_out_frame_exclusive"]) for c in lane0] == [
        (0, 30),
        (30, 60),
    ]
    # ...yet the sequence timeline that operations target has zero lane-0 clips.
    on_sequence = repos.list_timeline_clips(db, seq_id)
    assert [c for c in on_sequence if c["lane"] == 0] == []


def test_assemble_trim_lane0_clip_singlescene(tmp_path: Path) -> None:
    """A single-scene sequence can now trim its lane-0 clip: the op routes to the scene timeline
    (flatten offset 0) and the flattened clip shows the new, 5-frame-shorter length."""
    client, db, seq_id, flattened = _assemble_sequence(
        tmp_path, scene_clips=[[(100, 130)]]
    )
    clip = _lane0(flattened)[0]  # flattened seq_in == 0, length 30

    resp = client.post(
        f"/timelines/{seq_id}/operations",
        json={
            "op": "trim",
            "at_seq_frame": clip["seq_in_frame"],
            "new_src_in_frame": clip["src_in_frame"],
            "new_src_out_frame_exclusive": clip["src_out_frame_exclusive"] - 5,
        },
    )
    assert resp.status_code == 200, resp.text

    after = _lane0(client.get(f"/sequences/{seq_id}/flattened").json())
    assert len(after) == 1
    assert after[0]["seq_in_frame"] == 0
    assert after[0]["seq_out_frame_exclusive"] == 25  # 30 - 5
    assert after[0]["src_out_frame_exclusive"] == 125  # 130 - 5
    # The edit landed on the scene timeline, not the (still lane-0-empty) sequence timeline.
    assert [c for c in repos.list_timeline_clips(db, seq_id) if c["lane"] == 0] == []


def test_assemble_trim_lane0_clip_multiscene_second(tmp_path: Path) -> None:
    """The user's real 422: trimming the SECOND scene's flattened clip (seq_in == 30).

    It must route into scene 2 (window [30, 60)), shorten that clip, and leave scene 1 untouched."""
    client, _db, seq_id, flattened = _two_single(tmp_path)
    second = _lane0(flattened)[1]  # flattened seq_in == 30
    assert second["seq_in_frame"] == 30

    resp = client.post(
        f"/timelines/{seq_id}/operations",
        json={
            "op": "trim",
            "at_seq_frame": second["seq_in_frame"],
            "new_src_in_frame": second["src_in_frame"],
            "new_src_out_frame_exclusive": second["src_out_frame_exclusive"] - 10,
        },
    )
    assert resp.status_code == 200, resp.text

    after = _lane0(client.get(f"/sequences/{seq_id}/flattened").json())
    assert [(c["seq_in_frame"], c["seq_out_frame_exclusive"]) for c in after] == [
        (0, 30),   # scene 1 unchanged
        (30, 50),  # scene 2 now 20f (30 - 10), still contiguous after scene 1
    ]
    assert after[1]["src_out_frame_exclusive"] == 220  # 230 - 10


def test_assemble_set_audio_offset_within_scene_cut(tmp_path: Path) -> None:
    """The user's "+21f" L/J case: set_audio_offset on a WITHIN-SCENE cut of a flattened clip.

    The L/J ``audio_offset_samples`` is the leading-edge shift of the cut between a clip and its
    PREDECESSOR — an *intra-timeline* concept. Scene 2 here holds two lane-0 clips
    [A(15f), B(15f)] flattened at [30,45)+[45,60); dragging B's head (flattened seq_in 45) routes
    into scene 2 (scene-local at 15, where B is NOT the scene head) and persists the canonical
    sample offset (invariant #3) on B."""
    client, db, seq_id, flattened = _assemble_sequence(
        tmp_path,
        scene_clips=[[(100, 130)], [(200, 215), (300, 315)]],
        audio_sample_rate=_SAMPLE_RATE,
    )
    lane0 = _lane0(flattened)
    assert [(c["seq_in_frame"], c["seq_out_frame_exclusive"]) for c in lane0] == [
        (0, 30), (30, 45), (45, 60)
    ]
    clip_b = lane0[2]  # flattened seq_in == 45 (scene 2's SECOND clip; a within-scene cut)

    resp = client.post(
        f"/timelines/{seq_id}/operations",
        json={
            "op": "set_audio_offset",
            "at_seq_frame": clip_b["seq_in_frame"],
            "audio_offset_frames": 21,
        },
    )
    assert resp.status_code == 200, resp.text

    after = _lane0(client.get(f"/sequences/{seq_id}/flattened").json())
    # +21 frames at 1600 samples/frame == +33600 samples land on B; other heads stay hard cuts.
    assert [c["audio_offset_samples"] for c in after] == [0, 0, 21 * _SPF]
    # It persisted on the scene timeline (the lane-0 source of truth), not the sequence timeline.
    assert [c for c in repos.list_timeline_clips(db, seq_id) if c["lane"] == 0] == []


def test_assemble_set_audio_offset_on_scene_head_is_hard_cut(tmp_path: Path) -> None:
    """Documented boundary: a scene's HEAD clip is the scene's lane-0 head in its own timeline, so
    its leading cut is a hard cut (first-clip-0 invariant) — setting an L/J there is a no-op (200,
    offset stays 0). The inter-scene boundary is a SEQUENCE transition (``transition_after_kind``),
    not an intra-timeline L/J split, so this is correct, not a corruption."""
    client, _db, seq_id, flattened = _assemble_sequence(
        tmp_path, scene_clips=[[(100, 130)], [(200, 230)]], audio_sample_rate=_SAMPLE_RATE
    )
    scene2_head = _lane0(flattened)[1]  # flattened seq_in == 30 (scene 2's HEAD clip)

    resp = client.post(
        f"/timelines/{seq_id}/operations",
        json={
            "op": "set_audio_offset",
            "at_seq_frame": scene2_head["seq_in_frame"],
            "audio_offset_frames": 21,
        },
    )
    assert resp.status_code == 200, resp.text
    after = _lane0(client.get(f"/sequences/{seq_id}/flattened").json())
    assert [c["audio_offset_samples"] for c in after] == [0, 0]  # scene-head cut stays hard


def test_assemble_move_within_scene_succeeds(tmp_path: Path) -> None:
    """A within-scene move (a scene with TWO lane-0 clips) re-packs that scene's lane and succeeds.

    Scene 1 = [A(10f), B(20f)] flattened at [0,10)+[10,30); scene 2 = one 30f clip at [30,60).
    Dragging B (flattened seq_in 10) to the front of its own scene (to 0) reorders within scene 1."""
    client, _db, seq_id, flattened = _assemble_sequence(
        tmp_path, scene_clips=[[(100, 110), (200, 220)], [(300, 330)]]
    )
    lane0 = _lane0(flattened)
    assert [(c["seq_in_frame"], c["seq_out_frame_exclusive"]) for c in lane0] == [
        (0, 10), (10, 30), (30, 60)
    ]
    clip_b = lane0[1]  # flattened seq_in == 10, the 20f clip (src 200..220)

    resp = client.post(
        f"/timelines/{seq_id}/operations",
        json={"op": "move", "at_seq_frame": clip_b["seq_in_frame"], "to_seq_frame": 0},
    )
    assert resp.status_code == 200, resp.text

    after = _lane0(client.get(f"/sequences/{seq_id}/flattened").json())
    # B (20f) now leads scene 1, then A (10f); scene 2 still contiguous after the 30f scene-1 span.
    assert [
        (c["src_in_frame"], c["src_out_frame_exclusive"], c["seq_in_frame"],
         c["seq_out_frame_exclusive"])
        for c in after
    ] == [
        (200, 220, 0, 20),   # B moved to the front of scene 1
        (100, 110, 20, 30),  # A follows
        (300, 330, 30, 60),  # scene 2 unchanged
    ]


def test_assemble_move_cross_scene_rejected(tmp_path: Path) -> None:
    """Dragging scene 2's clip to the front (to=0, a DIFFERENT scene) is a scene-reorder, not a
    clip move: the fix rejects it with a specific 422 rather than silently corrupting the cut.

    (This is the original ``test_assemble_move_lane0_clip_multiscene`` repro, now resolved by an
    explicit, frontend-surfaceable rejection — scene reordering goes through the storyboard.)"""
    client, db, seq_id, flattened = _two_single(tmp_path)
    second = _lane0(flattened)[1]  # flattened seq_in == 30 (scene 2)

    resp = client.post(
        f"/timelines/{seq_id}/operations",
        json={"op": "move", "at_seq_frame": second["seq_in_frame"], "to_seq_frame": 0},
    )
    assert resp.status_code == 422, resp.text
    assert "cross-scene move" in resp.json()["detail"]

    # Nothing was mutated: the flattened lane-0 layout is byte-identical to before.
    after = _lane0(client.get(f"/sequences/{seq_id}/flattened").json())
    assert [(c["seq_in_frame"], c["seq_out_frame_exclusive"]) for c in after] == [
        (0, 30), (30, 60)
    ]
    assert [c for c in repos.list_timeline_clips(db, seq_id) if c["lane"] == 0] == []


def test_assemble_split_lane0_clip_multiscene_second(tmp_path: Path) -> None:
    """Splitting inside the second scene's flattened clip routes into that scene and yields two
    contiguous sub-clips; the split point maps to scene-local frames, not the absolute seq frame."""
    client, _db, seq_id, flattened = _two_single(tmp_path)
    second = _lane0(flattened)[1]  # flattened [30, 60), src 200..230

    # Split at flattened frame 45 (== scene-local 15) -> source mid 215.
    resp = client.post(
        f"/timelines/{seq_id}/operations",
        json={"op": "split", "at_seq_frame": 45},
    )
    assert resp.status_code == 200, resp.text

    after = _lane0(client.get(f"/sequences/{seq_id}/flattened").json())
    assert [
        (c["src_in_frame"], c["src_out_frame_exclusive"], c["seq_in_frame"],
         c["seq_out_frame_exclusive"])
        for c in after
    ] == [
        (100, 130, 0, 30),    # scene 1 untouched
        (200, 215, 30, 45),   # scene 2, left half of the split
        (215, 230, 45, 60),   # scene 2, right half
    ]
    assert second["src_out_frame_exclusive"] == 230  # sanity: pre-split span was the full clip
