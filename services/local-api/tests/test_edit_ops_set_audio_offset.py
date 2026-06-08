"""The ``set_audio_offset`` editing op — the 2-lane manual L/J drag (m3).

m1 added the ``timeline_clips.audio_offset_samples`` column (signed per-clip LEADING-edge
audio-vs-video offset in SAMPLES, invariant #3; 0 = hard cut); m2 made every editing op preserve it.
m3 adds the op that SETS it from a UI gesture, composing with ``apply_operation`` so it shares the
trim / move / split undo-redo snapshot path. Pinned here:

* the pure op writes the column (samples) on the clip at ``at_seq_frame``;
* the sequence-first clip is non-draggable: setting it is a no-op (stays 0, first-clip-0 invariant);
* a sub-perception drag (``|offset| < 1 frame``) is hard-clamped to 0;
* a missing target clip raises (surfaced as 422 by the endpoint);
* through the HTTP endpoint the frame delta is projected to samples via the project rate (mirrors
  the accept endpoint), undo restores the prior offset, redo re-applies, and a column-only export
  (blob wiped) carries the offset.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.editing.operations import EditClip, ordered, set_audio_offset
from laura.editing.otio_sync import (
    accepted_offsets_from_clips,
    build_model,
    serialize_timeline_otio,
)
from laura.main import create_app
from laura.timebase.sampling import frame_to_sample

RATE_NUM, RATE_DEN = 30, 1
SAMPLE_RATE = 48_000  # 1 frame == 1600 samples at 48 kHz / 30 fps
SPF = frame_to_sample(1, SAMPLE_RATE, RATE_NUM, RATE_DEN)  # samples per frame == 1600

J_SAMPLES = frame_to_sample(-4, SAMPLE_RATE, RATE_NUM, RATE_DEN)  # -6400
L_SAMPLES = frame_to_sample(5, SAMPLE_RATE, RATE_NUM, RATE_DEN)  # 8000


def _clip(asset: str, src_in: int, src_out: int, seq_in: int, seq_out: int) -> EditClip:
    return EditClip(
        asset_id=asset,
        src_in_frame=src_in,
        src_out_frame_exclusive=src_out,
        seq_in_frame=seq_in,
        seq_out_frame_exclusive=seq_out,
    )


def _three() -> list[EditClip]:
    """A,B,C back-to-back, all hard cuts to start (A is the sequence head)."""
    return [
        _clip("a", 0, 10, 0, 10),
        _clip("a", 100, 115, 10, 25),
        _clip("a", 200, 215, 25, 40),
    ]


def _by_src(clips: list[EditClip]) -> dict[int, int]:
    return {c.src_in_frame: c.audio_offset_samples for c in ordered(clips)}


# === pure op =====================================================================================


def test_set_audio_offset_writes_the_column_in_samples() -> None:
    out = set_audio_offset(_three(), at_seq_frame=10, audio_offset_samples=J_SAMPLES,
                           samples_per_frame=SPF)
    by_src = _by_src(out)
    assert by_src[100] == J_SAMPLES  # B's head now carries the J offset (samples-exact)
    assert by_src[0] == 0  # A untouched
    assert by_src[200] == 0  # C untouched


def test_set_audio_offset_on_first_clip_is_a_no_op() -> None:
    # The sequence head has no predecessor; first-clip-0 must hold even if a value is passed.
    out = set_audio_offset(_three(), at_seq_frame=0, audio_offset_samples=L_SAMPLES,
                           samples_per_frame=SPF)
    assert _by_src(out) == {0: 0, 100: 0, 200: 0}


def test_set_audio_offset_sub_perception_clamps_to_hard_cut() -> None:
    # |offset| < 1 frame (== SPF samples) is a hard cut.
    out = set_audio_offset(_three(), at_seq_frame=10, audio_offset_samples=SPF - 1,
                           samples_per_frame=SPF)
    assert _by_src(out)[100] == 0
    # Exactly one frame is meaningful and kept.
    out2 = set_audio_offset(_three(), at_seq_frame=10, audio_offset_samples=SPF,
                            samples_per_frame=SPF)
    assert _by_src(out2)[100] == SPF


def test_set_audio_offset_overwrites_an_existing_offset() -> None:
    # A drag onto a clip that already carries an offset replaces it (last write wins) — this is how
    # a manual drag reconciles with an accepted recommendation on the one column.
    base = set_audio_offset(_three(), at_seq_frame=10, audio_offset_samples=J_SAMPLES,
                            samples_per_frame=SPF)
    out = set_audio_offset(base, at_seq_frame=10, audio_offset_samples=L_SAMPLES,
                           samples_per_frame=SPF)
    assert _by_src(out)[100] == L_SAMPLES


def test_set_audio_offset_missing_clip_raises() -> None:
    try:
        set_audio_offset(_three(), at_seq_frame=7, audio_offset_samples=J_SAMPLES,
                         samples_per_frame=SPF)
    except ValueError:
        return
    raise AssertionError("expected ValueError for a non-existent target clip")


# === HTTP endpoint: compose with apply_operation + undo/redo + export ============================


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


def _offsets(db: SqliteDatabase, timeline_id: str) -> dict[int, int]:
    return {c["src_in_frame"]: c["audio_offset_samples"]
            for c in repos.list_timeline_clips(db, timeline_id)}


def _time(frames: int) -> str:
    return "0s" if frames == 0 else f"{frames * RATE_DEN}/{RATE_NUM}s"


def test_endpoint_set_audio_offset_projects_frames_to_samples(tmp_path: Path) -> None:
    client, db, project, asset = _setup(tmp_path)
    try:
        tl = _rough_cut(db, project["id"], asset["id"])
        # Drag B's audio (seq_in 50) to a J-cut of -4 frames.
        resp = client.post(
            f"/timelines/{tl['id']}/operations",
            json={"op": "set_audio_offset", "at_seq_frame": 50, "audio_offset_frames": J_FRAMES},
        )
        assert resp.status_code == 200, resp.text
        assert _offsets(db, tl["id"])[CUT1] == frame_to_sample(
            J_FRAMES, SAMPLE_RATE, RATE_NUM, RATE_DEN
        )
        # The response carries the offset on the clip too (round-trips for the UI).
        clips = {c["src_in_frame"]: c["audio_offset_samples"] for c in resp.json()["clips"]}
        assert clips[CUT1] == frame_to_sample(J_FRAMES, SAMPLE_RATE, RATE_NUM, RATE_DEN)
    finally:
        client.__exit__(None, None, None)


def test_endpoint_set_audio_offset_first_clip_stays_hard(tmp_path: Path) -> None:
    client, db, project, asset = _setup(tmp_path)
    try:
        tl = _rough_cut(db, project["id"], asset["id"])
        resp = client.post(
            f"/timelines/{tl['id']}/operations",
            json={"op": "set_audio_offset", "at_seq_frame": 0, "audio_offset_frames": L_FRAMES},
        )
        assert resp.status_code == 200, resp.text
        assert _offsets(db, tl["id"])[0] == 0  # head clip never carries a leading cut
    finally:
        client.__exit__(None, None, None)


def test_endpoint_set_audio_offset_unknown_clip_is_422(tmp_path: Path) -> None:
    client, db, project, asset = _setup(tmp_path)
    try:
        tl = _rough_cut(db, project["id"], asset["id"])
        resp = client.post(
            f"/timelines/{tl['id']}/operations",
            json={"op": "set_audio_offset", "at_seq_frame": 51, "audio_offset_frames": L_FRAMES},
        )
        assert resp.status_code == 422, resp.text
    finally:
        client.__exit__(None, None, None)


def test_endpoint_set_audio_offset_undo_redo_round_trips(tmp_path: Path) -> None:
    """Drag sets the offset; the undo snapshot (GET clips -> PUT clips) restores the prior value;
    re-applying the op re-sets it. This is the exact undo/redo path the UI drives."""
    client, db, project, asset = _setup(tmp_path)
    try:
        tl = _rough_cut(db, project["id"], asset["id"])
        # Put B at a J-cut, then snapshot it (the UI's pre-edit history entry for the NEXT op).
        client.post(
            f"/timelines/{tl['id']}/operations",
            json={"op": "set_audio_offset", "at_seq_frame": 50, "audio_offset_frames": J_FRAMES},
        ).raise_for_status()
        prior = client.get(f"/timelines/{tl['id']}").json()["clips"]
        j_samples = frame_to_sample(J_FRAMES, SAMPLE_RATE, RATE_NUM, RATE_DEN)
        assert _offsets(db, tl["id"])[CUT1] == j_samples

        # Drag B to an L-cut (the op the user would undo).
        client.post(
            f"/timelines/{tl['id']}/operations",
            json={"op": "set_audio_offset", "at_seq_frame": 50, "audio_offset_frames": L_FRAMES},
        ).raise_for_status()
        l_samples = frame_to_sample(L_FRAMES, SAMPLE_RATE, RATE_NUM, RATE_DEN)
        assert _offsets(db, tl["id"])[CUT1] == l_samples

        # UNDO: restore the pre-drag snapshot wholesale -> back to the J-cut.
        client.put(f"/timelines/{tl['id']}/clips", json={"clips": prior}).raise_for_status()
        assert _offsets(db, tl["id"])[CUT1] == j_samples

        # REDO: re-apply the op -> L-cut again.
        client.post(
            f"/timelines/{tl['id']}/operations",
            json={"op": "set_audio_offset", "at_seq_frame": 50, "audio_offset_frames": L_FRAMES},
        ).raise_for_status()
        assert _offsets(db, tl["id"])[CUT1] == l_samples
    finally:
        client.__exit__(None, None, None)


def test_endpoint_set_audio_offset_exports_from_column(tmp_path: Path) -> None:
    """A manually-dragged offset (no accept call) exports the audio on the shifted frame, sourced
    from the column alone (blob wiped) — proving the drag and accept paths share one column."""
    client, db, project, asset = _setup(tmp_path)
    try:
        tl = _rough_cut(db, project["id"], asset["id"])
        client.post(
            f"/timelines/{tl['id']}/operations",
            json={"op": "set_audio_offset", "at_seq_frame": 50, "audio_offset_frames": J_FRAMES},
        ).raise_for_status()
        client.post(
            f"/timelines/{tl['id']}/operations",
            json={"op": "set_audio_offset", "at_seq_frame": 120, "audio_offset_frames": L_FRAMES},
        ).raise_for_status()

        # Wipe the blob: only the column can carry the split now.
        repos.update_timeline_otio(db, tl["id"], "{}")
        row = repos.get_timeline(db, tl["id"])
        assert row is not None
        derived = {s.seq_cut: s.offset
                   for s in accepted_offsets_from_clips(build_model(db, row), SAMPLE_RATE)}
        assert derived == {CUT1: J_FRAMES, CUT2: L_FRAMES}

        exp = client.post(f"/timelines/{tl['id']}/exports", json={"format": "fcpxml"})
        assert exp.status_code == 201, exp.text
        spine = ET.fromstring(exp.json()["content"]).find(
            "./library/event/project/sequence/spine"
        )
        assert spine is not None
        picture = spine.findall("asset-clip")
        b_audio = picture[1].find("asset-clip[@lane='-1']")
        c_audio = picture[2].find("asset-clip[@lane='-1']")
        assert b_audio is not None and c_audio is not None
        assert b_audio.attrib["offset"] == _time(VIDEO_B1 + J_FRAMES)  # 46
        assert c_audio.attrib["offset"] == _time(VIDEO_B2 + L_FRAMES)  # 125
    finally:
        client.__exit__(None, None, None)
