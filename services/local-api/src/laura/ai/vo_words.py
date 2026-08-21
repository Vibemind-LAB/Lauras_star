"""Word-timing sidecar for narration captions (spec §4).

Every synthesized voiceover WAV gets a ``<wav>.words.json`` sidecar next to it so the
karaoke-caption path can show the AUTHORED text (never ASR text -- ASR mishears names)
with per-word timing. Whisper word timings are an optional refinement (only used when
``faster_whisper`` -- the ``[asr]`` extra -- is importable and transcription succeeds);
the fallback is an even distribution of the authored words across the measured clip
length. Sidecar generation is NEVER fatal: any failure degrades to the even fallback,
and a failure to even write the file is logged and swallowed.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_DASH_TOKENS = {"-", "–", "—"}  # "-", en dash "-", em dash "--"


def authored_words(text: str) -> list[str]:
    """Split *text* on whitespace, dropping tokens that are ONLY a dash.

    A standalone ``-``/``–``/``—`` token is a spoken-pause marker, not a word,
    so it is dropped. Punctuation attached to a word (``"Laura,"``, ``"geht's?"``) stays
    attached -- only whitespace-delimited tokenization happens here.
    """
    return [token for token in text.split() if token not in _DASH_TOKENS]


def map_words_to_slots(
    words: list[str], slots: list[tuple[int, int]]
) -> list[dict[str, object]]:
    """Map authored *words* onto timing *slots* (``(start_frame, end_frame_exclusive)``).

    Produces one entry per slot (so the result always has ``len(slots)`` entries): when
    the counts are equal each slot takes the word at the same index; otherwise slot *j*
    takes word ``min(n_w-1, j*n_w//n_a)`` (proportional compression/expansion when the
    whisper-detected word count ``n_a`` differs from the authored word count ``n_w`` --
    the equal-count case is the same formula's fixed point, so no special branch is
    needed). Every returned span is at least 1 frame wide.
    """
    n_w = len(words)
    n_a = len(slots)
    if n_w == 0 or n_a == 0:
        return []

    out: list[dict[str, object]] = []
    for j, (start, end) in enumerate(slots):
        word_idx = min(n_w - 1, (j * n_w) // n_a)
        end_excl = end if end > start else start + 1
        out.append(
            {
                "text": words[word_idx],
                "start_frame": start,
                "end_frame_exclusive": end_excl,
            }
        )
    return out


def _even_slots(n_words: int, total_frames: int) -> list[tuple[int, int]]:
    """*n_words* evenly-sized ``(start, end_exclusive)`` frame spans covering
    ``[0, total_frames)``. Each span is at least 1 frame wide."""
    if n_words <= 0 or total_frames <= 0:
        return []
    slots: list[tuple[int, int]] = []
    for i in range(n_words):
        start = (i * total_frames) // n_words
        end = ((i + 1) * total_frames) // n_words
        if end <= start:
            end = start + 1
        slots.append((start, end))
    return slots


def _seconds_to_frame_floor(seconds: float, rate_num: int, rate_den: int) -> int:
    return math.floor(seconds * rate_num / rate_den)


def _seconds_to_frame_ceil(seconds: float, rate_num: int, rate_den: int) -> int:
    return math.ceil(seconds * rate_num / rate_den)


def _transcribe_words(
    wav_path: Path, language: str | None
) -> list[tuple[str, float, float]] | None:
    """Transcribe *wav_path* with faster-whisper; return ``[(word, start_sec, end_sec)]``.

    Returns ``None`` on ANY failure: the ``[asr]`` extra is not installed, the model
    fails to load, or transcription itself raises. Kept as a bare module-level function
    (rather than inlined) so tests can monkeypatch it directly without importing the
    real, heavy, optional faster-whisper dependency.
    """
    try:
        from ..analysis.asr import transcribe as _whisper_transcribe
    except Exception:  # noqa: BLE001 - missing optional extra must never be fatal
        return None
    try:
        segments = _whisper_transcribe(wav_path, language=language)
    except Exception:  # noqa: BLE001 - model load / transcription failure -> fallback
        return None

    words: list[tuple[str, float, float]] = []
    for segment in segments:
        for word in segment.words:
            words.append((word.text, word.start_sec, word.end_sec))
    return words


def write_word_sidecar(
    wav_path: Path,
    *,
    text: str,
    measured_frames: int,
    rate_num: int,
    rate_den: int,
    language: str | None,
) -> Path | None:
    """Write ``<wav_path>.words.json`` next to *wav_path* (schema per spec §4).

    ``{"words": [{"text", "start_frame", "end_frame_exclusive"}], "source": "whisper" | "even"}``

    Tries faster-whisper timings first (mapped onto the AUTHORED text via
    :func:`map_words_to_slots` -- the text always comes from the payload, never from ASR,
    since ASR mishears names); on any failure at all (whisper unavailable, transcription
    error, or an empty word result) falls back to an even distribution of the authored
    words across ``measured_frames``. NEVER raises: a sidecar problem is logged as a
    warning and must never fail the voiceover job that produced the WAV.
    """
    words = authored_words(text)
    entries: list[dict[str, object]] = []
    source = "even"

    if words:
        try:
            whisper_words = _transcribe_words(wav_path, language)
            if whisper_words:
                slots = [
                    (
                        _seconds_to_frame_floor(start_sec, rate_num, rate_den),
                        _seconds_to_frame_ceil(end_sec, rate_num, rate_den),
                    )
                    for _text, start_sec, end_sec in whisper_words
                ]
                mapped = map_words_to_slots(words, slots)
                if mapped:
                    entries = mapped
                    source = "whisper"
        except Exception:  # noqa: BLE001 - whisper-path failures must never be fatal
            _log.warning(
                "voiceover word sidecar: whisper mapping failed for %s, using even "
                "fallback",
                wav_path,
                exc_info=True,
            )
            entries = []
            source = "even"

    if not entries:
        entries = map_words_to_slots(words, _even_slots(len(words), measured_frames))
        source = "even"

    sidecar_path = Path(f"{wav_path}.words.json")
    payload: dict[str, Any] = {"words": entries, "source": source}
    try:
        sidecar_path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        _log.warning(
            "voiceover word sidecar: failed to write %s", sidecar_path, exc_info=True
        )
        return None
    return sidecar_path
