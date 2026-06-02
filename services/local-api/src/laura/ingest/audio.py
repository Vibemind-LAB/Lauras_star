"""Audio extraction: a mono 16 kHz track for ASR/alignment and a 48 kHz mix for
playback and waveform rendering (docs/06-storage.md)."""

from __future__ import annotations

from pathlib import Path

from .ffmpeg import run_ffmpeg

ASR_SAMPLE_RATE = 16000
MIX_SAMPLE_RATE = 48000


def extract_mono16k(src: Path | str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        ["-i", str(src), "-vn", "-ac", "1", "-ar", str(ASR_SAMPLE_RATE),
         "-c:a", "pcm_s16le", str(dest)]
    )


def extract_mix48k(src: Path | str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        ["-i", str(src), "-vn", "-ac", "2", "-ar", str(MIX_SAMPLE_RATE),
         "-c:a", "pcm_s16le", str(dest)]
    )
