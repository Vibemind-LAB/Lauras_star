# Feinschnitt 4a (Core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Build the Feinschnitt core: open a scene → lazily materialize it into its own `kind="scene"` timeline, then edit it with the existing operation pipeline plus a new transcript-driven ripple cut.

**Architecture:** A scene (marker over the rough_cut) materializes into a `kind="scene"` timeline whose clips are the scene's rough_cut slice, re-based to sequence 0. A `scenes.scene_timeline_id` column links them (idempotent — never re-materialize). Editing reuses the existing `/timelines/{id}/operations` pipeline; a new `delete_words` op maps a transcript word-range (asset frames) onto the scene timeline's sequence frames and ripple-deletes it. OTIO regeneration is factored into a shared `build_model`/`rebuild_otio` helper so the new code doesn't depend on `timelines.py` internals. Frontend adds a `useSceneTimeline` hook and a `FineCutView` that composes existing `Player`/`TimelineBar`/`TranscriptBar`/`SceneInspector`. The single `App.tsx` edit (wire the `finecut` branch) is last.

**Tech Stack:** Python 3.11 / FastAPI / SQLite / `uv` / `pytest` / `ruff` / `mypy` strict (no `print`). TypeScript strict (never `any`) / React / Vitest (plain asserts) / Tailwind. Conventional Commits. Invariants: integer frames, end-exclusive, samples for audio, OTIO source of truth.

**Reference spec:** `docs/superpowers/specs/2026-06-08-feinschnitt-stage-design.md`. This is **increment 4a** (core); 4b (audio/music) is a separate later plan. The migration here already adds the music columns so 4b needs no schema change.

**Conventions (same as the Rough Cut plan `2026-06-08-rough-cut-stage.md`):** repos use `db.transaction()`/`db.connection()`, ids `new_id()`, timestamps `utcnow_iso()`; routers `APIRouter(dependencies=[Depends(require_token)])` + `_db(request)`; backend commands `uv run --directory services/local-api …`; frontend `npm --prefix apps/desktop …`. **GIT HYGIENE:** stage only the files listed per task; NEVER `.claude/`/`build/`/`uv.lock`; do not switch branches; do not touch `App.tsx` before Task 9.

**Verbatim anchors (mirror these):**
- `_build_model` (`api/timelines.py:83-97`): fetches project, clip rows, builds `assets`/`speakers` dicts, returns `timeline_from_rows(timeline_row, clip_rows, project, assets, speakers)`.
- operations handler (`api/timelines.py:522-536`): `current = [EditClip.from_row(c) for c in repos.list_timeline_clips(...)]`; `new = _apply(db, current, body)`; `repos.replace_timeline_clips(db, tid, [c.to_row() for c in ordered(new)])`; then `update_timeline_otio(..., timeline_to_otio_string(_build_model(db, fresh)))`.
- `_apply` dispatch lives at `api/timelines.py:449-519`; `delete_range(clips, seq_in, seq_out)` ripples (closes the gap).
- `repos.create_timeline(db, *, project_id, name, kind, created_from=None)`; `repos.replace_timeline_clips(db, timeline_id, rows)` (rows are dicts; fresh ids; reads `asset_id`/`src_*`/`seq_*`/`lane`/`speaker_id`/`origin_word_*`/`speed_*`).
- `repos.get_word(db, word_id)` → dict with `start_frame`/`end_frame`/`asset_id`.

---

### Task 1: Migration `0009_scene_edit.sql`

**Files:** Create `services/local-api/src/laura/db/migrations/0009_scene_edit.sql`; Test `services/local-api/tests/test_scene_edit_migration.py`

- [ ] **Step 1: Failing test**

```python
# services/local-api/tests/test_scene_edit_migration.py
from __future__ import annotations
from pathlib import Path
from laura.config import Settings
from laura.db.database import SqliteDatabase


def test_scene_edit_migration_adds_columns(tmp_path: Path) -> None:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    with db.connection() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(scenes)").fetchall()}
    assert {"scene_timeline_id", "music_asset_id", "music_gain_percent"} <= cols
```

- [ ] **Step 2: Run — expect FAIL.** `uv run --directory services/local-api pytest tests/test_scene_edit_migration.py -q`

- [ ] **Step 3: Migration**

```sql
-- services/local-api/src/laura/db/migrations/0009_scene_edit.sql
-- Feinschnitt: link a scene to its materialised editable sub-timeline, plus one music
-- asset + gain per scene (music columns are used by increment 4b).
ALTER TABLE scenes ADD COLUMN scene_timeline_id TEXT;
ALTER TABLE scenes ADD COLUMN music_asset_id TEXT;
ALTER TABLE scenes ADD COLUMN music_gain_percent INTEGER NOT NULL DEFAULT 100;
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/db/migrations/0009_scene_edit.sql services/local-api/tests/test_scene_edit_migration.py
git commit -m "feat(feinschnitt): scene edit/music migration"
```

