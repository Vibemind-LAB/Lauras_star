"""One-shot ``aria2c`` wrapper for protocols httpx cannot handle (torrent/ftp/metalink).

aria2c is invoked as a separate process (like ffmpeg) — never linked — so its GPLv2
license stays at arm's length. It is an optional extra: if absent, only non-HTTP
sources are unavailable.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class Aria2Error(RuntimeError):
    """Raised when aria2c is missing or exits non-zero."""


class Aria2Cancelled(RuntimeError):
    """Raised when a ``cancel_event`` aborted the aria2c download."""


@dataclass(frozen=True)
class Aria2Opts:
    connections: int = 16
    max_overall_download_limit: str | None = None  # e.g. "2M"; None = unlimited
    all_proxy: str | None = None                    # e.g. "http://127.0.0.1:8080"


_SIZE_RE = re.compile(r"(?P<num>[\d.]+)(?P<unit>[KMGT]?i?B)", re.IGNORECASE)
_UNIT_FACTORS = {
    "b": 1, "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4,
    "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4,
}
_PROGRESS_RE = re.compile(
    r"(?P<dl>[\d.]+[KMGT]?i?B)/(?P<total>[\d.]+[KMGT]?i?B)\(\d+%\).*?DL:(?P<speed>[\d.]+[KMGT]?i?B)"
)


def _to_bytes(token: str) -> int | None:
    m = _SIZE_RE.fullmatch(token.strip())
    if m is None:
        return None
    factor = _UNIT_FACTORS.get(m.group("unit").lower())
    if factor is None:
        return None
    return int(float(m.group("num")) * factor)


def _parse_aria2_progress(line: str) -> tuple[int, int, int] | None:
    """Extract (downloaded_bytes, total_bytes, speed_bps) from an aria2c progress line.

    Returns None for non-progress lines or if any field can't be parsed."""
    m = _PROGRESS_RE.search(line)
    if m is None:
        return None
    downloaded = _to_bytes(m.group("dl"))
    total = _to_bytes(m.group("total"))
    speed = _to_bytes(m.group("speed"))
    if downloaded is None or total is None or speed is None:
        return None
    return downloaded, total, speed


def aria2_bin() -> str:
    return os.environ.get("LAURA_ARIA2") or shutil.which("aria2c") or "aria2c"


def aria2_available() -> bool:
    return shutil.which(os.environ.get("LAURA_ARIA2", "aria2c")) is not None


def _list_downloaded(dest_dir: Path) -> list[Path]:
    """Files aria2 produced, excluding its control/metadata files."""
    out: list[Path] = []
    for p in sorted(dest_dir.rglob("*")):
        if p.is_file() and p.suffix not in (".aria2", ".torrent"):
            out.append(p)
    return out


def aria2_download(
    url: str,
    dest_dir: Path | str,
    *,
    filename: str | None = None,
    opts: Aria2Opts | None = None,
    on_progress: Callable[[int, int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> list[Path]:
    """Download ``url`` into ``dest_dir`` via one-shot aria2c, streaming progress.

    ``on_progress(downloaded, total, speed_bps)`` is called for each periodic summary
    line aria2c emits (best-effort). If ``cancel_event`` is provided and gets set, the
    aria2c subprocess is terminated and ``Aria2Cancelled`` is raised (best-effort —
    relies on terminating the child process). Returns the produced file paths."""
    opts = opts or Aria2Opts()
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        aria2_bin(),
        "--dir", str(dest_dir),
        "--continue=true",
        "--auto-file-renaming=false",
        "--allow-overwrite=true",
        "--summary-interval=1",
        "--console-log-level=warn",
        f"--max-connection-per-server={opts.connections}",
        f"--split={opts.connections}",
        "--seed-time=0",
    ]
    if filename:
        cmd += ["--out", filename]
    if opts.max_overall_download_limit:
        cmd += [f"--max-overall-download-limit={opts.max_overall_download_limit}"]
    if opts.all_proxy:
        cmd += [f"--all-proxy={opts.all_proxy}"]
    cmd.append(url)

    tail: list[str] = []
    try:
        proc = subprocess.Popen(  # noqa: S603
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
    except FileNotFoundError as exc:
        raise Aria2Error(f"aria2c not found: {aria2_bin()}") from exc

    # Watch the cancel flag on a side thread: when set, terminate aria2c so the
    # blocking stdout iteration below unblocks (the pipe closes) and we can raise.
    watcher_stop = threading.Event()

    def _watch_cancel() -> None:
        while not watcher_stop.wait(0.5):
            if cancel_event is not None and cancel_event.is_set():
                proc.terminate()
                return

    watcher: threading.Thread | None = None
    if cancel_event is not None:
        watcher = threading.Thread(target=_watch_cancel, daemon=True)
        watcher.start()

    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            tail.append(line)
            if len(tail) > 100:
                del tail[0]
            if on_progress is not None:
                parsed = _parse_aria2_progress(line)
                if parsed is not None:
                    on_progress(*parsed)
        returncode = proc.wait()
    finally:
        watcher_stop.set()
        if watcher is not None:
            watcher.join(timeout=1.0)

    if cancel_event is not None and cancel_event.is_set():
        raise Aria2Cancelled("aria2c download cancelled")
    if returncode != 0:
        raise Aria2Error(("".join(tail) or "aria2c failed").strip()[-2000:])

    files = _list_downloaded(dest_dir)
    if not files:
        raise Aria2Error(f"aria2c produced no files in {dest_dir}")
    return files
