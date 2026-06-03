"""One-shot ``aria2c`` wrapper for protocols httpx cannot handle (torrent/ftp/metalink).

aria2c is invoked as a separate process (like ffmpeg) — never linked — so its GPLv2
license stays at arm's length. It is an optional extra: if absent, only non-HTTP
sources are unavailable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class Aria2Error(RuntimeError):
    """Raised when aria2c is missing or exits non-zero."""


@dataclass(frozen=True)
class Aria2Opts:
    connections: int = 16
    max_overall_download_limit: str | None = None  # e.g. "2M"; None = unlimited
    all_proxy: str | None = None                    # e.g. "http://127.0.0.1:8080"


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
) -> list[Path]:
    """Download ``url`` into ``dest_dir`` via one-shot aria2c. Returns the produced
    file paths (1 for HTTP/single-file torrent, N for multi-file torrent)."""
    opts = opts or Aria2Opts()
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        aria2_bin(),
        "--dir", str(dest_dir),
        "--continue=true",
        "--auto-file-renaming=false",
        "--allow-overwrite=true",
        "--summary-interval=0",
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

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
    except FileNotFoundError as exc:
        raise Aria2Error(f"aria2c not found: {aria2_bin()}") from exc
    if proc.returncode != 0:
        raise Aria2Error((proc.stderr or "aria2c failed").strip()[-2000:])

    files = _list_downloaded(dest_dir)
    if not files:
        raise Aria2Error(f"aria2c produced no files in {dest_dir}")
    return files
