# Smart Rough-Cut Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `from-shots` produce a shorter, cleaner rough cut by detecting and dropping weak shots (black, frozen/static, near-duplicate, micro) with deterministic CPU image-math, plus a stronger default detector.

**Architecture:** A new `analysis/quality.py` computes per-shot metrics by sampling a few grayscale frames via ffmpeg (numpy only — no OpenCV/GPU). Metrics + a `keep`/`drop_reason` decision are stored on the `shots` row (migration). `from-shots` filters on those, merges micro-shots, and returns the dropped list so the UI can re-include. Default shot detector becomes PySceneDetect `AdaptiveDetector`.

**Tech Stack:** Python 3.11 / uv, PySceneDetect (`[scene]` extra), ffmpeg (`ingest/ffmpeg.run_ffmpeg`), numpy, FastAPI, pytest. Work in the worktree `C:\Users\User\Desktop\Laura-scene` on branch `sw-iso`; commit+push each task.

**Spec:** `docs/superpowers/specs/2026-06-03-rough-cut-improvements-design.md`

---

## File Structure

- `services/local-api/src/laura/analysis/quality.py` — **new**: `ShotMetrics`, `compute_shot_metrics()`, `decide_keep()`, `mark_duplicates()`. Pure-ish: frame sampling via ffmpeg, math via numpy. One responsibility: per-shot quality.
- `services/local-api/src/laura/analysis/shots.py` — **modify**: add `detector` param, default `"adaptive"`.
- `services/local-api/src/laura/db/migrations/0005_shot_quality.sql` — **new**: add metric columns to `shots`.
- `services/local-api/src/laura/db/repos.py` — **modify**: `insert_shots` stores metric fields; `list_shots`/`get_shot` already `SELECT *` so they surface them.
- `services/local-api/src/laura/analysis/handlers.py` — **modify**: in the scene stage, after `detect_shots`, compute metrics + decide keep, pass to `insert_shots`.
- `services/local-api/src/laura/__init__.py` — **modify**: bump `PIPELINE_VERSION`.
- `services/local-api/src/laura/api/models.py` — **modify**: extend `FromShotsRequest` (filter flags) + `ShotOut` (metric fields); add `FromShotsOut`.
- `services/local-api/src/laura/api/timelines.py` — **modify**: `from-shots` filters/merges, returns dropped list.
- Tests: `tests/test_shot_quality.py`, `tests/test_timeline_from_shots.py` (extend).

---

## Task 1: AdaptiveDetector as the default detector

**Files:**
- Modify: `services/local-api/src/laura/analysis/shots.py`
- Test: `services/local-api/tests/test_analysis_shots.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_analysis_shots.py`:

```python
def test_detect_shots_accepts_detector_name() -> None:
    import inspect

    from laura.analysis import shots

    sig = inspect.signature(shots.detect_shots)
    assert "detector" in sig.parameters
    assert sig.parameters["detector"].default == "adaptive"
```

- [ ] **Step 2: Run it, expect FAIL**

Run: `uv run --no-sync pytest tests/test_analysis_shots.py::test_detect_shots_accepts_detector_name -q`
Expected: FAIL (`detector` not in signature).

- [ ] **Step 3: Implement** — replace the body of `detect_shots` in `shots.py`:

