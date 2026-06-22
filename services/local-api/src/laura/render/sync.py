"""Frame-count based sync guard for rendered and synthetic media outputs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from shutil import move

from ..ingest.ffmpeg import probe, run_ffmpeg


class MediaSyncError(ValueError):
    """Raised when a media file drifts away from its expected frame duration."""


@dataclass(frozen=True)
class MediaSyncReport:
    path: Path
    expected_frames: int
    video_frames: int | None
    audio_frames: int | None
    max_abs_drift_frames: int


def assert_media_sync(
    path: Path,
    *,
    expected_frames: int,
    rate_num: int,
    rate_den: int,
    tolerance_frames: int = 1,
    require_video: bool = False,
    require_audio: bool = False,
) -> MediaSyncReport:
    """Verify that present A/V streams match the expected sequence duration.

    The guard is intentionally frame-based: all durations are projected to the
    project's integer frame rate before comparison.  Missing optional streams
    are ignored; required streams raise a clear error.
    """
    data = probe(path)
    streams = _streams(data)
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)

    video_frames = _video_frame_count(video, rate_num=rate_num, rate_den=rate_den)
    audio_frames = _audio_frame_count(audio, rate_num=rate_num, rate_den=rate_den)

    if require_video and video_frames is None:
        raise MediaSyncError(f"video stream missing in {path}")
    if require_audio and audio_frames is None:
        raise MediaSyncError(f"audio stream missing in {path}")

    drifts: list[int] = []
    if video_frames is not None:
        drift = video_frames - expected_frames
        drifts.append(abs(drift))
        if abs(drift) > tolerance_frames:
            raise MediaSyncError(
                f"video frame drift: expected {expected_frames}, got {video_frames}"
            )
    if audio_frames is not None:
        drift = audio_frames - expected_frames
        drifts.append(abs(drift))
        if abs(drift) > tolerance_frames:
            raise MediaSyncError(
                f"audio duration drift: expected {expected_frames}, got {audio_frames}"
            )

    return MediaSyncReport(
        path=path,
        expected_frames=expected_frames,
        video_frames=video_frames,
        audio_frames=audio_frames,
        max_abs_drift_frames=max(drifts, default=0),
    )


def assert_or_fix_media_sync(
    path: Path,
    *,
    expected_frames: int,
    rate_num: int,
    rate_den: int,
    tolerance_frames: int = 1,
    require_video: bool = False,
    require_audio: bool = False,
    fix: bool = False,
) -> MediaSyncReport:
    """Verify sync, optionally normalize duration once, then verify again."""
    try:
        return assert_media_sync(
            path,
            expected_frames=expected_frames,
            rate_num=rate_num,
            rate_den=rate_den,
            tolerance_frames=tolerance_frames,
            require_video=require_video,
            require_audio=require_audio,
        )
    except MediaSyncError:
        if not fix:
            raise
    fix_media_duration(
        path,
        expected_frames=expected_frames,
        rate_num=rate_num,
        rate_den=rate_den,
    )
    return assert_media_sync(
        path,
        expected_frames=expected_frames,
        rate_num=rate_num,
        rate_den=rate_den,
        tolerance_frames=tolerance_frames,
        require_video=require_video,
        require_audio=require_audio,
    )


def fix_media_duration(
    path: Path,
    *,
    expected_frames: int,
    rate_num: int,
    rate_den: int,
) -> None:
    """Normalize media duration to exactly the expected frame span via ffmpeg.

    Video is trimmed or padded by cloning the final frame. Audio is trimmed or
    padded with silence. The original file is atomically replaced after ffmpeg
    succeeds.
    """
    data = probe(path)
    streams = _streams(data)
    has_video = any(stream.get("codec_type") == "video" for stream in streams)
    has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
    if not has_video and not has_audio:
        raise MediaSyncError(f"no audio or video streams in {path}")

    seconds = _decimal_seconds(expected_frames, rate_num=rate_num, rate_den=rate_den)
    tmp = path.with_name(f"{path.stem}.syncfix{path.suffix}")
    filter_parts: list[str] = []
    maps: list[str] = []
    args = ["-i", str(path)]
    if has_video:
        filter_parts.append(
            "[0:v]"
            f"fps={rate_num}/{rate_den},"
            f"trim=duration={seconds},setpts=PTS-STARTPTS,"
            f"tpad=stop_mode=clone:stop_duration={seconds},"
            f"trim=duration={seconds},setpts=PTS-STARTPTS[v]"
        )
        maps.extend(["-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p"])
    if has_audio:
        filter_parts.append(
            "[0:a]"
            f"atrim=duration={seconds},asetpts=PTS-STARTPTS,"
            f"apad,atrim=duration={seconds},asetpts=PTS-STARTPTS[a]"
        )
        maps.extend(["-map", "[a]"])
        if path.suffix.lower() == ".wav":
            maps.extend(["-c:a", "pcm_s16le"])
        else:
            maps.extend(["-c:a", "aac"])

    try:
        run_ffmpeg(
            [
                *args,
                "-filter_complex",
                ";".join(filter_parts),
                *maps,
                str(tmp),
            ]
        )
        move(str(tmp), str(path))
    finally:
        tmp.unlink(missing_ok=True)


def _streams(data: Mapping[str, object]) -> list[Mapping[str, object]]:
    raw = data.get("streams")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _video_frame_count(
    stream: Mapping[str, object] | None,
    *,
    rate_num: int,
    rate_den: int,
) -> int | None:
    if stream is None:
        return None
    for key in ("nb_read_frames", "nb_frames"):
        count = _int_value(stream.get(key))
        if count is not None:
            return count

    duration = _duration_seconds(stream)
    if duration is None:
        return None
    stream_rate = (
        _fraction_value(stream.get("avg_frame_rate"))
        or _fraction_value(stream.get("r_frame_rate"))
        or Fraction(rate_num, rate_den)
    )
    return _round_fraction(duration * stream_rate)


def _audio_frame_count(
    stream: Mapping[str, object] | None,
    *,
    rate_num: int,
    rate_den: int,
) -> int | None:
    if stream is None:
        return None
    duration = _duration_seconds(stream)
    if duration is None:
        return None
    return _round_fraction(duration * Fraction(rate_num, rate_den))


def _duration_seconds(stream: Mapping[str, object]) -> Fraction | None:
    duration_ts = _int_value(stream.get("duration_ts"))
    time_base = _fraction_value(stream.get("time_base"))
    if duration_ts is not None and time_base is not None:
        return duration_ts * time_base

    raw_duration = stream.get("duration")
    if isinstance(raw_duration, int):
        return Fraction(raw_duration, 1)
    if isinstance(raw_duration, float):
        return Fraction(str(raw_duration))
    if isinstance(raw_duration, str) and raw_duration and raw_duration != "N/A":
        try:
            return Fraction(raw_duration)
        except ValueError:
            return None
    return None


def _int_value(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value and value != "N/A":
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _fraction_value(value: object) -> Fraction | None:
    if isinstance(value, int):
        return Fraction(value, 1)
    if isinstance(value, float):
        return Fraction(str(value))
    if isinstance(value, str) and value and value != "N/A":
        try:
            return Fraction(value)
        except (ValueError, ZeroDivisionError):
            return None
    return None


def _round_fraction(value: Fraction) -> int:
    if value < 0:
        return -_round_fraction(-value)
    return (value.numerator + value.denominator // 2) // value.denominator


def _decimal_seconds(frames: int, *, rate_num: int, rate_den: int) -> str:
    duration = Fraction(frames * rate_den, rate_num)
    whole = duration.numerator // duration.denominator
    rest = duration.numerator % duration.denominator
    if rest == 0:
        return str(whole)
    scaled = (rest * 1_000_000 + duration.denominator // 2) // duration.denominator
    return f"{whole}.{scaled:06d}".rstrip("0").rstrip(".")
