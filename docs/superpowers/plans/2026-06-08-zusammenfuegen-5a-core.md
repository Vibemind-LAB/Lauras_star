# Zusammenfügen 5a (Core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Arrange the project's scenes into one final `kind="sequence"` timeline (ordered scene references, flattened on demand), make render/OTIO sequence-aware so the sequence exports to MP4, and build the Bin + Sequence-track drag-reorder UI.

**Architecture:** A `sequence_items(sequence_timeline_id, scene_id, order_index)` table holds the ordered scene references (source of truth) — `timeline_clips`/`EditClip` are untouched. `flatten_sequence` resolves items → scenes' materialized `kind="scene"` timelines → concatenated flat clip rows at runtime (so Feinschnitt edits propagate). `resolve_clip_rows(db, timeline_row)` returns the flattened rows for `kind="sequence"` and `list_timeline_clips` otherwise; both `build_model` (OTIO) and the render handler use it, so the existing `render_clips_mp4` renders the whole sequence and Export works. Frontend: `useSequence` + `AssembleView` (scene Bin + drag-reorder Sequence-track + structural preview). The single `App.tsx` edit (wire `assemble`) is last.

**Tech Stack / conventions:** identical to the Feinschnitt 4a plan (`2026-06-08-feinschnitt-4a-core.md`) — uv/pytest/ruff/mypy strict, TS strict no `any`, vitest plain asserts, Conventional Commits, frame/sample invariants, `uv run --directory services/local-api …`, `npm --prefix apps/desktop …`. **GIT HYGIENE:** stage only listed files; never `.claude/`/`build/`/`uv.lock`; don't switch branches; don't touch `App.tsx` before Task 9.

**Reference spec:** `docs/superpowers/specs/2026-06-08-zusammenfuegen-stage-design.md`. **Increment 5a**; 5b (real concatenating `SequencePlayer`) is a separate later plan.

**Verbatim anchors:**
- `editing/otio_sync.py` `build_model(db, timeline_row)`: `clip_rows = repos.list_timeline_clips(db, timeline_row["id"])`, builds `assets`/`speakers`, `timeline_from_rows(timeline_row, clip_rows, project, assets, speakers)`.
- `render/handlers.py` `handle_render`: has `tl = repos.get_timeline(ctx.db, exp["timeline_id"])` (non-None), builds `clips: list[tuple[Path,int,int]]` by iterating `repos.list_timeline_clips(ctx.db, exp["timeline_id"])` + `repos.get_asset`.
- `scenes/materialize.py` `materialize_scene(db, scene) -> dict` (idempotent; sets `scene.scene_timeline_id`).
- `repos.create_timeline(db, *, project_id, name, kind, created_from=None)`; `ClipOut` model has `id, asset_id, src_in_frame, src_out_frame_exclusive, seq_in_frame, seq_out_frame_exclusive, lane, speaker_id, origin_word_start_id, origin_word_end_id, speed_num, speed_den`.

---

### Task 1: Migration `0010_sequences.sql`

**Files:** Create `services/local-api/src/laura/db/migrations/0010_sequences.sql`; Test `services/local-api/tests/test_sequences_migration.py`

- [ ] **Step 1: Failing test**

```python
# services/local-api/tests/test_sequences_migration.py
from __future__ import annotations
from pathlib import Path
from laura.config import Settings
from laura.db.database import SqliteDatabase


def test_sequence_items_table(tmp_path: Path) -> None:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    with db.connection() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(sequence_items)").fetchall()}
    assert {"id", "sequence_timeline_id", "scene_id", "order_index", "created_at"} <= cols
```

- [ ] **Step 2: Run — expect FAIL.** `uv run --directory services/local-api pytest tests/test_sequences_migration.py -q`
- [ ] **Step 3: Migration**

