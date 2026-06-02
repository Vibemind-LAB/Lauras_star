"""Plain result types produced by analysis stages (before DB mapping)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ShotResult:
    src_in_frame: int
    src_out_frame_exclusive: int
    method: str
    confidence: float | None = None


@dataclass
class WordResult:
    text: str
    start_sec: float
    end_sec: float
    confidence: float | None = None
    is_punctuation: bool = False


@dataclass
class SegmentResult:
    text: str
    start_sec: float
    end_sec: float
    confidence: float | None = None
    words: list[WordResult] = field(default_factory=list)
    speaker_label: str | None = None


@dataclass(frozen=True)
class SpeakerTurn:
    start_sec: float
    end_sec: float
    label: str
