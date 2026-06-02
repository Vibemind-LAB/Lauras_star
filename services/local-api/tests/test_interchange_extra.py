"""FCPXML export structure + caption line-wrapping."""

from __future__ import annotations

from typing import Any

from laura.interchange.captions import segments_to_srt
from laura.interchange.fcpx_xml import timeline_to_fcpx_xml
from laura.interchange.timeline import Clip, Timeline


def test_fcpx_xml_structure() -> None:
    tl = Timeline(
        name="Cut", rate_num=30000, rate_den=1001,
        clips=[Clip("A.mov", 0, 30, 0, 30, source_url="C:/a.mov", asset_id="a1")],
    )
    xml = timeline_to_fcpx_xml(tl)
    assert "<!DOCTYPE fcpxml>" in xml
    assert '<fcpxml version="1.9">' in xml
    assert 'frameDuration="1001/30000s"' in xml
    assert "<asset-clip" in xml
    assert 'offset="0s"' in xml
    assert 'duration="30030/30000s"' in xml  # 30 frames @ 30000/1001
    assert "file://localhost/C:/a.mov" in xml


def test_caption_wraps_long_lines() -> None:
    long_text = " ".join(["word"] * 25)  # ~125 chars on one line if unwrapped
    segments: list[dict[str, Any]] = [
        {"start_frame": 0, "end_frame": 30, "text": long_text, "speaker_label": None}
    ]
    srt = segments_to_srt(segments, 30, 1)
    # every line stays within a readable width (wrapping happened)
    assert all(len(line) <= 50 for line in srt.splitlines())
    assert long_text not in srt  # the single long line was broken up