```sql
-- services/local-api/src/laura/db/migrations/0010_sequences.sql
-- Stage 5: a sequence (kind="sequence" timeline) is an ordered list of scene references.
CREATE TABLE sequence_items (
  id TEXT PRIMARY KEY,
  sequence_timeline_id TEXT NOT NULL,
  scene_id TEXT NOT NULL,
  order_index INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_sequence_items ON sequence_items (sequence_timeline_id, order_index);
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** `git add` the two files; `git commit -m "feat(zusammenfuegen): sequence_items migration"` (+ co-author trailer).

---

### Task 2: `flatten_sequence` + sequence repos

**Files:** Create `services/local-api/src/laura/sequences/__init__.py` (empty), `services/local-api/src/laura/sequences/flatten.py`; Modify `services/local-api/src/laura/db/repos.py` (append); Test `services/local-api/tests/test_flatten_sequence.py`

- [ ] **Step 1: Failing test**

```python
# services/local-api/tests/test_flatten_sequence.py
from __future__ import annotations
from pathlib import Path
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.sequences.flatten import flatten_sequence


def _scene_timeline(db, project_id, asset_id, src_in, src_out):
    """A materialized scene timeline with one clip, rebased to seq 0."""
    tl = repos.create_timeline(db, project_id=project_id, name="s", kind="scene")
    repos.replace_timeline_clips(db, tl["id"], [{
        "asset_id": asset_id, "src_in_frame": src_in, "src_out_frame_exclusive": src_out,
        "seq_in_frame": 0, "seq_out_frame_exclusive": src_out - src_in,
        "lane": 0, "speed_num": 1, "speed_den": 1}])
    return tl


def test_flatten_concatenates_scenes_with_offsets(tmp_path: Path) -> None:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    project = repos.create_project(db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p")
    a1 = repos.create_asset(db, project_id=project["id"], type="video", display_name="a1", source_path="/tmp/a1.mp4")
    a2 = repos.create_asset(db, project_id=project["id"], type="video", display_name="a2", source_path="/tmp/a2.mp4")
    rc = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_scenes(db, project["id"], rc["id"], [(0, 30), (30, 70)])
    s1, s2 = repos.list_scenes(db, rc["id"])
    # materialize each scene to its own timeline (40f and 30f from two assets)
    t1 = _scene_timeline(db, project["id"], a1["id"], 100, 130)  # 30 frames
    t2 = _scene_timeline(db, project["id"], a2["id"], 200, 240)  # 40 frames
    repos.set_scene_timeline(db, s1["id"], t1["id"])
    repos.set_scene_timeline(db, s2["id"], t2["id"])
    seq = repos.create_timeline(db, project_id=project["id"], name="seq", kind="sequence")
    repos.replace_sequence_items(db, seq["id"], [s2["id"], s1["id"]])  # s2 first
    rows = flatten_sequence(db, seq["id"])
    assert [(r["asset_id"], r["seq_in_frame"], r["seq_out_frame_exclusive"]) for r in rows] == [
        (a2["id"], 0, 40),     # s2: 40 frames at offset 0
        (a1["id"], 40, 70),    # s1: 30 frames at offset 40
    ]
    assert rows[0]["src_in_frame"] == 200 and rows[1]["src_in_frame"] == 100
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: laura.sequences` / missing repos).
- [ ] **Step 3a: repos** — append to `repos.py`:

```python
def get_or_create_project_sequence(db: Database, project_id: str) -> dict[str, Any]:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM timelines WHERE project_id=? AND kind='sequence' "
            "ORDER BY created_at LIMIT 1",
            (project_id,),
        ).fetchone()
    if row is not None:
        return dict(row)
    return create_timeline(db, project_id=project_id, name="Sequenz", kind="sequence")