```python
DEFAULT_THRESHOLD = 27.0
_DETECTORS = {"adaptive", "content", "histogram"}


def detect_shots(
    video_path: Path | str,
    *,
    detector: str = "adaptive",
    threshold: float = DEFAULT_THRESHOLD,
) -> list[ShotResult]:
    """Detect shot boundaries (end-exclusive source-frame ranges).

    ``detector`` selects PySceneDetect's algorithm: ``adaptive`` (rolling content score,
    fewer false cuts on motion — default), ``content`` (HSV content), or ``histogram``
    (Y-channel histogram correlation). Raises ImportError if the ``scene`` extra is absent.
    """
    if detector not in _DETECTORS:
        raise ValueError(f"unknown detector {detector!r}; choose one of {sorted(_DETECTORS)}")
    from scenedetect import AdaptiveDetector, ContentDetector, HistogramDetector, detect

    if detector == "adaptive":
        algo = AdaptiveDetector()
    elif detector == "histogram":
        algo = HistogramDetector()
    else:
        algo = ContentDetector(threshold=threshold)

    scenes = detect(str(video_path), algo)
    return [
        ShotResult(
            src_in_frame=int(start.frame_num),
            src_out_frame_exclusive=int(end.frame_num),
            method=f"pyscenedetect:{detector}",
        )
        for start, end in scenes
    ]
```

- [ ] **Step 4: Run it, expect PASS**

Run: `uv run --no-sync pytest tests/test_analysis_shots.py -q`
Expected: PASS. (If `HistogramDetector` import fails on the installed PySceneDetect version, import it lazily inside the `histogram` branch instead.)

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/analysis/shots.py services/local-api/tests/test_analysis_shots.py
git commit -m "feat(scene): selectable shot detector, default AdaptiveDetector"
git push origin sw-iso
```

---

## Task 2: Per-shot quality metrics + keep decision (`analysis/quality.py`)

**Files:**
- Create: `services/local-api/src/laura/analysis/quality.py`
- Test: `services/local-api/tests/test_shot_quality.py`

Design: sample up to `k` evenly-spaced grayscale frames of a shot at a small size via ffmpeg
rawvideo, as `numpy.uint8` arrays. Metrics:
- `black_ratio` = fraction of sampled frames with mean luma < `black_luma` (default 16/255).
- `static_score` = `1 - mean(|f[i]-f[i-1]|)/255` clamped to [0,1] (1 ⇒ frozen).
- `phash` = 64-bit dHash (9×8 gray, horizontal gradient) as 16 hex chars.
- `blur_score` = variance of a numpy Laplacian of the largest sampled frame (low ⇒ blurry).

`decide_keep(metrics, *, thresholds)` → `(keep, reason)`, reason ∈
{`black`,`static`,`blur`,None}. Duplicates need the whole list, so `mark_duplicates(rows)`
sets reason `duplicate` on later members of a pHash cluster (Hamming ≤ `dup_hamming`, default 6).

- [ ] **Step 1: Write the failing tests** — `tests/test_shot_quality.py`:

```python
"""Deterministic per-shot quality metrics + keep decisions."""

from __future__ import annotations

import numpy as np

from laura.analysis.quality import (
    ShotMetrics,
    decide_keep,
    dhash,
    hamming,
    mark_duplicates,
    static_score,
)


def test_black_frame_metrics() -> None:
    frames = [np.zeros((36, 64), dtype=np.uint8) for _ in range(4)]
    m = ShotMetrics.from_frames(frames)
    assert m.black_ratio == 1.0
    assert decide_keep(m)[1] == "black"


def test_static_frames_score_high() -> None:
    f = np.full((36, 64), 120, dtype=np.uint8)
    assert static_score([f, f.copy(), f.copy()]) == 1.0


def test_moving_frames_score_low() -> None:
    a = np.zeros((36, 64), dtype=np.uint8)
    b = np.full((36, 64), 255, dtype=np.uint8)
    assert static_score([a, b, a.copy()]) < 0.2


def test_identical_frames_share_phash() -> None:
    rng = np.arange(72, dtype=np.uint8).reshape(8, 9)
    assert dhash(rng) == dhash(rng.copy())
    assert hamming(dhash(rng), dhash(rng.copy())) == 0


def test_mark_duplicates_keeps_first() -> None:
    rows = [
        {"phash": "ffffffffffffffff", "keep": True, "drop_reason": None},
        {"phash": "ffffffffffffffff", "keep": True, "drop_reason": None},
        {"phash": "0000000000000000", "keep": True, "drop_reason": None},
    ]
    mark_duplicates(rows, dup_hamming=2)
    assert [r["drop_reason"] for r in rows] == [None, "duplicate", None]
