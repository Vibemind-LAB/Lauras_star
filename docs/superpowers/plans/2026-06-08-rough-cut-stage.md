# Rough Cut (Stufe 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the bespoke Rough-Cut stage: group an analysed asset's shots into **scenes** (a lightweight, frame-exact marker layer over the rough-cut timeline) shown in a scene-strip with split/merge/rename.

**Architecture:** A new `scenes` table marks ordered, end-exclusive frame ranges over an existing `rough_cut` timeline; boundaries always fall on clip junctions. A pure grouping function turns the rough-cut's clips + the asset's transcript into scene ranges (break on speaker change or silence gap ≥ threshold; fallback "1 shot = 1 scene" when there is no transcript). A new `/scenes` router exposes generate/list/split/merge/rename. The frontend adds a `Scene` type, a `useScenes` hook, a `SceneStrip` component, and a `RoughCutView` that composes the existing `Player` + scene-strip. Scene generation only groups existing clips; the rough-cut itself is built by the existing, OTIO-correct `from-shots` endpoint (orchestrated in `RoughCutView`), so no tested code is touched. The single `App.tsx` edit (swap the `roughcut` branch to render `RoughCutView`) is the last step, kept tiny because the user edits `App.tsx` in parallel.

**Tech Stack:** Python 3.11 / FastAPI / SQLite / `uv` / `pytest` / `ruff` / `mypy` (strict, no `print` → project logger). TypeScript strict (never `any`) / React / Vitest (plain asserts, no jsdom matchers) / Tailwind. Conventional Commits. Invariants: integer frames, end-exclusive ranges, audio in samples (frames are a projection), OTIO stays source of truth.

**Reference spec:** `docs/superpowers/specs/2026-06-08-rough-cut-stage-design.md`.

**Conventions confirmed from the codebase (mirror these exactly):**
- repos: `with db.transaction() as conn:` for writes, `with db.connection() as conn:` for reads; rows are `sqlite3.Row` → `dict(r)`. IDs via `new_id()`, timestamps via `utcnow_iso()` (both from `laura.util`).
- Migrations: numbered `NNNN_*.sql` in `services/local-api/src/laura/db/migrations/`, auto-applied in order (split on `;`, tracked in `schema_meta`). Next number is `0008`.
- API routers: `router = APIRouter(tags=[...], dependencies=[Depends(require_token)])`; `def _db(request: Request) -> Database: return request.app.state.db`; raise `HTTPException(status.HTTP_4xx_*, "msg")`. Pydantic models live in `api/models.py` (`BaseModel`, `Field`).
- Frontend client: `this.request<T>(path, init?)` (JSON + `X-Laura-Token`); types are flat `interface`s in `api.ts`.
- Hooks mirror `hooks/useAnalysis.ts`; strips mirror `components/ShotStrip.tsx`; frame thumbnails via `client.assetFrameUrl(assetId, frame)` (see `SceneInspector.tsx`).

**GIT HYGIENE (the user works in `App.tsx` in parallel):** stage only the files listed per task. NEVER stage `.claude/`, `build/`, or `uv.lock`. Do NOT switch branches (shared working tree). Do not touch `App.tsx` until Task 9.

All backend commands run from `services/local-api` via `uv run --directory services/local-api …`. Frontend via `npm --prefix apps/desktop …`.

---

### Task 1: `scenes` table migration

**Files:**
- Create: `services/local-api/src/laura/db/migrations/0008_scenes.sql`
- Test: `services/local-api/tests/test_scenes_migration.py`

- [ ] **Step 1: Write the failing test**

```python
# services/local-api/tests/test_scenes_migration.py
from __future__ import annotations

from pathlib import Path

from laura.config import Settings
from laura.db.database import SqliteDatabase


def test_scenes_migration_creates_table(tmp_path: Path) -> None:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    with db.connection() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(scenes)").fetchall()}
    assert {
        "id", "project_id", "source_timeline_id", "name",
        "order_index", "seq_in_frame", "seq_out_frame_exclusive", "created_at",
    } <= cols
```

- [ ] **Step 2: Run it — expect FAIL** (`no such table: scenes` → empty set).

```
uv run --directory services/local-api pytest tests/test_scenes_migration.py -q
```

- [ ] **Step 3: Write the migration**

```sql
-- services/local-api/src/laura/db/migrations/0008_scenes.sql
-- Scenes: a lightweight, end-exclusive marker layer over a rough_cut timeline.
-- Boundaries always fall on clip junctions; scenes tile the timeline contiguously.
CREATE TABLE scenes (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  source_timeline_id TEXT NOT NULL,
  name TEXT NOT NULL,
  order_index INTEGER NOT NULL,
  seq_in_frame INTEGER NOT NULL,
  seq_out_frame_exclusive INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_scenes_timeline ON scenes (source_timeline_id, order_index);
```

- [ ] **Step 4: Run it — expect PASS.**

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/db/migrations/0008_scenes.sql services/local-api/tests/test_scenes_migration.py
git commit -m "feat(scenes): add scenes table migration"
```

---

### Task 2: Pure grouping function `group_into_scenes`

**Files:**
- Create: `services/local-api/src/laura/scenes/__init__.py` (empty)
- Create: `services/local-api/src/laura/scenes/grouping.py`
- Test: `services/local-api/tests/test_scene_grouping.py`

- [ ] **Step 1: Write the failing tests**

```python
# services/local-api/tests/test_scene_grouping.py
from __future__ import annotations

from laura.scenes.grouping import group_into_scenes


def _clip(seq_in: int, seq_out: int) -> dict:
    return {"seq_in_frame": seq_in, "seq_out_frame_exclusive": seq_out}


def _w(start: int, end: int, speaker: str | None) -> dict:
    return {"start_frame": start, "end_frame": end, "speaker": speaker}


def test_empty_clips_returns_empty() -> None:
    assert group_into_scenes([], [], gap_frames=45) == []


def test_single_clip_one_scene() -> None:
    assert group_into_scenes([_clip(0, 30)], [[_w(0, 10, "A")]], gap_frames=45) == [(0, 30)]


def test_speaker_change_breaks() -> None:
    clips = [_clip(0, 30), _clip(30, 60)]
    words = [[_w(0, 10, "A")], [_w(31, 40, "B")]]
    assert group_into_scenes(clips, words, gap_frames=1000) == [(0, 30), (30, 60)]


def test_small_gap_same_speaker_keeps_together() -> None:
    clips = [_clip(0, 30), _clip(30, 60)]
    words = [[_w(0, 28, "A")], [_w(31, 50, "A")]]  # gap = 3 < 45
    assert group_into_scenes(clips, words, gap_frames=45) == [(0, 60)]


def test_large_gap_same_speaker_breaks() -> None:
    clips = [_clip(0, 30), _clip(30, 60)]
    words = [[_w(0, 10, "A")], [_w(58, 60, "A")]]  # gap = 48 >= 45
    assert group_into_scenes(clips, words, gap_frames=45) == [(0, 30), (30, 60)]