def list_sequence_items(db: Database, sequence_timeline_id: str) -> list[dict[str, Any]]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM sequence_items WHERE sequence_timeline_id=? ORDER BY order_index",
            (sequence_timeline_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def replace_sequence_items(
    db: Database, sequence_timeline_id: str, scene_ids: list[str]
) -> None:
    now = utcnow_iso()
    with db.transaction() as conn:
        conn.execute(
            "DELETE FROM sequence_items WHERE sequence_timeline_id=?", (sequence_timeline_id,)
        )
        for i, sid in enumerate(scene_ids):
            conn.execute(
                "INSERT INTO sequence_items (id, sequence_timeline_id, scene_id, order_index, "
                "created_at) VALUES (?,?,?,?,?)",
                (new_id(), sequence_timeline_id, sid, i, now),
            )
```

- [ ] **Step 3b: flatten** — `sequences/__init__.py` (empty) + `sequences/flatten.py`:

```python
# services/local-api/src/laura/sequences/flatten.py
"""Resolve a sequence (ordered scene references) into a flat clip list at runtime, so scene
edits propagate. Each scene contributes its materialized timeline's clips, offset by the
running sequence position."""
from __future__ import annotations

from typing import Any

from ..db import repos
from ..db.database import Database


def flatten_sequence(db: Database, sequence_timeline_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    for item in repos.list_sequence_items(db, sequence_timeline_id):
        scene = repos.get_scene(db, item["scene_id"])
        if scene is None or not scene.get("scene_timeline_id"):
            continue  # not materialized -> skip (the assemble PUT materializes scenes)
        clips = repos.list_timeline_clips(db, scene["scene_timeline_id"])
        scene_len = max((c["seq_out_frame_exclusive"] for c in clips), default=0)
        for c in clips:
            rows.append({
                **c,
                "seq_in_frame": offset + c["seq_in_frame"],
                "seq_out_frame_exclusive": offset + c["seq_out_frame_exclusive"],
            })
        offset += scene_len
    return rows
```

- [ ] **Step 4: Run — expect PASS.** ruff + mypy on `sequences/flatten.py` + `repos.py`.
- [ ] **Step 5: Commit** `git add` `sequences/__init__.py sequences/flatten.py db/repos.py tests/test_flatten_sequence.py`; `git commit -m "feat(zusammenfuegen): flatten_sequence + sequence repos"`.

---

### Task 3: `resolve_clip_rows` — sequence-aware OTIO + render

**Files:** Modify `services/local-api/src/laura/editing/otio_sync.py` (add `resolve_clip_rows`, use it in `build_model`); Modify `services/local-api/src/laura/render/handlers.py` (use `resolve_clip_rows`); Test `services/local-api/tests/test_sequence_render.py`

- [ ] **Step 1: Failing test** (real ffmpeg; two assets → one MP4 via the export render path; mirror `tests/test_render_job.py` setup):

```python
# services/local-api/tests/test_sequence_render.py
import os
import shutil
from pathlib import Path
import pytest
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.ingest.ffmpeg import run_ffmpeg
from laura.jobs import JobRunner, default_registry, enqueue

pytestmark = pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None, reason="ffmpeg")


def _src(tmp_path, name, secs):
    p = tmp_path / name
    run_ffmpeg(["-f", "lavfi", "-i", f"testsrc=duration={secs}:size=320x240:rate=30",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)])
    return p


def _scene_tl(db, pid, aid):
    tl = repos.create_timeline(db, project_id=pid, name="s", kind="scene")
    repos.replace_timeline_clips(db, tl["id"], [{
        "asset_id": aid, "src_in_frame": 0, "src_out_frame_exclusive": 30,
        "seq_in_frame": 0, "seq_out_frame_exclusive": 30, "lane": 0, "speed_num": 1, "speed_den": 1}])
    return tl


def test_sequence_renders_to_mp4(tmp_path):
    m1, m2 = _src(tmp_path, "a1.mp4", 1), _src(tmp_path, "a2.mp4", 1)
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path); db.migrate()
    proot = settings.workspace_root / "project"; proot.mkdir(parents=True, exist_ok=True)
    project = repos.create_project(db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root=str(proot))
    a1 = repos.create_asset(db, project_id=project["id"], type="video", display_name="a1", source_path=str(m1))
    a2 = repos.create_asset(db, project_id=project["id"], type="video", display_name="a2", source_path=str(m2))
    rc = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_scenes(db, project["id"], rc["id"], [(0, 30), (30, 60)])
    s1, s2 = repos.list_scenes(db, rc["id"])
    repos.set_scene_timeline(db, s1["id"], _scene_tl(db, project["id"], a1["id"])["id"])
    repos.set_scene_timeline(db, s2["id"], _scene_tl(db, project["id"], a2["id"])["id"])
    seq = repos.get_or_create_project_sequence(db, project["id"])
    repos.replace_sequence_items(db, seq["id"], [s1["id"], s2["id"]])
    exp = repos.create_export(db, project_id=project["id"], timeline_id=seq["id"], format="mp4")
    registry = default_registry()
    from laura.render.handlers import register_render_handlers
    register_render_handlers(registry)
    runner = JobRunner(db, registry)
    enqueue(db, queue="export", kind="export.render", payload={"export_id": exp["id"]}, idempotency_key=f"r:{exp['id']}")
    while runner.run_once():
        pass
    done = repos.get_export(db, exp["id"])
    assert done["status"] == "ready", done
    assert Path(done["path"]).exists() and done["size_bytes"] > 0