```

- [ ] **Step 2: Run, expect FAIL**

Run: `uv run --no-sync pytest tests/test_shot_quality.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `quality.py`:**

```python
"""Per-shot quality metrics for video-driven rough-cut filtering.

Deterministic and CPU-only: sample a few small grayscale frames per shot via ffmpeg and
score them with numpy. No OpenCV, no GPU. Used to drop black / frozen / duplicate / blurry
shots when building a rough cut from scenes.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

SAMPLE_W, SAMPLE_H = 64, 36
SAMPLE_K = 5
BLACK_LUMA = 16.0


def _sample_gray_frames(
    video: Path | str, src_in: int, src_out: int, *, k: int = SAMPLE_K,
    w: int = SAMPLE_W, h: int = SAMPLE_H,
) -> list[np.ndarray]:
    """Extract up to ``k`` evenly-spaced grayscale frames of [src_in, src_out) as HxW uint8."""
    n = max(1, src_out - src_in)
    count = min(k, n)
    idxs = sorted({src_in + (i * n) // count for i in range(count)})
    expr = "+".join(f"eq(n\\,{i})" for i in idxs)
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(video),
        "-vf", f"select='{expr}',scale={w}:{h},format=gray",
        "-vsync", "0", "-frames:v", str(len(idxs)), "-f", "rawvideo", "-",
    ]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    frame_bytes = w * h
    frames: list[np.ndarray] = []
    for off in range(0, len(out) - frame_bytes + 1, frame_bytes):
        frames.append(np.frombuffer(out[off : off + frame_bytes], dtype=np.uint8).reshape(h, w))
    return frames


def static_score(frames: list[np.ndarray]) -> float:
    if len(frames) < 2:
        return 1.0
    diffs = [float(np.mean(np.abs(frames[i].astype(np.int16) - frames[i - 1])))
             for i in range(1, len(frames))]
    return max(0.0, min(1.0, 1.0 - (sum(diffs) / len(diffs)) / 255.0))


def dhash(frame: np.ndarray, *, hash_w: int = 9, hash_h: int = 8) -> str:
    """64-bit difference hash as 16 hex chars (resize then horizontal gradient sign)."""
    small = _resize_nearest(frame, hash_w, hash_h).astype(np.int16)
    bits = small[:, 1:] > small[:, :-1]
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def _resize_nearest(frame: np.ndarray, w: int, h: int) -> np.ndarray:
    ys = (np.arange(h) * frame.shape[0] // h).clip(0, frame.shape[0] - 1)
    xs = (np.arange(w) * frame.shape[1] // w).clip(0, frame.shape[1] - 1)
    return frame[np.ix_(ys, xs)]


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _laplacian_var(frame: np.ndarray) -> float:
    f = frame.astype(np.float32)
    lap = (-4 * f
           + np.roll(f, 1, 0) + np.roll(f, -1, 0)
           + np.roll(f, 1, 1) + np.roll(f, -1, 1))
    return float(np.var(lap[1:-1, 1:-1]))


@dataclass(frozen=True)
class ShotMetrics:
    black_ratio: float
    static: float
    phash: str
    blur: float

    @classmethod
    def from_frames(cls, frames: list[np.ndarray]) -> "ShotMetrics":
        if not frames:
            return cls(black_ratio=1.0, static=1.0, phash="0" * 16, blur=0.0)
        black = sum(1 for f in frames if float(np.mean(f)) < BLACK_LUMA) / len(frames)
        return cls(
            black_ratio=black,
            static=static_score(frames),
            phash=dhash(frames[len(frames) // 2]),
            blur=_laplacian_var(frames[len(frames) // 2]),
        )


def compute_shot_metrics(video: Path | str, src_in: int, src_out: int) -> ShotMetrics:
    return ShotMetrics.from_frames(_sample_gray_frames(video, src_in, src_out))


@dataclass(frozen=True)
class KeepThresholds:
    black_ratio: float = 0.8
    static: float = 0.985
    blur: float = 5.0  # below = too blurry; 0 disables


def decide_keep(m: ShotMetrics, *, thresholds: KeepThresholds = KeepThresholds()) -> tuple[bool, str | None]:
    if m.black_ratio >= thresholds.black_ratio:
        return False, "black"
    if m.static >= thresholds.static:
        return False, "static"
    if thresholds.blur > 0 and m.blur < thresholds.blur:
        return False, "blur"
    return True, None


def mark_duplicates(rows: list[dict[str, Any]], *, dup_hamming: int = 6) -> None:
    """Set drop_reason='duplicate' on later shots whose phash is near an earlier kept shot."""
    kept: list[str] = []
    for row in rows:
        if not row.get("keep") or not row.get("phash"):
            continue
        if any(hamming(row["phash"], h) <= dup_hamming for h in kept):
            row["keep"] = False
            row["drop_reason"] = "duplicate"
        else:
            kept.append(row["phash"])
```