---

### Task 2: Shared `build_model` / `rebuild_otio` helper

**Files:** Create `services/local-api/src/laura/editing/otio_sync.py`; Modify `services/local-api/src/laura/api/timelines.py` (delegate `_build_model`); Test `services/local-api/tests/test_otio_sync.py`

- [ ] **Step 1: Failing test**

```python
# services/local-api/tests/test_otio_sync.py
from __future__ import annotations
from pathlib import Path
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.editing.otio_sync import rebuild_otio


def test_rebuild_otio_writes_nonempty_otio(tmp_path: Path) -> None:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    project = repos.create_project(db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p")
    asset = repos.create_asset(db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4")
    tl = repos.create_timeline(db, project_id=project["id"], name="t", kind="scene")
    repos.replace_timeline_clips(db, tl["id"], [{
        "asset_id": asset["id"], "src_in_frame": 0, "src_out_frame_exclusive": 30,
        "seq_in_frame": 0, "seq_out_frame_exclusive": 30, "lane": 0, "speed_num": 1, "speed_den": 1}])
    rebuild_otio(db, tl["id"])
    fresh = repos.get_timeline(db, tl["id"])
    assert fresh["otio_json"] and fresh["otio_json"] != "{}"
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: laura.editing.otio_sync`).

- [ ] **Step 3: Implement**

```python
# services/local-api/src/laura/editing/otio_sync.py
"""Build the canonical interchange model from DB rows and regenerate a timeline's OTIO.
Extracted so non-timeline routers (scenes) can keep OTIO as the source of truth without
importing private helpers from api/timelines.py."""
from __future__ import annotations

from typing import Any

from ..db import repos
from ..db.database import Database
from ..interchange.otio_io import timeline_to_otio_string
from ..interchange.timeline import Timeline, timeline_from_rows


def build_model(db: Database, timeline_row: dict[str, Any]) -> Timeline:
    project = repos.get_project(db, timeline_row["project_id"])
    assert project is not None
    clip_rows = repos.list_timeline_clips(db, timeline_row["id"])
    assets = {
        aid: a
        for aid in {c["asset_id"] for c in clip_rows}
        if (a := repos.get_asset(db, aid)) is not None
    }
    speakers = {
        sid: s
        for sid in {c["speaker_id"] for c in clip_rows if c.get("speaker_id")}
        if (s := repos.get_speaker(db, sid)) is not None
    }
    return timeline_from_rows(timeline_row, clip_rows, project, assets, speakers)


def rebuild_otio(db: Database, timeline_id: str) -> None:
    row = repos.get_timeline(db, timeline_id)
    if row is None:
        return
    repos.update_timeline_otio(db, timeline_id, timeline_to_otio_string(build_model(db, row)))
```

Then in `api/timelines.py` replace the body of `_build_model` (lines 83-97) with a delegation, and add the import `from ..editing.otio_sync import build_model`:

```python
def _build_model(db: Database, timeline_row: dict[str, Any]) -> Timeline:
    return build_model(db, timeline_row)
```

- [ ] **Step 4: Run — expect PASS.** Then ensure the refactor didn't break timelines:

```
uv run --directory services/local-api pytest tests/test_otio_sync.py tests/test_exports_api.py -q
uv run --directory services/local-api ruff check src/laura/editing/otio_sync.py src/laura/api/timelines.py
uv run --directory services/local-api mypy src/laura/editing/otio_sync.py
```
(If timeline-specific test files exist — `tests/test_timelines*.py` — run them too; all must stay green.)

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/editing/otio_sync.py services/local-api/src/laura/api/timelines.py services/local-api/tests/test_otio_sync.py
git commit -m "refactor(editing): extract build_model/rebuild_otio helper"
```

---

### Task 3: `set_scene_timeline` repo helper

**Files:** Modify `services/local-api/src/laura/db/repos.py` (append near the scene helpers); Test `services/local-api/tests/test_scene_timeline_repo.py`

- [ ] **Step 1: Failing test**

```python
# services/local-api/tests/test_scene_timeline_repo.py
from __future__ import annotations
from pathlib import Path
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase


def test_set_scene_timeline_links(tmp_path: Path) -> None:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    repos.replace_scenes(db, "p1", "tl1", [(0, 30)])
    sid = repos.list_scenes(db, "tl1")[0]["id"]
    repos.set_scene_timeline(db, sid, "scene-tl-9")
    assert repos.get_scene(db, sid)["scene_timeline_id"] == "scene-tl-9"