```

- [ ] **Step 2: Run — expect FAIL** (sequence has no `timeline_clips` → render errors "no clips").
- [ ] **Step 3a: `resolve_clip_rows`** in `editing/otio_sync.py` (add at top-level + use in `build_model`):

```python
from ..sequences.flatten import flatten_sequence
# ...
def resolve_clip_rows(db: Database, timeline_row: dict[str, Any]) -> list[dict[str, Any]]:
    if timeline_row.get("kind") == "sequence":
        return flatten_sequence(db, timeline_row["id"])
    return repos.list_timeline_clips(db, timeline_row["id"])
```

In `build_model`, replace `clip_rows = repos.list_timeline_clips(db, timeline_row["id"])` with `clip_rows = resolve_clip_rows(db, timeline_row)`.

- [ ] **Step 3b: render handler** in `render/handlers.py` — change the clip-building loop source from `repos.list_timeline_clips(ctx.db, exp["timeline_id"])` to `resolve_clip_rows(ctx.db, tl)` (import `from ..editing.otio_sync import resolve_clip_rows`; `tl` is already in scope). The per-row asset lookup + tuple building stay the same.

- [ ] **Step 4: Run — expect PASS** (sequence renders to a real MP4). Regression: `uv run --directory services/local-api pytest tests/test_render_mp4.py tests/test_render_job.py tests/test_otio_sync.py tests/test_exports_api.py -q`. ruff + mypy.
- [ ] **Step 5: Commit** `git add` `editing/otio_sync.py render/handlers.py tests/test_sequence_render.py`; `git commit -m "feat(zusammenfuegen): sequence-aware OTIO + render (flatten)"`.

---

### Task 4: Assemble API (`api/sequences.py` + models + register)

**Files:** Modify `api/models.py` (3 models); Create `api/sequences.py`; Modify `main.py` (register); Test `services/local-api/tests/test_sequences_api.py`

- [ ] **Step 1: Failing test** (mirror `tests/test_scene_open.py` fixture):

```python
# services/local-api/tests/test_sequences_api.py
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


