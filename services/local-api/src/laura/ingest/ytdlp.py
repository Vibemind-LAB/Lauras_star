"""yt-dlp based ingest for "site" URLs (YouTube/Drive/Vimeo/...) — optional extra ``[fetch]``.

The direct-HTTP downloader (``download.py``) only handles links that point at a real
media file. Pages like a YouTube watch URL need extraction: yt-dlp resolves the page to
the actual media stream(s), downloads them, and (for adaptive formats) merges the best
video+audio with ffmpeg. yt-dlp is invoked in-process but kept an OPTIONAL dependency —
if absent, only site URLs are unavailable; direct links still work.

ffmpeg is reused from ``ffmpeg.py`` (same ``LAURA_FFMPEG`` resolution): yt-dlp needs it on
PATH (or pointed at via ``ffmpeg_location``) to merge separate video/audio streams.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

# Hosts whose URLs are pages/players, not direct media files: they always need
# yt-dlp extraction. Matched against the registrable host suffix, so e.g.
# ``www.youtube.com`` and ``m.youtube.com`` both match ``youtube.com``.
_SITE_HOST_SUFFIXES: tuple[str, ...] = (
    "youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "drive.google.com",
    "docs.google.com",
    "vimeo.com",
    "dailymotion.com",
    "dai.ly",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "facebook.com",
    "fb.watch",
    "instagram.com",
    "twitch.tv",
    "reddit.com",
    "soundcloud.com",
    "streamable.com",
    "bilibili.com",
)

# Direct-media file extensions: a URL whose path ends in one of these is a real file
# the fast direct-HTTP downloader can handle — no extraction needed.
_MEDIA_SUFFIXES: frozenset[str] = frozenset({
    ".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".flv", ".wmv", ".mpg",
    ".mpeg", ".ts", ".m2ts", ".mxf", ".ogv", ".3gp",
    ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma",
})


def ytdlp_available() -> bool:
    """True if the optional ``yt_dlp`` package is importable (extra ``[fetch]``)."""
    try:
        import yt_dlp  # noqa: F401
    except Exception:
        return False
    return True


def _host_matches_site(host: str) -> bool:
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return any(host == s or host.endswith("." + s) for s in _SITE_HOST_SUFFIXES)


def needs_ytdlp(url: str) -> bool:
    """True if ``url`` needs yt-dlp extraction rather than a direct download.

    A URL needs extraction when its host is a known media *site* (YouTube/Drive/...)
    OR when its path has no recognised direct-media file extension (a page/player).
    Direct media links (``.mp4``/``.mov``/``.mkv``/...) return False so the fast
    segmented HTTP downloader keeps handling them.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    # Only http(s) URLs are candidates; magnet/ftp/torrent are the aria2 engine's job.
    if scheme not in ("http", "https"):
        return False
    if _host_matches_site(parts.netloc):
        return True
    suffix = Path(parts.path).suffix.lower()
    return suffix not in _MEDIA_SUFFIXES


def _ffmpeg_dir() -> str | None:
    """Directory holding the ffmpeg binary, derived from ``LAURA_FFMPEG`` if set.

    yt-dlp's ``ffmpeg_location`` accepts a directory (it finds ffmpeg/ffprobe inside).
    Returns None when ``LAURA_FFMPEG`` is unset → yt-dlp falls back to PATH.
    """
    binary = os.environ.get("LAURA_FFMPEG")
    if not binary:
        return None
    parent = Path(binary).expanduser().parent
    return str(parent) if str(parent) not in ("", ".") else None


def download_via_ytdlp(
    url: str,
    dest_dir: Path | str,
    *,
    on_progress: Callable[[int, int | None], None] | None = None,
    ffmpeg_dir: str | None = None,
) -> Path:
    """Download ``url`` into ``dest_dir`` via yt-dlp, returning the final media file.

    Picks the best mp4-ish video+audio (``bv*+ba/b``) and merges to a single ``.mp4``
    when the source is adaptive. Progress is mapped from yt-dlp's byte counters to
    ``on_progress(downloaded, total)`` (``total`` may be None when the size is unknown).
    Raises ``RuntimeError`` with a clear message on any failure.
    """
    import yt_dlp

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    ff_dir = ffmpeg_dir if ffmpeg_dir is not None else _ffmpeg_dir()

    def _hook(status: dict[str, object]) -> None:
        if on_progress is None or status.get("status") != "downloading":
            return
        downloaded = status.get("downloaded_bytes")
        if not isinstance(downloaded, int):
            return
        total = status.get("total_bytes")
        if not isinstance(total, int):
            estimate = status.get("total_bytes_estimate")
            total = estimate if isinstance(estimate, int) else None
        on_progress(downloaded, total)

    ydl_opts: dict[str, object] = {
        "outtmpl": str(dest_dir / "%(title).200B [%(id)s].%(ext)s"),
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "restrictfilenames": True,
        "progress_hooks": [_hook],
    }
    if ff_dir is not None:
        ydl_opts["ffmpeg_location"] = ff_dir

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise RuntimeError(f"yt-dlp returned no info for {url}")
            # extract_info may wrap a single entry (e.g. a playlist collapsed by
            # noplaylist) — unwrap to the actually-downloaded entry.
            if "entries" in info:
                entries = [e for e in info.get("entries") or [] if e]
                if not entries:
                    raise RuntimeError(f"yt-dlp found no downloadable entry for {url}")
                info = entries[0]
            filename = ydl.prepare_filename(info)
    except RuntimeError:
        raise
    except Exception as exc:  # yt-dlp raises a variety of DownloadError subclasses
        raise RuntimeError(f"yt-dlp failed for {url}: {exc}") from exc

    candidate = Path(filename)
    # When streams are merged, the on-disk extension follows merge_output_format and
    # may differ from prepare_filename's guess. Prefer the merged path, then the
    # requested-merge extension, then the requested format, then any sibling stem.
    if not candidate.exists():
        requested = info.get("requested_downloads")
        if isinstance(requested, list) and requested:
            first = requested[0]
            fp = first.get("filepath") if isinstance(first, dict) else None
            if isinstance(fp, str) and Path(fp).exists():
                candidate = Path(fp)
    if not candidate.exists():
        merged = candidate.with_suffix(".mp4")
        if merged.exists():
            candidate = merged
        else:
            siblings = sorted(dest_dir.glob(candidate.stem + ".*"))
            if siblings:
                candidate = siblings[0]

    if not candidate.exists():
        raise RuntimeError(
            f"yt-dlp reported success but produced no file for {url} "
            f"(expected near {candidate})"
        )
    return candidate