def test_no_transcript_anywhere_one_scene_per_clip() -> None:
    clips = [_clip(0, 30), _clip(30, 60), _clip(60, 90)]
    words = [[], [], []]
    assert group_into_scenes(clips, words, gap_frames=45) == [(0, 30), (30, 60), (60, 90)]


def test_partial_empty_clip_keeps_together() -> None:
    clips = [_clip(0, 30), _clip(30, 60)]
    words = [[_w(0, 10, "A")], []]  # one side has words -> no break from emptiness
    assert group_into_scenes(clips, words, gap_frames=45) == [(0, 60)]
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: laura.scenes`).

```
uv run --directory services/local-api pytest tests/test_scene_grouping.py -q
```

- [ ] **Step 3: Implement**

```python
# services/local-api/src/laura/scenes/__init__.py
```

```python
# services/local-api/src/laura/scenes/grouping.py
"""Pure shot/clip -> scene grouping. Boundaries fall only on clip junctions; scenes tile
the rough-cut contiguously. A break is placed between two adjacent clips when the speaker
changes or the inter-clip silence gap reaches ``gap_frames``. When the whole asset has no
transcript words, every clip becomes its own scene (fallback)."""

from __future__ import annotations

from typing import Any


def _speaker(words: list[dict[str, Any]]) -> str | None:
    return words[0].get("speaker") if words else None


def _is_boundary(
    cur: list[dict[str, Any]], nxt: list[dict[str, Any]], gap_frames: int
) -> bool:
    if not cur or not nxt:
        return False  # no transcript evidence across this junction -> keep together
    if _speaker(cur) != _speaker(nxt):
        return True
    gap = nxt[0]["start_frame"] - cur[-1]["end_frame"]
    return gap >= gap_frames


def group_into_scenes(
    clips: list[dict[str, Any]],
    words_by_clip: list[list[dict[str, Any]]],
    *,
    gap_frames: int,
) -> list[tuple[int, int]]:
    """``clips`` ordered by ``seq_in_frame`` (each = one kept shot). ``words_by_clip[i]`` are
    the transcript words covering ``clips[i]`` (ordered by ``start_frame``; each a dict with
    ``start_frame``/``end_frame``/``speaker``; may be empty). Returns ``(seq_in_frame,
    seq_out_frame_exclusive)`` ranges tiling the clips."""
    if not clips:
        return []
    has_any_words = any(words_by_clip)
    scenes: list[tuple[int, int]] = []
    start = clips[0]["seq_in_frame"]
    for i in range(len(clips) - 1):
        boundary = True if not has_any_words else _is_boundary(
            words_by_clip[i], words_by_clip[i + 1], gap_frames
        )
        if boundary:
            scenes.append((start, clips[i]["seq_out_frame_exclusive"]))
            start = clips[i + 1]["seq_in_frame"]
    scenes.append((start, clips[-1]["seq_out_frame_exclusive"]))
    return scenes
```

- [ ] **Step 4: Run — expect PASS.** Then lint/type:

```
uv run --directory services/local-api ruff check src/laura/scenes tests/test_scene_grouping.py
uv run --directory services/local-api mypy src/laura/scenes
```

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/scenes/__init__.py services/local-api/src/laura/scenes/grouping.py services/local-api/tests/test_scene_grouping.py
git commit -m "feat(scenes): transcript-aware shot grouping (pure)"
```

---

### Task 3: Scene repo helpers

**Files:**
- Modify: `services/local-api/src/laura/db/repos.py` (append new helpers near the timeline helpers)
- Test: `services/local-api/tests/test_scenes_repos.py`

- [ ] **Step 1: Write the failing tests**

```python
# services/local-api/tests/test_scenes_repos.py
from __future__ import annotations

from pathlib import Path

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase


def _db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    return db


def test_replace_scenes_auto_names_and_orders(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.replace_scenes(db, "p1", "tl1", [(0, 30), (30, 90)])
    scenes = repos.list_scenes(db, "tl1")
    assert [(s["name"], s["order_index"], s["seq_in_frame"], s["seq_out_frame_exclusive"]) for s in scenes] == [
        ("Szene 1", 0, 0, 30),
        ("Szene 2", 1, 30, 90),
    ]


def test_replace_scenes_is_idempotent(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.replace_scenes(db, "p1", "tl1", [(0, 30)])
    repos.replace_scenes(db, "p1", "tl1", [(0, 60), (60, 90)])
    assert len(repos.list_scenes(db, "tl1")) == 2


def test_update_scene_name(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.replace_scenes(db, "p1", "tl1", [(0, 30)])
    sid = repos.list_scenes(db, "tl1")[0]["id"]
    repos.update_scene_name(db, sid, "Intro")
    assert repos.get_scene(db, sid)["name"] == "Intro"
```

- [ ] **Step 2: Run — expect FAIL** (`AttributeError: module 'laura.db.repos' has no attribute 'replace_scenes'`).

```
uv run --directory services/local-api pytest tests/test_scenes_repos.py -q
```

- [ ] **Step 3: Implement** — append to `services/local-api/src/laura/db/repos.py`:

```python
# --- scenes (rough-cut marker layer) ---------------------------------------

def get_scene(db: Database, scene_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM scenes WHERE id=?", (scene_id,)).fetchone()
        return dict(row) if row is not None else None


def list_scenes(db: Database, source_timeline_id: str) -> list[dict[str, Any]]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM scenes WHERE source_timeline_id=? ORDER BY order_index",
            (source_timeline_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def replace_scenes(
    db: Database,
    project_id: str,
    source_timeline_id: str,
    ranges: list[tuple[int, int]],
) -> None:
    """Replace all scenes of a timeline with ``ranges`` (``(seq_in, seq_out_exclusive)``),
    ordered. Reassigns ids + ``order_index``; names are positional ("Szene N")."""
    now = utcnow_iso()
    with db.transaction() as conn:
        conn.execute("DELETE FROM scenes WHERE source_timeline_id=?", (source_timeline_id,))
        for i, (sin, sout) in enumerate(ranges):
            conn.execute(
                "INSERT INTO scenes (id, project_id, source_timeline_id, name, order_index, "
                "seq_in_frame, seq_out_frame_exclusive, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (new_id(), project_id, source_timeline_id, f"Szene {i + 1}", i, sin, sout, now),
            )


def update_scene_name(db: Database, scene_id: str, name: str) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE scenes SET name=? WHERE id=?", (name, scene_id))
```

Verify `new_id` and `utcnow_iso` are already imported at the top of `repos.py` (they are used by existing helpers like `create_export`). If not, add `from ..util import new_id, utcnow_iso`.

- [ ] **Step 4: Run — expect PASS.** Then:

