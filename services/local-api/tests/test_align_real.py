"""Real WhisperX forced alignment (optional ``[align]`` extra).

Synthesizes speech with ffmpeg ``flite`` and refines the word timestamps of a known
transcript, checking the per-word boundaries are tight, in-bounds and monotonic. Skips
without the ``[align]`` extra or flite.
"""

from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

import pytest

pytest.importorskip("whisperx")

from laura.analysis.align import align_words  # noqa: E402
from laura.analysis.types import SegmentResult  # noqa: E402


def _have_flite() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, check=False
    )
    return "flite" in out.stdout


def _speak(text: str, dest: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"flite=text={text}", "-ar", "16000", "-ac", "1", "-y", str(dest)],
        check=True,
    )


@pytest.mark.skipif(not _have_flite(), reason="ffmpeg flite speech synthesis unavailable")
def test_whisperx_refines_word_timestamps(tmp_path: Path) -> None:
    text = "the quick brown fox jumps over the lazy dog"
    wav = tmp_path / "s.wav"
    _speak(text, wav)
    with wave.open(str(wav), "rb") as wf:
        duration = wf.getnframes() / wf.getframerate()

    seg = SegmentResult(text=text, start_sec=0.0, end_sec=duration)
    refined = align_words(wav, [seg], language="en", device="cpu")

    assert len(refined) == 1
    words = refined[0].words
    assert len(words) >= 7  # most of the nine words align
    assert all(0.0 <= w.start_sec < w.end_sec <= duration + 0.5 for w in words)
    starts = [w.start_sec for w in words]
    assert starts == sorted(starts)  # timestamps advance monotonically
    assert any("fox" in w.text.lower() for w in words)
