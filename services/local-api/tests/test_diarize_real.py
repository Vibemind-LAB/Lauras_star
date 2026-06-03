"""Real speaker diarization (optional ``[diarize]`` extra + HF token).

Synthesizes a two-speaker sample (two ffmpeg ``flite`` voices concatenated) and runs the
pyannote pipeline, verifying two distinct speakers are found and ``assign_speakers`` maps
transcript segments to them. Skips without the ``[diarize]`` extra, ffmpeg/flite, or
``HF_TOKEN`` (the pyannote models are gated), so it never blocks the default suite.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("pyannote.audio")

from laura.analysis.diarize import assign_speakers, diarize  # noqa: E402
from laura.analysis.types import SegmentResult  # noqa: E402


def _have_flite() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, check=False
    )
    return "flite" in out.stdout


def _speak(text: str, voice: str, dest: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"flite=text={text}:voice={voice}", "-ar", "16000", "-ac", "1", "-y", str(dest)],
        check=True,
    )


@pytest.mark.skipif(not _have_flite(), reason="ffmpeg flite speech synthesis unavailable")
@pytest.mark.skipif(not os.environ.get("HF_TOKEN"), reason="HF_TOKEN required (gated pyannote)")
def test_real_diarization_finds_two_speakers(tmp_path: Path) -> None:
    a, b, both = tmp_path / "a.wav", tmp_path / "b.wav", tmp_path / "both.wav"
    _speak(
        "hello my name is alice and i work on frame accurate video editing every day in the studio",
        "slt", a,
    )
    _speak(
        "and i am bob i take care of the audio analysis and the speaker detection pipeline here",
        "awb", b,
    )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(a), "-i", str(b),
         "-filter_complex", "[0:0][1:0]concat=n=2:v=0:a=1", "-ar", "16000", "-ac", "1",
         "-y", str(both)],
        check=True,
    )

    turns = diarize(both)
    assert turns, "expected speaker turns"
    assert len({t.label for t in turns}) >= 2  # two distinct voices -> >= 2 speakers

    segs = [
        SegmentResult(text="first", start_sec=1.0, end_sec=4.0),
        SegmentResult(text="second", start_sec=8.0, end_sec=11.0),
    ]
    assign_speakers(segs, turns)
    assert segs[0].speaker_label is not None
    assert segs[1].speaker_label is not None
    assert segs[0].speaker_label != segs[1].speaker_label  # different halves -> different speakers
