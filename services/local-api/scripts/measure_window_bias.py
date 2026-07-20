"""Window-bias report over production-board scene reviews.

    uv run python scripts/measure_window_bias.py [AGENT_RUNS_DIR]

Reads every board's CURRENT scene reviews under ``AGENT_RUNS_DIR`` (default
``workspace-livetest/agent-runs`` relative to the CWD; archived ``versions/`` stay out of
scope) and prints one row per review — scene length, window count, min/median window
duration, hook_score, a static-content indicator — plus a static-vs-moving summary over the
live (non-degraded) rows. Baseline table and purpose:
docs/superpowers/specs/2026-07-20-window-bias-design.md.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

_DEFAULT_ROOT = Path("workspace-livetest") / "agent-runs"
_FPS = 30.0
_STATIC_RX = re.compile(
    r"no significant change|static|remains|unchanged|slightly|stationary|little change",
    re.IGNORECASE,
)


def scan_reviews(root: Path, fps: float = _FPS) -> list[dict[str, Any]]:
    """One row per readable scene-review JSON under ``root``; unreadable files are skipped."""
    rows: list[dict[str, Any]] = []
    for review_path in sorted(root.glob("*/board/scene_reviews/scene_*.json")):
        try:
            data = json.loads(review_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        try:
            scene_s = (data["src_end_frame_exclusive"] - data["src_start_frame"]) / fps
            windows = [float(w["duration_s"]) for w in data.get("windows") or []] or [
                float(data["best_window"]["duration_s"])
            ]
        except (KeyError, TypeError, ValueError):
            continue
        text = f"{data.get('description', '')} {data.get('whats_happening', '')}"
        rows.append(
            {
                "run": review_path.parts[-4][:8],
                "scene": data.get("scene_number"),
                "scene_s": round(scene_s, 1),
                "n_windows": len(windows),
                "min_window_s": round(min(windows), 2),
                "median_window_s": round(statistics.median(windows), 2),
                "hook_score": data.get("hook_score"),
                "static": bool(_STATIC_RX.search(text)),
                "degraded": bool(data.get("degraded", False)),
            }
        )
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Static-vs-moving summary over the LIVE (non-degraded) rows."""
    live = [r for r in rows if not r["degraded"]]
    out: dict[str, dict[str, Any]] = {}
    for name in ("static", "moving"):
        group = [r for r in live if r["static"] is (name == "static")]
        if not group:
            continue
        hooks = [r["hook_score"] for r in group if isinstance(r["hook_score"], int)]
        out[name] = {
            "n": len(group),
            "median_window_median_s": round(
                statistics.median([r["median_window_s"] for r in group]), 2
            ),
            "median_hook": statistics.median(hooks) if hooks else None,
            "reviews_with_subsecond_window": sum(
                1 for r in group if r["min_window_s"] < 1.0
            ),
        }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=_DEFAULT_ROOT)
    args = parser.parse_args(argv)
    root: Path = args.root
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    rows = scan_reviews(root)
    live = [r for r in rows if not r["degraded"]]
    print(f"reviews total={len(rows)} live(VLM)={len(live)}")
    print(
        f"{'run':<10}{'scene':<7}{'scene_s':<9}{'n':<4}"
        f"{'min_w':<8}{'med_w':<8}{'hook':<6}static"
    )
    for r in live:
        print(
            f"{r['run']:<10}{str(r['scene']):<7}{r['scene_s']:<9}{r['n_windows']:<4}"
            f"{r['min_window_s']:<8}{r['median_window_s']:<8}{str(r['hook_score']):<6}"
            f"{r['static']}"
        )
    for name, stats in summarize(rows).items():
        print(
            f"{name}: n={stats['n']} "
            f"median_window_median={stats['median_window_median_s']}s "
            f"median_hook={stats['median_hook']} "
            f"reviews_with_subsecond_window={stats['reviews_with_subsecond_window']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
