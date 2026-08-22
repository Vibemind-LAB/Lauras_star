#!/usr/bin/env python3
"""demo.py — see Laura produce a finished video, without supplying any footage.

What it does (about a minute, fully offline, no API keys, no models):

  1. generates a synthetic source clip with ffmpeg (colour bars + tone),
  2. creates a project and imports the clip,
  3. builds a *narrated reel*: three beats, each one line of narration over a
     window of the source. Laura synthesizes the narration with the
     dependency-free ``stub`` voice, derives each clip's length from the spoken
     length, adds crossfades, burns karaoke captions and renders,
  4. prints the path of the finished MP4.

This is the real pipeline — the same endpoint the desktop app and the MCP tools
use, not a mock.

Usage:
    # with the backend running (cd services/local-api && uv run laura-api)
    python scripts/demo.py
    python scripts/demo.py --keep       # keep the generated project

Requirements: a running backend, ffmpeg on PATH. Nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_URL = os.environ.get("LAURA_URL", "http://127.0.0.1:8765")
FPS = 30
CLIP_SECONDS = 20

BEATS = [
    "This is Laura, a local-first editorial assistant.",
    "It cuts from the transcript and keeps every edit frame accurate.",
    "Everything you just saw was rendered on this machine.",
]


def _token() -> str:
    """Read the API token from the environment, else from the repo .env."""
    token = os.environ.get("LAURA_TOKEN")
    if token:
        return token
    env_file = REPO_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("LAURA_TOKEN="):
                return line.partition("=")[2].strip()
    return ""


def _request(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    token = _token()
    if token:
        headers["X-Laura-Token"] = token
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:  # surface the backend's own message
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise SystemExit(f"error: {method} {path} failed with HTTP {exc.code}\n{detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"error: cannot reach the backend at {BASE_URL} ({exc.reason}).\n"
            "Start it first:  cd services/local-api && uv run laura-api"
        ) from exc


def _ffmpeg() -> str:
    return os.environ.get("LAURA_FFMPEG") or "ffmpeg"


def _make_clip(dest: Path) -> None:
    """Colour bars with a burnt-in timecode and a quiet tone — 20 s, CFR."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate={FPS}:duration={CLIP_SECONDS}",
        "-f", "lavfi", "-i", f"sine=frequency=220:duration={CLIP_SECONDS}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except FileNotFoundError as exc:
        raise SystemExit("error: ffmpeg is not on PATH — install it or set LAURA_FFMPEG.") from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"error: ffmpeg failed:\n{exc.stderr.decode(errors='replace')[:400]}") from exc


def _wait_for_job(job_id: str, what: str, timeout_s: int = 600) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        job = _request("GET", f"/jobs/{job_id}")
        status = job.get("status")
        if status == "succeeded":
            return job
        if status == "failed":
            raise SystemExit(f"error: {what} failed:\n{job.get('error_json') or '(no detail)'}")
        time.sleep(2)
    raise SystemExit(f"error: {what} did not finish within {timeout_s}s")


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a demo video with Laura.")
    parser.add_argument("--keep", action="store_true", help="keep the demo project afterwards")
    args = parser.parse_args()

    print(f"Laura demo - backend {BASE_URL}")
    health = _request("GET", "/healthz")
    print(f"  backend ok (version {health.get('version', '?')})")

    clip = REPO_ROOT / "workspace" / "demo" / "demo-source.mp4"
    print(f"  generating a {CLIP_SECONDS}s synthetic clip ...")
    _make_clip(clip)

    project = _request("POST", "/projects", {"name": "Laura Demo"})
    project_id = project["id"]
    print(f"  project {project_id[:8]} created")

    imported = _request(
        "POST", f"/projects/{project_id}/assets/import",
        {"source_path": str(clip), "display_name": "Demo source"},
    )
    _wait_for_job(imported["job_id"], "import")
    asset_id = imported["asset_id"]
    print(f"  imported and probed asset {asset_id[:8]}")

    # Three beats over three different windows of the source. Each beat's length
    # comes from how long the narration actually takes to speak.
    step = (CLIP_SECONDS * FPS) // (len(BEATS) + 1)
    beats = [
        {"text": text, "asset_id": asset_id, "src_in_frame": step * i}
        for i, text in enumerate(BEATS)
    ]
    print(f"  building a narrated reel from {len(beats)} beats (stub voice, captions) ...")
    reel = _request(
        "POST", f"/projects/{project_id}/narrated-reel",
        {
            "name": "Laura Demo Reel",
            "beats": beats,
            "backend": "stub",  # deterministic and offline; no model, no key
            "render": True,
            "caption_preset": "wide",
        },
    )
    job = _wait_for_job(reel["job_id"], "narrated reel")

    result = job.get("result_json")
    result = json.loads(result) if isinstance(result, str) else (result or {})
    export_id = result.get("export_id")
    if not export_id:
        raise SystemExit("error: the reel finished but produced no export")

    deadline = time.time() + 600
    while time.time() < deadline:
        export = _request("GET", f"/exports/{export_id}")
        if export.get("status") == "ready":
            break
        if export.get("status") == "failed":
            raise SystemExit("error: the render failed — see the backend log")
        time.sleep(3)
    else:
        raise SystemExit("error: the render did not finish in time")

    print()
    print("Done. Laura rendered:")
    print(f"  {export['path']}")
    print()
    print("What just happened: three narration lines were synthesized, each clip was cut to")
    print("the length of its spoken line, crossfades and a fade-out were applied, and the")
    print("narration was burnt in as karaoke captions - all locally.")
    if not args.keep:
        print()
        print(f"Clean up with:  DELETE {BASE_URL}/projects/{project_id}   (or pass --keep next time)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
