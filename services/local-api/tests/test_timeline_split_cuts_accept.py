"""API test: accept recommended L/J split edits (the „Übernehmen" endpoint, 3c).

The planner RECOMMENDS per-cut L/J splits (surfaced as ``FromShotsOut.split_cuts``); this endpoint
lets the user ACCEPT them so they actually get stored — migration-free, in the OTIO blob metadata
only (3a's :func:`serialize_timeline_otio` -> :func:`repos.update_timeline_otio`). No clip mutation:
the internal timeline stays a hard cut; the L/J lives purely in the serialised OTIO + NLE exports.

These pin the endpoint contract:

* POST accept -> the offsets are read back by :func:`accepted_offsets_from_otio` from the blob;
* an ``export_timeline`` (FCPXML) then carries the audio on the OFFSET frame (picture frame-exact);
* re-accepting the same set is idempotent (no-op);
* a sub-perception (``|offset| <= 1``) offset is cleared to a hard cut;
* the clips table is never mutated by accept;
* bad project / timeline / cross-project -> 404 / 422.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from laura.api.otio_split import accepted_offsets_from_otio
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app

RATE_NUM, RATE_DEN = 30, 1
SAMPLE_RATE = 48_000

# Inter-clip cuts identified by the next clip's src_in_frame (== planner SplitCut.seq_cut).
CUT1 = 200  # A|B boundary, video at sequence frame 50
CUT2 = 400  # B|C boundary, video at sequence frame 120
VIDEO_B1 = 50
VIDEO_B2 = 120
J_OFFSET = -4  # J-cut on cut1: audio EARLIER
L_OFFSET = 5  # L-cut on cut2: audio LATER


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
    # An audio sample rate so the accepted split projects to samples (invariant #3).
    repos.update_asset_probe(
        db, asset["id"], type="video", duration_frames=500, rate_num=RATE_NUM, rate_den=RATE_DEN,
        audio_sample_rate=SAMPLE_RATE, start_timecode=None, width=1920, height=1080,
        codec_video="h264", codec_audio="aac", is_vfr=False, sha256=None,
    )
    return client, db, project, asset


def _rough_cut_timeline(db: SqliteDatabase, project_id: str, asset_id: str) -> dict[str, Any]:
    """Three clips A,B,C packed back-to-back (same geometry as test_export_split)."""
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
    # Seed the stored OTIO cache from the clips (what the build path does).
    repos.update_timeline_otio(db, fresh["id"], serialize_blob(db, fresh))
    refreshed = repos.get_timeline(db, fresh["id"])
    assert refreshed is not None
    return refreshed


def serialize_blob(db: SqliteDatabase, row: dict[str, Any]) -> str:
    from laura.editing.otio_sync import serialize_timeline_otio

    return serialize_timeline_otio(db, row)


def _accept(
    client: TestClient, project_id: str, timeline_id: str, accepted: list[dict[str, int]]
) -> httpx.Response:
    resp = client.post(
        f"/projects/{project_id}/timelines/{timeline_id}/split-cuts",
        json={"accepted": accepted},
    )
    assert isinstance(resp, httpx.Response)
    return resp


def test_accept_persists_offsets_in_otio_blob(tmp_path: Path) -> None:
    client, db, project, asset = _setup(tmp_path)
    try:
        tl = _rough_cut_timeline(db, project["id"], asset["id"])
        resp = _accept(client, project["id"], tl["id"], [
            {"seq_cut": CUT1, "offset": J_OFFSET},
            {"seq_cut": CUT2, "offset": L_OFFSET},
        ])
        assert resp.status_code == 200, resp.text
        # The response confirms the stored set read back from the blob.
        got = {a["seq_cut"]: a["offset"] for a in resp.json()["accepted"]}
        assert got == {CUT1: J_OFFSET, CUT2: L_OFFSET}
        # And it really lives in the stored OTIO blob metadata (migration-free persistence).
        stored = repos.get_timeline(db, tl["id"])
        assert stored is not None
        offsets = {s.seq_cut: s.offset for s in accepted_offsets_from_otio(stored["otio_json"])}
        assert offsets == {CUT1: J_OFFSET, CUT2: L_OFFSET}
        # No clip mutation — the internal timeline stays a hard cut.
        clips = repos.list_timeline_clips(db, tl["id"])
        assert [(c["seq_in_frame"], c["seq_out_frame_exclusive"]) for c in clips] == [
            (0, 50), (50, 120), (120, 180),
        ]
    finally:
        client.__exit__(None, None, None)


def test_accepted_split_carried_into_fcpxml_export(tmp_path: Path) -> None:
    """After accept, an FCPXML export carries the audio on the OFFSET frame; picture frame-exact."""
    client, db, project, asset = _setup(tmp_path)
    try:
        tl = _rough_cut_timeline(db, project["id"], asset["id"])
        _accept(client, project["id"], tl["id"], [
            {"seq_cut": CUT1, "offset": J_OFFSET},
            {"seq_cut": CUT2, "offset": L_OFFSET},
        ]).raise_for_status()
        exp = client.post(f"/timelines/{tl['id']}/exports", json={"format": "fcpxml"})
        assert exp.status_code == 201, exp.text
        root = ET.fromstring(exp.json()["content"])
        spine = root.find("./library/event/project/sequence/spine")
        assert spine is not None
        picture = spine.findall("asset-clip")
        assert len(picture) == 3

        def _time(frames: int) -> str:
            return "0s" if frames == 0 else f"{frames * RATE_DEN}/{RATE_NUM}s"

        # Picture stays frame-exact on the visual cut.
        assert picture[1].attrib["offset"] == _time(VIDEO_B1)
        assert picture[2].attrib["offset"] == _time(VIDEO_B2)
        # Connected audio on lane -1 lands on the OFFSET frames (J: 46, L: 125).
        b_audio = picture[1].find("asset-clip[@lane='-1']")
        c_audio = picture[2].find("asset-clip[@lane='-1']")
        assert b_audio is not None and c_audio is not None
        assert b_audio.attrib["offset"] == _time(VIDEO_B1 + J_OFFSET)  # 46
        assert c_audio.attrib["offset"] == _time(VIDEO_B2 + L_OFFSET)  # 125
    finally:
        client.__exit__(None, None, None)


def test_accept_is_idempotent(tmp_path: Path) -> None:
    client, db, project, asset = _setup(tmp_path)
    try:
        tl = _rough_cut_timeline(db, project["id"], asset["id"])
        payload = [{"seq_cut": CUT1, "offset": J_OFFSET}, {"seq_cut": CUT2, "offset": L_OFFSET}]
        first = _accept(client, project["id"], tl["id"], payload)
        first.raise_for_status()
        blob_after_first = repos.get_timeline(db, tl["id"])["otio_json"]  # type: ignore[index]
        second = _accept(client, project["id"], tl["id"], payload)
        second.raise_for_status()
        # Re-accepting the same set is a no-op: identical stored blob + identical response.
        assert repos.get_timeline(db, tl["id"])["otio_json"] == blob_after_first  # type: ignore[index]
        assert second.json() == first.json()
    finally:
        client.__exit__(None, None, None)


def test_taking_back_a_split_returns_it_to_hard_cut(tmp_path: Path) -> None:
    client, db, project, asset = _setup(tmp_path)
    try:
        tl = _rough_cut_timeline(db, project["id"], asset["id"])
        _accept(client, project["id"], tl["id"], [
            {"seq_cut": CUT1, "offset": J_OFFSET},
            {"seq_cut": CUT2, "offset": L_OFFSET},
        ]).raise_for_status()
        # „Zurücknehmen" CUT1: re-post without it -> only CUT2 remains.
        resp = _accept(client, project["id"], tl["id"], [{"seq_cut": CUT2, "offset": L_OFFSET}])
        resp.raise_for_status()
        got = {a["seq_cut"]: a["offset"] for a in resp.json()["accepted"]}
        assert got == {CUT2: L_OFFSET}
        # Posting an empty list clears everything back to hard cuts.
        cleared = _accept(client, project["id"], tl["id"], [])
        cleared.raise_for_status()
        assert cleared.json()["accepted"] == []
        stored = repos.get_timeline(db, tl["id"])
        assert stored is not None
        assert accepted_offsets_from_otio(stored["otio_json"]) == []
    finally:
        client.__exit__(None, None, None)


def test_hard_offset_is_cleared_on_accept(tmp_path: Path) -> None:
    """A sub-perception offset (|offset| <= 1) shifts nothing and is dropped from the stored set."""
    client, db, project, asset = _setup(tmp_path)
    try:
        tl = _rough_cut_timeline(db, project["id"], asset["id"])
        resp = _accept(client, project["id"], tl["id"], [
            {"seq_cut": CUT1, "offset": 1},          # hard -> cleared
            {"seq_cut": CUT2, "offset": L_OFFSET},   # meaningful -> kept
        ])
        resp.raise_for_status()
        got = {a["seq_cut"]: a["offset"] for a in resp.json()["accepted"]}
        assert got == {CUT2: L_OFFSET}
    finally:
        client.__exit__(None, None, None)


def test_accept_bad_ids(tmp_path: Path) -> None:
    client, db, project, asset = _setup(tmp_path)
    try:
        tl = _rough_cut_timeline(db, project["id"], asset["id"])
        # Unknown project -> 404.
        assert _accept(client, "nope", tl["id"], []).status_code == 404
        # Unknown timeline -> 404.
        assert _accept(client, project["id"], "nope", []).status_code == 404
        # Timeline of another project -> 422.
        other = client.post(
            "/projects",
            json={"name": "other", "sequence_rate_num": RATE_NUM, "sequence_rate_den": RATE_DEN},
        ).json()
        assert _accept(client, other["id"], tl["id"], []).status_code == 422
        # An out-of-range offset is rejected by the request model -> 422.
        assert _accept(
            client, project["id"], tl["id"], [{"seq_cut": CUT1, "offset": 10_000_000}]
        ).status_code == 422
    finally:
        client.__exit__(None, None, None)