```
uv run --directory services/local-api ruff check src/laura/db/repos.py tests/test_scenes_repos.py
uv run --directory services/local-api mypy src/laura/db/repos.py
```

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/db/repos.py services/local-api/tests/test_scenes_repos.py
git commit -m "feat(scenes): repo helpers (get/list/replace/rename)"
```

---

### Task 4: Scenes API router + models

**Files:**
- Modify: `services/local-api/src/laura/api/models.py` (append scene models)
- Create: `services/local-api/src/laura/api/scenes.py`
- Modify: `services/local-api/src/laura/main.py` (import + `include_router`)
- Test: `services/local-api/tests/test_scenes_api.py`

- [ ] **Step 1: Write the failing tests** (mirror the app/token fixture used by `tests/test_exports_api.py`; if that file uses a shared fixture/helper, import the same one).

```python
# services/local-api/tests/test_scenes_api.py
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app


def _client(tmp_path: Path) -> tuple[TestClient, SqliteDatabase, str]:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    app = create_app(settings)
    db: SqliteDatabase = app.state.db
    token = settings.token  # the API token the app expects
    return TestClient(app), db, token


def _seed_rough_cut(db: SqliteDatabase) -> tuple[str, str, str]:
    """Project + asset + a 3-clip rough_cut (no analysis run / transcript)."""
    proot = "/tmp/p"
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root=proot
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4"
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_timeline_clips(db, tl["id"], [
        {"asset_id": asset["id"], "src_in_frame": 0, "src_out_frame_exclusive": 30,
         "seq_in_frame": 0, "seq_out_frame_exclusive": 30, "lane": 0, "speed_num": 1, "speed_den": 1},
        {"asset_id": asset["id"], "src_in_frame": 30, "src_out_frame_exclusive": 60,
         "seq_in_frame": 30, "seq_out_frame_exclusive": 60, "lane": 0, "speed_num": 1, "speed_den": 1},
        {"asset_id": asset["id"], "src_in_frame": 60, "src_out_frame_exclusive": 90,
         "seq_in_frame": 60, "seq_out_frame_exclusive": 90, "lane": 0, "speed_num": 1, "speed_den": 1},
    ])
    return project["id"], asset["id"], tl["id"]


def test_generate_without_transcript_one_scene_per_clip(tmp_path: Path) -> None:
    client, db, token = _client(tmp_path)
    _pid, asset_id, tl_id = _seed_rough_cut(db)
    # an analysis run must exist (grouping still works without transcript words)
    repos.create_analysis_run(db, asset_id=asset_id, pipeline_version="t")
    h = {"X-Laura-Token": token}
    r = client.post(f"/timelines/{tl_id}/scenes:generate", json={"asset_id": asset_id}, headers=h)
    assert r.status_code == 200, r.text
    scenes = r.json()
    assert len(scenes) == 3
    assert scenes[0]["seq_in_frame"] == 0 and scenes[0]["seq_out_frame_exclusive"] == 30


def test_merge_then_split_roundtrip(tmp_path: Path) -> None:
    client, db, token = _client(tmp_path)
    _pid, asset_id, tl_id = _seed_rough_cut(db)
    repos.create_analysis_run(db, asset_id=asset_id, pipeline_version="t")
    h = {"X-Laura-Token": token}
    client.post(f"/timelines/{tl_id}/scenes:generate", json={"asset_id": asset_id}, headers=h)
    scenes = client.get(f"/timelines/{tl_id}/scenes", headers=h).json()
    # merge scene 0 with its successor -> 2 scenes
    merged = client.post(
        f"/timelines/{tl_id}/scenes/merge", json={"scene_id": scenes[0]["id"]}, headers=h
    ).json()
    assert len(merged) == 2
    assert merged[0]["seq_in_frame"] == 0 and merged[0]["seq_out_frame_exclusive"] == 60
    # split it back at the clip boundary 30
    split = client.post(
        f"/timelines/{tl_id}/scenes/{merged[0]['id']}/split",
        json={"at_seq_frame": 30}, headers=h,
    ).json()
    assert [(s["seq_in_frame"], s["seq_out_frame_exclusive"]) for s in split] == [(0, 30), (30, 60), (60, 90)]


def test_split_at_non_boundary_is_422(tmp_path: Path) -> None:
    client, db, token = _client(tmp_path)
    _pid, asset_id, tl_id = _seed_rough_cut(db)
    repos.create_analysis_run(db, asset_id=asset_id, pipeline_version="t")
    h = {"X-Laura-Token": token}
    client.post(f"/timelines/{tl_id}/scenes:generate", json={"asset_id": asset_id}, headers=h)
    merged = client.post(
        f"/timelines/{tl_id}/scenes/merge",
        json={"scene_id": client.get(f"/timelines/{tl_id}/scenes", headers=h).json()[0]["id"]},
        headers=h,
    ).json()
    r = client.post(
        f"/timelines/{tl_id}/scenes/{merged[0]['id']}/split", json={"at_seq_frame": 15}, headers=h
    )
    assert r.status_code == 422


def test_generate_on_empty_timeline_is_422(tmp_path: Path) -> None:
    client, db, token = _client(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4"
    )
    repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="t")
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    r = client.post(
        f"/timelines/{tl['id']}/scenes:generate", json={"asset_id": asset["id"]},
        headers={"X-Laura-Token": token},
    )
    assert r.status_code == 422
```

> NOTE for the implementer: the exact names of `Settings.token`, `repos.create_project`/`create_asset`/`create_analysis_run` signatures may differ slightly — check `tests/test_exports_api.py` and `tests/test_render_job.py` for the real fixture/seed calls and adapt the seed helper. The endpoint behaviour asserted above is the contract.

- [ ] **Step 2: Run — expect FAIL** (404 routes / import error).

```
uv run --directory services/local-api pytest tests/test_scenes_api.py -q
```

- [ ] **Step 3a: Add models** — append to `services/local-api/src/laura/api/models.py`:

```python
class SceneOut(BaseModel):
    id: str
    project_id: str
    source_timeline_id: str
    name: str
    order_index: int
    seq_in_frame: int
    seq_out_frame_exclusive: int


class GenerateScenesRequest(BaseModel):
    asset_id: str
    gap_frames: int | None = Field(default=None, ge=0)


class SplitSceneRequest(BaseModel):
    at_seq_frame: int = Field(ge=0)


class MergeScenesRequest(BaseModel):
    scene_id: str


class RenameSceneRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
```

- [ ] **Step 3b: Create the router** — `services/local-api/src/laura/api/scenes.py`:

```python
"""Scene endpoints: group a rough-cut's clips into scenes and adjust their boundaries.

Scenes are a lightweight marker layer over an existing rough_cut timeline (see
docs/superpowers/specs/2026-06-08-rough-cut-stage-design.md). Generation only *groups*
existing clips — building the rough-cut is the existing /timelines/from-shots endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..db import repos
from ..db.database import Database
from ..scenes.grouping import group_into_scenes
from .models import (
    GenerateScenesRequest,
    MergeScenesRequest,
    RenameSceneRequest,
    SceneOut,
    SplitSceneRequest,
)
from .security import require_token

router = APIRouter(tags=["scenes"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _asset_words(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for seg in transcript:
        spk = seg.get("speaker_id")
        for w in seg["words"]:
            words.append(
                {"start_frame": w["start_frame"], "end_frame": w["end_frame"], "speaker": spk}
            )
    words.sort(key=lambda w: w["start_frame"])
    return words


def _assign_words(
    clips: list[dict[str, Any]], words: list[dict[str, Any]]
) -> list[list[dict[str, Any]]]:
    out: list[list[dict[str, Any]]] = []
    for c in clips:
        lo, hi = c["src_in_frame"], c["src_out_frame_exclusive"]
        out.append([w for w in words if w["start_frame"] < hi and w["end_frame"] > lo])
    return out


@router.post("/timelines/{timeline_id}/scenes:generate", response_model=list[SceneOut])
def generate_scenes(
    timeline_id: str, body: GenerateScenesRequest, request: Request
) -> list[SceneOut]:
    db = _db(request)
    tl = repos.get_timeline(db, timeline_id)
    if tl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")
    asset = repos.get_asset(db, body.asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    run = repos.get_latest_analysis_run(db, body.asset_id)
    if run is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "asset has no analysis run")
    clips = repos.list_timeline_clips(db, timeline_id)
    if not clips:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "rough cut is empty; build it from shots first",
        )
    words = _asset_words(repos.get_transcript(db, body.asset_id, run["id"]))
    words_by_clip = _assign_words(clips, words)
    if body.gap_frames is not None:
        gap = body.gap_frames
    else:
        gap = round(1.5 * (asset["rate_num"] or 30) / (asset["rate_den"] or 1))
    ranges = group_into_scenes(clips, words_by_clip, gap_frames=gap)
    repos.replace_scenes(db, tl["project_id"], timeline_id, ranges)
    return [SceneOut(**s) for s in repos.list_scenes(db, timeline_id)]


@router.get("/timelines/{timeline_id}/scenes", response_model=list[SceneOut])
def list_scenes(timeline_id: str, request: Request) -> list[SceneOut]:
    db = _db(request)
    if repos.get_timeline(db, timeline_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")
    return [SceneOut(**s) for s in repos.list_scenes(db, timeline_id)]


@router.post("/timelines/{timeline_id}/scenes/{scene_id}/split", response_model=list[SceneOut])
def split_scene(
    timeline_id: str, scene_id: str, body: SplitSceneRequest, request: Request
) -> list[SceneOut]:
    db = _db(request)
    scene = repos.get_scene(db, scene_id)
    if scene is None or scene["source_timeline_id"] != timeline_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scene not found")
    at = body.at_seq_frame
    if not (scene["seq_in_frame"] < at < scene["seq_out_frame_exclusive"]):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "split point outside scene")
    boundaries = {c["seq_in_frame"] for c in repos.list_timeline_clips(db, timeline_id)}
    if at not in boundaries:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "split point is not a clip boundary"
        )
    ranges: list[tuple[int, int]] = []
    for s in repos.list_scenes(db, timeline_id):
        if s["id"] == scene_id:
            ranges.append((s["seq_in_frame"], at))
            ranges.append((at, s["seq_out_frame_exclusive"]))
        else:
            ranges.append((s["seq_in_frame"], s["seq_out_frame_exclusive"]))
    repos.replace_scenes(db, scene["project_id"], timeline_id, ranges)
    return [SceneOut(**s) for s in repos.list_scenes(db, timeline_id)]


@router.post("/timelines/{timeline_id}/scenes/merge", response_model=list[SceneOut])
def merge_scenes(
    timeline_id: str, body: MergeScenesRequest, request: Request
) -> list[SceneOut]:
    db = _db(request)
    scene = repos.get_scene(db, body.scene_id)
    if scene is None or scene["source_timeline_id"] != timeline_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scene not found")
    scenes = repos.list_scenes(db, timeline_id)
    idx = next((i for i, s in enumerate(scenes) if s["id"] == body.scene_id), None)
    if idx is None or idx == len(scenes) - 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "no following scene to merge")
    ranges: list[tuple[int, int]] = []
    i = 0
    while i < len(scenes):
        if i == idx:
            ranges.append((scenes[i]["seq_in_frame"], scenes[i + 1]["seq_out_frame_exclusive"]))
            i += 2
        else:
            ranges.append((scenes[i]["seq_in_frame"], scenes[i]["seq_out_frame_exclusive"]))
            i += 1
    repos.replace_scenes(db, scene["project_id"], timeline_id, ranges)
    return [SceneOut(**s) for s in repos.list_scenes(db, timeline_id)]


@router.patch("/scenes/{scene_id}", response_model=SceneOut)
def rename_scene(scene_id: str, body: RenameSceneRequest, request: Request) -> SceneOut:
    db = _db(request)
    if repos.get_scene(db, scene_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scene not found")
    repos.update_scene_name(db, scene_id, body.name)
    updated = repos.get_scene(db, scene_id)
    assert updated is not None
    return SceneOut(**updated)
```

- [ ] **Step 3c: Register the router** in `services/local-api/src/laura/main.py`: add `scenes` to the `from .api import (...)` import list and add, next to the other `include_router` calls:

```python
    app.include_router(scenes.router)
```

- [ ] **Step 4: Run — expect PASS.** Then full backend gate:

```
uv run --directory services/local-api pytest tests/test_scenes_api.py tests/test_scene_grouping.py tests/test_scenes_repos.py tests/test_scenes_migration.py -q
uv run --directory services/local-api ruff check src/laura/api/scenes.py src/laura/api/models.py src/laura/main.py tests/test_scenes_api.py
uv run --directory services/local-api mypy src/laura/api/scenes.py
```

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/api/scenes.py services/local-api/src/laura/api/models.py services/local-api/src/laura/main.py services/local-api/tests/test_scenes_api.py
git commit -m "feat(scenes): generate/list/split/merge/rename API"
```

---

### Task 5: Frontend API client — `Scene` + methods

**Files:**
- Modify: `apps/desktop/src/api.ts` (additive: type + 5 methods on `LauraClient`)

- [ ] **Step 1: Add the type** near the other interfaces in `api.ts`:

```typescript
export interface Scene {
  id: string;
  project_id: string;
  source_timeline_id: string;
  name: string;
  order_index: number;
  seq_in_frame: number;
  seq_out_frame_exclusive: number;
}
```

- [ ] **Step 2: Add methods** inside the `LauraClient` class (near `buildRoughCutFromShots`):

```typescript
  generateScenes(timelineId: string, assetId: string, gapFrames?: number): Promise<Scene[]> {
    return this.request<Scene[]>(`/timelines/${timelineId}/scenes:generate`, {
      method: "POST",
      body: JSON.stringify({ asset_id: assetId, gap_frames: gapFrames ?? null }),
    });
  }

  listScenes(timelineId: string): Promise<Scene[]> {
    return this.request<Scene[]>(`/timelines/${timelineId}/scenes`);
  }

  splitScene(timelineId: string, sceneId: string, atSeqFrame: number): Promise<Scene[]> {
    return this.request<Scene[]>(`/timelines/${timelineId}/scenes/${sceneId}/split`, {
      method: "POST",
      body: JSON.stringify({ at_seq_frame: atSeqFrame }),
    });
  }

  mergeScenes(timelineId: string, sceneId: string): Promise<Scene[]> {
    return this.request<Scene[]>(`/timelines/${timelineId}/scenes/merge`, {
      method: "POST",
      body: JSON.stringify({ scene_id: sceneId }),
    });
  }

  renameScene(sceneId: string, name: string): Promise<Scene> {
    return this.request<Scene>(`/scenes/${sceneId}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    });
  }
```

- [ ] **Step 3: Typecheck**

```
npm --prefix apps/desktop run typecheck
```
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add apps/desktop/src/api.ts
git commit -m "feat(scenes): api client type + methods"
```

---

### Task 6: `useScenes` hook

**Files:**
- Create: `apps/desktop/src/hooks/useScenes.ts`
- Test: `apps/desktop/src/hooks/useScenes.test.ts`

- [ ] **Step 1: Write the failing test** (mirror imports/`renderHook` usage from an existing `apps/desktop/src/**/*.test.ts(x)`; use plain asserts, no jsdom matchers):

```typescript
// apps/desktop/src/hooks/useScenes.test.ts
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type LauraClient, type Scene } from "../api";
import { useScenes } from "./useScenes";

const SCENE: Scene = {
  id: "s1", project_id: "p", source_timeline_id: "tl",
  name: "Szene 1", order_index: 0, seq_in_frame: 0, seq_out_frame_exclusive: 30,
};

function fakeClient(over: Partial<LauraClient>): LauraClient {
  return {
    listScenes: vi.fn().mockResolvedValue([]),
    generateScenes: vi.fn().mockResolvedValue([SCENE]),
    splitScene: vi.fn().mockResolvedValue([SCENE]),
    mergeScenes: vi.fn().mockResolvedValue([SCENE]),
    renameScene: vi.fn().mockResolvedValue(SCENE),
    ...over,
  } as unknown as LauraClient;
}

describe("useScenes", () => {
  it("loads scenes for a timeline on mount", async () => {
    const client = fakeClient({ listScenes: vi.fn().mockResolvedValue([SCENE]) });
    const { result } = renderHook(() => useScenes(client, "tl"));
    await waitFor(() => expect(result.current.scenes.length).toBe(1));
    expect(result.current.scenes[0].name).toBe("Szene 1");
  });

  it("generate replaces scenes from the response", async () => {
    const client = fakeClient({});
    const { result } = renderHook(() => useScenes(client, "tl"));
    await act(async () => { await result.current.generate("asset1"); });
    expect(result.current.scenes.length).toBe(1);
    expect(client.generateScenes).toHaveBeenCalledWith("tl", "asset1");
  });
});
```

- [ ] **Step 2: Run — expect FAIL**

```
npm --prefix apps/desktop test -- useScenes
```

- [ ] **Step 3: Implement** — `apps/desktop/src/hooks/useScenes.ts`:

```typescript
import { useCallback, useEffect, useState } from "react";

import { type LauraClient, type Scene } from "../api";

export interface ScenesController {
  scenes: Scene[];
  loading: boolean;
  error: string | null;
  generate: (assetId: string) => Promise<void>;
  split: (sceneId: string, atSeqFrame: number) => Promise<void>;
  merge: (sceneId: string) => Promise<void>;
  rename: (sceneId: string, name: string) => Promise<void>;
  reload: () => Promise<void>;
}

export function useScenes(
  client: LauraClient | null,
  timelineId: string | null,
): ScenesController {
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!client || !timelineId) {
      setScenes([]);
      return;
    }
    try {
      setError(null);
      setScenes(await client.listScenes(timelineId));
    } catch (e) {
      setError(String(e));
    }
  }, [client, timelineId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const generate = useCallback(
    async (assetId: string) => {
      if (!client || !timelineId) return;
      setLoading(true);
      setError(null);
      try {
        setScenes(await client.generateScenes(timelineId, assetId));
      } catch (e) {
        setError(String(e));
      } finally {
        setLoading(false);
      }
    },
    [client, timelineId],
  );

  const split = useCallback(
    async (sceneId: string, atSeqFrame: number) => {
      if (!client || !timelineId) return;
      try {
        setScenes(await client.splitScene(timelineId, sceneId, atSeqFrame));
      } catch (e) {
        setError(String(e));
      }
    },
    [client, timelineId],
  );

  const merge = useCallback(
    async (sceneId: string) => {
      if (!client || !timelineId) return;
      try {
        setScenes(await client.mergeScenes(timelineId, sceneId));
      } catch (e) {
        setError(String(e));
      }
    },
    [client, timelineId],
  );

  const rename = useCallback(
    async (sceneId: string, name: string) => {
      if (!client || !timelineId) return;
      try {
        await client.renameScene(sceneId, name);
        await reload();
      } catch (e) {
        setError(String(e));
      }
    },
    [client, timelineId, reload],
  );

  return { scenes, loading, error, generate, split, merge, rename, reload };
}
```

- [ ] **Step 4: Run — expect PASS.** Then `npm --prefix apps/desktop run typecheck`.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/hooks/useScenes.ts apps/desktop/src/hooks/useScenes.test.ts
git commit -m "feat(scenes): useScenes hook"
```

---

### Task 7: `SceneStrip` component

**Files:**
- Create: `apps/desktop/src/components/SceneStrip.tsx`
- Test: `apps/desktop/src/components/SceneStrip.test.tsx`

Per-scene the strip derives, from props, the rough-cut clips inside the scene's seq-range and the transcript excerpt; it never fetches scene membership from the server.

- [ ] **Step 1: Write the failing test**

```typescript
// apps/desktop/src/components/SceneStrip.test.tsx
import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type Asset, type LauraClient, type Scene, type Segment, type TimelineClip } from "../api";
import { SceneStrip } from "./SceneStrip";

const asset = { id: "a", rate_num: 30, rate_den: 1 } as unknown as Asset;
const clip = (id: string, sin: number, sout: number): TimelineClip =>
  ({ id, asset_id: "a", src_in_frame: sin, src_out_frame_exclusive: sout,
     seq_in_frame: sin, seq_out_frame_exclusive: sout, lane: 0, speaker_id: null,
     origin_word_start_id: null, origin_word_end_id: null, speed_num: 1, speed_den: 1 });
const scenes: Scene[] = [
  { id: "s1", project_id: "p", source_timeline_id: "tl", name: "Szene 1",
    order_index: 0, seq_in_frame: 0, seq_out_frame_exclusive: 60 },
  { id: "s2", project_id: "p", source_timeline_id: "tl", name: "Szene 2",
    order_index: 1, seq_in_frame: 60, seq_out_frame_exclusive: 90 },
];
const clips = [clip("c1", 0, 30), clip("c2", 30, 60), clip("c3", 60, 90)];
const segments: Segment[] = [];

function client(): LauraClient {
  return { assetFrameUrl: vi.fn().mockResolvedValue("blob:x") } as unknown as LauraClient;
}

describe("SceneStrip", () => {
  it("renders one card per scene with its name", () => {
    const { getByText } = render(
      <SceneStrip client={client()} asset={asset} scenes={scenes} clips={clips}
        segments={segments} onSplit={vi.fn()} onMerge={vi.fn()} onRename={vi.fn()} onSeek={vi.fn()} />,
    );
    expect(getByText("Szene 1")).toBeTruthy();
    expect(getByText("Szene 2")).toBeTruthy();
  });

  it("seeks to a scene's start frame on card click", () => {
    const onSeek = vi.fn();
    const { getByText } = render(
      <SceneStrip client={client()} asset={asset} scenes={scenes} clips={clips}
        segments={segments} onSplit={vi.fn()} onMerge={vi.fn()} onRename={vi.fn()}
        onSeek={onSeek} />,
    );
    fireEvent.click(getByText("Szene 2"));
    expect(onSeek).toHaveBeenCalledWith(60);
  });

  it("merge button is hidden on the last scene", () => {
    const onMerge = vi.fn();
    const { getAllByTitle } = render(
      <SceneStrip client={client()} asset={asset} scenes={scenes} clips={clips}
        segments={segments} onSplit={vi.fn()} onMerge={onMerge} onRename={vi.fn()} onSeek={vi.fn()} />,
    );
    // only the first scene (has a successor) shows a merge button
    expect(getAllByTitle("Mit nächster Szene zusammenführen").length).toBe(1);
  });

  it("split at the middle clip boundary of a multi-clip scene", () => {
    const onSplit = vi.fn();
    const { getAllByTitle } = render(
      <SceneStrip client={client()} asset={asset} scenes={scenes} clips={clips}
        segments={segments} onSplit={onSplit} onMerge={vi.fn()} onRename={vi.fn()} onSeek={vi.fn()} />,
    );
    fireEvent.click(getAllByTitle("Szene teilen")[0]); // scene 1 spans clips c1,c2 -> boundary 30
    expect(onSplit).toHaveBeenCalledWith("s1", 30);
  });
});
```

- [ ] **Step 2: Run — expect FAIL.**

```
npm --prefix apps/desktop test -- SceneStrip
```

- [ ] **Step 3: Implement** — `apps/desktop/src/components/SceneStrip.tsx`:

```typescript
import { type ReactElement, useEffect, useState } from "react";

import { type Asset, type LauraClient, type Scene, type Segment, type TimelineClip } from "../api";

function clipsInScene(scene: Scene, clips: TimelineClip[]): TimelineClip[] {
  return clips.filter(
    (c) =>
      c.seq_in_frame >= scene.seq_in_frame &&
      c.seq_in_frame < scene.seq_out_frame_exclusive,
  );
}

/** Clip boundary nearest the middle of the scene, or null if it has <2 clips. */
function midBoundary(inScene: TimelineClip[]): number | null {
  if (inScene.length < 2) return null;
  return inScene[Math.floor(inScene.length / 2)].seq_in_frame;
}

function excerpt(scene: Scene, inScene: TimelineClip[], segments: Segment[]): string {
  const lo = Math.min(...inScene.map((c) => c.src_in_frame), Number.MAX_SAFE_INTEGER);
  const hi = Math.max(...inScene.map((c) => c.src_out_frame_exclusive), 0);
  const text = segments
    .filter((s) => s.start_frame < hi && s.end_frame > lo)
    .map((s) => s.text)
    .join(" ")
    .trim();
  return text.length > 90 ? `${text.slice(0, 90)}…` : text;
}

function Thumb({
  client,
  assetId,
  frame,
}: {
  client: LauraClient;
  assetId: string;
  frame: number;
}): ReactElement {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    client
      .assetFrameUrl(assetId, Math.max(0, frame))
      .then((u) => {
        if (!active) {
          URL.revokeObjectURL(u);
          return;
        }
        objectUrl = u;
        setUrl(u);
      })
      .catch(() => {
        /* colour fallback */
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [client, assetId, frame]);
  return (
    <span className="h-9 w-16 shrink-0 overflow-hidden rounded border border-edge">
      {url ? (
        <img src={url} alt="" className="h-full w-full object-cover" />
      ) : (
        <span className="block h-full w-full bg-sky-700/40" />
      )}
    </span>
  );
}

function SceneCard({
  client,
  asset,
  scene,
  inScene,
  excerptText,
  canMerge,
  onSplit,
  onMerge,
  onRename,
  onSeek,
}: {
  client: LauraClient;
  asset: Asset;
  scene: Scene;
  inScene: TimelineClip[];
  excerptText: string;
  canMerge: boolean;
  onSplit: (sceneId: string, atSeqFrame: number) => void;
  onMerge: (sceneId: string) => void;
  onRename: (sceneId: string, name: string) => void;
  onSeek: (frame: number) => void;
}): ReactElement {
  const splitAt = midBoundary(inScene);
  return (
    <div className="flex w-56 shrink-0 flex-col gap-1 rounded border border-edge p-2">
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => onSeek(scene.seq_in_frame)}
          title="Zur Szene springen"
          className="truncate text-left text-xs font-medium text-slate-200 hover:underline"
        >
          {scene.name}
        </button>
        <span className="ml-auto flex gap-1">
          {splitAt !== null && (
            <button
              type="button"
              title="Szene teilen"
              onClick={() => onSplit(scene.id, splitAt)}
              className="rounded px-1 text-xs text-slate-300 hover:bg-slate-700"
            >
              ✂
            </button>
          )}
          {canMerge && (
            <button
              type="button"
              title="Mit nächster Szene zusammenführen"
              onClick={() => onMerge(scene.id)}
              className="rounded px-1 text-xs text-slate-300 hover:bg-slate-700"
            >
              ⇄
            </button>
          )}
        </span>
      </div>
      <div className="flex gap-1 overflow-x-auto">
        {inScene.slice(0, 4).map((c) => (
          <Thumb key={c.id} client={client} assetId={asset.id} frame={c.src_in_frame} />
        ))}
      </div>
      <input
        defaultValue={scene.name}
        onBlur={(e) => {
          if (e.target.value && e.target.value !== scene.name) onRename(scene.id, e.target.value);
        }}
        className="w-full rounded bg-slate-800 px-1 py-0.5 text-[11px] text-slate-200"
        aria-label="Szenenname"
      />
      <p className="line-clamp-2 text-[11px] text-slate-500">{excerptText || "—"}</p>
    </div>
  );
}

export function SceneStrip({
  client,
  asset,
  scenes,
  clips,
  segments,
  onSplit,
  onMerge,
  onRename,
  onSeek,
}: {
  client: LauraClient;
  asset: Asset;
  scenes: Scene[];
  clips: TimelineClip[];
  segments: Segment[];
  onSplit: (sceneId: string, atSeqFrame: number) => void;
  onMerge: (sceneId: string) => void;
  onRename: (sceneId: string, name: string) => void;
  onSeek: (frame: number) => void;
}): ReactElement {
  if (scenes.length === 0) {
    return (
      <div className="flex h-24 items-center justify-center text-xs text-slate-600">
        Noch keine Szenen — wähle ein Asset und erzeuge Szenen.
      </div>
    );
  }
  return (
    <div className="flex w-full gap-2 overflow-x-auto p-2">
      {scenes.map((scene, i) => {
        const inScene = clipsInScene(scene, clips);
        return (
          <SceneCard
            key={scene.id}
            client={client}
            asset={asset}
            scene={scene}
            inScene={inScene}
            excerptText={excerpt(scene, inScene, segments)}
            canMerge={i < scenes.length - 1}
            onSplit={onSplit}
            onMerge={onMerge}
            onRename={onRename}
            onSeek={onSeek}
          />
        );
      })}
    </div>
  );
}
```

> The test clicks the scene *name button* (text "Szene 2") for the seek assertion; the name `<input>` carries the same default value, so query the button via `getByText`. If `getByText` is ambiguous with the input, the implementer should switch the name button to a `title="Zur Szene springen"` query in the test. Keep `line-clamp-2` only if the Tailwind plugin is enabled; otherwise drop it.

- [ ] **Step 4: Run — expect PASS.** Then `npm --prefix apps/desktop run typecheck`.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/components/SceneStrip.tsx apps/desktop/src/components/SceneStrip.test.tsx
git commit -m "feat(scenes): SceneStrip component"
```

---

### Task 8: `RoughCutView` stage component

**Files:**
- Create: `apps/desktop/src/components/RoughCutView.tsx`
- Test: `apps/desktop/src/components/RoughCutView.test.tsx`

`RoughCutView` composes the existing `Player`, a "Szenen erzeugen" action, and `SceneStrip`. "Szenen erzeugen" ensures the rough-cut exists via the existing `from-shots` endpoint (only when it has no clips), then groups.

- [ ] **Step 1: Write the failing test**

```typescript
// apps/desktop/src/components/RoughCutView.test.tsx
import { fireEvent, render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type Asset, type LauraClient, type Scene, type Timeline } from "../api";
import { RoughCutView } from "./RoughCutView";

vi.mock("./Player", () => ({ Player: () => <div data-testid="player" /> }));

const asset = { id: "a", rate_num: 30, rate_den: 1, display_name: "a" } as unknown as Asset;
const emptyRc: Timeline = { id: "tl", project_id: "p", name: "rc", kind: "rough_cut", created_at: "", clips: [] };
const SCENE: Scene = { id: "s1", project_id: "p", source_timeline_id: "tl", name: "Szene 1",
  order_index: 0, seq_in_frame: 0, seq_out_frame_exclusive: 30 };

function client(over: Partial<LauraClient>): LauraClient {
  return {
    listScenes: vi.fn().mockResolvedValue([]),
    generateScenes: vi.fn().mockResolvedValue([SCENE]),
    buildRoughCutFromShots: vi.fn().mockResolvedValue({}),
    splitScene: vi.fn(), mergeScenes: vi.fn(), renameScene: vi.fn(),
    assetFrameUrl: vi.fn().mockResolvedValue("blob:x"),
    ...over,
  } as unknown as LauraClient;
}

describe("RoughCutView", () => {
  it("builds the rough cut then generates scenes when clips are empty", async () => {
    const c = client({});
    const onRoughCutChange = vi.fn().mockResolvedValue(undefined);
    const { getByText } = render(
      <RoughCutView client={c} projectId="p" asset={asset} roughCut={emptyRc}
        segments={[]} onRoughCutChange={onRoughCutChange}
        seek={null} currentFrame={0} onSeek={vi.fn()} onFrame={vi.fn()} />,
    );
    fireEvent.click(getByText("Szenen erzeugen"));
    await waitFor(() => expect(c.generateScenes).toHaveBeenCalledWith("tl", "a"));
    expect(c.buildRoughCutFromShots).toHaveBeenCalledWith("p", "a", "tl");
    expect(onRoughCutChange).toHaveBeenCalled();
  });

  it("skips the build when the rough cut already has clips", async () => {
    const c = client({});
    const rc: Timeline = { ...emptyRc, clips: [
      { id: "c1", asset_id: "a", src_in_frame: 0, src_out_frame_exclusive: 30, seq_in_frame: 0,
        seq_out_frame_exclusive: 30, lane: 0, speaker_id: null, origin_word_start_id: null,
        origin_word_end_id: null, speed_num: 1, speed_den: 1 }] };
    const { getByText } = render(
      <RoughCutView client={c} projectId="p" asset={asset} roughCut={rc}
        segments={[]} onRoughCutChange={vi.fn().mockResolvedValue(undefined)}
        seek={null} currentFrame={0} onSeek={vi.fn()} onFrame={vi.fn()} />,
    );
    fireEvent.click(getByText("Szenen erzeugen"));
    await waitFor(() => expect(c.generateScenes).toHaveBeenCalled());
    expect(c.buildRoughCutFromShots).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run — expect FAIL.**

```
npm --prefix apps/desktop test -- RoughCutView
```

- [ ] **Step 3: Implement** — `apps/desktop/src/components/RoughCutView.tsx`:

```typescript
import { type ReactElement, useCallback, useState } from "react";

import { type Asset, type LauraClient, type Segment, type Timeline } from "../api";
import { useScenes } from "../hooks/useScenes";
import { Player } from "./Player";
import { SceneStrip } from "./SceneStrip";

export function RoughCutView({
  client,
  projectId,
  asset,
  roughCut,
  segments,
  onRoughCutChange,
  seek,
  currentFrame,
  onSeek,
  onFrame,
}: {
  client: LauraClient;
  projectId: string | null;
  asset: Asset | null;
  roughCut: Timeline | null;
  segments: Segment[];
  onRoughCutChange: () => Promise<void>;
  seek: { frame: number } | null;
  currentFrame: number;
  onSeek: (frame: number) => void;
  onFrame: (frame: number) => void;
}): ReactElement {
  const scenes = useScenes(client, roughCut?.id ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onGenerate = useCallback(async () => {
    if (!asset || !roughCut || !projectId) return;
    setBusy(true);
    setError(null);
    try {
      if (roughCut.clips.length === 0) {
        await client.buildRoughCutFromShots(projectId, asset.id, roughCut.id);
        await onRoughCutChange();
      }
      await scenes.generate(asset.id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }, [asset, roughCut, projectId, client, onRoughCutChange, scenes]);

  if (!asset || !roughCut) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-slate-600">
        Wähle ein Asset (in Import), um Szenen zu erzeugen.
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex min-h-0 flex-1 items-center justify-center bg-black/40 p-2">
        <Player asset={asset} seekTo={seek} onFrame={onFrame} />
      </div>
      <div className="flex items-center gap-2 border-t border-edge px-3 py-2">
        <button
          type="button"
          onClick={() => void onGenerate()}
          disabled={busy}
          className="rounded bg-sky-600 px-3 py-1 text-xs text-white disabled:opacity-40"
        >
          {busy ? "Erzeuge…" : "Szenen erzeugen"}
        </button>
        <span className="text-[11px] text-slate-500">Frame {currentFrame}</span>
        {(error ?? scenes.error) && (
          <span className="text-[11px] text-red-400">{error ?? scenes.error}</span>
        )}
      </div>
      <div className="border-t border-edge">
        <SceneStrip
          client={client}
          asset={asset}
          scenes={scenes.scenes}
          clips={roughCut.clips}
          segments={segments}
          onSplit={(id, at) => void scenes.split(id, at)}
          onMerge={(id) => void scenes.merge(id)}
          onRename={(id, name) => void scenes.rename(id, name)}
          onSeek={onSeek}
        />
      </div>
    </div>
  );
}
```

> Confirm `Player`'s prop names against `components/Player.tsx` (`asset`, `seekTo`, `onFrame` per the codebase map). If `seekTo` expects a different shape, adapt the `seek` prop type here to match the parent.

- [ ] **Step 4: Run — expect PASS.** Then `npm --prefix apps/desktop run typecheck`.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/components/RoughCutView.tsx apps/desktop/src/components/RoughCutView.test.tsx
git commit -m "feat(scenes): RoughCutView stage component"
```

---

### Task 9: Wire `RoughCutView` into `App.tsx` (LAST — collision-sensitive)

**Files:**
- Modify: `apps/desktop/src/App.tsx` (one import + swap the `roughcut` render branch)

**Precondition:** `git status --short apps/desktop/src/App.tsx` must show it **committed/clean** (the user edits it in parallel). If it shows uncommitted changes, STOP and hand this task to the user with the exact diff below.

- [ ] **Step 1: Add the import** (alphabetical, near the other component imports):

```typescript
import { RoughCutView } from "./components/RoughCutView";
```

- [ ] **Step 2: Split the shared stage condition.** The current branch is:

```tsx
{(stage === "roughcut" || stage === "finecut" || stage === "assemble") && (
```

Change it to gate only finecut/assemble on the shared layout:

```tsx
{(stage === "finecut" || stage === "assemble") && (
```

- [ ] **Step 3: Add the `roughcut` branch** immediately before that block (mirror the props the shared layout already has in scope — `client`, `selectedProjectId`, `detailAsset`, `roughCut`, `analysis.segments`, `loadRoughCut`, `seek`, `currentFrame`, `seekToFrame`, the `onFrame` setter):

```tsx
{stage === "roughcut" && client && (
  <RoughCutView
    client={client}
    projectId={selectedProjectId}
    asset={detailAsset ?? null}
    roughCut={roughCut}
    segments={analysis.segments}
    onRoughCutChange={loadRoughCut}
    seek={seek}
    currentFrame={currentFrame}
    onSeek={seekToFrame}
    onFrame={(f) => setCurrentFrame(f)}
  />
)}
```

> Verify each referenced symbol's exact name in `App.tsx` (the codebase map lists `detailAsset`, `roughCut`, `analysis` from `useAnalysis`, `seek`, `currentFrame`, `seekToFrame`, `loadRoughCut`). If `loadRoughCut` is not `async`/returns `void`, wrap it: `onRoughCutChange={async () => { await loadRoughCut(); }}`. If `setCurrentFrame` is named differently, use the existing `onFrame` handler the shared `Player` already uses.

- [ ] **Step 4: Verify** the whole frontend:

```
npm --prefix apps/desktop run typecheck
npm --prefix apps/desktop test
```
Expected: typecheck clean; all tests pass (existing 42 + new scene tests).

- [ ] **Step 5: Commit (App.tsx only)**

```bash
git add apps/desktop/src/App.tsx
git commit -m "feat(scenes): wire RoughCutView into the rough-cut stage"
```

---

## Final verification (after all tasks)

- [ ] Backend gate:
```
uv run --directory services/local-api pytest tests/test_scene_grouping.py tests/test_scenes_repos.py tests/test_scenes_api.py tests/test_scenes_migration.py -q
uv run --directory services/local-api ruff check src/laura/scenes src/laura/api/scenes.py
uv run --directory services/local-api mypy src/laura/scenes src/laura/api/scenes.py
```
- [ ] Frontend gate:
```
npm --prefix apps/desktop run typecheck
npm --prefix apps/desktop test
```
- [ ] Manual (headless can't verify): launch the app, enter **Rough Cut**, pick an analysed asset, click **Szenen erzeugen**, confirm the scene-strip populates, and that ✂ / ⇄ / rename / click-to-seek behave.

## Spec coverage check

| Spec section | Task |
|---|---|
| §3 Datenmodell (`scenes`) | 1 |
| §4.1 Gruppierung (transcript/speaker/gap/fallback) | 2 |
| §4.2 Repos | 3 |
| §4.3 API (generate/list/split/merge/rename, 422s, idempotent) | 4 |
| §5 Frontend (api.ts / useScenes / SceneStrip / RoughCutView) | 5–8 |
| §6 Datenfluss (build-then-group orchestration) | 8 |
| §7 Kollisionssichere Verdrahtung (App.tsx last) | 9 |
| §8 Leerzustände | 7, 8 |
| §9 Tests | every task (TDD) |

## Notes / deferred (per spec §10–§11)

- Scene names reset to positional ("Szene N") on generate/split/merge; rename persists only until the next structural op (v1 simplification — keeps `replace_scenes` trivial).
- `:generate` groups existing clips only; building the rough-cut is the existing `from-shots` endpoint, orchestrated in `RoughCutView` (keeps OTIO-source-of-truth logic in one place, avoids touching tested code).
- No `kind="scene"` sub-timelines, no reorder, no audio lane, no transitions (Stufe 4/5+).
