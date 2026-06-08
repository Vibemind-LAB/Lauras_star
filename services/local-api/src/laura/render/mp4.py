"""Render a list of timeline clip segments into one MP4 via ffmpeg (trim + concat)."""
from __future__ import annotations

from pathlib import Path

from ..ingest.ffmpeg import run_ffmpeg


def render_clips_mp4(
    clips: list[tuple[Path, int, int]],
    dest: Path,
    *,
    rate_num: int,
    rate_den: int,
    music: tuple[Path, int] | None = None,
) -> None:
    """Trim each clip by frame range (end-exclusive) and concat into one MP4.

    With ``music`` (path, gain_percent) a single audio track is mixed at that
    gain, trimmed to the video length. Without ``music`` the output is
    video-only (unchanged — backward compatible)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    filt: list[str] = []
    for i, (src, fin, fout) in enumerate(clips):
        # Frame-exact, end-exclusive: trim keeps input frames [start_frame, end_frame).
        # Integer frames only — never float seconds (frame-accuracy invariant).
        inputs += ["-i", str(src)]
        filt.append(
            f"[{i}:v]trim=start_frame={fin}:end_frame={fout},setpts=PTS-STARTPTS[v{i}]"
        )
    concat_in = "".join(f"[v{i}]" for i in range(len(clips)))
    parts = ";".join(filt) + f";{concat_in}concat=n={len(clips)}:v=1:a=0[out]"
    audio_maps: list[str] = []
    if music is not None:
        music_path, gain_percent = music
        total = sum(fout - fin for _, fin, fout in clips)
        dur = total * rate_den / rate_num
        inputs += ["-i", str(music_path)]  # input index == len(clips)
        parts += (
            f";[{len(clips)}:a]volume={gain_percent / 100},"
            f"atrim=0:{dur},asetpts=PTS-STARTPTS[aout]"
        )
        audio_maps = ["-map", "[aout]", "-c:a", "aac"]
    run_ffmpeg([
        *inputs,
        "-filter_complex", parts,
        "-map", "[out]",
        *audio_maps,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-r", f"{rate_num}/{rate_den}",
        str(dest),
    ])
