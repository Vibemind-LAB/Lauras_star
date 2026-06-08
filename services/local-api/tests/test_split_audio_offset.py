"""Persist the L/J audio offset on clips — 2-lane foundation (editorial m1).

m1 promotes the L/J split from migration-free OTIO-blob metadata (3a) to a REAL, editable per-clip
column ``timeline_clips.audio_offset_samples`` (canonical in SAMPLES, invariant #3). Tests pin:

* the migration adds the column with default ``0`` (every existing clip becomes a hard cut);
* a clip round-trips a non-zero offset through write -> read;
* the accept endpoint now WRITES the column (frame offset -> samples) instead of OTIO metadata, and
  the split survives a regenerate-from-clips with NO metadata, exporting on the offset frame
  (samples-exact);
* the samples<->frames projection is exact;
* a legacy metadata-only timeline (column all-zero) still exports via the fallback (nothing lost);
* a no-offset timeline is byte-for-byte the single-track OTIO/EDL Laura builds today.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.api.otio_split import AcceptedSplit, accepted_offsets_from_otio, apply_split_cuts
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.editing.otio_sync import (
    accepted_offsets_from_clips,
    build_model,
    export_audio_clips,
    serialize_timeline_otio,
)
from laura.interchange.otio_io import timeline_to_otio_string
from laura.main import create_app
from laura.timebase.sampling import frame_to_sample, sample_to_frame

RATE_NUM, RATE_DEN = 30, 1
SAMPLE_RATE = 48_000  # 1 frame == 1600 samples at 48 kHz / 30 fps

# Three clips A,B,C packed back-to-back (same geometry as test_export_split / accept).
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
    repos.update_asset_probe(
        db, asset["id"], type="video", duration_frames=500, rate_num=RATE_NUM, rate_den=RATE_DEN,
        audio_sample_rate=SAMPLE_RATE, start_timecode=None, width=1920, height=1080,
        codec_video="h264", codec_audio="aac", is_vfr=False, sha256=None,
    )
    return client, db, project, asset


def _rough_cut_timeline(db: SqliteDatabase, project_id: str, asset_id: str) -> dict[str, Any]:
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
) -> dict[str, Any]:
    resp = client.post(
        f"/projects/{project_id}/timelines/{timeline_id}/split-cuts",
        json={"accepted": accepted},
    )
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    return data


# === migration: the column exists and defaults to 0 =============================================


def test_migration_adds_audio_offset_column_default_zero(tmp_path: Path) -> None:
    db = SqliteDatabase(Settings(workspace_root=tmp_path, start_runner=False).db_path)
    db.migrate()
    with db.connection() as conn:
        info = {
            r["name"]: r
            for r in conn.execute("PRAGMA table_info(timeline_clips)").fetchall()
        }
        versions = {
            r["version"] for r in conn.execute("SELECT version FROM schema_meta").fetchall()
        }
    assert "audio_offset_samples" in info
    col = info["audio_offset_samples"]
    assert col["notnull"] == 1
    assert int(col["dflt_value"]) == 0
    # The migration is recorded with the next free version number (0012).
    assert 12 in versions


def test_existing_clip_defaults_to_hard_cut(tmp_path: Path) -> None:
    """A clip written without an explicit offset reads back 0 (a hard cut) — additive default."""
    client, db, project, asset = _setup(tmp_path)
    try:
        tl = _rough_cut_timeline(db, project["id"], asset["id"])
        clips = repos.list_timeline_clips(db, tl["id"])
        assert [c["audio_offset_samples"] for c in clips] == [0, 0, 0]
    finally:
        client.__exit__(None, None, None)


# === write/read round-trip of a non-zero offset =================================================


def test_offset_round_trips_through_write_read(tmp_path: Path) -> None:
    client, db, project, asset = _setup(tmp_path)
    try:
        tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
        samples = frame_to_sample(L_OFFSET, SAMPLE_RATE, RATE_NUM, RATE_DEN)
        repos.replace_timeline_clips(db, tl["id"], [
            {"asset_id": asset["id"], "src_in_frame": 0, "src_out_frame_exclusive": 50,
             "seq_in_frame": 0, "seq_out_frame_exclusive": 50, "audio_offset_samples": 0},
            {"asset_id": asset["id"], "src_in_frame": CUT2, "src_out_frame_exclusive": CUT2 + 60,
             "seq_in_frame": 50, "seq_out_frame_exclusive": 110,
             "audio_offset_samples": samples},
        ])
        clips = repos.list_timeline_clips(db, tl["id"])
        assert clips[0]["audio_offset_samples"] == 0
        assert clips[1]["audio_offset_samples"] == samples
        # add_timeline_clip carries the kwarg too.
        repos.add_timeline_clip(
            db, timeline_id=tl["id"], asset_id=asset["id"], src_in_frame=600,
            src_out_frame_exclusive=650, seq_in_frame=110, seq_out_frame_exclusive=160,
            audio_offset_samples=samples,
        )
        added = repos.list_timeline_clips(db, tl["id"])[-1]
        assert added["audio_offset_samples"] == samples
    finally:
        client.__exit__(None, None, None)


# === samples <-> frames projection is exact (invariant #3) ======================================


def test_samples_frames_projection_is_exact() -> None:
    for offset in (J_OFFSET, L_OFFSET, -12, 17):
        samples = frame_to_sample(offset, SAMPLE_RATE, RATE_NUM, RATE_DEN)
        assert samples == offset * 1600  # 48000/30
        assert sample_to_frame(samples, SAMPLE_RATE, RATE_NUM, RATE_DEN) == offset


# === accept writes the COLUMN (not metadata) and survives a regenerate ==========================


def test_accept_persists_offset_on_clip_column(tmp_path: Path) -> None:
    client, db, project, asset = _setup(tmp_path)
    try:
        tl = _rough_cut_timeline(db, project["id"], asset["id"])
        out = _accept(client, project["id"], tl["id"], [
            {"seq_cut": CUT1, "offset": J_OFFSET},
            {"seq_cut": CUT2, "offset": L_OFFSET},
        ])
        assert {a["seq_cut"]: a["offset"] for a in out["accepted"]} == {
            CUT1: J_OFFSET, CUT2: L_OFFSET,
        }
        # The offset now lives on the clips, keyed by the cut's src_in_frame — samples-canonical.
        by_src = {c["src_in_frame"]: c["audio_offset_samples"] for c in
                  repos.list_timeline_clips(db, tl["id"])}
        assert by_src[CUT1] == frame_to_sample(J_OFFSET, SAMPLE_RATE, RATE_NUM, RATE_DEN)
        assert by_src[CUT2] == frame_to_sample(L_OFFSET, SAMPLE_RATE, RATE_NUM, RATE_DEN)
        assert by_src[0] == 0  # first clip has no leading cut
    finally:
        client.__exit__(None, None, None)


def test_split_survives_regenerate_from_clips_without_metadata(tmp_path: Path) -> None:
    """After accept, blow away the OTIO blob and regenerate purely from clips — split persists."""
    client, db, project, asset = _setup(tmp_path)
    try:
        tl = _rough_cut_timeline(db, project["id"], asset["id"])
        _accept(client, project["id"], tl["id"], [
            {"seq_cut": CUT1, "offset": J_OFFSET},
            {"seq_cut": CUT2, "offset": L_OFFSET},
        ])
        # Wipe the metadata cache: only the COLUMN remains as the source of truth.
        repos.update_timeline_otio(db, tl["id"], "{}")
        row = repos.get_timeline(db, tl["id"])
        assert row is not None
        assert accepted_offsets_from_otio(row["otio_json"]) == []  # no metadata fallback available

        model = build_model(db, row)
        derived = {s.seq_cut: s.offset for s in accepted_offsets_from_clips(model, SAMPLE_RATE)}
        assert derived == {CUT1: J_OFFSET, CUT2: L_OFFSET}

        # Regenerated OTIO places the audio on the offset boundary (samples-exact).
        blob = serialize_timeline_otio(db, row)
        offsets = {s.seq_cut: s.offset for s in accepted_offsets_from_otio(blob)}
        assert offsets == {CUT1: J_OFFSET, CUT2: L_OFFSET}

        # FCPXML export carries the audio on the offset frame, picture frame-exact.
        repos.update_timeline_otio(db, tl["id"], blob)
        exp = client.post(f"/timelines/{tl['id']}/exports", json={"format": "fcpxml"})
        assert exp.status_code == 201, exp.text
        spine = ET.fromstring(exp.json()["content"]).find(
            "./library/event/project/sequence/spine"
        )
        assert spine is not None
        picture = spine.findall("asset-clip")

        def _time(frames: int) -> str:
            return "0s" if frames == 0 else f"{frames * RATE_DEN}/{RATE_NUM}s"

        assert picture[1].attrib["offset"] == _time(VIDEO_B1)  # picture untouched
        assert picture[2].attrib["offset"] == _time(VIDEO_B2)
        b_audio = picture[1].find("asset-clip[@lane='-1']")
        c_audio = picture[2].find("asset-clip[@lane='-1']")
        assert b_audio is not None and c_audio is not None
        assert b_audio.attrib["offset"] == _time(VIDEO_B1 + J_OFFSET)  # 46
        assert c_audio.attrib["offset"] == _time(VIDEO_B2 + L_OFFSET)  # 125
    finally:
        client.__exit__(None, None, None)


# === legacy metadata-only timeline still exports via the fallback ===============================


def test_legacy_metadata_only_timeline_exports_via_fallback(tmp_path: Path) -> None:
    """A pre-m1 timeline whose split lives ONLY in the OTIO blob (column all-zero) still exports."""
    client, db, project, asset = _setup(tmp_path)
    try:
        tl = _rough_cut_timeline(db, project["id"], asset["id"])
        # Simulate the legacy state: column stays 0, the split lives in the blob metadata only
        # (exactly what 3a's apply_split_cuts produced before m1).
        model = build_model(db, tl)
        legacy_blob = apply_split_cuts(
            model,
            [AcceptedSplit(CUT1, J_OFFSET), AcceptedSplit(CUT2, L_OFFSET)],
            audio_sample_rate=SAMPLE_RATE,
        )
        repos.update_timeline_otio(db, tl["id"], legacy_blob)
        row = repos.get_timeline(db, tl["id"])
        assert row is not None
        # Column carries nothing — the fallback must recover the split from the blob metadata.
        assert all(c["audio_offset_samples"] == 0 for c in repos.list_timeline_clips(db, tl["id"]))
        assert accepted_offsets_from_clips(build_model(db, row), SAMPLE_RATE) == []

        audio = export_audio_clips(db, row, build_model(db, row))
        by_in = {ac.clip.src_in_frame: ac.clip for ac in audio}
        assert by_in[CUT1 + J_OFFSET].seq_in_frame == VIDEO_B1 + J_OFFSET == 46
        assert by_in[CUT2 + L_OFFSET].seq_in_frame == VIDEO_B2 + L_OFFSET == 125
    finally:
        client.__exit__(None, None, None)


def test_column_wins_over_metadata(tmp_path: Path) -> None:
    """When both the column and legacy metadata carry offsets, the COLUMN is authoritative."""
    client, db, project, asset = _setup(tmp_path)
    try:
        tl = _rough_cut_timeline(db, project["id"], asset["id"])
        # Column says CUT1 -> +7 (only).
        repos.set_timeline_clip_audio_offsets(
            db, tl["id"], {CUT1: frame_to_sample(7, SAMPLE_RATE, RATE_NUM, RATE_DEN)}
        )
        # Stale metadata says something else entirely.
        model = build_model(db, tl)
        stale = apply_split_cuts(
            model, [AcceptedSplit(CUT2, L_OFFSET)], audio_sample_rate=SAMPLE_RATE
        )
        repos.update_timeline_otio(db, tl["id"], stale)
        row = repos.get_timeline(db, tl["id"])
        assert row is not None
        blob = serialize_timeline_otio(db, row)
        assert {s.seq_cut: s.offset for s in accepted_offsets_from_otio(blob)} == {CUT1: 7}
    finally:
        client.__exit__(None, None, None)


# === no offset: byte-for-byte the single-track OTIO Laura builds today ===========================


def test_no_offset_is_byte_for_byte_unchanged(tmp_path: Path) -> None:
    client, db, project, asset = _setup(tmp_path)
    try:
        tl = _rough_cut_timeline(db, project["id"], asset["id"])
        row = repos.get_timeline(db, tl["id"])
        assert row is not None
        model = build_model(db, row)
        # No clip carries an offset -> no audio track, byte-for-byte the single-track writer.
        assert serialize_timeline_otio(db, row).strip() == timeline_to_otio_string(model).strip()
        assert export_audio_clips(db, row, model) == []
    finally:
        client.__exit__(None, None, None)
