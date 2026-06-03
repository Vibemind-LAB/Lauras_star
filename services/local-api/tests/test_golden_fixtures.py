"""Portion 17.3 — reproducible demo + golden export fixtures (byte-exact drift guard).

A canonical in-memory demo project (timeline + transcript) is rendered to every
deterministic exporter and compared byte-for-byte against committed goldens under
``fixtures/golden/``. Run with ``LAURA_REGEN_GOLDEN=1`` to regenerate them after an
intentional format change. OTIO is checked by round-trip (its JSON serialisation is
library-version dependent, so it is not byte-goldened).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from laura.interchange.captions import segments_to_srt, segments_to_vtt
from laura.interchange.edl import timeline_to_edl
from laura.interchange.fcp7_xml import timeline_to_fcp7_xml
from laura.interchange.otio_io import otio_string_to_timeline, timeline_to_otio_string
from laura.interchange.timeline import Clip, Timeline

GOLDEN = Path(__file__).resolve().parents[3] / "fixtures" / "golden"
_REGEN = os.environ.get("LAURA_REGEN_GOLDEN") == "1"


def demo_timeline() -> Timeline:
    """A small, fixed two-clip / two-speaker rough cut at 30 fps."""
    return Timeline(
        name="Laura Demo",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        clips=[
            Clip("A.mov", 0, 60, 0, 60, source_url="C:/media/A.mov",
                 asset_id="a1", speaker_label="SPK1"),
            Clip("B.mov", 10, 40, 60, 90, source_url="C:/media/B.mov",
                 asset_id="a2", speaker_label="SPK2"),
        ],
    )


def demo_segments() -> list[dict[str, Any]]:
    return [
        {"start_frame": 0, "end_frame": 60,
         "text": "Hallo und herzlich willkommen.", "speaker_label": "SPK1"},
        {"start_frame": 60, "end_frame": 90,
         "text": "Das ist der zweite Clip über Zeitcode.", "speaker_label": "SPK2"},
    ]


EXPORTS: dict[str, Callable[[], str]] = {
    "demo.edl": lambda: timeline_to_edl(demo_timeline()),
    "demo.fcp7.xml": lambda: timeline_to_fcp7_xml(demo_timeline()),
    "demo.srt": lambda: segments_to_srt(demo_segments(), 30, 1),
    "demo.vtt": lambda: segments_to_vtt(demo_segments(), 30, 1),
}


@pytest.mark.parametrize("name", sorted(EXPORTS))
def test_golden_export_matches(name: str) -> None:
    generated = EXPORTS[name]()
    path = GOLDEN / name
    if _REGEN:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(generated.encode("utf-8"))  # byte-exact, no newline translation
    assert path.exists(), f"missing golden {path} (run with LAURA_REGEN_GOLDEN=1)"
    assert generated == path.read_bytes().decode("utf-8")


def test_demo_otio_roundtrips() -> None:
    text = timeline_to_otio_string(demo_timeline())
    back = otio_string_to_timeline(text, rate_num=30, rate_den=1)
    assert len(back.clips) == 2
    ordered = back.ordered()
    assert (ordered[0].seq_in_frame, ordered[0].seq_out_frame_exclusive) == (0, 60)
    assert (ordered[1].seq_in_frame, ordered[1].seq_out_frame_exclusive) == (60, 90)
    assert ordered[1].src_in_frame == 10
