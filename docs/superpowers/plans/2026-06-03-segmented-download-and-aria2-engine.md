# Segmented HTTP Download + aria2 Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make large HTTP downloads robust on flaky networks via multi-connection segmentation in our own httpx engine, and add an optional `aria2` engine for protocols httpx can't do (torrent/magnet/ftp/metalink), creating one asset per media file.

**Architecture:** `download_resumable` becomes a dispatcher: it probes for HTTP Range support + size, then either segments the download across N parallel connections (each segment resumable, per-segment retry) or falls back to the existing single-stream path. A protocol-based `select_engine` routes non-HTTP URLs to a new one-shot `aria2c` subprocess wrapper; `handle_fetch` fans the resulting 1..N media files out into assets.

**Tech Stack:** Python 3.11, `httpx` (existing), `concurrent.futures` (stdlib), `aria2c` (optional external binary, invoked as subprocess like ffmpeg), `ffmpeg`/`ffprobe`, `pytest`.

**Working directory for all commands:** `services/local-api`. Tests run with `uv run --no-sync pytest` (the `--no-sync` avoids a venv re-sync that can fail while the API server is running).

**Spec:** [`docs/superpowers/specs/2026-06-03-segmented-download-and-aria2-engine-design.md`](../specs/2026-06-03-segmented-download-and-aria2-engine-design.md)

**Git hygiene:** The user works in `apps/desktop/` in parallel. For EVERY commit, `git add` only the exact files listed and run `git status` to confirm nothing under `apps/desktop/` is staged before committing. Branch: `docs/resilient-url-ingest-spec` (do not switch).

**Defaults:** `connections = 8`, `min_segment_bytes = 8 MiB` (8 * 1024 * 1024), both overridable via env `LAURA_DOWNLOAD_CONNECTIONS` / `LAURA_DOWNLOAD_MIN_SEGMENT_BYTES`.

---

## File Structure

- Modify: `services/local-api/tests/_flaky_http.py` — honor bounded `Range` (`bytes=start-end`), return `206` whenever a Range header is present.
- Modify: `services/local-api/src/laura/ingest/download.py` — extract single-stream path, add range probe + segmented downloader + dispatch.
- Modify: `services/local-api/tests/test_download.py` — segmentation tests.
- Create: `services/local-api/src/laura/ingest/engine.py` — `select_engine(url)` pure function.
- Create: `services/local-api/tests/test_engine_select.py`
- Create: `services/local-api/src/laura/ingest/aria2.py` — `aria2_available`, `aria2_download`, `Aria2Error`.
- Create: `services/local-api/tests/test_aria2.py`
- Modify: `services/local-api/src/laura/ingest/integrity.py` — add `is_media_file`.
- Modify: `services/local-api/tests/test_integrity.py` — `is_media_file` tests.
- Modify: `services/local-api/src/laura/ingest/handlers.py` — engine dispatch + one-asset-per-file fan-out in `handle_fetch`.
- Modify: `services/local-api/tests/test_fetch.py` — aria2 fan-out test (aria2 layer mocked, real ffmpeg).

---

# PHASE 1 — Segmentation in httpx (independently shippable)

## Task 1: Extend the flaky test server to honor bounded ranges

**Files:**
- Modify: `services/local-api/tests/_flaky_http.py`

The segmented downloader sends `Range: bytes=START-END` (bounded) and probes with `bytes=0-0`. The current server only parses START, serves to EOF, and returns `206` only when START>0. It must honor END and return `206` whenever a Range header is present.

- [ ] **Step 1: Read the current file**

Read `services/local-api/tests/_flaky_http.py` to see the current `do_GET` (Range parsing, the `cut`/`fake_content_length`/`ignore_range` params).

- [ ] **Step 2: Replace the non-cut response branch to honor bounded ranges**

Find the block that currently handles the normal (non-cut) response — it parses only `start`, sends `content[start:]`, and chooses `206` only when `start > 0`. Replace the Range parsing + normal response so that:
- a `Range: bytes=START-END` header is parsed for BOTH bounds (END optional → end of file),
- whenever a Range header is present (even `start == 0`), respond `206` with `Content-Range: bytes START-END/TOTAL` and a `Content-Length` of the slice length,
- with no Range header, respond `200` with the full body,
- `ignore_range=True` keeps overriding to a full `200` (unchanged).

