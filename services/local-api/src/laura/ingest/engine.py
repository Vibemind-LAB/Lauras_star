"""Pick the download engine for a URL by protocol/extension.

httpx (our segmented engine) handles HTTP(S). aria2 handles everything httpx cannot:
BitTorrent/Magnet, Metalink, and FTP/SFTP.
"""

from __future__ import annotations

from urllib.parse import urlsplit

_ARIA2_SCHEMES = {"magnet", "ftp", "ftps", "sftp"}
_ARIA2_SUFFIXES = (".torrent", ".metalink", ".meta4")


def select_engine(url: str) -> str:
    """Return 'httpx' or 'aria2' for the given URL."""
    parts = urlsplit(url)
    if parts.scheme.lower() in _ARIA2_SCHEMES:
        return "aria2"
    if parts.path.lower().endswith(_ARIA2_SUFFIXES):
        return "aria2"
    return "httpx"
