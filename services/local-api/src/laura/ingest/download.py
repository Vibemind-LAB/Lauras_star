"""Resumable HTTP(S) download with size/hash verification.

Streams to ``<dest>.part`` and resumes from an existing partial file via a Range
request, so an interrupted download is retried by simply re-running. Only after the
final size (and optional SHA-256) check passes is the file atomically promoted to
``dest``. Generic HTTP(S) only — for Google Drive pass a direct ``googleusercontent``
link (no confirm-token handling here).
"""

from __future__ import annotations

import concurrent.futures
import os
import shutil
import threading
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
    """Return (supports_range, total_size). Sends a 1-byte ranged GET via a stream and
    closes WITHOUT consuming the body — critical, because a server that ignores Range
    answers 200 with the whole file, and we must not download a 30 GB body just to probe.
    A 206 with a Content-Range total means Range is supported and the size is known."""
    with client.stream("GET", url, headers={"Range": "bytes=0-0"}) as resp:
        status = resp.status_code
        content_range = resp.headers.get("Content-Range", "")
        length = resp.headers.get("Content-Length")
    if status == 206:
        if "/" in content_range:
            tail = content_range.rsplit("/", 1)[1].strip()
            if tail.isdigit():
                return True, int(tail)
        return True, None
    return False, int(length) if length and length.isdigit() else None


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
    parts_dir = dest.with_name(dest.name + ".parts")
    seg_len = -(-total // connections)  # ceil
    sentinel = parts_dir / ".seglen"
    if parts_dir.exists() and (
        not sentinel.exists() or sentinel.read_text().strip() != str(seg_len)
    ):
        shutil.rmtree(parts_dir, ignore_errors=True)
    parts_dir.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(str(seg_len))

    segments = [
        (i, i * seg_len, min((i + 1) * seg_len, total) - 1)
        for i in range(connections)
        if i * seg_len < total
    ]

    lock = threading.Lock()
    downloaded: list[int] = [0]

    def fetch(segment: tuple[int, int, int]) -> None:
        idx, start, end = segment
        seg_path = parts_dir / f"seg-{idx:04d}"
        want = end - start + 1
        have = seg_path.stat().st_size if seg_path.exists() else 0
        if have > want:
            seg_path.unlink()
            have = 0
        if have == want:
            with lock:
                downloaded[0] += want
                if on_progress is not None:
                    on_progress(downloaded[0], total)
            return
        last_exc: Exception | None = None
        for _ in range(3):
            try:
                resume_at = start + have
                headers = {"Range": f"bytes={resume_at}-{end}"}
                with (
                    httpx.Client(follow_redirects=True, timeout=timeout) as client,
                    client.stream("GET", url, headers=headers) as resp,
                ):
                    if resp.status_code != 206:
                        raise DownloadError(
                            f"segment {idx}: expected 206, got {resp.status_code}"
                        )
                    with open(seg_path, "ab" if have else "wb") as fh:
                        for chunk in resp.iter_raw():
                            fh.write(chunk)
                            with lock:
                                downloaded[0] += len(chunk)
                                if on_progress is not None:
                                    on_progress(downloaded[0], total)
                return
            except httpx.HTTPError as exc:
                last_exc = exc
                have = seg_path.stat().st_size if seg_path.exists() else 0
        raise DownloadError(f"segment {idx} failed after retries: {last_exc}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=connections) as pool:
        # list() forces consumption so the first worker exception propagates here
        list(pool.map(fetch, segments))

    part = dest.with_name(dest.name + ".part")
    with open(part, "wb") as out:
        for idx, _, _ in segments:
            with open(parts_dir / f"seg-{idx:04d}", "rb") as seg:
                shutil.copyfileobj(seg, out)

    size = part.stat().st_size
    if size != total:
        raise DownloadError(f"size mismatch after reassembly: got {size}, expected {total}")
    sha = sha256_file(part)
    if expected_sha256 is not None and sha.lower() != expected_sha256.lower():
        raise DownloadError(f"sha256 mismatch: got {sha}, expected {expected_sha256}")

    os.replace(part, dest)
    shutil.rmtree(parts_dir, ignore_errors=True)
    return DownloadResult(path=dest, size_bytes=size, sha256=sha)


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