Concretely, ensure the parsing computes both ends:

```python
            rng = self.headers.get("Range")
            start, end = 0, len(content) - 1
            if rng and rng.startswith("bytes=") and not ignore_range:
                spec = rng.split("=", 1)[1]
                lo, _, hi = spec.partition("-")
                start = int(lo) if lo else 0
                end = int(hi) if hi else len(content) - 1
            body = content[start : end + 1]
```

and the normal (non-cut, non-ignore_range) send becomes:

```python
            if rng and not ignore_range:
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(content)}")
            else:
                self.send_response(200)
            self.send_header(
                "Content-Length",
                str(fake_content_length if fake_content_length is not None else len(body)),
            )
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(body)
```

Keep the `do_cut` branch (chunked + RST) exactly as-is, and keep the `cut_after`/`fake_content_length`/`ignore_range` params. Make sure `body`/`start`/`end` are computed before the `do_cut` branch uses them (the cut branch writes `body[:cut_after]`).

- [ ] **Step 3: Verify existing download tests still pass**

Run: `uv run --no-sync pytest tests/test_download.py -v`
Expected: the 4 existing tests still PASS (simple, cut+resume, sha mismatch, ignore-range restart). The resume test now gets `206` for `bytes=cut-` which is still correct.

- [ ] **Step 4: Commit**

```bash
git add services/local-api/tests/_flaky_http.py
git status   # confirm nothing under apps/desktop/ is staged
git commit -m "$(printf 'test: flaky server honors bounded Range requests\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 2: Refactor download.py — extract single-stream, add range probe (no behavior change yet)

**Files:**
- Modify: `services/local-api/src/laura/ingest/download.py`

Goal: make `download_resumable` a thin dispatcher and move the current body into `_download_single_stream`, plus add `_probe_range`. No segmentation yet — behavior stays identical so existing tests keep passing.

- [ ] **Step 1: Add imports and helpers at the top of download.py**

After the existing imports (`os`, `Callable`, `dataclass`, `Path`, `httpx`, `sha256_file`), add:

```python
import concurrent.futures
import shutil
import threading

_DEFAULT_CONNECTIONS = int(os.environ.get("LAURA_DOWNLOAD_CONNECTIONS", "8"))
_DEFAULT_MIN_SEGMENT_BYTES = int(
    os.environ.get("LAURA_DOWNLOAD_MIN_SEGMENT_BYTES", str(8 * 1024 * 1024))
)
```

- [ ] **Step 2: Rename the current `download_resumable` body to `_download_single_stream`**

Take the entire current body of `download_resumable` (lines from `dest = Path(dest)` through the `return DownloadResult(...)`) and move it into a new private function with the same parameters minus the segmentation ones:

```python
def _download_single_stream(
    url: str,
    dest: Path,
    *,
    expected_sha256: str | None,
    chunk_bytes: int,
    timeout: float,
    on_progress: Callable[[int, int | None], None] | None,
) -> DownloadResult:
    # (verbatim the current download_resumable body, with `dest` already a Path)
    ...
```

Note: the current body's first line `dest = Path(dest)` can stay; it is idempotent.

- [ ] **Step 3: Add the range probe**

```python
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
```

- [ ] **Step 4: Rewrite `download_resumable` as a dispatcher (single-stream only for now)**

```python
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
```

- [ ] **Step 5: Add a temporary stub for `_download_segmented` so the module imports**

(The real implementation lands in Task 3; for now make it raise so the dispatch never silently misbehaves, and so this task's tests — which all use small payloads < 8 MiB — never reach it.)

```python
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
```

- [ ] **Step 6: Run the existing download tests**

Run: `uv run --no-sync pytest tests/test_download.py -v`
Expected: all 4 existing tests PASS. They use a ~94 KiB payload (< 8 MiB `min_segment_bytes`), so the dispatcher always falls back to `_download_single_stream` and never hits the stub.

- [ ] **Step 7: Typecheck + lint**

Run: `uv run --no-sync mypy src/laura/ingest/download.py && uv run --no-sync ruff check src/laura/ingest/download.py`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add services/local-api/src/laura/ingest/download.py
git status   # confirm nothing under apps/desktop/ is staged
git commit -m "$(printf 'refactor(ingest): split single-stream download, add range probe + dispatch\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 3: Implement segmented download

**Files:**
- Modify: `services/local-api/src/laura/ingest/download.py`
- Test: `services/local-api/tests/test_download.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_download.py` (it already imports `hashlib`, `Path`, `pytest`, `download_resumable`, `DownloadError`, `serve`, `CONTENT`, `_sha`):

```python
BIG = b"laura-segment-" * 80_000  # ~1.1 MiB, deterministic


