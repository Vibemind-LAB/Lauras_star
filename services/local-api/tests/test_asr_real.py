"""Real ASR run (optional ``[asr]`` extra).

Synthesizes speech with ffmpeg's ``flite`` filter and transcribes it on CPU with
faster-whisper, verifying recognizable words and that the result maps through the time
core to integer samples/frames. Skips when faster-whisper or ffmpeg/flite is absent
(e.g. CI without the extra), so it never blocks the default suite.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("faster_whisper")

from laura.analysis.asr import transcribe  # noqa: E402
from laura.analysis.mapping import map_segment  # noqa: E402


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
def test_real_asr_transcribes_speech(tmp_path: Path) -> None:
    wav = tmp_path / "speech.wav"
    _speak("The quick brown fox jumps over the lazy dog", wav)

    # device="cpu": reliable everywhere (auto would fall back, but skip the GPU attempt)
    segments = transcribe(wav, model_size="base", language="en", device="cpu")
    assert segments, "expected at least one transcript segment"

    text = " ".join(s.text for s in segments).lower()
    keywords = ["quick", "brown", "fox", "jumps", "lazy", "dog"]
    hits = sum(1 for k in keywords if k in text)
    assert hits >= 3, f"only matched {hits} keywords in {text!r}"

    # the segment maps through the time core to integer samples + frames (30 fps)
    seg_row, word_rows = map_segment(segments[0], 16000, 30, 1)
    assert seg_row["end_sample"] > seg_row["start_sample"] >= 0
    assert seg_row["end_frame"] > seg_row["start_frame"] >= 0
    assert len(word_rows) >= 3
    assert all(w["end_frame"] >= w["start_frame"] for w in word_rows)