```

- [ ] **Step 2: Run — expect FAIL** (`has no attribute 'set_scene_timeline'`).

- [ ] **Step 3: Implement** — append to `repos.py`:

```python
def set_scene_timeline(db: Database, scene_id: str, timeline_id: str) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE scenes SET scene_timeline_id=? WHERE id=?", (timeline_id, scene_id))
```

- [ ] **Step 4: Run — expect PASS.** Then `ruff`/`mypy` on `repos.py`.
- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/db/repos.py services/local-api/tests/test_scene_timeline_repo.py
git commit -m "feat(feinschnitt): set_scene_timeline repo helper"
```

---

### Task 4: Materialization service + `POST /scenes/{id}/open` + `SceneOut` fields

**Files:** Create `services/local-api/src/laura/scenes/materialize.py`; Modify `services/local-api/src/laura/api/models.py` (extend `SceneOut`); Modify `services/local-api/src/laura/api/scenes.py` (endpoint + local `_timeline_out`); Test `services/local-api/tests/test_scene_open.py`

- [ ] **Step 1: Failing test**

```python
# services/local-api/tests/test_scene_open.py
from __future__ import annotations
from pathlib import Path
from fastapi.testclient import TestClient
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app

_TOKEN = "test-token"


def _app(tmp_path: Path):
    app = create_app(Settings(workspace_root=tmp_path / "ws", start_runner=False, token=_TOKEN))
    return TestClient(app), app.state.db


def _seed(db):
    project = repos.create_project(db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p")
    asset = repos.create_asset(db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4")
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_timeline_clips(db, tl["id"], [
        {"asset_id": asset["id"], "src_in_frame": 0, "src_out_frame_exclusive": 30, "seq_in_frame": 0,
         "seq_out_frame_exclusive": 30, "lane": 0, "speed_num": 1, "speed_den": 1},
        {"asset_id": asset["id"], "src_in_frame": 30, "src_out_frame_exclusive": 60, "seq_in_frame": 30,
         "seq_out_frame_exclusive": 60, "lane": 0, "speed_num": 1, "speed_den": 1},
        {"asset_id": asset["id"], "src_in_frame": 60, "src_out_frame_exclusive": 90, "seq_in_frame": 60,
         "seq_out_frame_exclusive": 90, "lane": 0, "speed_num": 1, "speed_den": 1}])
    # scene over clips 2..3 (seq 30..90)
    repos.replace_scenes(db, project["id"], tl["id"], [(0, 30), (30, 90)])
    return project["id"], tl["id"], repos.list_scenes(db, tl["id"])


def test_open_materializes_rebased_to_zero(tmp_path: Path) -> None:
    client, db = _app(tmp_path)
    _pid, _tl, scenes = _seed(db)
    sid = scenes[1]["id"]  # seq 30..90
    r = client.post(f"/scenes/{sid}/open", headers={"X-Laura-Token": _TOKEN})
    assert r.status_code == 200, r.text
    tl = r.json()
    assert tl["kind"] == "scene"
    assert [(c["seq_in_frame"], c["seq_out_frame_exclusive"]) for c in tl["clips"]] == [(0, 30), (30, 60)]
    # idempotent: same timeline id on re-open
    again = client.post(f"/scenes/{sid}/open", headers={"X-Laura-Token": _TOKEN})
    assert again.json()["id"] == tl["id"]
    assert repos.get_scene(db, sid)["scene_timeline_id"] == tl["id"]
```

> NOTE: mirror the real `Settings`/token + `create_project`/`create_asset` usage from `tests/test_scenes_api.py` (already in the repo from the Rough Cut work) if any kwarg differs.

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3a: Extend `SceneOut`** in `api/models.py` (add three optional fields after the existing ones):

```python
    scene_timeline_id: str | None = None
    music_asset_id: str | None = None
    music_gain_percent: int = 100
```

- [ ] **Step 3b: Materialization service** — `services/local-api/src/laura/scenes/materialize.py`:

```python
"""Lazily materialize a scene (marker over a rough_cut) into its own kind="scene" timeline:
the scene's clip slice, re-based to sequence 0. Idempotent — never re-materializes."""
from __future__ import annotations

from typing import Any

from ..db import repos
from ..db.database import Database
from ..editing.otio_sync import rebuild_otio


def materialize_scene(db: Database, scene: dict[str, Any]) -> dict[str, Any]:
    existing = scene.get("scene_timeline_id")
    if existing:
        row = repos.get_timeline(db, existing)
        if row is not None:
            return row
    tl = repos.create_timeline(
        db, project_id=scene["project_id"], name=scene["name"], kind="scene",
        created_from=scene["source_timeline_id"],
    )
    base = scene["seq_in_frame"]
    rows: list[dict[str, Any]] = []
    for c in repos.list_timeline_clips(db, scene["source_timeline_id"]):
        if scene["seq_in_frame"] <= c["seq_in_frame"] < scene["seq_out_frame_exclusive"]:
            rows.append({
                **c,
                "seq_in_frame": c["seq_in_frame"] - base,
                "seq_out_frame_exclusive": c["seq_out_frame_exclusive"] - base,
            })
    repos.replace_timeline_clips(db, tl["id"], rows)
    rebuild_otio(db, tl["id"])
    repos.set_scene_timeline(db, scene["id"], tl["id"])
    out = repos.get_timeline(db, tl["id"])
    assert out is not None
    return out
```