- [ ] **Step 4: Run, expect PASS**

Run: `uv run --no-sync pytest tests/test_shot_quality.py -q`
Expected: PASS (5 tests). Then `uv run --no-sync ruff check src/laura/analysis/quality.py tests/test_shot_quality.py` and `uv run --no-sync mypy src/laura/analysis/quality.py` — both clean. Add `numpy.*` to mypy overrides if needed (already present per pyproject).

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/analysis/quality.py services/local-api/tests/test_shot_quality.py
git commit -m "feat(analysis): deterministic per-shot quality metrics + keep decision"
git push origin sw-iso
```

---

## Task 3: Persist metrics (migration + repos + handler wiring + pipeline bump)

**Files:**
- Create: `services/local-api/src/laura/db/migrations/0005_shot_quality.sql`
- Modify: `services/local-api/src/laura/db/repos.py` (`insert_shots`)
- Modify: `services/local-api/src/laura/analysis/handlers.py` (scene stage)
- Modify: `services/local-api/src/laura/__init__.py` (`PIPELINE_VERSION`)
- Test: `services/local-api/tests/test_shot_quality.py` (extend), `tests/test_db_backend.py` (schema version)

- [ ] **Step 1: Write the migration** `0005_shot_quality.sql` (match the existing migrations' statement-per-line style; verify the next number by listing `db/migrations/`):

```sql
ALTER TABLE shots ADD COLUMN black_ratio REAL;
ALTER TABLE shots ADD COLUMN static_score REAL;
ALTER TABLE shots ADD COLUMN phash TEXT;
ALTER TABLE shots ADD COLUMN blur_score REAL;
ALTER TABLE shots ADD COLUMN keep INTEGER NOT NULL DEFAULT 1;
ALTER TABLE shots ADD COLUMN drop_reason TEXT;
```

- [ ] **Step 2: Extend `insert_shots`** in `repos.py` to persist the new optional fields. Read the current function first; replace its INSERT to include the columns, defaulting missing keys:

```python
def insert_shots(
    db: Database, *, asset_id: str, run_id: str, shots: list[dict[str, Any]]
) -> int:
    with db.transaction() as conn:
        for shot in shots:
            conn.execute(
                "INSERT INTO shots (id, asset_id, analysis_run_id, src_in_frame, "
                "src_out_frame_exclusive, confidence, method, thumbnail_path, "
                "black_ratio, static_score, phash, blur_score, keep, drop_reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id(), asset_id, run_id, shot["src_in_frame"],
                 shot["src_out_frame_exclusive"], shot.get("confidence"),
                 shot.get("method"), shot.get("thumbnail_path"),
                 shot.get("black_ratio"), shot.get("static_score"), shot.get("phash"),
                 shot.get("blur_score"), 1 if shot.get("keep", True) else 0,
                 shot.get("drop_reason")),
            )
    return len(shots)
