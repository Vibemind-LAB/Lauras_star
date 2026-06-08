"""NLE exports carry accepted L/J split edits (3b).

3a represents an accepted L/J split in the OTIO source of truth as a separate audio track whose
boundary is offset from the video boundary. The exports, however, build their model FRESH from the
clips table, so they would emit single-track HARD cuts unless routed through the split-aware build.
3b fixes that: :func:`laura.api.otio_split.split_audio_clips` derives the split-shifted audio clips
(the model-level analog of 3a's A1 track) and the EDL / FCP7-XML / FCPXML writers emit them as a
separate, offset audio track. These tests pin:

* **FCPXML / FCP7-XML** carry the audio edit on the OFFSET frame for a J-cut AND an L-cut, while the
  picture stays frame-exact on the visual cut;
* **EDL** emits the split as parallel ``V``/``A`` events on the offset sound cut (its documented
  faithful form — CMX3600 has no single split-edit event);
* a **no-split** export is byte-for-byte identical to what Laura emits today (additive);
* the audio offset is **sample-accurate** (invariant #3);
* **SRT/VTT** transcript exports are unaffected.

Timeline: three clips A,B,C packed back-to-back on the sequence. Two inter-clip cuts —
cut1 (A|B) at seq frame 50 gets a **J-cut** (offset -4: audio earlier), cut2 (B|C) at seq frame 120
gets an **L-cut** (offset +5: audio later). Video boundaries stay on 50 and 120.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from laura.api.otio_split import AcceptedSplit, split_audio_clips
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.editing.otio_sync import export_audio_clips, serialize_timeline_otio
from laura.interchange.edl import timeline_to_edl
from laura.interchange.fcp7_xml import timeline_to_fcp7_xml
from laura.interchange.fcpx_xml import timeline_to_fcpx_xml
from laura.interchange.timeline import Clip, Timeline
from laura.interchange.validate import validate_export
from laura.timebase.sampling import frame_to_sample

RATE_NUM, RATE_DEN = 30, 1
SAMPLE_RATE = 48_000  # 1 frame == 1600 samples at 48 kHz / 30 fps

# Inter-clip cuts identified by the next clip's src_in_frame (== planner SplitCut.seq_cut).
CUT1_SEQ = 200  # A|B boundary, video at sequence frame 50
CUT2_SEQ = 400  # B|C boundary, video at sequence frame 120
VIDEO_B1 = 50
VIDEO_B2 = 120
J_OFFSET = -4  # J-cut on cut1: audio EARLIER
L_OFFSET = 5  # L-cut on cut2: audio LATER


def _rough_cut() -> Timeline:
    return Timeline(
        name="rc", rate_num=RATE_NUM, rate_den=RATE_DEN,
        clips=[
            Clip(name="A", src_in_frame=0, src_out_frame_exclusive=50,
                 seq_in_frame=0, seq_out_frame_exclusive=50,
                 asset_id="a1", source_url="/a.mov"),
            Clip(name="B", src_in_frame=200, src_out_frame_exclusive=270,
                 seq_in_frame=50, seq_out_frame_exclusive=120,
                 asset_id="a1", source_url="/a.mov"),
            Clip(name="C", src_in_frame=400, src_out_frame_exclusive=460,
                 seq_in_frame=120, seq_out_frame_exclusive=180,
                 asset_id="a1", source_url="/a.mov"),
        ],
    )


def _splits() -> list[AcceptedSplit]:
    return [
        AcceptedSplit(seq_cut=CUT1_SEQ, offset=J_OFFSET),
        AcceptedSplit(seq_cut=CUT2_SEQ, offset=L_OFFSET),
    ]


def _audio_clips() -> list[Clip]:
    return [
        ac.clip
        for ac in split_audio_clips(
            _rough_cut(), _splits(), audio_sample_rate=SAMPLE_RATE
        )
    ]


# === the model-level split-shifted audio clips land on the offset boundaries ====================


def test_split_audio_clips_carry_offset_boundaries_and_samples() -> None:
    acs = split_audio_clips(_rough_cut(), _splits(), audio_sample_rate=SAMPLE_RATE)
    assert len(acs) == 3
    a, b, c = (ac.clip for ac in acs)

    # J-cut (-4) on cut1: A's audio-out and B's audio-in both move 4 frames EARLIER (46, not 50).
    assert a.seq_out_frame_exclusive == VIDEO_B1 + J_OFFSET == 46
    assert b.seq_in_frame == VIDEO_B1 + J_OFFSET == 46
    # L-cut (+5) on cut2: B's audio-out and C's audio-in both move 5 frames LATER (125, not 120).
    assert b.seq_out_frame_exclusive == VIDEO_B2 + L_OFFSET == 125
    assert c.seq_in_frame == VIDEO_B2 + L_OFFSET == 125
    # Source IN follows the sequence shift exactly (audio shares a source with the picture).
    assert b.src_in_frame == 200 + J_OFFSET == 196
    assert c.src_in_frame == 400 + L_OFFSET == 405

    # Sample-accurate (invariant #3): B's audio-in sample == frame 196 @ 48kHz/30fps.
    assert acs[1].src_in_sample == frame_to_sample(196, SAMPLE_RATE, RATE_NUM, RATE_DEN)
    assert acs[2].src_in_sample == frame_to_sample(405, SAMPLE_RATE, RATE_NUM, RATE_DEN)


def test_no_split_yields_no_audio_clips() -> None:
    assert split_audio_clips(_rough_cut(), []) == []
    # A sub-perception (hard) offset shifts nothing -> no audio track.
    assert split_audio_clips(_rough_cut(), [AcceptedSplit(CUT1_SEQ, 1)]) == []


# === FCP7-XML carries the audio edit on the offset frame; video stays frame-exact ===============


def test_fcp7_xml_carries_audio_offset_for_j_and_l() -> None:
    xml = timeline_to_fcp7_xml(_rough_cut(), _audio_clips())
    root = ET.fromstring(xml)

    video_items = root.findall("./sequence/media/video/track/clipitem")
    audio_items = root.findall("./sequence/media/audio/track/clipitem")
    assert len(video_items) == 3
    assert len(audio_items) == 3

    # Video boundaries stay frame-exact on the visual cut: A ends 50, B [50,120), C starts 120.
    assert video_items[0].findtext("end") == str(VIDEO_B1)
    assert video_items[1].findtext("start") == str(VIDEO_B1)
    assert video_items[1].findtext("end") == str(VIDEO_B2)
    assert video_items[2].findtext("start") == str(VIDEO_B2)

    # Audio edits land on the OFFSET frames: J-cut -> A_out/B_in at 46; L-cut -> B_out/C_in at 125.
    assert audio_items[0].findtext("end") == str(VIDEO_B1 + J_OFFSET) == "46"
    assert audio_items[1].findtext("start") == str(VIDEO_B1 + J_OFFSET) == "46"
    assert audio_items[1].findtext("end") == str(VIDEO_B2 + L_OFFSET) == "125"
    assert audio_items[2].findtext("start") == str(VIDEO_B2 + L_OFFSET) == "125"
    # The audio source-in follows the shift (shared media): B audio-in source == 196.
    assert audio_items[1].findtext("in") == "196"
    # Audio clipitems declare an audio source track so the NLE routes them to a channel.
    assert audio_items[0].find("sourcetrack/mediatype").text == "audio"  # type: ignore[union-attr]


def test_fcp7_xml_no_split_unchanged() -> None:
    # No audio_clips -> byte-for-byte the single video-track document Laura emits today.
    assert timeline_to_fcp7_xml(_rough_cut(), None) == timeline_to_fcp7_xml(_rough_cut())
    assert "<audio>" not in timeline_to_fcp7_xml(_rough_cut())


# === FCPXML carries the audio edit on lane -1 at the offset frame; video frame-exact ============


def test_fcpx_xml_carries_audio_offset_for_j_and_l() -> None:
    xml = timeline_to_fcpx_xml(_rough_cut(), _audio_clips())
    root = ET.fromstring(xml)
    spine = root.find("./library/event/project/sequence/spine")
    assert spine is not None

    picture = spine.findall("asset-clip")
    assert len(picture) == 3
    # Picture stays frame-exact on the visual cut. offset is the sequence position.
    assert picture[0].attrib["offset"] == "0s"
    assert picture[1].attrib["offset"] == _time(VIDEO_B1)  # B at seq frame 50
    assert picture[2].attrib["offset"] == _time(VIDEO_B2)  # C at seq frame 120

    # Each picture clip carries its connected audio on lane -1 on the OFFSET frame.
    a_audio = picture[0].find("asset-clip[@lane='-1']")
    b_audio = picture[1].find("asset-clip[@lane='-1']")
    c_audio = picture[2].find("asset-clip[@lane='-1']")
    assert a_audio is not None and b_audio is not None and c_audio is not None
    # B's audio starts on the J-cut frame 46 (4 earlier); C's on the L-cut frame 125 (5 later).
    assert b_audio.attrib["offset"] == _time(VIDEO_B1 + J_OFFSET)  # 46
    assert c_audio.attrib["offset"] == _time(VIDEO_B2 + L_OFFSET)  # 125
    # Source start follows the shift: B audio source-in == frame 196.
    assert b_audio.attrib["start"] == _time(196)
    assert b_audio.attrib["audioRole"] == "dialogue"


def test_fcpx_xml_no_split_unchanged() -> None:
    assert timeline_to_fcpx_xml(_rough_cut(), None) == timeline_to_fcpx_xml(_rough_cut())
    assert "lane=\"-1\"" not in timeline_to_fcpx_xml(_rough_cut())


# === EDL: parallel V/A events on the offset sound cut (its documented faithful form) =============


def test_edl_emits_parallel_v_and_a_events_on_offset() -> None:
    edl = timeline_to_edl(_rough_cut(), _audio_clips())
    lines = [ln for ln in edl.splitlines() if ln and ln[0].isdigit()]
    # Three V events (picture) then three A events (audio) — parallel, not flattened.
    channels = [ln.split()[2] for ln in lines]
    assert channels == ["V", "V", "V", "A", "A", "A"]

    fr_in = lambda frame: _tc(frame)  # noqa: E731 - terse local timecode helper
    # The B audio event's record-in is the J-cut frame 46 (audio earlier), not the video frame 50.
    a_events = [ln for ln in lines if ln.split()[2] == "A"]
    # Event B (second A) record-in == 46; its source-in == 196.
    b_audio = a_events[1]
    assert f" {fr_in(196)} " in b_audio  # source-in timecode of frame 196
    assert b_audio.rstrip().endswith(fr_in(125))  # record-out is the L-cut at 125
    assert fr_in(46) in b_audio  # record-in is the J-cut at 46


def test_edl_no_split_unchanged() -> None:
    # No audio_clips -> byte-for-byte the V-only list Laura emits today.
    assert timeline_to_edl(_rough_cut(), None) == timeline_to_edl(_rough_cut())
    assert " A " not in timeline_to_edl(_rough_cut())


def test_edl_validate_warns_on_split_limitation() -> None:
    # The preflight documents EDL's limitation when a split is present (no single split-edit event).
    diag = validate_export(_rough_cut(), "edl", has_split=True)
    assert any("parallel V/A events" in w for w in diag["warnings"])
    # No split -> no such warning (byte-for-byte preflight as before).
    assert not any(
        "parallel V/A" in w for w in validate_export(_rough_cut(), "edl")["warnings"]
    )


# === SRT/VTT are transcript exports — wholly unaffected by the split representation ==============


def test_captions_unaffected_by_split() -> None:
    from laura.interchange.captions import segments_to_srt, segments_to_vtt

    segments: list[dict[str, Any]] = [
        {"start_frame": 0, "end_frame": 50, "text": "Hallo", "speaker_label": None},
        {"start_frame": 50, "end_frame": 120, "text": "Welt", "speaker_label": None},
    ]
    # Captions take segments, not the split model — there is no split parameter to regress.
    assert "Hallo" in segments_to_srt(segments, RATE_NUM, RATE_DEN)
    assert segments_to_vtt(segments, RATE_NUM, RATE_DEN).startswith("WEBVTT")


# === end-to-end: the export RECOVERS the accepted split from the stored blob (the 3b-critical path)

# The export builds its model FRESH from the clips table; the accepted offset lives only in the
# stored OTIO blob metadata, so the export path must read it back or it flattens to a hard cut.


def _db_with_split_rough_cut(tmp_path: Path) -> tuple[SqliteDatabase, dict[str, Any]]:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    project = repos.create_project(
        db, name="p", rate_num=RATE_NUM, rate_den=RATE_DEN, drop_frame=False,
        workspace_root="/tmp/p",
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mov", source_path="/a.mov"
    )
    repos.update_asset_probe(
        db, asset["id"], type="video", duration_frames=500, rate_num=RATE_NUM, rate_den=RATE_DEN,
        audio_sample_rate=SAMPLE_RATE, start_timecode=None, width=1920, height=1080,
        codec_video="h264", codec_audio="aac", is_vfr=False, sha256=None,
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    base = {"asset_id": asset["id"], "lane": 0, "speed_num": 1, "speed_den": 1}
    repos.replace_timeline_clips(db, tl["id"], [
        {**base, "src_in_frame": 0, "src_out_frame_exclusive": 50,
         "seq_in_frame": 0, "seq_out_frame_exclusive": 50},
        {**base, "src_in_frame": 200, "src_out_frame_exclusive": 270,
         "seq_in_frame": 50, "seq_out_frame_exclusive": 120},
        {**base, "src_in_frame": 400, "src_out_frame_exclusive": 460,
         "seq_in_frame": 120, "seq_out_frame_exclusive": 180},
    ])
    fresh = repos.get_timeline(db, tl["id"])
    assert fresh is not None
    # Accept the J-cut + L-cut and persist via the 3a serialiser (what the "Übernehmen" UI will do).
    blob = serialize_timeline_otio(db, fresh, accepted=_splits())
    repos.update_timeline_otio(db, fresh["id"], blob)
    refreshed = repos.get_timeline(db, fresh["id"])
    assert refreshed is not None
    return db, refreshed


def test_export_recovers_split_from_stored_blob(tmp_path: Path) -> None:
    db, tl = _db_with_split_rough_cut(tmp_path)
    model = Timeline(
        name=tl["name"], rate_num=RATE_NUM, rate_den=RATE_DEN,
        clips=_rough_cut().clips,
    )
    # The export path recovers the accepted offsets from the stored blob (no explicit accepted arg).
    acs = export_audio_clips(db, tl, model)
    assert len(acs) == 3
    # Sample-accurate boundaries survive the round-trip through the stored blob.
    assert acs[1].clip.seq_in_frame == VIDEO_B1 + J_OFFSET == 46
    assert acs[2].clip.seq_in_frame == VIDEO_B2 + L_OFFSET == 125
    assert acs[1].src_in_sample == frame_to_sample(196, SAMPLE_RATE, RATE_NUM, RATE_DEN)


def test_export_no_split_blob_yields_no_audio(tmp_path: Path) -> None:
    db, tl = _db_with_split_rough_cut(tmp_path)
    # Overwrite the stored blob with a plain (no-split) one — the export must then carry no audio.
    plain = serialize_timeline_otio(db, tl, accepted=[])
    repos.update_timeline_otio(db, tl["id"], plain)
    reloaded = repos.get_timeline(db, tl["id"])
    assert reloaded is not None
    model = Timeline(name="rc", rate_num=RATE_NUM, rate_den=RATE_DEN, clips=_rough_cut().clips)
    assert export_audio_clips(db, reloaded, model) == []


# --- helpers --------------------------------------------------------------------------------------


def _time(frames: int) -> str:
    if frames == 0:
        return "0s"
    return f"{frames * RATE_DEN}/{RATE_NUM}s"


def _tc(frame: int) -> str:
    from laura.timebase import FrameRate, frames_to_timecode

    return frames_to_timecode(frame, FrameRate(RATE_NUM, RATE_DEN, False))