- [ ] **Step 3c: Endpoint** — add to `api/scenes.py` (import `ClipOut`, `TimelineOut` from `.models`, and `materialize_scene` from `..scenes.materialize`):

```python
def _timeline_out(db: Database, row: dict[str, Any]) -> TimelineOut:
    clips = [ClipOut(**c) for c in repos.list_timeline_clips(db, row["id"])]
    return TimelineOut(
        id=row["id"], project_id=row["project_id"], name=row["name"],
        kind=row["kind"], created_at=row["created_at"], clips=clips,
    )


@router.post("/scenes/{scene_id}/open", response_model=TimelineOut)
def open_scene(scene_id: str, request: Request) -> TimelineOut:
    db = _db(request)
    scene = repos.get_scene(db, scene_id)
    if scene is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scene not found")
    return _timeline_out(db, materialize_scene(db, scene))
```

(Add `from typing import Any` and the model imports at the top of `scenes.py` if missing.)

- [ ] **Step 4: Run — expect PASS.** Then `ruff`/`mypy` on the new/changed files.
- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/scenes/materialize.py services/local-api/src/laura/api/models.py services/local-api/src/laura/api/scenes.py services/local-api/tests/test_scene_open.py
git commit -m "feat(feinschnitt): materialize scene into editable sub-timeline"
```

---

### Task 5: `map_asset_range_to_seq` + `delete_words` operation

**Files:** Create `services/local-api/src/laura/editing/word_cut.py`; Modify `services/local-api/src/laura/api/timelines.py` (`_apply` branch + import); Test `services/local-api/tests/test_word_cut.py`, `services/local-api/tests/test_delete_words_op.py`

- [ ] **Step 1: Failing unit test (pure mapping)**

```python
# services/local-api/tests/test_word_cut.py
from __future__ import annotations
from laura.editing.operations import EditClip
from laura.editing.word_cut import map_asset_range_to_seq


def _clip(asset, src_in, src_out, seq_in):
    return EditClip(asset_id=asset, src_in_frame=src_in, src_out_frame_exclusive=src_out,
                    seq_in_frame=seq_in, seq_out_frame_exclusive=seq_in + (src_out - src_in))


def test_maps_word_range_within_single_clip() -> None:
    clips = [_clip("a", 100, 200, 0)]   # asset 100..200 -> seq 0..100
    assert map_asset_range_to_seq(clips, asset_id="a", src_lo=120, src_hi=140) == (20, 40)


def test_returns_none_when_asset_absent() -> None:
    clips = [_clip("a", 100, 200, 0)]
    assert map_asset_range_to_seq(clips, asset_id="b", src_lo=120, src_hi=140) is None


def test_spans_two_adjacent_clips() -> None:
    clips = [_clip("a", 100, 150, 0), _clip("a", 150, 200, 50)]  # seq 0..50, 50..100
    assert map_asset_range_to_seq(clips, asset_id="a", src_lo=140, src_hi=160) == (40, 60)
```

- [ ] **Step 2: Run — expect FAIL.** `uv run --directory services/local-api pytest tests/test_word_cut.py -q`

- [ ] **Step 3a: Implement** — `services/local-api/src/laura/editing/word_cut.py`:

```python
"""Map an asset source-frame range onto a timeline's sequence frames (speed=1) so a
transcript word selection can be ripple-deleted. Pure; operates on EditClip lists."""
from __future__ import annotations

from .operations import EditClip


def map_asset_range_to_seq(
    clips: list[EditClip], *, asset_id: str, src_lo: int, src_hi: int
) -> tuple[int, int] | None:
    """Sequence span covering the asset frames ``[src_lo, src_hi)`` across clips of
    ``asset_id``. Returns ``(seq_in, seq_out_exclusive)`` or ``None`` if nothing overlaps."""
    seq_in: int | None = None
    seq_out: int | None = None
    for c in clips:
        if c.asset_id != asset_id:
            continue
        if c.src_out_frame_exclusive <= src_lo or c.src_in_frame >= src_hi:
            continue
        lo = max(src_lo, c.src_in_frame)
        hi = min(src_hi, c.src_out_frame_exclusive)
        s_in = c.seq_in_frame + (lo - c.src_in_frame)
        s_out = c.seq_in_frame + (hi - c.src_in_frame)
        seq_in = s_in if seq_in is None else min(seq_in, s_in)
        seq_out = s_out if seq_out is None else max(seq_out, s_out)
    if seq_in is None or seq_out is None or seq_out <= seq_in:
        return None
    return (seq_in, seq_out)