```

- [ ] **Step 3: Wire the quality pass into the handler.** Read `analysis/handlers.py`; find where `detect_shots(...)` results are turned into rows for `insert_shots`. For each detected shot, compute metrics + keep, build the enriched row, then `mark_duplicates` over the list before insert. Insert this block (proxy path is already resolved in the handler as the scene-stage input; reuse that variable):

```python
from .quality import compute_shot_metrics, decide_keep, mark_duplicates

shot_rows: list[dict[str, Any]] = []
for s in detect_shots(scene_input_path):
    m = compute_shot_metrics(scene_input_path, s.src_in_frame, s.src_out_frame_exclusive)
    keep, reason = decide_keep(m)
    shot_rows.append({
        "src_in_frame": s.src_in_frame,
        "src_out_frame_exclusive": s.src_out_frame_exclusive,
        "method": s.method,
        "black_ratio": m.black_ratio, "static_score": m.static,
        "phash": m.phash, "blur_score": m.blur,
        "keep": keep, "drop_reason": reason,
    })
mark_duplicates(shot_rows)
repos.insert_shots(db, asset_id=asset_id, run_id=run_id, shots=shot_rows)
```

(Keep the existing thumbnail logic if the handler also generates shot thumbnails — add `thumbnail_path` into each row where it already does.)

- [ ] **Step 4: Bump pipeline version** in `laura/__init__.py`: change `PIPELINE_VERSION = "1"` → `"2"` (metrics are part of the analysis state; idempotency by `(input, pipeline_version)`).

- [ ] **Step 5: Schema-version test** — bump the expected `schema_version` in `tests/test_db_backend.py` (and any test asserting it) from its current value to +1. Run `uv run --no-sync pytest tests/test_db_backend.py -q` and fix the constant until green.

- [ ] **Step 6: Run backend suite**

Run: `uv run --no-sync pytest -q` then `uv run --no-sync ruff check .` and `uv run --no-sync mypy src`.
Expected: all green. (The scene stage runs only when the `[scene]` extra is installed; the metric helpers are pure and covered by Task 2.)

- [ ] **Step 7: Commit**

```bash
git add services/local-api/src/laura/db/migrations/0005_shot_quality.sql services/local-api/src/laura/db/repos.py services/local-api/src/laura/analysis/handlers.py services/local-api/src/laura/__init__.py services/local-api/tests
git commit -m "feat(analysis): persist shot quality metrics; pipeline_version 2"
git push origin sw-iso
```

---

## Task 4: `from-shots` filters weak shots and reports the dropped list

**Files:**
- Modify: `services/local-api/src/laura/api/models.py` (`FromShotsRequest`, `ShotOut`, new `FromShotsOut`)
- Modify: `services/local-api/src/laura/api/timelines.py` (`timeline_from_shots`)
- Test: `services/local-api/tests/test_timeline_from_shots.py` (extend)

- [ ] **Step 1: Write the failing test** — append to `tests/test_timeline_from_shots.py` (reuse its `_setup`):

```python
def test_from_shots_drops_weak_and_reports_them(tmp_path: Path) -> None:
    client, db, project, asset = _setup(tmp_path)
    try:
        run = repos.create_analysis_run(
            db, asset_id=asset["id"], pipeline_version="2", config={"stages": {}}
        )
        repos.insert_shots(
            db, asset_id=asset["id"], run_id=run["id"],
            shots=[
                {"src_in_frame": 0, "src_out_frame_exclusive": 50, "method": "t",
                 "keep": True, "drop_reason": None},
                {"src_in_frame": 50, "src_out_frame_exclusive": 90, "method": "t",
                 "keep": False, "drop_reason": "black"},
                {"src_in_frame": 90, "src_out_frame_exclusive": 160, "method": "t",
                 "keep": True, "drop_reason": None},
            ],
        )
        resp = client.post(
            f"/projects/{project['id']}/timelines/from-shots",
            json={"asset_id": asset["id"], "quality": True},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        # only the two kept shots, packed contiguously
        assert [(c["seq_in_frame"], c["seq_out_frame_exclusive"]) for c in body["timeline"]["clips"]] == [
            (0, 50), (50, 120),
        ]
        assert body["dropped"] == [{"src_in_frame": 50, "src_out_frame_exclusive": 90,
                                     "drop_reason": "black"}]
    finally:
        client.__exit__(None, None, None)
```

Note: this changes the success response shape to `FromShotsOut {timeline, dropped}`. Update the
three existing `from-shots` tests to read `resp.json()["timeline"]["clips"]` and
`["timeline"]["id"]` accordingly.

- [ ] **Step 2: Run, expect FAIL**

Run: `uv run --no-sync pytest tests/test_timeline_from_shots.py -q`
Expected: FAIL (no `quality` handling / no `dropped`).

- [ ] **Step 3: Update models** in `models.py`:

```python
class FromShotsRequest(BaseModel):
    """Build a rough cut with one contiguous clip per kept shot of an asset."""

    asset_id: str
    run_id: str | None = None
    timeline_id: str | None = None
    name: str | None = Field(default=None, max_length=200)
    lane: int = Field(default=0, ge=0)
    quality: bool = True                # drop weak shots + merge micro by default
    drop_black: bool | None = None      # per-filter overrides (None = follow `quality`)
    drop_static: bool | None = None
    drop_duplicates: bool | None = None
    drop_blur: bool | None = None
    merge_min_frames: int = Field(default=0, ge=0)


class DroppedShot(BaseModel):
    src_in_frame: int
    src_out_frame_exclusive: int
    drop_reason: str


class FromShotsOut(BaseModel):
    timeline: TimelineOut
    dropped: list[DroppedShot] = Field(default_factory=list)
```

Also add the metric fields to `ShotOut` so the UI can show badges:

```python
class ShotOut(BaseModel):
    id: str
    src_in_frame: int
    src_out_frame_exclusive: int
    confidence: float | None = None
    method: str | None = None
    thumbnail_path: str | None = None
    black_ratio: float | None = None
    static_score: float | None = None
    phash: str | None = None
    blur_score: float | None = None
    keep: bool = True
    drop_reason: str | None = None
```

- [ ] **Step 4: Update the endpoint** `timeline_from_shots` in `timelines.py` — change `response_model` to `FromShotsOut`, import `FromShotsOut`/`DroppedShot`, and replace the shot loop with keep-filtering + micro-merge + dropped collection:

```python
@router.post(
    "/projects/{project_id}/timelines/from-shots",
    response_model=FromShotsOut,
    status_code=status.HTTP_201_CREATED,
)
def timeline_from_shots(
    project_id: str, body: FromShotsRequest, request: Request
) -> FromShotsOut:
    db = _db(request)
    # ... unchanged project/asset/run validation ...

    def enabled(override: bool | None) -> bool:
        return body.quality if override is None else override

    reasons_on = {
        "black": enabled(body.drop_black),
        "static": enabled(body.drop_static),
        "duplicate": enabled(body.drop_duplicates),
        "blur": enabled(body.drop_blur),
    }
    dropped: list[DroppedShot] = []
    rows: list[dict[str, Any]] = []
    offset = 0
    for shot in repos.list_shots(db, body.asset_id, run_id):
        reason = shot.get("drop_reason") if not shot.get("keep", True) else None
        if reason is not None and reasons_on.get(reason, False):
            dropped.append(DroppedShot(
                src_in_frame=shot["src_in_frame"],
                src_out_frame_exclusive=shot["src_out_frame_exclusive"],
                drop_reason=reason,
            ))
            continue
        length = shot["src_out_frame_exclusive"] - shot["src_in_frame"]
        if length <= 0:
            continue
        merged = (
            body.merge_min_frames > 0 and length < body.merge_min_frames and rows
        )
        if merged:
            rows[-1]["src_out_frame_exclusive"] = shot["src_out_frame_exclusive"]
            rows[-1]["seq_out_frame_exclusive"] += length
            offset += length
            continue
        rows.append({
            "asset_id": body.asset_id,
            "src_in_frame": shot["src_in_frame"],
            "src_out_frame_exclusive": shot["src_out_frame_exclusive"],
            "seq_in_frame": offset,
            "seq_out_frame_exclusive": offset + length,
            "lane": body.lane, "speed_num": 1, "speed_den": 1,
        })
        offset += length
    if not rows:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "no shots left after filtering")

    # ... unchanged target-timeline selection (empty timeline_id else new) ...
    repos.replace_timeline_clips(db, target["id"], rows)
    fresh = repos.get_timeline(db, target["id"])
    assert fresh is not None
    repos.update_timeline_otio(db, fresh["id"], timeline_to_otio_string(_build_model(db, fresh)))
    return FromShotsOut(timeline=_timeline_out(db, fresh), dropped=dropped)
