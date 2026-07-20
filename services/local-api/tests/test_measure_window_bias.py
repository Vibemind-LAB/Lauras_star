"""The window-bias report script, run against a synthetic board tree.

Spec 2026-07-20-window-bias-design.md §3: the same script that produced the baseline table
must run again after the next live run — this test pins its reading of the board layout
(current scene_reviews only, degraded rows excluded from the summary, unreadable files
skipped) so that rerun stays comparable.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "measure_window_bias.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("measure_window_bias", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_review(root: Path, run: str, scene: int, payload: dict[str, Any]) -> None:
    reviews = root / run / "board" / "scene_reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    (reviews / f"scene_{scene}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_scan_and_summary_over_fixture_boards(tmp_path: Path) -> None:
    mod = _load()
    _write_review(
        tmp_path,
        "run-a",
        1,
        {
            "scene_number": 1,
            "src_start_frame": 0,
            "src_end_frame_exclusive": 1350,
            "description": "an org chart",
            "whats_happening": "no significant changes",
            "hook_score": 3,
            "best_window": {"offset_s": 0.0, "duration_s": 0.5},
            "windows": [
                {"offset_s": 0.0, "duration_s": 0.5},
                {"offset_s": 1.0, "duration_s": 0.5},
            ],
        },
    )
    _write_review(
        tmp_path,
        "run-a",
        2,
        {
            "scene_number": 2,
            "src_start_frame": 0,
            "src_end_frame_exclusive": 900,
            "description": "the cursor drags a node",
            "whats_happening": "a panel opens",
            "hook_score": 7,
            "best_window": {"offset_s": 2.0, "duration_s": 8.0},
            "windows": [],
        },
    )
    _write_review(
        tmp_path,
        "run-a",
        3,
        {
            "scene_number": 3,
            "src_start_frame": 0,
            "src_end_frame_exclusive": 300,
            "description": "transcript only",
            "whats_happening": "",
            "hook_score": 5,
            "best_window": {"offset_s": 0.0, "duration_s": 4.0},
            "windows": [],
            "degraded": True,
        },
    )
    (tmp_path / "run-a" / "board" / "scene_reviews" / "scene_9.json").write_text(
        "{not json", encoding="utf-8"
    )

    rows = mod.scan_reviews(tmp_path)

    assert [r["scene"] for r in rows] == [1, 2, 3]  # scene_9 skipped, order stable
    static_row, moving_row, degraded_row = rows
    assert static_row["static"] is True
    assert static_row["scene_s"] == 45.0
    assert static_row["n_windows"] == 2
    assert static_row["min_window_s"] == 0.5
    assert moving_row["static"] is False
    assert moving_row["median_window_s"] == 8.0  # best_window fallback for windows == []
    assert degraded_row["degraded"] is True

    summary = mod.summarize(rows)
    assert summary["static"] == {
        "n": 1,
        "median_window_median_s": 0.5,
        "median_hook": 3,
        "reviews_with_subsecond_window": 1,
    }
    assert summary["moving"] == {
        "n": 1,
        "median_window_median_s": 8.0,
        "median_hook": 7,
        "reviews_with_subsecond_window": 0,
    }


def test_main_reports_missing_root(tmp_path: Path) -> None:
    mod = _load()
    assert mod.main([str(tmp_path / "nope")]) == 2
