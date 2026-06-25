"""Render a list of timeline clip segments into one MP4 via ffmpeg (trim + concat).

Hard cuts use a single ``concat`` (byte-identical to the pre-crossfade renderer). When any
boundary carries a real ``crossfade`` transition the video/audio assembly switches to a
pairwise fold: ``xfade``/``acrossfade`` with a **reserve overlap** (clip A is extended by the
transition duration into its post-cut source handles), so the assembled length stays exactly
the sum of clip content lengths (sync invariant) and B loses no content.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ..ingest.ffmpeg import FFmpegError, probe, run_ffmpeg
from .audio import AudioOverlay
from .reel import reel_video_chain, resolve_font

logger = logging.getLogger(__name__)

_DEFAULT_DISCLOSURE: str = "KI · synthetisch"


def _effective_disclosure(disclosure_text: str | None) -> str | None:
    """Map disclosure_text to the effective value for the renderer.

    - ``None``        → ``None``  (no overlay; plain export, not a reel)
    - ``""`` / blank  → ``_DEFAULT_DISCLOSURE``  (explicit blank cannot suppress a
                        requested disclosure — EU AI Act enforcement for the reel path)
    - non-blank str   → stripped text
    """
    if disclosure_text is None:
        return None
    text = disclosure_text.strip()
    return text if text else _DEFAULT_DISCLOSURE


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


def _source_video_frames(path: Path) -> int:
    """Total frame count of ``path``'s first video stream (0 when unknown).

    Used to decide whether a clip has post-cut handles for a crossfade reserve. Prefers the
    container's ``nb_frames``; falls back to ``duration * avg_frame_rate``. Returns 0 when neither
    is available so the caller degrades the crossfade to a hard cut rather than guessing."""
    try:
        data = probe(path)
    except FFmpegError:
        return 0
    for stream in data.get("streams", []):
        if not isinstance(stream, dict) or stream.get("codec_type") != "video":
            continue
        nb = stream.get("nb_frames")
        if nb not in (None, "N/A"):
            try:
                return int(str(nb))
            except (TypeError, ValueError):
                pass
        dur = stream.get("duration") or data.get("format", {}).get("duration")
        rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1"
        try:
            num, den = (int(x) for x in str(rate).split("/"))
            if dur is not None and den:
                return int(float(dur) * num / den)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return 0


def _crossfade_durations(
    clips: list[tuple[Path, int, int]],
    video_transitions: list[VideoTransition] | None,
    *,
    source_frames: object | None = None,
) -> list[int]:
    """Per-boundary crossfade duration in frames (``0`` = hard cut), length ``len(clips) - 1``.

    Only ``kind == "crossfade"`` boundaries produce a non-zero entry. Each is clamped to the
    reserve actually available in clip A's source (frames after its out-point); when no reserve
    exists the boundary degrades to a hard cut (logged). ``source_frames`` is the frame-count
    function (defaults to :func:`_source_video_frames`); inject a stub in unit tests.
    """
    n = len(clips)
    out = [0] * max(0, n - 1)
    if not video_transitions or n < 2:
        return out
    frames_of = _source_video_frames if source_frames is None else source_frames
    by_boundary = {
        t.boundary_frame: t.duration_frames
        for t in video_transitions
        if t.kind == "crossfade" and t.duration_frames > 0
    }
    cum = 0
    for i in range(n - 1):
        cum += clips[i][2] - clips[i][1]  # cumulative content length up to end of clip i
        want = by_boundary.get(cum)
        if not want:
            continue
        avail = frames_of(clips[i][0]) - clips[i][2]  # type: ignore[operator]
        eff = min(int(want), max(0, int(avail)))
        out[i] = eff
        if eff < want:
            logger.warning(
                "crossfade at boundary %s shortened %s->%s frames (reserve=%s)",
                cum, want, eff, avail,
            )
    return out


def _xfade_base_graph(
    clips: list[tuple[Path, int, int]],
    *,
    xdur: list[int],
    audio_flags: list[bool],
    has_base_audio: bool,
    rate_num: int,
    rate_den: int,
) -> tuple[list[str], str, str | None]:
    """Build the trim + pairwise xfade/concat filter parts for the crossfade path.

    ``xdur[i]`` is the crossfade duration (frames) for the boundary after clip ``i`` (0 = hard).
    A crossfade clip is trimmed ``xdur[i]`` frames past its out-point (reserve overlap). Returns
    ``(parts, video_label, audio_label_or_None)``. Pure — no IO, fully unit-testable."""
    n = len(clips)
    parts: list[str] = []
    lens = [fout - fin for _, fin, fout in clips]
    for i, (_src, fin, fout) in enumerate(clips):
        reserve = xdur[i] if i < n - 1 else 0
        end = fout + reserve
        parts.append(
            f"[{i}:v]trim=start_frame={fin}:end_frame={end},setpts=PTS-STARTPTS[v{i}]"
        )
        if has_base_audio:
            if audio_flags[i]:
                start = _seconds(fin * rate_den / rate_num)
                end_s = _seconds(end * rate_den / rate_num)
                parts.append(
                    f"[{i}:a]atrim=start={start}:end={end_s},asetpts=PTS-STARTPTS[ba{i}]"
                )
            else:
                dur = _seconds((end - fin) * rate_den / rate_num)
                parts.append(
                    "anullsrc=channel_layout=stereo:sample_rate=48000,"
                    f"atrim=duration={dur},asetpts=PTS-STARTPTS[ba{i}]"
                )
    v_acc = "v0"
    a_acc: str | None = "ba0" if has_base_audio else None
    content = lens[0]
    for i in range(1, n):
        d = xdur[i - 1]
        if d > 0:
            offset = _seconds(content * rate_den / rate_num)
            dd = _seconds(d * rate_den / rate_num)
            nxt_v = f"xv{i}"
            parts.append(
                f"[{v_acc}][v{i}]xfade=transition=fade:duration={dd}:offset={offset}[{nxt_v}]"
            )
            v_acc = nxt_v
            if has_base_audio:
                nxt_a = f"xa{i}"
                parts.append(f"[{a_acc}][ba{i}]acrossfade=d={dd}[{nxt_a}]")
                a_acc = nxt_a
        else:
            nxt_v = f"cv{i}"
            parts.append(f"[{v_acc}][v{i}]concat=n=2:v=1:a=0[{nxt_v}]")
            v_acc = nxt_v
            if has_base_audio:
                nxt_a = f"ca{i}"
                parts.append(f"[{a_acc}][ba{i}]concat=n=2:v=0:a=1[{nxt_a}]")
                a_acc = nxt_a
        content += lens[i]
    return parts, v_acc, a_acc


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
    loudnorm: bool = False,
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

    ``loudnorm`` (default ``False``) appends an EBU R128 ``loudnorm`` stage
    (``I=-14:TP=-1.5:LRA=11`` — the common social-media loudness target) as the
    final step of the audio graph when the output has audio.  With it off (or no
    audio) the audio graph is byte-identical to the pre-loudnorm behaviour.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    audio_flags = [_source_has_audio(src) for src, _, _ in clips]
    has_base_audio = any(audio_flags)
    for src, _fin, _fout in clips:
        inputs += ["-i", str(src)]
    # Per-boundary crossfade durations (frames; 0 = hard cut). Any non-zero entry switches the
    # video/audio assembly to the xfade/acrossfade fold; otherwise the byte-identical concat path
    # (which also renders the dip-to-black ``fade`` transition via _video_transition_chain).
    xdur = _crossfade_durations(clips, video_transitions)
    has_crossfade = any(d > 0 for d in xdur)

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
    disclosure = _effective_disclosure(disclosure_text)
    if disclosure is not None:
        disc_path = dest.parent / f"{dest.stem}.reel_disclosure.txt"
        disc_path.write_text(disclosure, encoding="utf-8")
        reel_files.append(disc_path)
        disc_tf = disc_path.name

    ass_basename: str | None = None
    if caption_ass:
        ass_path = dest.parent / f"{dest.stem}.reel_caption.ass"
        ass_path.write_text(caption_ass, encoding="utf-8")
        reel_files.append(ass_path)
        ass_basename = ass_path.name

    try:
        reel = reel_video_chain(
            vertical=vertical,
            hook_textfile=hook_tf,
            disclosure_textfile=disc_tf,
            font=resolve_font(),
        )
        caption_filter = f"ass={ass_basename}" if ass_basename else ""
        if has_crossfade:
            # Real cross-dissolve: pairwise xfade/acrossfade fold with reserve overlap.
            fold_parts, v_label, a_label = _xfade_base_graph(
                clips,
                xdur=xdur,
                audio_flags=audio_flags,
                has_base_audio=has_base_audio,
                rate_num=rate_num,
                rate_den=rate_den,
            )
            post = ",".join(p for p in (reel, caption_filter) if p)
            fold_parts.append(
                f"[{v_label}]{post}[out]" if post else f"[{v_label}]null[out]"
            )
            if has_base_audio and a_label is not None:
                fold_parts.append(f"[{a_label}]anull[abase]")
            parts = ";".join(fold_parts)
        else:
            # Byte-identical concat path (hard cuts + optional dip-to-black ``fade``).
            filt: list[str] = []
            for i, (_src, fin, fout) in enumerate(clips):
                # Frame-exact, end-exclusive: trim keeps input frames [start_frame, end_frame).
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
            transition_chain = _video_transition_chain(
                video_transitions, rate_num=rate_num, rate_den=rate_den
            )
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

        # Assemble the final audio graph. With loudnorm on, the mixed/single track is folded
        # one extra stage through an EBU R128 loudnorm filter (social standard -14 LUFS) before
        # the [aout] map; with it off the graph is byte-identical to the pre-loudnorm behaviour.
        apply_loudnorm = loudnorm and bool(audio_labels)
        final_label = "[aout_pre]" if apply_loudnorm else "[aout]"
        if len(audio_labels) == 1:
            # Single track — route directly to the final label (no amix needed).
            parts += f";{audio_labels[0]}anull{final_label}"
        elif len(audio_labels) > 1:
            joined = "".join(audio_labels)
            parts += f";{joined}amix=inputs={len(audio_labels)}:normalize=0{final_label}"

        if apply_loudnorm:
            # EBU R128 two-pass-style single-pass loudnorm; I/TP/LRA are the common social target.
            parts += f";{final_label}loudnorm=I=-14:TP=-1.5:LRA=11[aout]"

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