```

- [ ] **Step 3b: Add the op** to `_apply` in `api/timelines.py` (import `from ..editing.word_cut import map_asset_range_to_seq`), placed next to the `append_from_words` branch:

```python
    if op == "delete_words":
        w0 = repos.get_word(db, _require(body.word_start_id, "word_start_id required"))
        w1 = repos.get_word(db, _require(body.word_end_id, "word_end_id required"))
        if w0 is None or w1 is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "word not found")
        if w0["asset_id"] != w1["asset_id"]:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                "words must be from the same asset")
        lo = min(w0["start_frame"], w1["start_frame"])
        hi = max(w0["end_frame"], w1["end_frame"])
        span = map_asset_range_to_seq(current, asset_id=w0["asset_id"], src_lo=lo, src_hi=hi)
        if span is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                "selected words are not present in this timeline")
        return delete_range(current, span[0], span[1])
```

- [ ] **Step 3c: Integration test** through the operations endpoint:

```python
# services/local-api/tests/test_delete_words_op.py
from __future__ import annotations
from pathlib import Path
from fastapi.testclient import TestClient
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app

_TOKEN = "test-token"


def test_delete_words_ripple_closes_gap(tmp_path: Path) -> None:
    app = create_app(Settings(workspace_root=tmp_path / "ws", start_runner=False, token=_TOKEN))
    client, db = TestClient(app), app.state.db
    project = repos.create_project(db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p")
    asset = repos.create_asset(db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4")
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="t", config={})
    seg = repos.create_transcript_segment(  # adapt to the real seed helper if the name differs
        db, asset_id=asset["id"], analysis_run_id=run["id"], speaker_id=None,
        start_sample=0, end_sample=0, start_frame=10, end_frame=20, text="uh", confidence=1.0)
    w = repos.create_transcript_word(
        db, segment_id=seg["id"], idx=0, start_sample=0, end_sample=0,
        start_frame=10, end_frame=20, text="uh", confidence=1.0, is_punctuation=False)
    tl = repos.create_timeline(db, project_id=project["id"], name="s", kind="scene")
    repos.replace_timeline_clips(db, tl["id"], [{
        "asset_id": asset["id"], "src_in_frame": 0, "src_out_frame_exclusive": 30, "seq_in_frame": 0,
        "seq_out_frame_exclusive": 30, "lane": 0, "speed_num": 1, "speed_den": 1}])
    r = client.post(f"/timelines/{tl['id']}/operations",
                    json={"op": "delete_words", "word_start_id": w["id"], "word_end_id": w["id"]},
                    headers={"X-Laura-Token": _TOKEN})
    assert r.status_code == 200, r.text
    clips = r.json()["clips"]
    # word at src 10..20 removed (ripple): two clips 0..10 and 10..20 remain, total length 20
    assert max(c["seq_out_frame_exclusive"] for c in clips) == 20
```

> NOTE: the transcript-seed helper names (`create_transcript_segment`/`create_transcript_word`) are a guess — open `services/local-api/src/laura/db/repos.py` and use the real helpers that insert into `transcript_segments`/`transcript_words` (or insert via `db.transaction()` directly). The asserted behaviour (ripple-delete shrinks the sequence to 20) is the contract.

- [ ] **Step 4: Run both — expect PASS.** `ruff`/`mypy` on `word_cut.py` + `timelines.py`.
- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/editing/word_cut.py services/local-api/src/laura/api/timelines.py services/local-api/tests/test_word_cut.py services/local-api/tests/test_delete_words_op.py
git commit -m "feat(feinschnitt): delete_words ripple operation"
```

---

### Task 6: Frontend api.ts — Scene fields + `openScene` + `deleteWords`

**Files:** Modify `apps/desktop/src/api.ts` (additive)

- [ ] **Step 1: Collision guard** `git status --short apps/desktop/src/api.ts`; if dirty, STOP and report. Else add to the `Scene` interface:

```typescript
  scene_timeline_id: string | null;
  music_asset_id: string | null;
  music_gain_percent: number;
```

- [ ] **Step 2: Add methods** in `LauraClient`:

```typescript
  openScene(sceneId: string): Promise<Timeline> {
    return this.request<Timeline>(`/scenes/${sceneId}/open`, { method: "POST" });
  }

  deleteWords(timelineId: string, wordStartId: string, wordEndId: string): Promise<Timeline> {
    return this.request<Timeline>(`/timelines/${timelineId}/operations`, {
      method: "POST",
      body: JSON.stringify({ op: "delete_words", word_start_id: wordStartId, word_end_id: wordEndId }),
    });
  }
```

- [ ] **Step 3: Typecheck** `npm --prefix apps/desktop run typecheck` — clean.
- [ ] **Step 4: Commit**

```bash
git add apps/desktop/src/api.ts
git commit -m "feat(feinschnitt): api client openScene + deleteWords"
```

---

### Task 7: `useSceneTimeline` hook

**Files:** Create `apps/desktop/src/hooks/useSceneTimeline.ts`; Test `apps/desktop/src/hooks/useSceneTimeline.test.ts`

- [ ] **Step 1: Failing test**

```typescript
// apps/desktop/src/hooks/useSceneTimeline.test.ts
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type LauraClient, type Timeline } from "../api";
import { useSceneTimeline } from "./useSceneTimeline";

const TL: Timeline = { id: "stl", project_id: "p", name: "Szene 1", kind: "scene", created_at: "", clips: [] };

function client(over: Partial<LauraClient>): LauraClient {
  return { openScene: vi.fn().mockResolvedValue(TL), deleteWords: vi.fn().mockResolvedValue(TL),
    ...over } as unknown as LauraClient;
}

describe("useSceneTimeline", () => {
  it("opens (materializes) the selected scene", async () => {
    const c = client({});
    const { result } = renderHook(() => useSceneTimeline(c, "scene1"));
    await waitFor(() => expect(result.current.timeline?.id).toBe("stl"));
    expect(c.openScene).toHaveBeenCalledWith("scene1");
  });

  it("deleteWords updates the timeline", async () => {
    const c = client({ deleteWords: vi.fn().mockResolvedValue({ ...TL, id: "stl2" }) });
    const { result } = renderHook(() => useSceneTimeline(c, "scene1"));
    await waitFor(() => expect(result.current.timeline).toBeTruthy());
    await act(async () => { await result.current.deleteWords("w0", "w1"); });
    expect(result.current.timeline?.id).toBe("stl2");
  });
});
```

- [ ] **Step 2: Run — expect FAIL.** `npm --prefix apps/desktop test -- useSceneTimeline`

- [ ] **Step 3: Implement** — `apps/desktop/src/hooks/useSceneTimeline.ts`:

```typescript
import { useCallback, useEffect, useState } from "react";

import { type LauraClient, type Timeline } from "../api";

export interface SceneTimelineController {
  timeline: Timeline | null;
  loading: boolean;
  error: string | null;
  deleteWords: (wordStartId: string, wordEndId: string) => Promise<void>;
  reload: () => Promise<void>;
}

export function useSceneTimeline(
  client: LauraClient | null,
  sceneId: string | null,
): SceneTimelineController {
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!client || !sceneId) {
      setTimeline(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setTimeline(await client.openScene(sceneId));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [client, sceneId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const deleteWords = useCallback(
    async (wordStartId: string, wordEndId: string) => {
      if (!client || !timeline) return;
      try {
        setTimeline(await client.deleteWords(timeline.id, wordStartId, wordEndId));
      } catch (e) {
        setError(String(e));
      }
    },
    [client, timeline],
  );

  return { timeline, loading, error, deleteWords, reload };
}
```

- [ ] **Step 4: Run — expect PASS.** Typecheck clean.
- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/hooks/useSceneTimeline.ts apps/desktop/src/hooks/useSceneTimeline.test.ts
git commit -m "feat(feinschnitt): useSceneTimeline hook"
```

---

### Task 8: `FineCutView` component (core: scene list + player + timeline + transcript cut + trim)

**Files:** Create `apps/desktop/src/components/FineCutView.tsx`; Test `apps/desktop/src/components/FineCutView.test.tsx`; **possibly Modify** `apps/desktop/src/components/TranscriptBar.tsx` (additive `onDeleteWords` prop).

This composes existing components against the materialized scene timeline. **First read** `TranscriptBar.tsx`, `TimelineBar.tsx`, `SceneInspector.tsx`, `Player.tsx` to confirm their real prop names, then implement to match (typecheck is the guard).

- [ ] **Step 1: Failing test** (mock the heavy children; assert the orchestration — scene selection opens a scene; a transcript delete calls `deleteWords`):

```typescript
// apps/desktop/src/components/FineCutView.test.tsx
import { fireEvent, render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type Asset, type LauraClient, type Scene, type Timeline } from "../api";
import { FineCutView } from "./FineCutView";

vi.mock("./Player", () => ({ Player: () => <div data-testid="player" /> }));
vi.mock("./TimelineBar", () => ({ TimelineBar: () => <div data-testid="timeline" /> }));
vi.mock("./SceneInspector", () => ({ SceneInspector: () => <div data-testid="inspector" /> }));
vi.mock("./TranscriptBar", () => ({
  TranscriptBar: (p: { onDeleteWords?: (a: string, b: string) => void }) => (
    <button type="button" onClick={() => p.onDeleteWords?.("w0", "w1")}>cut-word</button>
  ),
}));

const asset = { id: "a", rate_num: 30, rate_den: 1, display_name: "a" } as unknown as Asset;
const TL: Timeline = { id: "stl", project_id: "p", name: "Szene 1", kind: "scene", created_at: "", clips: [] };
const scenes: Scene[] = [{ id: "s1", project_id: "p", source_timeline_id: "tl", name: "Szene 1",
  order_index: 0, seq_in_frame: 0, seq_out_frame_exclusive: 30, scene_timeline_id: null,
  music_asset_id: null, music_gain_percent: 100 }];

function client(over: Partial<LauraClient>): LauraClient {
  return { listScenes: vi.fn().mockResolvedValue(scenes), openScene: vi.fn().mockResolvedValue(TL),
    deleteWords: vi.fn().mockResolvedValue(TL), getTranscript: vi.fn().mockResolvedValue([]),
    ...over } as unknown as LauraClient;
}

describe("FineCutView", () => {
  it("opens the first scene and routes a transcript cut to deleteWords", async () => {
    const c = client({});
    const { getByText } = render(
      <FineCutView client={c} asset={asset} roughCutId="tl" segments={[]}
        currentFrame={0} seek={null} onSeek={vi.fn()} onFrame={vi.fn()} />);
    await waitFor(() => expect(c.openScene).toHaveBeenCalledWith("s1"));
    fireEvent.click(getByText("cut-word"));
    await waitFor(() => expect(c.deleteWords).toHaveBeenCalledWith("stl", "w0", "w1"));
  });
});
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3a: (If needed) extend `TranscriptBar`** — add an OPTIONAL prop `onDeleteWords?: (wordStartId: string, wordEndId: string) => void` and, when present, a per-word/selection delete affordance (e.g. a small ✂ on a selected word range, or a delete button per segment that passes the segment's first/last word ids). Keep it additive so existing `TranscriptBar` usage (Rough Cut) is unaffected. Match the component's existing word/segment rendering.

- [ ] **Step 3b: Implement `FineCutView`** — `apps/desktop/src/components/FineCutView.tsx`. Contract:
  - Props: `{ client: LauraClient; asset: Asset | null; roughCutId: string | null; segments: Segment[]; currentFrame: number; seek: { frame: number } | null; onSeek: (f: number) => void; onFrame: (f: number) => void }`. It fetches its own scene list via `useScenes(client, roughCutId)` (so `App.tsx` only passes `roughCutId`).
  - Local state `selectedSceneId` (defaults to the first scene once loaded). A left **scene list** (buttons, prev/next) sets it.
  - `const scene = useSceneTimeline(client, selectedSceneId)` — opens/materializes on selection.
  - Center: `<Player asset={asset} seekTo={seek} onFrame={onFrame} />`; below it `<TimelineBar client={client} timeline={scene.timeline} onChange={() => void scene.reload()} onSelect={…} />`.
  - `<TranscriptBar … onDeleteWords={(a, b) => void scene.deleteWords(a, b)} />` (pass the asset's transcript `segments`).
  - Right: `<SceneInspector … />` for the selected clip (mirror how `App.tsx` wires it today).
  - Empty states: no scenes → CTA "Erst Rough Cut ausführen"; no asset → CTA.

Representative skeleton (adapt prop names to the real components after reading them):

```typescript
import { type ReactElement, useEffect, useState } from "react";

import { type Asset, type LauraClient, type Segment } from "../api";
import { useSceneTimeline } from "../hooks/useSceneTimeline";
import { useScenes } from "../hooks/useScenes";
import { Player } from "./Player";
import { TimelineBar } from "./TimelineBar";
import { TranscriptBar } from "./TranscriptBar";

export function FineCutView({
  client, asset, roughCutId, segments, currentFrame, seek, onSeek, onFrame,
}: {
  client: LauraClient;
  asset: Asset | null;
  roughCutId: string | null;
  segments: Segment[];
  currentFrame: number;
  seek: { frame: number } | null;
  onSeek: (f: number) => void;
  onFrame: (f: number) => void;
}): ReactElement {
  const { scenes } = useScenes(client, roughCutId);
  const [selectedSceneId, setSelectedSceneId] = useState<string | null>(null);
  useEffect(() => {
    if (!selectedSceneId && scenes[0]) setSelectedSceneId(scenes[0].id);
  }, [scenes, selectedSceneId]);
  const scene = useSceneTimeline(client, selectedSceneId);

  if (scenes.length === 0) {
    return <div className="flex flex-1 items-center justify-center text-sm text-slate-600">
      Noch keine Szenen — erst Rough Cut ausführen.</div>;
  }
  return (
    <div className="grid min-h-0 flex-1 grid-cols-[200px_1fr] gap-px bg-edge">
      <aside className="flex flex-col gap-1 overflow-auto bg-ink p-2">
        {scenes.map((s) => (
          <button key={s.id} type="button" onClick={() => setSelectedSceneId(s.id)}
            className={`truncate rounded px-2 py-1 text-left text-xs ${s.id === selectedSceneId ? "bg-sky-700 text-white" : "text-slate-300 hover:bg-slate-700"}`}>
            {s.name}
          </button>
        ))}
      </aside>
      <section className="flex min-h-0 flex-col">
        <div className="flex min-h-0 flex-1 items-center justify-center bg-black/40">
          {asset && <Player asset={asset} seekTo={seek} onFrame={onFrame} />}
        </div>
        <TimelineBar client={client} timeline={scene.timeline} onChange={() => void scene.reload()} onScrub={(_a, f) => onSeek(f)} />
        <TranscriptBar client={client} assetId={asset?.id ?? null} assetName={asset?.display_name ?? null}
          segments={segments} note={null} currentFrame={currentFrame} onSeek={onSeek}
          canAppend={false} onAppendSegment={() => undefined}
          onDeleteWords={(a, b) => void scene.deleteWords(a, b)} />
        {scene.error && <div className="px-3 py-1 text-xs text-red-400">{scene.error}</div>}
      </section>
    </div>
  );
}
```

> The exact `TimelineBar`/`TranscriptBar`/`SceneInspector` props MUST match their real signatures (read them; typecheck is the guard). Drop the `SceneInspector` from the skeleton if wiring it cleanly needs the selected-clip plumbing — it can be added once the clip-selection state is in place. Keep `onDeleteWords` wired.

- [ ] **Step 4: Run — expect PASS.** `npm --prefix apps/desktop run typecheck` clean; `npm --prefix apps/desktop test` (full) green.
- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/components/FineCutView.tsx apps/desktop/src/components/FineCutView.test.tsx apps/desktop/src/components/TranscriptBar.tsx
git commit -m "feat(feinschnitt): FineCutView (trim + transcript cut)"
```

---

### Task 9: Wire `FineCutView` into `App.tsx` (LAST — collision-sensitive)

**Files:** Modify `apps/desktop/src/App.tsx`.

**Precondition:** `git status --short apps/desktop/src/App.tsx` must be clean. If not, STOP and hand the diff to the user.

- [ ] **Step 1: Import** `import { FineCutView } from "./components/FineCutView";` (alphabetical).

- [ ] **Step 2: Split the shared condition.** Change `{(stage === "finecut" || stage === "assemble") && (` to `{stage === "assemble" && (`, and add a `finecut` branch immediately before it:

```tsx
{stage === "finecut" && client && (
  <FineCutView
    client={client}
    asset={detailAsset}
    roughCutId={roughCut?.id ?? null}
    segments={analysis.segments}
    currentFrame={currentFrame}
    seek={seek}
    onSeek={seekToFrame}
    onFrame={(f) => setCurrentFrame(f)}
  />
)}
```

> `FineCutView` fetches its own scene list (via `useScenes(client, roughCutId)`), so `App.tsx` only passes `roughCutId` — the edit stays import + branch only. Verify `detailAsset`, `analysis`, `seek`, `currentFrame`, `seekToFrame`, `setCurrentFrame`, `roughCut` names against `App.tsx` (all confirmed present from the Rough Cut wiring).

- [ ] **Step 3: Verify** `npm --prefix apps/desktop run typecheck` + `npm --prefix apps/desktop test` — all green.
- [ ] **Step 4: Commit (App.tsx only)**

```bash
git add apps/desktop/src/App.tsx
git commit -m "feat(feinschnitt): wire FineCutView into the finecut stage"
```

---

## Final verification (4a)
```
uv run --directory services/local-api pytest tests/test_scene_edit_migration.py tests/test_otio_sync.py tests/test_scene_timeline_repo.py tests/test_scene_open.py tests/test_word_cut.py tests/test_delete_words_op.py -q
uv run --directory services/local-api pytest tests/test_exports_api.py tests/test_scenes_api.py -q   # no regression
npm --prefix apps/desktop run typecheck && npm --prefix apps/desktop test
```
Manual (headless can't verify): Rough Cut → Szenen erzeugen → Feinschnitt → Szene wählen (materialisiert) → Trim + Wort-Klick-Schnitt.

## Spec coverage (4a)
| Spec § | Task |
|---|---|
| §3 Migration (scene_timeline_id; music cols ready for 4b) | 1 |
| §4.1 Materialisierung | 2,3,4 |
| §4.2 delete_words (Ripple) + Wort→Seq | 5 |
| §4.3 Trim (reuse) | 8 |
| §6 Frontend (api/hook/FineCutView) | 6,7,8 |
| §6 App.tsx-Verdrahtung (last) | 9 |

## Deferred to 4b (separate plan)
Music API (`PUT/DELETE /scenes/{id}/music`), render audio-mix (`render_clips_mp4` music param + handler scene lookup), music picker + gain + `<audio>` preview. The migration (Task 1) already provides `music_asset_id`/`music_gain_percent`.