def _seed_two_scenes(db):
    project = repos.create_project(db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p")
    asset = repos.create_asset(db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4")
    rc = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_timeline_clips(db, rc["id"], [
        {"asset_id": asset["id"], "src_in_frame": 0, "src_out_frame_exclusive": 30, "seq_in_frame": 0,
         "seq_out_frame_exclusive": 30, "lane": 0, "speed_num": 1, "speed_den": 1},
        {"asset_id": asset["id"], "src_in_frame": 30, "src_out_frame_exclusive": 60, "seq_in_frame": 30,
         "seq_out_frame_exclusive": 60, "lane": 0, "speed_num": 1, "speed_den": 1}])
    repos.replace_scenes(db, project["id"], rc["id"], [(0, 30), (30, 60)])
    return project["id"], [s["id"] for s in repos.list_scenes(db, rc["id"])]


def test_get_creates_sequence_then_put_orders(tmp_path: Path) -> None:
    client, db = _app(tmp_path)
    pid, scene_ids = _seed_two_scenes(db)
    h = {"X-Laura-Token": _TOKEN}
    r = client.get(f"/projects/{pid}/sequence", headers=h)
    assert r.status_code == 200, r.text
    seq_id = r.json()["timeline_id"]
    r2 = client.put(f"/sequences/{seq_id}/scenes", json={"scene_ids": list(reversed(scene_ids))}, headers=h)
    assert r2.status_code == 200, r2.text
    items = r2.json()["items"]
    assert [it["scene_id"] for it in items] == list(reversed(scene_ids))
    assert [it["order_index"] for it in items] == [0, 1]
    # scenes got materialized -> flattened clips exist
    flat = client.get(f"/sequences/{seq_id}/flattened", headers=h).json()
    assert len(flat) == 2


def test_put_unknown_scene_422(tmp_path: Path) -> None:
    client, db = _app(tmp_path)
    pid, _ = _seed_two_scenes(db)
    h = {"X-Laura-Token": _TOKEN}
    seq_id = client.get(f"/projects/{pid}/sequence", headers=h).json()["timeline_id"]
    r = client.put(f"/sequences/{seq_id}/scenes", json={"scene_ids": ["nope"]}, headers=h)
    assert r.status_code == 422
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3a: models** — append to `api/models.py`:

```python
class SequenceItemOut(BaseModel):
    id: str
    scene_id: str
    scene_name: str
    order_index: int


class SequenceOut(BaseModel):
    timeline_id: str
    project_id: str
    items: list[SequenceItemOut] = Field(default_factory=list)


class SetSequenceScenesRequest(BaseModel):
    scene_ids: list[str]
```

- [ ] **Step 3b: router** — `api/sequences.py`:

```python
"""Sequence (stage 5) endpoints: arrange scenes into one final sequence and read the
flattened clip list. The sequence is a kind="sequence" timeline; its content is the ordered
scene references in `sequence_items`, flattened on demand."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..db import repos
from ..db.database import Database
from ..editing.otio_sync import rebuild_otio
from ..scenes.materialize import materialize_scene
from ..sequences.flatten import flatten_sequence
from .models import (
    ClipOut,
    SequenceItemOut,
    SequenceOut,
    SetSequenceScenesRequest,
)
from .security import require_token

router = APIRouter(tags=["sequences"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _sequence_out(db: Database, seq: dict[str, Any]) -> SequenceOut:
    items: list[SequenceItemOut] = []
    for it in repos.list_sequence_items(db, seq["id"]):
        scene = repos.get_scene(db, it["scene_id"])
        items.append(SequenceItemOut(
            id=it["id"], scene_id=it["scene_id"],
            scene_name=scene["name"] if scene is not None else "?",
            order_index=it["order_index"],
        ))
    return SequenceOut(timeline_id=seq["id"], project_id=seq["project_id"], items=items)


@router.get("/projects/{project_id}/sequence", response_model=SequenceOut)
def get_sequence(project_id: str, request: Request) -> SequenceOut:
    db = _db(request)
    if repos.get_project(db, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return _sequence_out(db, repos.get_or_create_project_sequence(db, project_id))


@router.put("/sequences/{sequence_id}/scenes", response_model=SequenceOut)
def set_sequence_scenes(
    sequence_id: str, body: SetSequenceScenesRequest, request: Request
) -> SequenceOut:
    db = _db(request)
    seq = repos.get_timeline(db, sequence_id)
    if seq is None or seq["kind"] != "sequence":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sequence not found")
    for sid in body.scene_ids:
        scene = repos.get_scene(db, sid)
        if scene is None or scene["project_id"] != seq["project_id"]:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown scene: {sid}")
        materialize_scene(db, scene)
    repos.replace_sequence_items(db, sequence_id, body.scene_ids)
    rebuild_otio(db, sequence_id)
    return _sequence_out(db, seq)


@router.get("/sequences/{sequence_id}/flattened", response_model=list[ClipOut])
def get_sequence_flattened(sequence_id: str, request: Request) -> list[ClipOut]:
    db = _db(request)
    if repos.get_timeline(db, sequence_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sequence not found")
    return [ClipOut(**c) for c in flatten_sequence(db, sequence_id)]
```

- [ ] **Step 3c: register** in `main.py`: add `sequences` to `from .api import (...)` and `app.include_router(sequences.router)`.

- [ ] **Step 4: Run — expect PASS** (2 tests). No regression: `uv run --directory services/local-api pytest tests/test_scenes_api.py tests/test_exports_api.py -q`. ruff + mypy.
- [ ] **Step 5: Commit** `git add` `api/models.py api/sequences.py main.py tests/test_sequences_api.py`; `git commit -m "feat(zusammenfuegen): assemble API"`.

---

### Task 5: Frontend api.ts — `Sequence` types + methods

**Files:** Modify `apps/desktop/src/api.ts` (additive)

- [ ] **Step 1: Collision guard** `git status --short apps/desktop/src/api.ts` (BLOCKED if dirty). Add types + methods:

```typescript
export interface SequenceItem {
  id: string;
  scene_id: string;
  scene_name: string;
  order_index: number;
}

export interface Sequence {
  timeline_id: string;
  project_id: string;
  items: SequenceItem[];
}
```

```typescript
  getProjectSequence(projectId: string): Promise<Sequence> {
    return this.request<Sequence>(`/projects/${projectId}/sequence`);
  }

  setSequenceScenes(sequenceId: string, sceneIds: string[]): Promise<Sequence> {
    return this.request<Sequence>(`/sequences/${sequenceId}/scenes`, {
      method: "PUT",
      body: JSON.stringify({ scene_ids: sceneIds }),
    });
  }

  getSequenceFlattened(sequenceId: string): Promise<TimelineClip[]> {
    return this.request<TimelineClip[]>(`/sequences/${sequenceId}/flattened`);
  }
```

- [ ] **Step 2: Typecheck** clean. **Step 3: Commit** `git add apps/desktop/src/api.ts`; `git commit -m "feat(zusammenfuegen): api client sequence methods"`.

---

### Task 6: `useSequence` hook

**Files:** Create `apps/desktop/src/hooks/useSequence.ts`; Test `apps/desktop/src/hooks/useSequence.test.ts`

- [ ] **Step 1: Failing test**

```typescript
// apps/desktop/src/hooks/useSequence.test.ts
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type LauraClient, type Sequence } from "../api";
import { useSequence } from "./useSequence";

const SEQ: Sequence = { timeline_id: "seq", project_id: "p", items: [] };

function client(over: Partial<LauraClient>): LauraClient {
  return { getProjectSequence: vi.fn().mockResolvedValue(SEQ),
    setSequenceScenes: vi.fn().mockResolvedValue({ ...SEQ, items: [
      { id: "i1", scene_id: "s2", scene_name: "Szene 2", order_index: 0 }] }),
    ...over } as unknown as LauraClient;
}

describe("useSequence", () => {
  it("loads the project sequence", async () => {
    const c = client({});
    const { result } = renderHook(() => useSequence(c, "p"));
    await waitFor(() => expect(result.current.sequence?.timeline_id).toBe("seq"));
    expect(c.getProjectSequence).toHaveBeenCalledWith("p");
  });
  it("setScenes PUTs the new order", async () => {
    const c = client({});
    const { result } = renderHook(() => useSequence(c, "p"));
    await waitFor(() => expect(result.current.sequence).toBeTruthy());
    await act(async () => { await result.current.setScenes(["s2"]); });
    expect(c.setSequenceScenes).toHaveBeenCalledWith("seq", ["s2"]);
    expect(result.current.sequence?.items[0].scene_id).toBe("s2");
  });
}
);
```

- [ ] **Step 2: Run — expect FAIL.** **Step 3: Implement**:

```typescript
// apps/desktop/src/hooks/useSequence.ts
import { useCallback, useEffect, useState } from "react";

import { type LauraClient, type Sequence } from "../api";

export interface SequenceController {
  sequence: Sequence | null;
  error: string | null;
  setScenes: (sceneIds: string[]) => Promise<void>;
  reload: () => Promise<void>;
}

export function useSequence(
  client: LauraClient | null,
  projectId: string | null,
): SequenceController {
  const [sequence, setSequence] = useState<Sequence | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!client || !projectId) {
      setSequence(null);
      return;
    }
    try {
      setError(null);
      setSequence(await client.getProjectSequence(projectId));
    } catch (e) {
      setError(String(e));
    }
  }, [client, projectId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const setScenes = useCallback(
    async (sceneIds: string[]) => {
      if (!client || !sequence) return;
      try {
        setSequence(await client.setSequenceScenes(sequence.timeline_id, sceneIds));
      } catch (e) {
        setError(String(e));
      }
    },
    [client, sequence],
  );

  return { sequence, error, setScenes, reload };
}
```

- [ ] **Step 4: Run — expect PASS.** Typecheck clean. **Step 5: Commit** the two files; `git commit -m "feat(zusammenfuegen): useSequence hook"`.

---

### Task 7: `AssembleView` (Bin + Sequence-track drag-reorder)

**Files:** Create `apps/desktop/src/components/AssembleView.tsx`; Test `apps/desktop/src/components/AssembleView.test.tsx`

This composes `useScenes` (the Bin — all scenes of the project's rough cut) + `useSequence` (the ordered track). Drag a scene from the Bin onto the track to append; drag within the track to reorder; both compute a new `scene_ids` order → `setScenes`. Structural preview: clicking a track block calls `onSeek`/selects (5a); the real concatenating player is 5b.

- [ ] **Step 1: Failing test** (assert the order-change → PUT, the core logic; keep DnD minimal by exposing buttons the test can click, OR test the reorder handler directly):

```typescript
// apps/desktop/src/components/AssembleView.test.tsx
import { fireEvent, render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type LauraClient, type Scene, type Sequence } from "../api";
import { AssembleView } from "./AssembleView";

const scenes: Scene[] = [
  { id: "s1", project_id: "p", source_timeline_id: "rc", name: "Szene 1", order_index: 0,
    seq_in_frame: 0, seq_out_frame_exclusive: 30 },
  { id: "s2", project_id: "p", source_timeline_id: "rc", name: "Szene 2", order_index: 1,
    seq_in_frame: 30, seq_out_frame_exclusive: 60 }];
const seq: Sequence = { timeline_id: "seq", project_id: "p",
  items: [{ id: "i1", scene_id: "s1", scene_name: "Szene 1", order_index: 0 }] };

function client(over: Partial<LauraClient>): LauraClient {
  return { listScenes: vi.fn().mockResolvedValue(scenes),
    getProjectSequence: vi.fn().mockResolvedValue(seq),
    setSequenceScenes: vi.fn().mockResolvedValue(seq), ...over } as unknown as LauraClient;
}

describe("AssembleView", () => {
  it("adds a bin scene to the sequence (PUT with appended id)", async () => {
    const c = client({});
    const { getByTitle } = render(
      <AssembleView client={c} projectId="p" roughCutId="rc" onSeekScene={vi.fn()} />);
    await waitFor(() => expect(c.getProjectSequence).toHaveBeenCalledWith("p"));
    await waitFor(() => expect(c.listScenes).toHaveBeenCalledWith("rc"));
    fireEvent.click(getByTitle("Szene 2 zur Sequenz hinzufügen"));
    await waitFor(() => expect(c.setSequenceScenes).toHaveBeenCalledWith("seq", ["s1", "s2"]));
  });
});
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement `AssembleView`** — `apps/desktop/src/components/AssembleView.tsx`. Contract:
  - Props `{ client: LauraClient; projectId: string | null; roughCutId: string | null; onSeekScene: (sceneId: string) => void }`.
  - `const { scenes } = useScenes(client, roughCutId)` (Bin); `const { sequence, setScenes } = useSequence(client, projectId)` (track).
  - **Bin:** all `scenes` as cards; each card has an **add button** `title={`${scene.name} zur Sequenz hinzufügen`}` → `setScenes([...currentIds, scene.id])` where `currentIds = sequence.items.map(i => i.scene_id)`.
  - **Track:** `sequence.items` in order as blocks; **reorder** via HTML5 `draggable` (compute the new id array on drop → `setScenes(newIds)`); a **remove** button per block → `setScenes(ids without that one)`. Clicking a block → `onSeekScene(scene_id)` (structural preview).
  - Empty states per spec §9.

Representative skeleton (adapt; keep the asserted add-button behaviour):

```typescript
import { type ReactElement } from "react";

import { type LauraClient } from "../api";
import { useScenes } from "../hooks/useScenes";
import { useSequence } from "../hooks/useSequence";

export function AssembleView({
  client, projectId, roughCutId, onSeekScene,
}: {
  client: LauraClient;
  projectId: string | null;
  roughCutId: string | null;
  onSeekScene: (sceneId: string) => void;
}): ReactElement {
  const { scenes } = useScenes(client, roughCutId);
  const { sequence, setScenes } = useSequence(client, projectId);
  const ids = (sequence?.items ?? []).map((i) => i.scene_id);

  const reorder = (from: number, to: number): void => {
    const next = [...ids];
    const [m] = next.splice(from, 1);
    next.splice(to, 0, m);
    void setScenes(next);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 p-3">
      <div className="text-xs text-slate-400">Szenen-Bin</div>
      <div className="flex gap-2 overflow-x-auto">
        {scenes.map((s) => (
          <div key={s.id} className="w-40 shrink-0 rounded border border-edge p-2 text-xs">
            <div className="truncate text-slate-200">{s.name}</div>
            <button type="button" title={`${s.name} zur Sequenz hinzufügen`}
              onClick={() => void setScenes([...ids, s.id])}
              className="mt-1 rounded bg-sky-600 px-2 py-0.5 text-white">+ Sequenz</button>
          </div>
        ))}
      </div>
      <div className="mt-2 text-xs text-slate-400">Sequenz</div>
      <div className="flex gap-2 overflow-x-auto">
        {(sequence?.items ?? []).map((it, i) => (
          <div key={it.id} draggable
            onDragStart={(e) => e.dataTransfer.setData("text/plain", String(i))}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => reorder(Number(e.dataTransfer.getData("text/plain")), i)}
            className="w-40 shrink-0 rounded border border-edge p-2 text-xs">
            <button type="button" onClick={() => onSeekScene(it.scene_id)}
              className="block w-full truncate text-left text-slate-200">{it.scene_name}</button>
            <button type="button" onClick={() => void setScenes(ids.filter((_, j) => j !== i))}
              className="mt-1 rounded bg-slate-700 px-2 py-0.5 text-slate-200">entfernen</button>
          </div>
        ))}
        {(sequence?.items ?? []).length === 0 && (
          <div className="text-xs text-slate-600">Szenen aus dem Bin hinzufügen.</div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run — expect PASS.** Full suite + typecheck green. **Step 5: Commit** the two files; `git commit -m "feat(zusammenfuegen): AssembleView (bin + sequence reorder)"`.

---

### Task 8: Wire `AssembleView` into `App.tsx` (LAST — collision-sensitive)

**Files:** Modify `apps/desktop/src/App.tsx`.

**Precondition:** `git status --short apps/desktop/src/App.tsx` clean; else STOP/hand to user.

- [ ] **Step 1: Import** `import { AssembleView } from "./components/AssembleView";` (alphabetical).
- [ ] **Step 2: Replace the `assemble` branch.** The current block is `{stage === "assemble" && (` followed by the shared `<>` layout. Change it to render `AssembleView` instead:

```tsx
{stage === "assemble" && client && (
  <AssembleView
    client={client}
    projectId={selectedProjectId}
    roughCutId={roughCut?.id ?? null}
    onSeekScene={() => undefined}
  />
)}
```

> This removes the last stage from the shared 4-zone layout. If the shared layout block (`<main>…</main>` + TimelineBar + TranscriptBar) was ONLY reachable via `assemble` now, delete that dead block too; if it is still used by another path, leave it. Verify by checking which `stage ===` guards remain on it. `onSeekScene` can stay a no-op in 5a (structural seek is refined in 5b); wire it to `seekToFrame`/scene lookup later if trivial.

- [ ] **Step 3: Verify** `npm --prefix apps/desktop run typecheck` + `npm --prefix apps/desktop test` — green.
- [ ] **Step 4: Commit** `git add apps/desktop/src/App.tsx`; `git commit -m "feat(zusammenfuegen): wire AssembleView into the assemble stage"`.

---

## Final verification (5a)
```
uv run --directory services/local-api pytest tests/test_sequences_migration.py tests/test_flatten_sequence.py tests/test_sequence_render.py tests/test_sequences_api.py -q
uv run --directory services/local-api pytest tests/test_render_job.py tests/test_otio_sync.py tests/test_exports_api.py tests/test_scenes_api.py -q   # no regression
npm --prefix apps/desktop run typecheck && npm --prefix apps/desktop test
```
Manual: Rough Cut → Feinschnitt → Zusammenfügen → Szenen in die Sequenz ziehen/umordnen → Export rendert die Gesamtsequenz.

## Spec coverage (5a)
| Spec § | Task |
|---|---|
| §3 Migration | 1 |
| §4.1 flatten_sequence + repos | 2 |
| §4.2 resolve_clip_rows (OTIO+render) | 3 |
| §4.3 Assemble-API | 4 |
| §5 Frontend (api/hook/AssembleView) | 5,6,7 |
| §5 App.tsx | 8 |

## Deferred to 5b (separate plan)
The real concatenating `SequencePlayer` (multi-asset playback of `getSequenceFlattened`) that replaces the structural preview. 5a delivers arrange + export.
