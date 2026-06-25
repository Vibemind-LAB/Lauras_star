"""Pure ffmpeg video-filter chain builder for social-media reel exports.

No I/O, no ffmpeg calls — only string manipulation. The returned string is
ready to be passed as the value of an ffmpeg ``-vf`` / ``-filter_complex`` option.

Overlay text is supplied via drawtext ``textfile=`` (a *basename*, resolved
against ffmpeg's working directory), NOT inline ``text='…'``. This is a
deliberate correctness choice: arbitrary user text (apostrophes, colons,
commas, ``%``) cannot be escaped reliably for inline drawtext on Windows, and
inline text is also a filtergraph-injection vector. A file read verbatim by
ffmpeg removes both problems. The caller writes the file and passes its
basename (see ``render_clips_mp4``). The basename must be free of ffmpeg filter
metacharacters — callers control it, so we keep this builder pure.
"""
from __future__ import annotations


def reel_video_chain(
    *,
    vertical: bool,
    hook_textfile: str | None = None,
    disclosure_textfile: str | None = None,
    font: str,
    reel_fit: bool = False,
) -> str:
    """Build a comma-joined ffmpeg video-filter string for a reel export.

    Returns an empty string when no filters are requested.

    Filter order:
    1. Reframe to 9:16 (1080 × 1920)              — when *vertical* is True.
       * Default (``reel_fit=False``): center-crop (``crop=ih*9/16:ih, scale=1080:1920``).
         Fills the frame but may cut off content that is off-center.
       * Fit mode (``reel_fit=True``): scale-to-fit with letterbox padding
         (``scale=1080:-2, pad=1080:1920:0:(1920-ih)/2:black``).  The entire source
         frame is kept visible; dead space is filled with black bars.  Use for screencasts
         or any content where center-crop would slice off readable text or UI.
    2. Centered top ``drawtext``                   — when *hook_textfile* is set.
    3. Bottom-right ``drawtext``                   — when *disclosure_textfile* is set.

    Note: this function does NOT handle ``reel_blur_fill`` mode.  That mode requires a
    split/overlay sub-graph that cannot be expressed as a simple comma chain.  See
    :func:`reel_blur_fill_graph` for the blur-fill filtergraph fragment and the caller
    (:func:`laura.render.mp4.render_clips_mp4`) for how it is wired in.

    Args:
        vertical:             Reframe to 9:16 (1080 × 1920).
        hook_textfile:        Basename of a UTF-8 file with the top-centre hook text.
        disclosure_textfile:  Basename of a UTF-8 file with the bottom-right disclosure.
        font:                 Resolved fontfile path passed verbatim to drawtext.
        reel_fit:             When True (and *vertical* is True) use letterbox fit instead
                              of center-crop.  Default ``False`` — existing behavior.
    """
    parts: list[str] = []

    if vertical:
        if reel_fit:
            # Scale source to fit within 1080 wide (preserve aspect ratio), then pad
            # top and bottom with black to reach full 1920 height.
            # force_original_aspect_ratio=decrease ensures we never upscale beyond 1080
            # wide, and -2 keeps the height divisible by 2 (required by libx264).
            parts.append(
                "scale=1080:-2:force_original_aspect_ratio=decrease"
            )
            parts.append("pad=1080:1920:0:(1920-ih)/2:black")
        else:
            parts.append("crop=ih*9/16:ih")
            parts.append("scale=1080:1920")

    if hook_textfile:
        parts.append(
            f"drawtext=fontfile={font}:textfile={hook_textfile}"
            ":x=(w-text_w)/2:y=120:fontsize=64:fontcolor=white"
            ":box=1:boxcolor=black@0.5"
        )

    if disclosure_textfile:
        parts.append(
            f"drawtext=fontfile={font}:textfile={disclosure_textfile}"
            ":x=w-text_w-24:y=h-text_h-24:fontsize=30:fontcolor=white"
            ":box=1:boxcolor=black@0.45"
        )

    return ",".join(parts)


def reel_blur_fill_graph(in_label: str, out_label: str) -> str:
    """Return the semicolon-joined ffmpeg filtergraph fragment for blurred-background fill.

    Produces a 1080 × 1920 (9:16) output from an arbitrary-aspect-ratio input by:
      1. Splitting the input into two copies (``split``).
      2. Background copy: scale-to-cover 1080×1920 (``force_original_aspect_ratio=increase``)
         + exact ``crop=1080:1920`` + heavy Gaussian blur (``boxblur=20:2``).
      3. Foreground copy: scale-to-fit 1080×1920 (``force_original_aspect_ratio=decrease``),
         preserving every pixel of the source frame.
      4. Overlay foreground centred on blurred background (``overlay=(W-w)/2:(H-h)/2``).

    This is the industry-standard "blurred-background fill" used by Instagram Reels /
    TikTok for landscape-to-vertical conversion: no black bars, no cropped content.

    The fragment is a *sub-graph* with internal labels — it must be embedded inside a
    larger ``-filter_complex`` string (separated by ``;``).  The caller connects
    ``in_label`` from the preceding stage and reads ``out_label`` for the next stage
    (e.g. drawtext / ass captions).

    Drawtext/ASS caption filters must be applied AFTER this graph (on the composited
    1080×1920 stream) — the caller is responsible for chaining them.

    Example (single-clip hard-cut path):
        ``[vcat]split=2[_bbg][_bfg];[_bbg]scale=...,boxblur=20:2[_bbl];
        [_bfg]scale=...[_bfl];[_bbl][_bfl]overlay=(W-w)/2:(H-h)/2[out]``

    Args:
        in_label:  The labeled input stream (e.g. ``"[vcat]"``).
        out_label: The labeled output stream (e.g. ``"[out]"``).
    """
    # Internal scratch labels — unique enough within any single render graph.
    bg_split = "[_rbbg]"
    fg_split = "[_rbfg]"
    bg_blurred = "[_rbbl]"
    fg_scaled = "[_rbfl]"

    parts = [
        # 1. Split input into background and foreground copies.
        f"{in_label}split=2{bg_split}{fg_split}",
        # 2. Background: scale to COVER 1080×1920 (may overshoot), crop exactly to canvas,
        #    then apply a strong Gaussian blur so it reads as a soft colour fill.
        (
            f"{bg_split}scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,"
            f"boxblur=20:2{bg_blurred}"
        ),
        # 3. Foreground: scale to FIT within 1080×1920 (never overflows canvas).
        (
            f"{fg_split}scale=1080:1920:force_original_aspect_ratio=decrease{fg_scaled}"
        ),
        # 4. Overlay foreground centred on blurred background.
        f"{bg_blurred}{fg_scaled}overlay=(W-w)/2:(H-h)/2{out_label}",
    ]
    return ";".join(parts)


def resolve_font() -> str:
    """ffmpeg-drawtext-safe font path. LAURA_FONT env overrides; default Windows Arial Bold.

    The Windows drive colon must be escaped as a DOUBLE backslash for the filtergraph
    (proven: ``C\\:/Windows/Fonts/arialbd.ttf`` works, single backslash fails).
    """
    import os

    cand = os.environ.get("LAURA_FONT") or r"C:/Windows/Fonts/arialbd.ttf"
    return cand.replace(":", r"\\:", 1)  # escape only the drive colon