```

(Note the micro-merge across `src` gaps merges sequence-contiguously but spans the source gap;
that is acceptable for a rough cut — the merged clip's source range covers both. If undesired,
only merge when the previous clip's `src_out_frame_exclusive == shot["src_in_frame"]`.)

- [ ] **Step 5: Update the three existing from-shots tests** to the new response shape (`["timeline"]["clips"]`, `["timeline"]["id"]`). Run:

`uv run --no-sync pytest tests/test_timeline_from_shots.py -q` → PASS (4 tests).

- [ ] **Step 6: Full backend gate**

Run: `uv run --no-sync pytest -q && uv run --no-sync ruff check . && uv run --no-sync mypy src`
Expected: all green.

- [ ] **Step 7: Update the desktop client** signature note: `api.ts buildRoughCutFromShots` now returns `{ timeline, dropped }`. Leave the desktop change for the frontend plan; record it here so nothing is missed.

- [ ] **Step 8: Commit**

```bash
git add services/local-api/src/laura/api/models.py services/local-api/src/laura/api/timelines.py services/local-api/tests/test_timeline_from_shots.py
git commit -m "feat(api): from-shots drops weak shots + reports the dropped list"
git push origin sw-iso
```

---

## Self-Review

- **Spec coverage:** detector default (T1), quality metrics black/static/duplicate/blur (T2),
  storage + pipeline bump (T3), filtering `from-shots` + dropped list + `ShotOut` metrics (T4).
  Frontend badges/tray/SceneInspector + Stage 2 drag + Stage 3 TransNetV2 are **out of scope**
  for this backend plan (separate follow-on plans) — matches the spec's staging.
- **Placeholders:** none — every code step shows real code; wiring tasks (T3 handler) instruct
  reading the file first because the exact insertion point follows existing handler structure.
- **Type consistency:** `ShotMetrics(black_ratio, static, phash, blur)` used consistently;
  `decide_keep` returns `(bool, str|None)`; `mark_duplicates` mutates rows in place; reasons
  `{black,static,blur,duplicate}` align between `decide_keep`/`mark_duplicates` and the
  endpoint's `reasons_on`. Response model `FromShotsOut{timeline,dropped}` used in endpoint +
  tests.

## Risk notes for the implementer

- `HistogramDetector` may be absent on older PySceneDetect — import lazily in its branch.
- ffmpeg `select`/`rawvideo` frame extraction is the one I/O-heavy step; `SAMPLE_K=5` keeps it
  cheap. If `scene_input_path` is the proxy (CFR), frame indices line up with shot frames.
- Bumping `PIPELINE_VERSION` invalidates cached analysis — expected; existing assets re-analyse.
