"""Resumable HTTP(S) download with size/hash verification.

Streams to ``<dest>.part`` and resumes from an existing partial file via a Range
request, so an interrupted download is retried by simply re-running. Only after the
final size (and optional SHA-256) check passes is the file atomically promoted to
``dest``. Generic HTTP(S) only — for Google Drive pass a direct ``googleusercontent``
link (no confirm-token handling here).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from .probe import sha256_file

_DEFAULT_CONNECTIONS = int(os.environ.get("LAURA_DOWNLOAD_CONNECTIONS", "8"))
_DEFAULT_MIN_SEGMENT_BYTES = int(
    os.environ.get("LAURA_DOWNLOAD_MIN_SEGMENT_BYTES", str(8 * 1024 * 1024))
)


class DownloadError(RuntimeError):
    """Raised when the download cannot complete or fails verification."""


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    size_bytes: int
    sha256: str


def _expected_total(resp: httpx.Response, resume_from: int) -> int | None:
    content_range = resp.headers.get("Content-Range")
    if content_range and "/" in content_range:
        tail = content_range.rsplit("/", 1)[1].strip()
        if tail.isdigit():
            return int(tail)
    length = resp.headers.get("Content-Length")
    if length and length.isdigit():
        # 206: Content-Length is the remaining range; 200: it is the whole file.
        return resume_from + int(length) if resp.status_code == 206 else int(length)
    return None


def _probe_range(client: httpx.Client, url: str) -> tuple[bool, int | None]:
    """Return (supports_range, total_size).

    Sends a HEAD request to avoid consuming the body.  Servers that support
    byte-range requests advertise ``Accept-Ranges: bytes``; ``Content-Length``
    gives the total file size.  If HEAD is not allowed (405) we conservatively
    return ``(False, None)`` so the caller falls back to single-stream.
    """
    resp = client.head(url)
    if resp.status_code == 405:
        return False, None
    accept_ranges = resp.headers.get("Accept-Ranges", "none").lower()
    length = resp.headers.get("Content-Length")
    supports = accept_ranges == "bytes"
    total = int(length) if length and length.isdigit() else None
    return supports, total


def _download_single_stream(
    url: str,
    dest: Path,
    *,
    expected_sha256: str | None,
    chunk_bytes: int,
    timeout: float,
    on_progress: Callable[[int, int | None], None] | None,
) -> DownloadResult:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")

    resume_from = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
    total: int | None = None

    # Open the part file BEFORE entering the stream context so the file descriptor
    # is ready when iter_raw() starts.  On Windows, opening a file inside the
    # stream context can introduce a small but critical delay that lets the server's
    # TCP RST arrive before the first read, causing iter_raw() to raise immediately
    # without yielding any buffered data.
    fh = open(part, "ab" if resume_from else "wb")  # noqa: SIM115
    try:
        with (
            httpx.Client(follow_redirects=True, timeout=timeout) as client,
            client.stream("GET", url, headers=headers) as resp,
        ):
            if resume_from and resp.status_code == 200:
                # Server ignored Range and is sending the whole file. Discard the
                # stale partial bytes so we don't append a second copy.
                resume_from = 0
                fh.seek(0)
                fh.truncate()
            elif resp.status_code not in (200, 206):
                raise DownloadError(f"unexpected status {resp.status_code} for {url}")
            total = _expected_total(resp, resume_from)
            downloaded = resume_from
            buf = bytearray()
            try:
                for raw in resp.iter_raw():
                    buf += raw
                    if len(buf) >= chunk_bytes:
                        fh.write(buf)
                        downloaded += len(buf)
                        if on_progress is not None:
                            on_progress(downloaded, total)
                        buf.clear()
            finally:
                # Flush any buffered data before the exception propagates
                # so a partial .part file survives a mid-stream error.
                if buf:
                    fh.write(buf)
                    downloaded += len(buf)
                    if on_progress is not None:
                        on_progress(downloaded, total)
    except httpx.HTTPError as exc:
        raise DownloadError(f"transport error for {url}: {exc}") from exc
    finally:
        fh.close()

    size = part.stat().st_size
    if total is not None and size != total:
        raise DownloadError(f"size mismatch: got {size}, expected {total}")

    sha = sha256_file(part)
    if expected_sha256 is not None and sha.lower() != expected_sha256.lower():
        raise DownloadError(f"sha256 mismatch: got {sha}, expected {expected_sha256}")

    os.replace(part, dest)
    return DownloadResult(path=dest, size_bytes=size, sha256=sha)


def _download_segmented(
    url: str,
    dest: Path,
    *,
    total: int,
    connections: int,
    expected_sha256: str | None,
    timeout: float,
    on_progress: Callable[[int, int | None], None] | None,
) -> DownloadResult:
    raise NotImplementedError("segmented download arrives in the next task")


def download_resumable(
    url: str,
    dest: Path | str,
    *,
    expected_sha256: str | None = None,
    chunk_bytes: int = 1 << 20,
    timeout: float = 30.0,
    connections: int | None = None,
    min_segment_bytes: int | None = None,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> DownloadResult:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    conns = connections if connections is not None else _DEFAULT_CONNECTIONS
    min_seg = min_segment_bytes if min_segment_bytes is not None else _DEFAULT_MIN_SEGMENT_BYTES

    if conns > 1:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            supports_range, total = _probe_range(client, url)
        if supports_range and total is not None and total >= min_seg:
            return _download_segmented(
                url, dest, total=total, connections=conns,
                expected_sha256=expected_sha256, timeout=timeout, on_progress=on_progress,
            )

    return _download_single_stream(
        url, dest, expected_sha256=expected_sha256, chunk_bytes=chunk_bytes,
        timeout=timeout, on_progress=on_progress,
    )
