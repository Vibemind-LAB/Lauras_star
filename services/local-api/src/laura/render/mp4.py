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
) -> None:
    """clips = [(source, in_frame, out_frame_exclusive)]. Trims each by frame range and
    concatenates them in order via the concat filter (re-encode -> robust across mixed sources)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fps = rate_num / rate_den
    inputs: list[str] = []
    filt: list[str] = []
    for i, (src, fin, fout) in enumerate(clips):
        ss = fin / fps
        to = fout / fps
        inputs += ["-i", str(src)]
        filt.append(f"[{i}:v]trim=start={ss:.4f}:end={to:.4f},setpts=PTS-STARTPTS[v{i}]")
    concat_in = "".join(f"[v{i}]" for i in range(len(clips)))
    filter_complex = ";".join(filt) + f";{concat_in}concat=n={len(clips)}:v=1:a=0[out]"
    run_ffmpeg([
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-r", f"{rate_num}/{rate_den}",
        str(dest),
    ])
