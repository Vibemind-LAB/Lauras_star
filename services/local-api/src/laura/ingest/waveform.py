"""Compute a downsampled waveform (peaks) from a PCM WAV using only the stdlib.

Produces ``waveform.json`` for the UI: a normalised max-amplitude per time bucket.
Stdlib-only (no numpy) keeps the base install light; for very long clips this can be
swapped for an ffmpeg ``astats`` pass later (docs/12-performance-hardware.md).
"""

from __future__ import annotations

import array
import json
import wave
from pathlib import Path
from typing import Any

DEFAULT_PIXELS_PER_SECOND = 100


def compute_waveform(
    wav_path: Path | str,
    dest_json: Path,
    *,
    pixels_per_second: int = DEFAULT_PIXELS_PER_SECOND,
) -> dict[str, Any]:
    with wave.open(str(wav_path), "rb") as wav:
        n_channels = wav.getnchannels()
        sampwidth = wav.getsampwidth()
        framerate = wav.getframerate()
        if sampwidth != 2:
            raise ValueError(f"expected 16-bit PCM, got sample width {sampwidth}")

        max_val = float(2 ** 15)
        samples_per_bucket = max(1, framerate // pixels_per_second)
        peaks: list[float] = []
        cur_max = 0
        remaining = samples_per_bucket
        chunk_frames = samples_per_bucket * 64

        while True:
            raw = wav.readframes(chunk_frames)
            if not raw:
                break
            data = array.array("h")
            data.frombytes(raw)
            # Fold channels to mono by taking the loudest channel per frame.
            step = n_channels
            for i in range(0, len(data), step):
                v = 0
                for c in range(step):
                    v = max(v, abs(data[i + c]))
                if v > cur_max:
                    cur_max = v
                remaining -= 1
                if remaining == 0:
                    peaks.append(round(cur_max / max_val, 4))
                    cur_max = 0
                    remaining = samples_per_bucket
        if remaining < samples_per_bucket:
            peaks.append(round(cur_max / max_val, 4))

    payload: dict[str, Any] = {
        "version": 1,
        "sample_rate": framerate,
        "samples_per_pixel": samples_per_bucket,
        "length": len(peaks),
        "peaks": peaks,
    }
    dest_json.parent.mkdir(parents=True, exist_ok=True)
    dest_json.write_text(json.dumps(payload), encoding="utf-8")
    return payload
