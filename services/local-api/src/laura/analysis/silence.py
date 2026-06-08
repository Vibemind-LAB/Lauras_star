"""Real audio-silence detection for editorial cut placement (ffmpeg ``silencedetect``).

Editorial alignment (:mod:`laura.analysis.editorial`) treats transcript *word-gaps* as a proxy
for where it is safe to cut. But the editor's real target is a genuine **audio silence** — a
breath, the beat between two sentences, the pause where neither speaker is talking. ASR word
timings miss many of these: a gap exists in the audio but no word boundary marks it (filler,
overlapping music, an un-transcribed breath), so a cut placed on the nearest *word* edge can
still land on top of speech or just shy of the real pause.

This module detects the true silences in an asset's audio by running ffmpeg's ``silencedetect``
audio filter and parsing its stderr, returning each silence as a half-open **source-frame**
range ``[start_frame, end_frame)`` (end-exclusive, like every other range in Laura). A cut that
lands *inside* one of these intervals is the ideal editorial cut — see
:func:`laura.analysis.joint.joint_place`, which scores a silence-interior frame above a mere
word edge.

``silencedetect`` reports times in **seconds**; we convert to source-frame indices with the
asset's frame rate (``rate_num / rate_den``). The parse is split into a pure, ffmpeg-free helper
:func:`parse_silencedetect` so the seconds->frames mapping is unit-testable on captured stderr.

Defensive by contract: a missing audio stream, an unreadable file, a non-zero ffmpeg exit, or any
parse hiccup yields ``[]`` — silence detection only ever *adds* information, so its absence must
never break the cut. It never raises.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..ingest.ffmpeg import ffmpeg_bin

# ffmpeg emits e.g.
#   [Parsed_silencedetect_0 @ 0x..] silence_start: 0.999917
#   [Parsed_silencedetect_0 @ 0x..] silence_end: 1.600062 | silence_duration: 0.600146
# We only need the start/end timestamps (seconds, float). ``silence_duration`` is ignored.
_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")
# ffmpeg's input ``Duration: HH:MM:SS.ss`` line — the fallback media end for an unmatched start.
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def _seconds_to_frame(seconds: float, rate_num: int, rate_den: int) -> int:
    """Convert a time in seconds to a source-frame index: ``round(seconds * rate_num/rate_den)``.

    Rounds to the nearest frame (silencedetect timestamps are continuous seconds; the editorial
    layer works in whole frames). ``rate_den <= 0`` is treated as ``1`` defensively.
    """
    den = rate_den if rate_den > 0 else 1
    return round(seconds * rate_num / den)


def _parse_media_end_frame(stderr: str, rate_num: int, rate_den: int) -> int | None:
    """The media duration as a frame count from ffmpeg's ``Duration:`` line, or ``None``.

    Used to close a final silence that runs to EOF (``silence_start`` with no ``silence_end``).
    """
    m = _DURATION_RE.search(stderr)
    if m is None:
        return None
    hours, minutes, secs = int(m.group(1)), int(m.group(2)), float(m.group(3))
    total_seconds = hours * 3600 + minutes * 60 + secs
    return _seconds_to_frame(total_seconds, rate_num, rate_den)


def parse_silencedetect(
    stderr: str, rate_num: int, rate_den: int
) -> list[tuple[int, int]]:
    """Parse ``silencedetect`` stderr into half-open source-frame silence ranges (pure, no IO).

    Walks the lines in order, pairing each ``silence_start: X`` with the next ``silence_end: Y``
    and converting both to source frames via ``rate_num / rate_den`` (seconds -> nearest frame).
    Each pair becomes ``[start_frame, end_frame)`` — end-exclusive, like every range in Laura.

    Robustness:

    * A ``silence_start`` whose matching ``silence_end`` is missing (silence runs to EOF) is
      closed at the media end parsed from ffmpeg's ``Duration:`` line if present, else **dropped**
      (we never invent an end past the asset).
    * A stray ``silence_end`` with no open start is ignored.
    * Degenerate or inverted ranges (``end_frame <= start_frame`` after rounding) are dropped, so
      every returned interval is non-empty.

    Returns the intervals in start order; never raises.
    """
    media_end: int | None = None  # parsed lazily only if an unmatched start needs it

    silences: list[tuple[int, int]] = []
    open_start: float | None = None
    for line in stderr.splitlines():
        sm = _START_RE.search(line)
        if sm is not None:
            if open_start is not None:
                # A new start before the previous one closed: the previous silence ran to EOF.
                if media_end is None:
                    media_end = _parse_media_end_frame(stderr, rate_num, rate_den)
                _append_interval(
                    silences, open_start, None, rate_num, rate_den, media_end
                )
            open_start = float(sm.group(1))
            continue
        em = _END_RE.search(line)
        if em is not None and open_start is not None:
            _append_interval(
                silences, open_start, float(em.group(1)), rate_num, rate_den, None
            )
            open_start = None

    if open_start is not None:
        # Trailing silence with no end line -> close at media end if known, else drop.
        if media_end is None:
            media_end = _parse_media_end_frame(stderr, rate_num, rate_den)
        _append_interval(silences, open_start, None, rate_num, rate_den, media_end)

    return silences


def _append_interval(
    out: list[tuple[int, int]],
    start_seconds: float,
    end_seconds: float | None,
    rate_num: int,
    rate_den: int,
    media_end_frame: int | None,
) -> None:
    """Append one ``[start_frame, end_frame)`` interval, applying the EOF/degenerate rules.

    ``end_seconds is None`` means the silence had no ``silence_end`` (ran to EOF): close it at
    ``media_end_frame`` if known, else drop it entirely. Non-positive-length intervals are
    dropped so every appended range is genuinely non-empty.
    """
    start_frame = _seconds_to_frame(start_seconds, rate_num, rate_den)
    if start_frame < 0:
        start_frame = 0
    if end_seconds is None:
        if media_end_frame is None:
            return  # unknown media end -> never invent one
        end_frame = media_end_frame
    else:
        end_frame = _seconds_to_frame(end_seconds, rate_num, rate_den)
    if end_frame <= start_frame:
        return
    out.append((start_frame, end_frame))


def detect_silence(
    media_path: Path | str,
    *,
    rate_num: int,
    rate_den: int,
    noise_db: float = -30.0,
    min_silence_s: float = 0.12,
) -> list[tuple[int, int]]:
    """Detect audio silences in ``media_path`` as half-open source-frame ranges ``[start, end)``.

    Runs ``ffmpeg -i <media> -af silencedetect=noise=<noise_db>dB:d=<min_silence_s> -f null -``
    and parses the ``silence_start``/``silence_end`` lines from stderr, converting the seconds
    timestamps to source frames via the asset rate (``rate_num / rate_den``). See
    :func:`parse_silencedetect` for the parsing/edge-case rules.

    ``noise_db`` is the loudness floor below which audio counts as silence (more negative = only
    quieter passages qualify); ``min_silence_s`` is the minimum pause length to report (filters
    out micro-gaps between words that are not real editorial pauses).

    Defensive: a missing audio stream (no silence lines), an unreadable/-codable file, ffmpeg not
    being on PATH, a non-zero exit, or any parse error all yield ``[]``. Never raises — silence
    detection is purely additive to cut placement, so its failure must not break the edit.
    """
    cmd = [
        ffmpeg_bin(),
        "-hide_banner",
        "-nostdin",
        "-i",
        str(media_path),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_silence_s}",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
    except (FileNotFoundError, OSError, ValueError):
        # ffmpeg missing / not executable / bad args -> no silence info, degrade gracefully.
        return []
    # silencedetect writes to stderr; ffmpeg may also print the banner/Duration there. We parse
    # whatever we got even on a non-zero exit (partial output is still usable), but guard the
    # parse so a malformed buffer can never propagate.
    stderr = proc.stderr or ""
    try:
        return parse_silencedetect(stderr, rate_num, rate_den)
    except Exception:  # noqa: BLE001 - detection is best-effort; never break the caller
        return []
