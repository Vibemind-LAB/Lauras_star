"""Pure ffmpeg video-filter chain builder for social-media reel exports.

No I/O, no ffmpeg calls — only string manipulation. The returned string is
ready to be passed as the value of an ffmpeg ``-vf`` option.
"""
from __future__ import annotations


def _escape_drawtext(s: str) -> str:
    """Escape special drawtext characters in *s*.

    Order matters: backslash must be escaped first so that the backslashes
    introduced by subsequent escapes are not re-escaped.

    Characters escaped: ``\\``, ``:``, ``'``, ``%``.
    Newlines are replaced with a single space.
    """
    s = s.replace("\\", "\\\\")
    s = s.replace(":", "\\:")
    s = s.replace("'", "\\'")
    s = s.replace("%", "\\%")
    s = s.replace("\n", " ")
    return s


def reel_video_chain(
    *,
    vertical: bool,
    hook_text: str | None,
    disclosure_text: str | None,
    font: str,
) -> str:
    """Build a comma-joined ffmpeg video-filter string for a reel export.

    Returns an empty string when no filters are requested.

    Filter order:
    1. ``crop=ih*9/16:ih`` + ``scale=1080:1920``  — when *vertical* is True.
    2. Centered top ``drawtext``                   — when *hook_text* is non-empty.
    3. Bottom-right ``drawtext``                   — when *disclosure_text* is non-empty.

    Args:
        vertical:         Crop and scale to 9:16 (1080 × 1920).
        hook_text:        Optional overlay text at the top-centre of the frame.
        disclosure_text:  Optional small overlay text at the bottom-right.
        font:             Resolved fontfile path passed verbatim to drawtext.
    """
    parts: list[str] = []

    if vertical:
        parts.append("crop=ih*9/16:ih")
        parts.append("scale=1080:1920")

    if hook_text:
        esc = _escape_drawtext(hook_text)
        parts.append(
            f"drawtext=fontfile={font}:text='{esc}'"
            ":x=(w-text_w)/2:y=120:fontsize=64:fontcolor=white"
            ":box=1:boxcolor=black@0.5"
        )

    if disclosure_text:
        esc = _escape_drawtext(disclosure_text)
        parts.append(
            f"drawtext=fontfile={font}:text='{esc}'"
            ":x=w-text_w-24:y=h-text_h-24:fontsize=30:fontcolor=white"
            ":box=1:boxcolor=black@0.45"
        )

    return ",".join(parts)