def test_segmented_download_succeeds(tmp_path: Path) -> None:
    dest = tmp_path / "big.bin"
    with serve(BIG) as url:
        result = download_resumable(url, dest, connections=4, min_segment_bytes=1024)
    assert dest.read_bytes() == BIG
    assert result.size_bytes == len(BIG)
    assert result.sha256 == _sha(BIG)
    assert not dest.with_name(dest.name + ".parts").exists()  # cleaned up


def test_segmented_resumes_incomplete_segments(tmp_path: Path) -> None:
    # Pre-seed the .parts dir with one complete and one partial segment, then ensure
    # the download completes correctly (incomplete segment is finished, not duplicated).
    dest = tmp_path / "big.bin"
    parts = dest.with_name(dest.name + ".parts")
    parts.mkdir(parents=True)
    # 4 connections over len(BIG): seg length = ceil(total/4)
    seg_len = -(-len(BIG) // 4)
    (parts / "seg-0000").write_bytes(BIG[0:seg_len])          # complete
    (parts / "seg-0001").write_bytes(BIG[seg_len:seg_len + 10])  # partial
    with serve(BIG) as url:
        result = download_resumable(url, dest, connections=4, min_segment_bytes=1024)
    assert dest.read_bytes() == BIG
    assert result.sha256 == _sha(BIG)


def test_no_range_support_falls_back_to_single_stream(tmp_path: Path) -> None:
    dest = tmp_path / "big.bin"
    with serve(BIG, ignore_range=True) as url:  # server answers 200, no Range
        result = download_resumable(url, dest, connections=4, min_segment_bytes=1024)
    assert dest.read_bytes() == BIG
    assert result.sha256 == _sha(BIG)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/test_download.py -k segment -v`
Expected: FAIL with `NotImplementedError` (the stub) for the first two; the fallback test may pass already (single-stream). Confirm the segmented ones fail at the stub.

- [ ] **Step 3: Replace the `_download_segmented` stub with the real implementation**

```python
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
    parts_dir.mkdir(parents=True, exist_ok=True)

    seg_len = -(-total // connections)  # ceil
    segments = [
        (i, i * seg_len, min((i + 1) * seg_len, total) - 1)
        for i in range(connections)
        if i * seg_len < total
    ]

    lock = threading.Lock()
    downloaded = [0]

    def fetch(segment: tuple[int, int, int]) -> None:
        idx, start, end = segment
        seg_path = parts_dir / f"seg-{idx:04d}"
        want = end - start + 1
        have = seg_path.stat().st_size if seg_path.exists() else 0
        if have >= want:
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
                with httpx.Client(follow_redirects=True, timeout=timeout) as client:
                    with client.stream("GET", url, headers=headers) as resp:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/test_download.py -v`
Expected: all PASS (4 existing + 3 new = 7).

- [ ] **Step 5: Typecheck + lint**

Run: `uv run --no-sync mypy src/laura/ingest/download.py && uv run --no-sync ruff check src/laura/ingest/download.py tests/test_download.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add services/local-api/src/laura/ingest/download.py services/local-api/tests/test_download.py
git status   # confirm nothing under apps/desktop/ is staged
git commit -m "$(printf 'feat(ingest): multi-connection segmented download with per-segment resume\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

**Phase 1 is complete and independently shippable here.** The flaky-network HTTP robustness is delivered.

---

# PHASE 2 — Optional aria2 engine + one-asset-per-file fan-out

## Task 4: `select_engine` pure function

**Files:**
- Create: `services/local-api/src/laura/ingest/engine.py`
- Test: `services/local-api/tests/test_engine_select.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_engine_select.py`:

```python
"""Protocol-based download-engine selection (pure function, no network)."""

from __future__ import annotations

import pytest

from laura.ingest.engine import select_engine


@pytest.mark.parametrize("url", [
    "http://example.com/a.mp4",
    "https://example.com/a.mp4",
    "https://drive.usercontent.google.com/download?id=x",
])
def test_http_uses_httpx(url: str) -> None:
    assert select_engine(url) == "httpx"


@pytest.mark.parametrize("url", [
    "magnet:?xt=urn:btih:abcdef",
    "https://example.com/film.torrent",
    "https://example.com/list.metalink",
    "https://example.com/list.meta4",
    "ftp://example.com/a.mp4",
    "ftps://example.com/a.mp4",
    "sftp://example.com/a.mp4",
])
def test_non_http_uses_aria2(url: str) -> None:
    assert select_engine(url) == "aria2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/test_engine_select.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'laura.ingest.engine'`.

- [ ] **Step 3: Implement `engine.py`**

```python
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
    scheme = urlsplit(url).scheme.lower()
    if scheme in _ARIA2_SCHEMES:
        return "aria2"
    path = urlsplit(url).path.lower()
    if path.endswith(_ARIA2_SUFFIXES):
        return "aria2"
    return "httpx"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/test_engine_select.py -v`
Expected: PASS (all parametrized cases).

- [ ] **Step 5: Typecheck + lint**

Run: `uv run --no-sync mypy src/laura/ingest/engine.py && uv run --no-sync ruff check src/laura/ingest/engine.py tests/test_engine_select.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add services/local-api/src/laura/ingest/engine.py services/local-api/tests/test_engine_select.py
git status   # confirm nothing under apps/desktop/ is staged
git commit -m "$(printf 'feat(ingest): protocol-based download engine selection\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 5: `aria2.py` — one-shot aria2c wrapper

**Files:**
- Create: `services/local-api/src/laura/ingest/aria2.py`
- Test: `services/local-api/tests/test_aria2.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_aria2.py`:

```python
"""aria2 engine wrapper. The download test is skipped if aria2c is not installed."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from laura.ingest.aria2 import aria2_available, aria2_download

from ._flaky_http import serve

CONTENT = b"laura-aria2-" * 8192  # ~96 KiB


def test_aria2_available_reflects_path() -> None:
    assert aria2_available() == (shutil.which("aria2c") is not None)


@pytest.mark.skipif(shutil.which("aria2c") is None, reason="aria2c not installed")
def test_aria2_downloads_http_file(tmp_path: Path) -> None:
    dest_dir = tmp_path / "dl"
    with serve(CONTENT) as url:
        files = aria2_download(url, dest_dir, filename="out.bin")
    assert len(files) == 1
    assert files[0].read_bytes() == CONTENT
    # no leftover control/metadata files reported
    assert all(not f.name.endswith(".aria2") for f in files)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/test_aria2.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'laura.ingest.aria2'`.

- [ ] **Step 3: Implement `aria2.py`**

```python
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
        "--continue=true",                 # resume from aria2's control file
        "--auto-file-renaming=false",
        "--allow-overwrite=true",
        "--summary-interval=0",
        "--console-log-level=warn",
        f"--max-connection-per-server={opts.connections}",
        f"--split={opts.connections}",
        "--seed-time=0",                   # do not seed torrents after completion
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
```

- [ ] **Step 4: Run tests to verify they pass (or skip)**

Run: `uv run --no-sync pytest tests/test_aria2.py -v`
Expected: `test_aria2_available_reflects_path` PASS; the download test PASS if `aria2c` is installed, else SKIP. If you want to exercise it, install aria2 (`winget install aria2.aria2` / `choco install aria2`) and re-run; report which happened.

- [ ] **Step 5: Typecheck + lint**

Run: `uv run --no-sync mypy src/laura/ingest/aria2.py && uv run --no-sync ruff check src/laura/ingest/aria2.py tests/test_aria2.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add services/local-api/src/laura/ingest/aria2.py services/local-api/tests/test_aria2.py
git status   # confirm nothing under apps/desktop/ is staged
git commit -m "$(printf 'feat(ingest): one-shot aria2c wrapper for torrent/ftp/metalink\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 6: `is_media_file` helper

**Files:**
- Modify: `services/local-api/src/laura/ingest/integrity.py`
- Test: `services/local-api/tests/test_integrity.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_integrity.py` (it already has the ffmpeg skip marker, `run_ffmpeg`, `Path`, `pytest`, and the `sample` fixture):

```python
from laura.ingest.integrity import is_media_file  # add to existing imports at top


def test_is_media_file_true_for_real_media(sample: Path) -> None:
    assert is_media_file(sample) is True


def test_is_media_file_false_for_text(tmp_path: Path) -> None:
    junk = tmp_path / "notes.txt"
    junk.write_text("just some text", encoding="utf-8")
    assert is_media_file(junk) is False
```

(Place the `import` with the existing `from laura.ingest.integrity import verify_decode` line — combine into `from laura.ingest.integrity import is_media_file, verify_decode`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/test_integrity.py -k is_media -v`
Expected: FAIL with `ImportError: cannot import name 'is_media_file'`.

- [ ] **Step 3: Implement `is_media_file` in integrity.py**

Add to `services/local-api/src/laura/ingest/integrity.py`:

```python
def is_media_file(path: Path | str) -> bool:
    """True if ffprobe can read the container AND it has an audio or video stream.

    Used to pick real media out of a torrent's mixed contents (.nfo/.txt/samples).
    """
    try:
        data = ffprobe(path)
    except FFmpegError:
        return False
    streams = data.get("streams", [])
    return any(s.get("codec_type") in ("video", "audio") for s in streams)
```

(`ffprobe` and `FFmpegError` are already imported at the top of `integrity.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/test_integrity.py -v`
Expected: all PASS (existing 3 + 2 new), or SKIP if ffmpeg absent.

- [ ] **Step 5: Typecheck + lint**

Run: `uv run --no-sync mypy src/laura/ingest/integrity.py && uv run --no-sync ruff check src/laura/ingest/integrity.py tests/test_integrity.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add services/local-api/src/laura/ingest/integrity.py services/local-api/tests/test_integrity.py
git status   # confirm nothing under apps/desktop/ is staged
git commit -m "$(printf 'feat(ingest): is_media_file to filter torrent contents\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 7: Wire engine dispatch + one-asset-per-file fan-out into `handle_fetch`

**Files:**
- Modify: `services/local-api/src/laura/ingest/handlers.py`
- Test: `services/local-api/tests/test_fetch.py`

Read `handle_fetch` first. The HTTP path must keep its exact current behavior (download → verify → on failure discard+raise → on success set_source+probe), so the existing `test_fetch.py` tests keep passing. The aria2 path is new: download to a dir, filter media, fan out one asset per media file with per-file (non-fatal) verify.

- [ ] **Step 1: Write the failing test (aria2 fan-out, aria2 layer mocked, real ffmpeg)**

Append to `tests/test_fetch.py` (it already imports `os`, `shutil`, `Path`, `pytest`, `Settings`, `repos`, `SqliteDatabase`, `run_ffmpeg`, `register_ingest_handlers`, `JobRunner`, `default_registry`, `enqueue`, `serve`, `_drain`):

```python
def test_fetch_torrent_fans_out_one_asset_per_media_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two real media files + one non-media file, as if produced by aria2 from a torrent.
    src = tmp_path / "src"
    src.mkdir()
    media_paths = []
    for name in ("a.mp4", "b.mp4"):
        out = src / name
        run_ffmpeg([
            "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=30",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
        ])
        media_paths.append(out)
    (src / "readme.nfo").write_text("not media", encoding="utf-8")

    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    project_root = settings.workspace_root / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    project = repos.create_project(
        db, name="t", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(project_root),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="film.torrent", source_path="url:pending", online=False,
    )

    # Make aria2 look installed and return our prepared files.
    import laura.ingest.handlers as h
    monkeypatch.setattr(h, "aria2_available", lambda: True)
    monkeypatch.setattr(
        h, "aria2_download",
        lambda url, dest_dir, **kw: [src / "a.mp4", src / "b.mp4", src / "readme.nfo"],
    )

    registry = default_registry()
    register_ingest_handlers(registry)
    runner = JobRunner(db, registry)
    enqueue(
        db, queue="ingest.io", kind="ingest.fetch",
        payload={"asset_id": asset["id"], "source_url": "magnet:?xt=urn:btih:deadbeef"},
        idempotency_key=f"fetch:{asset['id']}", max_attempts=2,
    )
    _drain(runner)

    assets = repos.list_assets(db, project["id"], limit=50, offset=0)
    online = [a for a in assets if a["online"] == 1]
    assert len(online) == 2  # one per media file, .nfo ignored
    assert all((a["width"], a["height"]) == (160, 120) for a in online)
```

The skip marker at the top of `test_fetch.py` already gates the whole module on ffmpeg.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/test_fetch.py -k torrent -v`
Expected: FAIL — `handle_fetch` has no aria2 branch yet (it treats the magnet URL as an HTTP download and fails), so no two online assets appear.

- [ ] **Step 3: Update imports + add a finalize helper in handlers.py**

Add to the imports near the top of `services/local-api/src/laura/ingest/handlers.py`:

```python
from .aria2 import aria2_available, aria2_download
from .engine import select_engine
from .integrity import is_media_file, verify_decode
```

(Replace the existing `from .integrity import verify_decode` line with the combined import above.)

Add this helper (place it just above `handle_fetch`):

```python
def _finalize_media_asset(
    ctx: JobContext, asset: dict[str, Any], media: Path, *, full_scan: bool
) -> bool:
    """Verify one downloaded media file and attach it to ``asset``. Returns True if the
    asset went online. On verify failure the asset stays offline with an integrity
    record (non-fatal — used by the multi-file torrent fan-out)."""
    report = verify_decode(media, full_scan=full_scan)
    if not report.ok:
        report_path = media.parent / f"{media.name}.integrity.json"
        report_path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
        repos.add_asset_file(
            ctx.db, asset_id=asset["id"], kind="integrity",
            path=str(report_path), size_bytes=report_path.stat().st_size,
        )
        return False
    repos.set_asset_source(ctx.db, asset["id"], source_path=str(media), online=True)
    enqueue(
        ctx.db, queue="ingest.io", kind="ingest.probe",
        payload={"asset_id": asset["id"]}, idempotency_key=f"probe:{asset['id']}",
        caused_by_job_id=ctx.job_id,
    )
    return True
```

- [ ] **Step 4: Rewrite `handle_fetch` to dispatch by engine**

Replace the body of `handle_fetch` with:

```python
def handle_fetch(ctx: JobContext) -> dict[str, Any]:
    asset = _require_asset(ctx)
    url = ctx.payload.get("source_url")
    if not url:
        raise ValueError("ingest.fetch payload missing required field: source_url")
    full_scan = bool(ctx.payload.get("full_scan", True))
    root = _project_root(ctx.db, asset)
    base_dir = root / "downloads" / asset["id"]

    last_hb = [0.0]

    def _heartbeat(_downloaded: int, _total: int | None) -> None:
        now = time.monotonic()
        if now - last_hb[0] > 10.0:
            ctx.heartbeat()
            last_hb[0] = now

    if select_engine(url) == "aria2":
        if not aria2_available():
            raise ValueError("aria2c required for this source but is not installed")
        files = aria2_download(url, base_dir)
        media = [f for f in files if is_media_file(f)]
        if not media:
            report_path = base_dir / "integrity.json"
            base_dir.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps({"ok": False, "detail": "no media file in download"}, indent=2),
                encoding="utf-8",
            )
            repos.add_asset_file(
                ctx.db, asset_id=asset["id"], kind="integrity",
                path=str(report_path), size_bytes=report_path.stat().st_size,
            )
            raise ValueError("no media file found in downloaded source")
        _finalize_media_asset(ctx, asset, media[0], full_scan=full_scan)
        for extra in media[1:]:
            child = repos.create_asset(
                ctx.db, project_id=asset["project_id"], type="video",
                display_name=extra.name, source_path=f"url:{url}", online=False,
            )
            _finalize_media_asset(ctx, child, extra, full_scan=full_scan)
        return {"asset_id": asset["id"], "engine": "aria2", "media_files": len(media)}

    # --- httpx engine (HTTP/S): keep the existing strict single-asset policy ---
    raw_name = Path(asset["display_name"]).name or "download.bin"
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw_name) or "download.bin"
    dest = base_dir / filename
    download_resumable(url, dest, on_progress=_heartbeat)
    report = verify_decode(dest, full_scan=full_scan)
    if not report.ok:
        report_path = dest.parent / "integrity.json"
        report_path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
        repos.add_asset_file(
            ctx.db, asset_id=asset["id"], kind="integrity",
            path=str(report_path), size_bytes=report_path.stat().st_size,
        )
        dest.unlink(missing_ok=True)
        (dest.with_name(dest.name + ".part")).unlink(missing_ok=True)
        raise ValueError(f"integrity check failed: {report.detail}")
    repos.set_asset_source(ctx.db, asset["id"], source_path=str(dest), online=True)
    enqueue(
        ctx.db, queue="ingest.io", kind="ingest.probe",
        payload={"asset_id": asset["id"]}, idempotency_key=f"probe:{asset['id']}",
        caused_by_job_id=ctx.job_id,
    )
    return {"asset_id": asset["id"], "engine": "httpx", "downloaded": str(dest),
            "size_bytes": os.path.getsize(dest)}
```

Confirm `time`, `re`, `json`, `asdict`, `Path`, `Any`, `repos`, `enqueue`, `download_resumable`, `verify_decode`, `_require_asset`, `_project_root` are all imported/defined in the module (they are, from the prior feature + Step 3's new imports).

- [ ] **Step 5: Run the fetch tests**

Run: `uv run --no-sync pytest tests/test_fetch.py -v`
Expected: all PASS — the 2 existing HTTP tests (`resumes_then_probes`, `corrupt_media_marks_offline`) AND the new torrent fan-out test (2 online assets, .nfo ignored), or SKIP if ffmpeg absent.

- [ ] **Step 6: Typecheck + lint**

Run: `uv run --no-sync mypy src/laura/ingest/handlers.py && uv run --no-sync ruff check src/laura/ingest/handlers.py tests/test_fetch.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add services/local-api/src/laura/ingest/handlers.py services/local-api/tests/test_fetch.py
git status   # confirm nothing under apps/desktop/ is staged
git commit -m "$(printf 'feat(ingest): aria2 engine dispatch + one-asset-per-file fan-out\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 8: Full verification + docs

**Files:**
- Modify: `docs/06-storage.md`

- [ ] **Step 1: Run the whole suite**

Run: `uv run --no-sync pytest -q`
Expected: all green (new download/engine/aria2/integrity/fetch tests included; aria2c- and ffmpeg-gated tests skip cleanly when those binaries are absent).

- [ ] **Step 2: Typecheck + lint the package**

Run: `uv run --no-sync mypy src && uv run --no-sync ruff check src tests`
Expected: no errors.

- [ ] **Step 3: Document the engines in `docs/06-storage.md`**

Append to the existing "URL-Ingest: Downloads" section (German prose, matching style):

```markdown
### Engines

HTTP(S)-Downloads laufen über die eigene httpx-Engine mit **Multi-Connection-
Segmentierung** (parallele Range-Requests, Resume pro Segment, Fallback auf Single-Stream
wenn der Server kein Range unterstützt). Zahl der Verbindungen: Env
`LAURA_DOWNLOAD_CONNECTIONS` (Default 8); ab `LAURA_DOWNLOAD_MIN_SEGMENT_BYTES`
(Default 8 MiB) wird segmentiert.

Torrent/Magnet/FTP/Metalink laufen über das optionale Binary **`aria2c`** (One-shot,
als separater Prozess aufgerufen — GPL bleibt arm's length, wie bei ffmpeg). Fehlt
`aria2c`, sind nur diese Protokolle nicht verfügbar; HTTP(S) funktioniert voll. Ein
Mehrdatei-Torrent erzeugt **ein Asset pro Mediendatei**; Nicht-Mediendateien (`.nfo`
etc.) werden ignoriert.
```

- [ ] **Step 4: Commit**

```bash
git add docs/06-storage.md
git status   # confirm nothing under apps/desktop/ is staged
git commit -m "$(printf 'docs: document segmented httpx + optional aria2 download engines\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Notes carried from the spec

- **GPL safety:** `aria2c` is only ever a subprocess (Task 5) — never linked. Optional extra; backend starts and does HTTP ingest without it.
- **Per-engine retry:** httpx path keeps "verify fails → discard + re-download" (single asset). aria2 path: a corrupt single media file marks only *its* asset offline (non-fatal `_finalize_media_asset` returns False); aria2 itself resumes on job retry via its control file.
- **Real torrent + real flaky-network runs are manual smoke tests** (need trackers/peers / network impairment) — automated tests cover segmentation, engine selection, the aria2 HTTP path (if installed), and the fan-out logic (aria2 layer mocked, real ffmpeg).
