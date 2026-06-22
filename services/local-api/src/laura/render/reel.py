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
) -> str:
    """Build a comma-joined ffmpeg video-filter string for a reel export.

    Returns an empty string when no filters are requested.

    Filter order:
    1. ``crop=ih*9/16:ih`` + ``scale=1080:1920``  — when *vertical* is True.
    2. Centered top ``drawtext``                   — when *hook_textfile* is set.
    3. Bottom-right ``drawtext``                   — when *disclosure_textfile* is set.

    Args:
        vertical:             Crop and scale to 9:16 (1080 × 1920).
        hook_textfile:        Basename of a UTF-8 file with the top-centre hook text.
        disclosure_textfile:  Basename of a UTF-8 file with the bottom-right disclosure.
        font:                 Resolved fontfile path passed verbatim to drawtext.
    """
    parts: list[str] = []

    if vertical:
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


def resolve_font() -> str:
    """ffmpeg-drawtext-safe font path. LAURA_FONT env overrides; default Windows Arial Bold.

    The Windows drive colon must be escaped as a DOUBLE backslash for the filtergraph
    (proven: ``C\\:/Windows/Fonts/arialbd.ttf`` works, single backslash fails).
    """
    import os

    cand = os.environ.get("LAURA_FONT") or r"C:/Windows/Fonts/arialbd.ttf"
    return cand.replace(":", r"\\:", 1)  # escape only the drive colon
