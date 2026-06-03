"""Speaker diarization via pyannote.audio (optional extra: ``[diarize]``).

Needs a Hugging Face token (env ``HF_TOKEN``) to download the pretrained pipeline.
``assign_speakers`` is a pure function (no ML) and is unit-tested independently.
"""

from __future__ import annotations

import os
import wave
from pathlib import Path
from typing import Any

from .device import torch_device
from .types import SegmentResult, SpeakerTurn


def pyannote_available() -> bool:
    try:
        import pyannote.audio  # noqa: F401
    except Exception:
        return False
    return True


def _load_waveform(audio_path: Path | str) -> tuple[Any, int]:
    """Decode a PCM WAV to a ``(1, samples)`` float32 tensor without torchcodec/FFmpeg.

    pyannote 4.x decodes files via torchcodec, which needs matching FFmpeg shared
    libraries (fragile on Windows). Feeding a pre-decoded waveform sidesteps that. Ingest
    writes mono 16 kHz PCM s16le WAV, which the stdlib ``wave`` module reads directly."""
    import numpy as np
    import torch

    with wave.open(str(audio_path), "rb") as wav:
        sample_rate = wav.getframerate()
        channels = wav.getnchannels()
        raw = wav.readframes(wav.getnframes())
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    waveform = torch.from_numpy(data).unsqueeze(0)  # (1, num_samples)
    return waveform, sample_rate


def diarize(audio_path: Path | str) -> list[SpeakerTurn]:
    """Run diarization. Raises (via lazy import) if ``[diarize]`` is absent."""
    from pyannote.audio import Pipeline

    token = os.environ.get("HF_TOKEN")
    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", token=token)
    assert pipeline is not None, "pyannote pipeline failed to load (check HF_TOKEN/licenses)"

    dev = torch_device()
    if dev != "cpu":
        try:
            import torch

            pipeline.to(torch.device(dev))
        except Exception:  # noqa: BLE001 - stay on CPU if the device move fails
            pass

    waveform, sample_rate = _load_waveform(audio_path)
    output = pipeline({"waveform": waveform, "sample_rate": sample_rate})

    # pyannote 4.x returns a DiarizeOutput (the Annotation is on .speaker_diarization);
    # older versions return the Annotation directly.
    annotation = getattr(output, "speaker_diarization", output)

    turns: list[SpeakerTurn] = []
    for segment, _track, label in annotation.itertracks(yield_label=True):
        turns.append(SpeakerTurn(start_sec=float(segment.start), end_sec=float(segment.end),
                                 label=str(label)))
    return turns


def assign_speakers(segments: list[SegmentResult], turns: list[SpeakerTurn]) -> None:
    """Assign each segment the speaker whose turn overlaps it most (in place)."""
    if not turns:
        return
    for seg in segments:
        best_label: str | None = None
        best_overlap = 0.0
        for turn in turns:
            overlap = min(seg.end_sec, turn.end_sec) - max(seg.start_sec, turn.start_sec)
            if overlap > best_overlap:
                best_overlap = overlap
                best_label = turn.label
        seg.speaker_label = best_label
