"""VE1 — visual frame-embedder + RGB sampler + ``shorts.embed_frames`` job.

All tests run **without any model or download**: the embedder and the RGB frame
loader are injected as deterministic fakes. The pure ``sample_frame_indices`` is
unit-tested in isolation, and the job is driven against an in-memory DB seeded
with an asset + succeeded analysis run + shots.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from laura.analysis.embeddings_store import SqliteVectorStore
from laura.analysis.visual_embed import (
    DEFAULT_FPS_SAMPLE,
    FRAME_H,
    FRAME_W,
    Embedder,
    handle_embed_frames,
    sample_frame_indices,
)
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.jobs.runner import JobContext

# ---------------------------------------------------------------------------
# Fakes (no model, no ffmpeg)
# ---------------------------------------------------------------------------


class FakeEmbedder:
    """Deterministic stand-in for the real CLIP embedder. ``(N, 4)`` float32."""

    def __init__(self) -> None:
        self.name = "fake"
        self.dims = 4

    def embed_frames(self, frames: list[np.ndarray]) -> np.ndarray:
        # Row i = [i, mean(frame_i), 0, 1]; deterministic and shape-correct.
        rows = [
            [float(i), float(np.mean(f)), 0.0, 1.0] for i, f in enumerate(frames)
        ]
        return np.asarray(rows, dtype=np.float32).reshape(len(frames), self.dims)


def _fake_loader(video: Path | str, frames: list[int], w: int, h: int) -> list[np.ndarray]:
    """Return one tiny RGB array per requested index (same count, same order)."""
    return [np.full((h, w, 3), idx % 256, dtype=np.uint8) for idx in frames]


def _short_loader(video: Path | str, frames: list[int], w: int, h: int) -> list[np.ndarray]:
    """Decoder that yields one fewer frame than requested (EOF truncation case)."""
    full = _fake_loader(video, frames, w, h)
    return full[:-1] if full else full


# ---------------------------------------------------------------------------
# Embedder protocol conformance
# ---------------------------------------------------------------------------


def test_fake_embedder_satisfies_protocol() -> None:
    assert isinstance(FakeEmbedder(), Embedder)


# ---------------------------------------------------------------------------
# sample_frame_indices (pure)
# ---------------------------------------------------------------------------


def test_sample_grid_one_fps_at_30fps() -> None:
    # 30 fps, 1 fps sample → step 30; grid over [0, 90) = {0, 30, 60}.
    idx = sample_frame_indices(90, 30, 1, [], fps_sample=DEFAULT_FPS_SAMPLE)
    assert idx == [0, 30, 60]


def test_sample_unions_and_sorts_shot_boundaries() -> None:
    # Boundaries 12 and 30 (30 already on the grid) union into the grid, sorted, deduped.
    idx = sample_frame_indices(90, 30, 1, [30, 12], fps_sample=1.0)
    assert idx == [0, 12, 30, 60]


def test_sample_clamps_boundaries_to_range() -> None:
    # Out-of-range boundaries (negative, == total, > total) are dropped.
    idx = sample_frame_indices(60, 30, 1, [-5, 60, 200, 45], fps_sample=1.0)
    assert idx == [0, 30, 45]
    assert all(0 <= f < 60 for f in idx)


def test_sample_total_frames_zero_is_empty() -> None:
    assert sample_frame_indices(0, 30, 1, [10, 20]) == []


def test_sample_no_shots_is_pure_grid() -> None:
    idx = sample_frame_indices(100, 25, 1, [], fps_sample=1.0)
    assert idx == [0, 25, 50, 75]


def test_sample_step_at_least_one() -> None:
    # fps_sample far above fps → step floors to 1 → every frame in range.
    idx = sample_frame_indices(5, 30, 1, [], fps_sample=1000.0)
    assert idx == [0, 1, 2, 3, 4]


# ---------------------------------------------------------------------------
# Job: seeding helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    return db


def _ctx(db: SqliteDatabase, payload: dict[str, Any]) -> JobContext:
    return JobContext(
        job_id="job-1",
        kind="shorts.embed_frames",
        queue="analysis.scene",
        payload=payload,
        db=db,
    )


def _seed(db: SqliteDatabase, *, succeeded: bool = True, duration_frames: int = 90) -> str:
    """Project + 30 fps asset + analysis run (+ shots). Returns asset_id."""
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a",
        source_path="/tmp/a.mp4",
    )
    with db.transaction() as conn:
        conn.execute(
            "UPDATE media_assets SET rate_num=30, rate_den=1, duration_frames=? WHERE id=?",
            (duration_frames, asset["id"]),
        )
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="t", config={})
    repos.start_analysis_run(db, run["id"])
    repos.insert_shots(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        shots=[
            {"src_in_frame": 0, "src_out_frame_exclusive": 45, "method": "test"},
            {"src_in_frame": 45, "src_out_frame_exclusive": 90, "method": "test"},
        ],
    )
    if succeeded:
        repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})
    return asset["id"]


# ---------------------------------------------------------------------------
# Job: happy path with injected fakes
# ---------------------------------------------------------------------------


def test_embed_persists_vectors_with_fakes(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    asset_id = _seed(db)

    result = handle_embed_frames(
        _ctx(db, {"asset_id": asset_id}),
        embedder=FakeEmbedder(),
        frame_loader=_fake_loader,
    )

    assert result["ok"] is True
    assert result["model"] == "fake"
    assert result["dims"] == 4
    # 30 fps over [0, 90) → {0, 30, 60}; boundary 45 unions in → 4 samples.
    assert result["frames"] == 4

    stored = SqliteVectorStore(db).list_frame_embeddings(asset_id, result["analysis_run_id"])
    assert len(stored) == 4
    assert all(e.model == "fake" for e in stored)
    assert all(e.dims == 4 for e in stored)
    # Persisted frames match the sampled grid ∪ boundary, ordered.
    assert [e.frame for e in stored] == [0, 30, 45, 60]
    # Frame↔vector BINDING: _fake_loader fills frame idx with value (idx % 256),
    # FakeEmbedder writes mean(frame) into vector[1], so vector[1] must equal
    # float(frame_index % 256).  A swap or shift in the binding would break this.
    for e in stored:
        assert e.vector[1] == float(e.frame % 256), (
            f"frame {e.frame}: expected vector[1]={float(e.frame % 256)}, got {e.vector[1]}"
        )


def test_embed_truncates_when_decoder_returns_fewer(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    asset_id = _seed(db)

    result = handle_embed_frames(
        _ctx(db, {"asset_id": asset_id}),
        embedder=FakeEmbedder(),
        frame_loader=_short_loader,  # yields N-1 frames
    )

    assert result["ok"] is True
    assert result["frames"] == 3  # 4 requested, 3 decoded → truncated to common prefix
    stored = SqliteVectorStore(db).list_frame_embeddings(asset_id, result["analysis_run_id"])
    assert [e.frame for e in stored] == [0, 30, 45]
    # Alignment after truncation: the surviving prefix must still carry the correct
    # per-frame content.  _short_loader drops the LAST requested frame (idx=60), so
    # frames [0, 30, 45] are decoded correctly and their vectors must reflect their
    # own indices — not shifted because one frame was dropped from the end.
    for e in stored:
        assert e.vector[1] == float(e.frame % 256), (
            f"frame {e.frame}: expected vector[1]={float(e.frame % 256)}, got {e.vector[1]}"
        )


def test_embed_loader_receives_sample_size(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    asset_id = _seed(db)
    seen: dict[str, Any] = {}

    def _spy(video: Path | str, frames: list[int], w: int, h: int) -> list[np.ndarray]:
        seen["w"], seen["h"], seen["idx"] = w, h, list(frames)
        return _fake_loader(video, frames, w, h)

    handle_embed_frames(
        _ctx(db, {"asset_id": asset_id}), embedder=FakeEmbedder(), frame_loader=_spy
    )
    assert seen["w"] == FRAME_W and seen["h"] == FRAME_H
    assert seen["idx"] == [0, 30, 45, 60]


# ---------------------------------------------------------------------------
# Job: graceful gate + error paths
# ---------------------------------------------------------------------------


def test_embed_skips_when_extra_absent(tmp_path: Path, monkeypatch: Any) -> None:
    db = _make_db(tmp_path)
    asset_id = _seed(db)
    # Force the gate: no injected embedder + extra reported missing → skip, no rows.
    monkeypatch.setattr("laura.analysis.visual_embed.visual_available", lambda: False)
    monkeypatch.setattr("laura.analysis.sidecar.sidecar_healthy", lambda *a, **k: False)

    result = handle_embed_frames(_ctx(db, {"asset_id": asset_id}))

    assert result["ok"] is False
    assert result["skipped"] == "no visual backend"
    assert result["asset_id"] == asset_id
    # No analysis run resolved, nothing written.
    run = repos.get_latest_analysis_run(db, asset_id)
    assert run is not None
    assert SqliteVectorStore(db).list_frame_embeddings(asset_id, run["id"]) == []


def test_embed_no_succeeded_run_returns_error(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    asset_id = _seed(db, succeeded=False)  # run is running, not succeeded

    result = handle_embed_frames(
        _ctx(db, {"asset_id": asset_id}), embedder=FakeEmbedder(), frame_loader=_fake_loader
    )

    assert result["ok"] is False
    assert result["error"] == "no succeeded analysis run"
    assert result["asset_id"] == asset_id


def test_embed_zero_samples_persists_empty(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    # duration_frames = 0 → sample_frame_indices returns [] → empty persist, ok True.
    asset_id = _seed(db, duration_frames=0)

    result = handle_embed_frames(
        _ctx(db, {"asset_id": asset_id}), embedder=FakeEmbedder(), frame_loader=_fake_loader
    )

    assert result["ok"] is True
    assert result["frames"] == 0
    assert SqliteVectorStore(db).list_frame_embeddings(asset_id, result["analysis_run_id"]) == []


# ---------------------------------------------------------------------------
# Optional real-model smoke (skipped unless the extra is installed; no assertions
# that trigger a download — kept here as a marker for manual/local runs only).
# ---------------------------------------------------------------------------


def test_fastembed_importable_smoke() -> None:
    import pytest

    pytest.importorskip("fastembed")
    pytest.importorskip("PIL")
    from laura.analysis.visual_embed import visual_available

    assert visual_available() is True
