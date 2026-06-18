"""Render a list of timeline clip segments into one MP4 via ffmpeg (trim + concat)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..ingest.ffmpeg import FFmpegError, probe, run_ffmpeg
from .audio import AudioOverlay
from .reel import reel_video_chain, resolve_font


@dataclass(frozen=True)
class VideoTransition:
    kind: str
    boundary_frame: int
    duration_frames: int


def _seconds(value: float) -> str:
    if abs(value) < 0.000001:
        return "0"
    rounded = round(value, 6)
    if float(rounded).is_integer():
        return str(int(rounded))
    return str(rounded)


def _music_overlays(
    music_tracks: list[tuple[Path, int, int, int]] | None,
) -> list[AudioOverlay]:
    return [
        AudioOverlay(
            path=path,
            seq_in_frame=seq_in,
            seq_out_frame_exclusive=seq_out,
            gain_percent=gain_percent,
        )
        for path, seq_in, seq_out, gain_percent in (music_tracks or [])
    ]


def _source_has_audio(path: Path) -> bool:
    try:
        data = probe(path)
    except FFmpegError:
        return False
    streams = data.get("streams", [])
    if not isinstance(streams, list):
        return False
    return any(
        isinstance(stream, dict) and stream.get("codec_type") == "audio"
        for stream in streams
    )


def _video_transition_chain(
    video_transitions: list[VideoTransition] | None,
    *,
    rate_num: int,
    rate_den: int,
) -> str:
    if not video_transitions:
        return ""
    filters: list[str] = []
    for transition in video_transitions:
        if transition.kind == "hard" or transition.duration_frames <= 0:
            continue
        duration = transition.duration_frames * rate_den / rate_num
        boundary = transition.boundary_frame * rate_den / rate_num
        fade_out_start = max(0.0, boundary - duration)
        filters.append(
            f"fade=t=out:st={_seconds(fade_out_start)}:d={_seconds(duration)}"
        )
        filters.append(
            f"fade=t=in:st={_seconds(boundary)}:d={_seconds(duration)}"
        )
    return ",".join(filters)


def render_clips_mp4(
    clips: list[tuple[Path, int, int]],
    dest: Path,
    *,
    rate_num: int,
    rate_den: int,
    music_tracks: list[tuple[Path, int, int, int]] | None = None,
    audio_overlays: list[AudioOverlay] | None = None,
    vertical: bool = False,
    hook_text: str | None = None,
    disclosure_text: str | None = None,
    caption_ass: str | None = None,
    video_transitions: list[VideoTransition] | None = None,
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

    ``audio_overlays`` is the structured v2 equivalent for sequence-level audio
    clips. It adds ``asset_in_frame`` and simple fade-in/out frame ranges while
    preserving the same end-exclusive sequence placement.

    ``caption_ass`` is an optional complete ASS document string (as returned by
    ``build_ass``).  When set, the subtitles are burned into the output using
    the ffmpeg ``ass=`` filter.  The file is written next to *dest*, added to
    the ``reel_files`` cleanup list, and removed after the render regardless of
    success or failure.  Defaults to ``None`` (no captions, byte-identical to
    the pre-R1.2 behaviour).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    filt: list[str] = []
    audio_flags = [_source_has_audio(src) for src, _, _ in clips]
    has_base_audio = any(audio_flags)
    for i, (src, fin, fout) in enumerate(clips):
        # Frame-exact, end-exclusive: trim keeps input frames [start_frame, end_frame).
        # Integer frames only — never float seconds (frame-accuracy invariant).
        inputs += ["-i", str(src)]
        filt.append(
            f"[{i}:v]trim=start_frame={fin}:end_frame={fout},setpts=PTS-STARTPTS[v{i}]"
        )
        if has_base_audio:
            start = fin * rate_den / rate_num
            end = fout * rate_den / rate_num
            duration = (fout - fin) * rate_den / rate_num
            if audio_flags[i]:
                filt.append(
                    f"[{i}:a]atrim=start={_seconds(start)}:end={_seconds(end)},"
                    f"asetpts=PTS-STARTPTS[ba{i}]"
                )
            else:
                filt.append(
                    "anullsrc=channel_layout=stereo:sample_rate=48000,"
                    f"atrim=duration={_seconds(duration)},asetpts=PTS-STARTPTS[ba{i}]"
                )
    concat_in = (
        "".join(f"[v{i}][ba{i}]" for i in range(len(clips)))
        if has_base_audio
        else "".join(f"[v{i}]" for i in range(len(clips)))
    )

    # Reel overlay text goes through drawtext ``textfile=`` (basename, resolved via
    # ffmpeg's cwd), NOT inline ``text='…'`` — inline text cannot be escaped reliably
    # for arbitrary user input on Windows and is a filtergraph-injection vector.
    # Write the UTF-8 files next to dest, reference by basename, run ffmpeg with
    # cwd=dest.parent, and always clean the files up afterwards.
    reel_files: list[Path] = []
    hook_tf: str | None = None
    disc_tf: str | None = None
    if hook_text:
        hook_path = dest.parent / f"{dest.stem}.reel_hook.txt"
        hook_path.write_text(hook_text, encoding="utf-8")
        reel_files.append(hook_path)
        hook_tf = hook_path.name
    if disclosure_text:
        disc_path = dest.parent / f"{dest.stem}.reel_disclosure.txt"
        disc_path.write_text(disclosure_text, encoding="utf-8")
        reel_files.append(disc_path)
        disc_tf = disc_path.name

    ass_basename: str | None = None
    if caption_ass:
        ass_path = dest.parent / f"{dest.stem}.reel_caption.ass"
        ass_path.write_text(caption_ass, encoding="utf-8")
        reel_files.append(ass_path)
        ass_basename = ass_path.name

    try:
        transition_chain = _video_transition_chain(
            video_transitions,
            rate_num=rate_num,
            rate_den=rate_den,
        )
        reel = reel_video_chain(
            vertical=vertical,
            hook_textfile=hook_tf,
            disclosure_textfile=disc_tf,
            font=resolve_font(),
        )
        caption_filter = f"ass={ass_basename}" if ass_basename else ""
        post = ",".join(p for p in (transition_chain, reel, caption_filter) if p)
        concat_out = "[vcat]" if post else "[out]"
        if has_base_audio:
            parts = (
                ";".join(filt)
                + f";{concat_in}concat=n={len(clips)}:v=1:a=1{concat_out}[abase]"
            )
        else:
            parts = ";".join(filt) + f";{concat_in}concat=n={len(clips)}:v=1:a=0{concat_out}"
        if post:
            parts += f";[vcat]{post}[out]"

        audio_maps: list[str] = []
        overlays = [*_music_overlays(music_tracks), *(audio_overlays or [])]
        audio_labels: list[str] = []
        if has_base_audio:
            base_label = "[abase]"
            for i, overlay in enumerate(overlays):
                ducking = (
                    0
                    if overlay.mix_mode in {"replace_original", "mute_original"}
                    else overlay.ducking_percent
                )
                if ducking >= 100:
                    continue
                next_label = f"[aduck{i}]"
                duck_start = _seconds(overlay.seq_in_frame * rate_den / rate_num)
                duck_end = _seconds(overlay.seq_out_frame_exclusive * rate_den / rate_num)
                parts += (
                    f";{base_label}volume=enable='between(t,{duck_start},{duck_end})'"
                    f":volume={ducking / 100}{next_label}"
                )
                base_label = next_label
            audio_labels.append(base_label)

        if overlays:
            n_vid = len(clips)
            for i, overlay in enumerate(overlays):
                duration_frames = overlay.seq_out_frame_exclusive - overlay.seq_in_frame
                if duration_frames <= 0:
                    continue
                idx = n_vid + i
                inputs += ["-i", str(overlay.path)]
                asset_start = overlay.asset_in_frame * rate_den / rate_num
                duration = duration_frames * rate_den / rate_num
                start_ms = overlay.seq_in_frame * rate_den / rate_num * 1000
                fade_in = overlay.fade_in_frames * rate_den / rate_num
                fade_out = overlay.fade_out_frames * rate_den / rate_num
                # Build the per-track filter chain:
                #   volume  — scale the gain
                #   atrim   — cut the track to at most the scene window duration
                #   afade   — optional simple clip fades
                #   adelay  — push it to the right start position in the mix
                #   asetpts — reset PTS after the trim so adelay gets a clean base
                label = f"[a{i}]"
                chain = [
                    f"volume={overlay.gain_percent / 100}",
                    f"atrim=start={_seconds(asset_start)}:duration={_seconds(duration)}",
                    "asetpts=PTS-STARTPTS",
                ]
                if fade_in > 0:
                    chain.append(f"afade=t=in:st=0:d={_seconds(fade_in)}")
                if fade_out > 0:
                    fade_start = max(0.0, duration - fade_out)
                    chain.append(
                        f"afade=t=out:st={_seconds(fade_start)}:d={_seconds(fade_out)}"
                    )
                chain.append(f"adelay={start_ms:.6f}|{start_ms:.6f}")
                parts += f";[{idx}:a]" + ",".join(chain) + label
                audio_labels.append(label)

        if len(audio_labels) == 1:
            # Single track — route directly to [aout] (no amix needed).
            parts += f";{audio_labels[0]}anull[aout]"
        elif len(audio_labels) > 1:
            joined = "".join(audio_labels)
            parts += f";{joined}amix=inputs={len(audio_labels)}:normalize=0[aout]"

        if audio_labels:
            audio_maps = ["-map", "[aout]", "-c:a", "aac"]

        ff_args = [
            *inputs,
            "-filter_complex", parts,
            "-map", "[out]",
            *audio_maps,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-r", f"{rate_num}/{rate_den}",
            str(dest),
        ]
        # cwd is only needed so drawtext textfile= basenames resolve. Pass it only
        # on the reel path so the plain concat call stays byte-identical (and any
        # run_ffmpeg monkeypatch without a cwd kwarg keeps working).
        if reel_files:
            run_ffmpeg(ff_args, cwd=dest.parent)
        else:
            run_ffmpeg(ff_args)
    finally:
        for reel_file in reel_files:
            reel_file.unlink(missing_ok=True)
