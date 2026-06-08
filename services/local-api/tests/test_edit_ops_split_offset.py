"""The editing ops preserve / recompute the per-clip L/J audio offset (2-lane m2).

m1 added the ``timeline_clips.audio_offset_samples`` column (signed per-clip LEADING-edge
audio-vs-video offset in SAMPLES, invariant #3; 0 = hard cut) and routed OTIO / exports / accept
through it. But the editing OPS did not thread the column, so a trim / move / split / delete reset
every offset to 0 and the split survived only via the legacy OTIO-metadata fallback. m2 makes the
column the LIVE truth across edits.

Per-op rules pinned here (the offset is a property of a clip's HEAD = the cut with its predecessor):

* ``trim_clip`` PRESERVES the clip's offset (first clip stays 0);
* ``split_clip`` keeps the original's offset, the new second clip's head is a hard cut -> 0;
* ``move_clip`` / reorder: the offset TRAVELS with the clip; a clip moved to position 0 -> 0;
* ``delete_range`` / ``lift_range``: an offset on a removed head vanishes; a sliced clip's leading
  offset survives only on the sub-piece that keeps the original head; first-clip-0 holds;
* ``insert_clip`` / ``append_clip``: inserted/appended clip -> 0 unless one is supplied;
* ``set_speed`` PRESERVES the head-cut offset (samples) unscaled.

Plus the integration proof: an accepted J/L split then a trim / move still exports the audio on the
offset frame FROM THE COLUMN ALONE (the OTIO blob is wiped to prove no metadata dependence), and
undo/redo (set_clips snapshot) restores the offsets. Samples-exact throughout.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.editing.operations import (
    EditClip,
    append_clip,
    delete_range,
    insert_clip,
    lift_range,
    move_clip,
    ordered,
    set_speed,
    split_clip,
    trim_clip,
)
from laura.editing.otio_sync import (
    accepted_offsets_from_clips,
    build_model,
    serialize_timeline_otio,
)
from laura.main import create_app
from laura.timebase.sampling import frame_to_sample

RATE_NUM, RATE_DEN = 30, 1
SAMPLE_RATE = 48_000  # 1 frame == 1600 samples at 48 kHz / 30 fps

# A J-cut (audio earlier) and L-cut (audio later) in SAMPLES (the canonical unit).
J_SAMPLES = frame_to_sample(-4, SAMPLE_RATE, RATE_NUM, RATE_DEN)  # -6400
L_SAMPLES = frame_to_sample(5, SAMPLE_RATE, RATE_NUM, RATE_DEN)  # 8000


def _clip(
    asset: str,
    src_in: int,
    src_out: int,
    seq_in: int,
    seq_out: int,
    *,
    offset: int = 0,
    speed_num: int = 1,
    speed_den: int = 1,
) -> EditClip:
    return EditClip(
        asset_id=asset,
        src_in_frame=src_in,
        src_out_frame_exclusive=src_out,
        seq_in_frame=seq_in,
        seq_out_frame_exclusive=seq_out,
        speed_num=speed_num,
        speed_den=speed_den,
        audio_offset_samples=offset,
    )


def _three_with_splits() -> list[EditClip]:
    """A,B,C back-to-back; B and C carry a J/L leading offset (A is the sequence head -> 0)."""
    return [
        _clip("a", 0, 10, 0, 10),
        _clip("a", 100, 115, 10, 25, offset=J_SAMPLES),
        _clip("a", 200, 215, 25, 40, offset=L_SAMPLES),
    ]


def _offsets_by_src(clips: list[EditClip]) -> dict[int, int]:
    return {c.src_in_frame: c.audio_offset_samples for c in ordered(clips)}


def _assert_first_is_zero(clips: list[EditClip]) -> None:
    assert ordered(clips)[0].audio_offset_samples == 0


# === EditClip carries the offset through from_row / to_row ======================================


def test_editclip_row_roundtrip_carries_offset() -> None:
    clip = EditClip("a", 1, 2, 3, 4, audio_offset_samples=L_SAMPLES)
    assert EditClip.from_row(clip.to_row()) == clip
    assert clip.to_row()["audio_offset_samples"] == L_SAMPLES
    # A row without the column reads back a hard cut (additive default).
    legacy = {k: v for k, v in clip.to_row().items() if k != "audio_offset_samples"}
    assert EditClip.from_row(legacy).audio_offset_samples == 0


# === trim PRESERVES the offset ==================================================================


def test_trim_preserves_offset_of_trimmed_clip() -> None:
    # Trim B (seq_in=10) tail; its leading J offset is intentional and must survive.
    out = trim_clip(_three_with_splits(), at_seq_frame=10, new_src_in=100, new_src_out=108)
    by_src = _offsets_by_src(out)
    assert by_src[100] == J_SAMPLES  # B keeps its leading J-cut
    assert by_src[200] == L_SAMPLES  # C unaffected
    _assert_first_is_zero(out)


def test_trim_first_clip_keeps_it_zero_and_preserves_others() -> None:
    # Trimming the first clip (head stays the sequence head) keeps it 0 and ripples B/C unchanged.
    out = trim_clip(_three_with_splits(), at_seq_frame=0, new_src_in=0, new_src_out=6)
    assert _offsets_by_src(out) == {0: 0, 100: J_SAMPLES, 200: L_SAMPLES}
    _assert_first_is_zero(out)


# === split: original keeps its offset, new second clip -> 0 (hard cut) ==========================


def test_split_original_keeps_offset_new_clip_is_hard_cut() -> None:
    # Split B (which carries a J offset) at seq 18 -> left keeps J, right is a brand-new hard cut.
    out = ordered(split_clip(_three_with_splits(), at_seq_frame=18))
    assert len(out) == 4
    left_b, right_b = out[1], out[2]
    assert left_b.seq_out_frame_exclusive == 18
    assert left_b.audio_offset_samples == J_SAMPLES  # original keeps its leading offset
    assert right_b.seq_in_frame == 18
    assert right_b.audio_offset_samples == 0  # new internal cut -> hard
    _assert_first_is_zero(out)


def test_split_first_clip_keeps_head_zero() -> None:
    # Splitting the head clip: the left part remains the sequence head (0); the right part is hard.
    out = ordered(split_clip(_three_with_splits(), at_seq_frame=5))
    assert out[0].audio_offset_samples == 0
    assert out[1].seq_in_frame == 5 and out[1].audio_offset_samples == 0
    _assert_first_is_zero(out)


# === move / reorder: the offset TRAVELS with the clip; position 0 resets to 0 ====================


def test_move_offset_travels_with_clip() -> None:
    # Move C (the L-cut clip, seq_in=25) to the front. Its leading cut becomes position 0 -> 0;
    # the clip that newly follows the front keeps its own head offset.
    out = ordered(move_clip(_three_with_splits(), at_seq_frame=25, to_seq_frame=0))
    # New order: C, A, B. C is first now -> its L offset clears to a hard cut.
    assert out[0].src_in_frame == 200 and out[0].audio_offset_samples == 0
    assert out[1].src_in_frame == 0 and out[1].audio_offset_samples == 0  # A, never had one
    assert out[2].src_in_frame == 100 and out[2].audio_offset_samples == J_SAMPLES  # B keeps J
    _assert_first_is_zero(out)


def test_move_non_first_clip_keeps_its_offset() -> None:
    # Move B (J-cut, seq_in=10) to the end: it leaves position 1 but never position 0, so it keeps
    # its leading J offset; C (L-cut) shifts forward but keeps its own L offset.
    out = ordered(move_clip(_three_with_splits(), at_seq_frame=10, to_seq_frame=40))
    by_src = _offsets_by_src(out)
    assert by_src[0] == 0  # A still first
    assert by_src[100] == J_SAMPLES  # B kept its J after the move
    assert by_src[200] == L_SAMPLES  # C kept its L
    _assert_first_is_zero(out)


def test_move_first_clip_later_adopts_hard_head() -> None:
    # A was first (offset 0). Moving it later, the clip that becomes first (B) loses its lead cut.
    out = ordered(move_clip(_three_with_splits(), at_seq_frame=0, to_seq_frame=40))
    # New order: B, C, A.
    assert out[0].src_in_frame == 100 and out[0].audio_offset_samples == 0  # B now first -> hard
    assert out[1].src_in_frame == 200 and out[1].audio_offset_samples == L_SAMPLES  # C keeps L
    assert out[2].src_in_frame == 0 and out[2].audio_offset_samples == 0  # A, still 0
    _assert_first_is_zero(out)


# === delete / lift: re-home offsets, never point at a vanished boundary ==========================


def test_delete_whole_clip_removes_its_offset_and_rehomes() -> None:
    # Delete all of B [10,25): its J offset vanishes with it; C keeps its own L offset.
    out = ordered(delete_range(_three_with_splits(), 10, 25))
    by_src = _offsets_by_src(out)
    assert set(by_src) == {0, 200}  # B gone
    assert by_src[0] == 0
    assert by_src[200] == L_SAMPLES  # C still carries its L head offset
    _assert_first_is_zero(out)


def test_delete_first_clip_makes_next_the_head_and_zeroes_it() -> None:
    # Delete A [0,10): B becomes the sequence head, so its J leading cut clears to a hard cut.
    out = ordered(delete_range(_three_with_splits(), 0, 10))
    by_src = _offsets_by_src(out)
    assert by_src[100] == 0  # B is first now -> hard
    assert by_src[200] == L_SAMPLES  # C keeps its L
    _assert_first_is_zero(out)


def test_delete_slicing_a_clip_keeps_offset_only_on_the_head_piece() -> None:
    # One clip A[0,30) src[0,30) carrying a (hypothetical) leading offset, then a lift in the middle
    # splits it. Only the sub-piece that still starts at the original head keeps the offset.
    clips = [
        _clip("a", 0, 10, 0, 10),  # head clip, offset 0 (it's first)
        _clip("a", 100, 130, 10, 40, offset=L_SAMPLES),  # B carries an L leading offset
    ]
    out = ordered(lift_range(clips, 20, 30))  # lift inside B -> two B pieces
    assert len(out) == 3
    head_piece = next(c for c in out if c.seq_in_frame == 10)
    tail_piece = next(c for c in out if c.seq_in_frame == 30)
    assert head_piece.audio_offset_samples == L_SAMPLES  # keeps B's original leading offset
    assert tail_piece.audio_offset_samples == 0  # begins at a fresh internal cut -> hard
    _assert_first_is_zero(out)


# === insert / append: new clip -> hard cut unless supplied ======================================


def test_insert_clip_is_hard_cut_and_keeps_others() -> None:
    out = ordered(insert_clip(_three_with_splits(), _clip("b", 0, 5, 0, 0), at_seq_frame=10))
    inserted = next(c for c in out if c.asset_id == "b")
    assert inserted.audio_offset_samples == 0  # fresh insert -> hard
    # B and C keep their offsets (their head cuts are unchanged by the ripple).
    by_src = {c.src_in_frame: c.audio_offset_samples for c in out if c.asset_id == "a"}
    assert by_src[100] == J_SAMPLES and by_src[200] == L_SAMPLES
    _assert_first_is_zero(out)


def test_append_clip_is_hard_cut() -> None:
    out = ordered(append_clip(_three_with_splits(), _clip("b", 0, 5, 0, 0)))
    assert out[-1].asset_id == "b"
    assert out[-1].audio_offset_samples == 0
    _assert_first_is_zero(out)


def test_insert_at_zero_zeroes_new_head() -> None:
    # An inserted clip that lands first must have a hard leading cut; the displaced A keeps 0.
    out = ordered(insert_clip(_three_with_splits(), _clip("b", 0, 5, 0, 0), at_seq_frame=0))
    assert out[0].asset_id == "b" and out[0].audio_offset_samples == 0
    _assert_first_is_zero(out)


# === set_speed PRESERVES the head-cut offset (samples), unscaled ================================


def test_set_speed_preserves_offset_unscaled() -> None:
    # Retime B (J-cut, seq_in=10) to 2x. The picture shortens; the head-cut sample offset is a cut
    # LOCATION relationship, not retimed content, so it is preserved exactly (no scaling).
    out = set_speed(_three_with_splits(), at_seq_frame=10, speed_num=2, speed_den=1)
    by_src = _offsets_by_src(out)
    assert by_src[100] == J_SAMPLES  # B keeps its exact sample offset across the retime
    assert by_src[200] == L_SAMPLES  # C unaffected
    _assert_first_is_zero(out)


# === every op leaves the first clip at 0 and the offsets sample-quantized ========================


def test_first_clip_zero_invariant_after_every_op() -> None:
    base = _three_with_splits()
    results = [
        trim_clip(base, 10, 100, 108),
        split_clip(base, 18),
        move_clip(base, 25, 0),
        delete_range(base, 0, 10),
        lift_range(base, 10, 25),
        insert_clip(base, _clip("b", 0, 5, 0, 0), 0),
        append_clip(base, _clip("b", 0, 5, 0, 0)),
        set_speed(base, 10, 2, 1),
    ]
    for res in results:
        _assert_first_is_zero(res)
        # Offsets are only ever copied or zeroed, never scaled, so each is an exact multiple of the
        # per-frame sample count (1600 here) — they stay sample-quantized.
        for c in res:
            assert c.audio_offset_samples % 1600 == 0


# ================================================================================================
# Integration: column-as-live-truth across an edit, proven by an export with the BLOB WIPED.
# ================================================================================================


def _setup(tmp_path: Path) -> tuple[TestClient, SqliteDatabase, dict[str, Any], dict[str, Any]]:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    client = TestClient(create_app(settings))
    client.__enter__()
    project = client.post(
        "/projects",
        json={"name": "p", "sequence_rate_num": RATE_NUM, "sequence_rate_den": RATE_DEN},
    ).json()
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mov", source_path="/a.mov"
    )
    repos.update_asset_probe(
        db, asset["id"], type="video", duration_frames=2000, rate_num=RATE_NUM, rate_den=RATE_DEN,
        audio_sample_rate=SAMPLE_RATE, start_timecode=None, width=1920, height=1080,
        codec_video="h264", codec_audio="aac", is_vfr=False, sha256=None,
    )
    return client, db, project, asset


# Three clips A,B,C; cuts identified by the next clip's src_in_frame.
CUT1, CUT2 = 200, 400
VIDEO_B1, VIDEO_B2 = 50, 120
J_FRAMES, L_FRAMES = -4, 5


def _rough_cut(db: SqliteDatabase, project_id: str, asset_id: str) -> dict[str, Any]:
    tl = repos.create_timeline(db, project_id=project_id, name="rc", kind="rough_cut")
    base = {"asset_id": asset_id, "lane": 0, "speed_num": 1, "speed_den": 1}
    repos.replace_timeline_clips(db, tl["id"], [
        {**base, "src_in_frame": 0, "src_out_frame_exclusive": 50,
         "seq_in_frame": 0, "seq_out_frame_exclusive": 50},
        {**base, "src_in_frame": CUT1, "src_out_frame_exclusive": CUT1 + 70,
         "seq_in_frame": 50, "seq_out_frame_exclusive": 120},
        {**base, "src_in_frame": CUT2, "src_out_frame_exclusive": CUT2 + 60,
         "seq_in_frame": 120, "seq_out_frame_exclusive": 180},
    ])
    fresh = repos.get_timeline(db, tl["id"])
    assert fresh is not None
    repos.update_timeline_otio(db, fresh["id"], serialize_timeline_otio(db, fresh))
    refreshed = repos.get_timeline(db, fresh["id"])
    assert refreshed is not None
    return refreshed


def _accept(
    client: TestClient, project_id: str, timeline_id: str, accepted: list[dict[str, int]]
) -> None:
    client.post(
        f"/projects/{project_id}/timelines/{timeline_id}/split-cuts",
        json={"accepted": accepted},
    ).raise_for_status()


def _audio_offsets_in_fcpxml(content: str) -> dict[int, str]:
    """Map each picture clip's index -> its connected (lane='-1') audio clip's offset string."""
    spine = ET.fromstring(content).find("./library/event/project/sequence/spine")
    assert spine is not None
    out: dict[int, str] = {}
    for i, ac in enumerate(spine.findall("asset-clip")):
        audio = ac.find("asset-clip[@lane='-1']")
        if audio is not None:
            out[i] = audio.attrib["offset"]
    return out


def _time(frames: int) -> str:
    return "0s" if frames == 0 else f"{frames * RATE_DEN}/{RATE_NUM}s"


def test_accept_then_trim_exports_split_from_column_only(tmp_path: Path) -> None:
    """Accept a J/L split, trim clip A (ripples B/C left), WIPE the blob, export from the column.

    The trim of A does not touch B's or C's src_in_frame (the offset keys), only their sequence
    position. The audio must still land on the offset frame, sourced purely from the live column.
    """
    client, db, project, asset = _setup(tmp_path)
    try:
        tl = _rough_cut(db, project["id"], asset["id"])
        _accept(client, project["id"], tl["id"], [
            {"seq_cut": CUT1, "offset": J_FRAMES},
            {"seq_cut": CUT2, "offset": L_FRAMES},
        ])
        # Trim A (seq_in=0) from 50 -> 30 frames; B and C ripple left by 20.
        resp = client.post(
            f"/timelines/{tl['id']}/operations",
            json={"op": "trim", "at_seq_frame": 0,
                  "new_src_in_frame": 0, "new_src_out_frame_exclusive": 30},
        )
        assert resp.status_code == 200, resp.text
        # The column survived the trim (B/C keep their sample offsets, keyed by their src_in_frame).
        by_src = {c["src_in_frame"]: c["audio_offset_samples"]
                  for c in repos.list_timeline_clips(db, tl["id"])}
        assert by_src[CUT1] == frame_to_sample(J_FRAMES, SAMPLE_RATE, RATE_NUM, RATE_DEN)
        assert by_src[CUT2] == frame_to_sample(L_FRAMES, SAMPLE_RATE, RATE_NUM, RATE_DEN)
        assert by_src[0] == 0

        # WIPE the OTIO blob: only the column can possibly carry the split now.
        repos.update_timeline_otio(db, tl["id"], "{}")
        row = repos.get_timeline(db, tl["id"])
        assert row is not None
        derived = {s.seq_cut: s.offset for s in accepted_offsets_from_clips(build_model(db, row),
                                                                            SAMPLE_RATE)}
        assert derived == {CUT1: J_FRAMES, CUT2: L_FRAMES}

        # Export reads the split FROM THE COLUMN ALONE and lands the audio on the shifted frame.
        exp = client.post(f"/timelines/{tl['id']}/exports", json={"format": "fcpxml"})
        assert exp.status_code == 201, exp.text
        audio = _audio_offsets_in_fcpxml(exp.json()["content"])
        # After the 20-frame ripple: B at seq 30, C at seq 100.
        new_b, new_c = VIDEO_B1 - 20, VIDEO_B2 - 20  # 30, 100
        assert audio[1] == _time(new_b + J_FRAMES)  # 26
        assert audio[2] == _time(new_c + L_FRAMES)  # 105
    finally:
        client.__exit__(None, None, None)


def test_accept_then_move_exports_split_from_column_only(tmp_path: Path) -> None:
    """Accept a J/L split, move clip C to the front, WIPE the blob, export from the column.

    Moving C to position 0 makes it the sequence head, so its L leading offset clears to a hard cut
    (the offset travelled with the clip, then first-clip-0 fired). B keeps its J offset.
    """
    client, db, project, asset = _setup(tmp_path)
    try:
        tl = _rough_cut(db, project["id"], asset["id"])
        _accept(client, project["id"], tl["id"], [
            {"seq_cut": CUT1, "offset": J_FRAMES},
            {"seq_cut": CUT2, "offset": L_FRAMES},
        ])
        # Move C (seq_in=120) to the front. New order C, A, B; lengths 60, 50, 70.
        resp = client.post(
            f"/timelines/{tl['id']}/operations",
            json={"op": "move", "at_seq_frame": 120, "to_seq_frame": 0},
        )
        assert resp.status_code == 200, resp.text
        by_src = {c["src_in_frame"]: c["audio_offset_samples"]
                  for c in repos.list_timeline_clips(db, tl["id"])}
        assert by_src[CUT2] == 0  # C is first now -> hard cut
        j_samples = frame_to_sample(J_FRAMES, SAMPLE_RATE, RATE_NUM, RATE_DEN)
        assert by_src[CUT1] == j_samples  # B keeps its J offset
        assert by_src[0] == 0

        repos.update_timeline_otio(db, tl["id"], "{}")
        row = repos.get_timeline(db, tl["id"])
        assert row is not None
        derived = {s.seq_cut: s.offset for s in accepted_offsets_from_clips(build_model(db, row),
                                                                            SAMPLE_RATE)}
        assert derived == {CUT1: J_FRAMES}  # only B's J split remains after C moved to the head

        exp = client.post(f"/timelines/{tl['id']}/exports", json={"format": "fcpxml"})
        assert exp.status_code == 201, exp.text
        audio = _audio_offsets_in_fcpxml(exp.json()["content"])
        # New order C[0,60), A[60,110), B[110,180). The split-aware writer emits a connected audio
        # clip per picture clip; a HARD cut places it coincident with its picture, a split shifts.
        assert audio[0] == _time(0)  # C: head, hard cut -> audio coincident with picture
        assert audio[1] == _time(60)  # A: hard cut -> audio coincident
        assert audio[2] == _time(110 + J_FRAMES)  # B: J-shifted audio -> 106
    finally:
        client.__exit__(None, None, None)


def test_undo_redo_restores_offsets_via_set_clips(tmp_path: Path) -> None:
    """A GET-clips snapshot round-trips the offset through PUT-clips (ClipOut/ClipIn): undo/redo."""
    client, db, project, asset = _setup(tmp_path)
    try:
        tl = _rough_cut(db, project["id"], asset["id"])
        _accept(client, project["id"], tl["id"], [
            {"seq_cut": CUT1, "offset": J_FRAMES},
            {"seq_cut": CUT2, "offset": L_FRAMES},
        ])
        # Snapshot the post-accept clips (this is what the UI keeps for undo).
        snapshot = client.get(f"/timelines/{tl['id']}").json()["clips"]
        j_samples = frame_to_sample(J_FRAMES, SAMPLE_RATE, RATE_NUM, RATE_DEN)
        l_samples = frame_to_sample(L_FRAMES, SAMPLE_RATE, RATE_NUM, RATE_DEN)
        snap_by_src = {c["src_in_frame"]: c["audio_offset_samples"] for c in snapshot}
        assert snap_by_src == {0: 0, CUT1: j_samples, CUT2: l_samples}

        # Destroy the split (clear all offsets), then restore the snapshot wholesale.
        _accept(client, project["id"], tl["id"], [])
        assert all(c["audio_offset_samples"] == 0
                   for c in repos.list_timeline_clips(db, tl["id"]))
        restored = client.put(f"/timelines/{tl['id']}/clips", json={"clips": snapshot})
        assert restored.status_code == 200, restored.text

        # The offsets are back on the column as the live truth.
        by_src = {c["src_in_frame"]: c["audio_offset_samples"]
                  for c in repos.list_timeline_clips(db, tl["id"])}
        assert by_src == {0: 0, CUT1: j_samples, CUT2: l_samples}
        # And a column-only export (blob wiped) still carries the split.
        repos.update_timeline_otio(db, tl["id"], "{}")
        row = repos.get_timeline(db, tl["id"])
        assert row is not None
        derived = {s.seq_cut: s.offset for s in accepted_offsets_from_clips(build_model(db, row),
                                                                            SAMPLE_RATE)}
        assert derived == {CUT1: J_FRAMES, CUT2: L_FRAMES}
    finally:
        client.__exit__(None, None, None)
