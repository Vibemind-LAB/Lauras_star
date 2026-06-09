"""Render a list of timeline clip segments into one MP4 via ffmpeg (trim + concat)."""
from __future__ import annotations

from pathlib import Path

from ..ingest.ffmpeg import run_ffmpeg
from .reel import reel_video_chain, resolve_font


def render_clips_mp4(
    clips: list[tuple[Path, int, int]],
    dest: Path,
    *,
    rate_num: int,
    rate_den: int,
    music_tracks: list[tuple[Path, int, int, int]] | None = None,
    vertical: bool = False,
    hook_text: str | None = None,
    disclosure_text: str | None = None,
) -> None:
    """Trim each clip by frame range (end-exclusive) and concat into one MP4.

    ``music_tracks`` is an optional list of
    ``(path, seq_in_frame, seq_out_frame_exclusive, gain_percent)`` tuples.
    Each track is mixed into the output audio at the specified position:

    * ``seq_in_frame`` / ``seq_out_frame_exclusive`` define the window inside
      the assembled sequence where the track should be audible (integer frames,
      end-exclusive — consistent with the frame-accuracy invariant).
    * ``gain_percent`` is applied as a ``volume`` filter (100 = unity gain).

    With no tracks (or ``music_tracks=None``) the output is video-only
    (unchanged — backward compatible).
    """
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
    reel = reel_video_chain(
        vertical=vertical,
        hook_text=hook_text,
        disclosure_text=disclosure_text,
        font=resolve_font(),
    )
    concat_out = "[vcat]" if reel else "[out]"
    parts = ";".join(filt) + f";{concat_in}concat=n={len(clips)}:v=1:a=0{concat_out}"
    if reel:
        parts += f";[vcat]{reel}[out]"

    audio_maps: list[str] = []
    tracks = music_tracks or []
    if tracks:
        n_vid = len(clips)
        audio_labels: list[str] = []
        for i, (music_path, seq_in, seq_out, gain_percent) in enumerate(tracks):
            idx = n_vid + i
            inputs += ["-i", str(music_path)]
            duration = (seq_out - seq_in) * rate_den / rate_num
            start_ms = seq_in * rate_den / rate_num * 1000
            # Build the per-track filter chain:
            #   volume  — scale the gain
            #   atrim   — cut the track to at most the scene window duration
            #   adelay  — push it to the right start position in the mix
            #   asetpts — reset PTS after the trim so adelay gets a clean base
            label = f"[a{i}]"
            parts += (
                f";[{idx}:a]"
                f"volume={gain_percent / 100},"
                f"atrim=0:{duration},"
                f"asetpts=PTS-STARTPTS,"
                f"adelay={start_ms:.6f}|{start_ms:.6f}"
                f"{label}"
            )
            audio_labels.append(label)

        if len(audio_labels) == 1:
            # Single track — route directly to [aout] (no amix needed).
            parts += f";{audio_labels[0]}anull[aout]"
        else:
            joined = "".join(audio_labels)
            parts += f";{joined}amix=inputs={len(audio_labels)}:normalize=0[aout]"

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
