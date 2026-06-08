# Pipeline Export Stage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Export stage — render a timeline to MP4 (new backend job) and surface a gallery of finished exports (interchange + MP4) with per-card progress, gallery-style.

**Architecture:** A new `export.render` job renders a timeline's clips to a single MP4 via ffmpeg (concat of clip segments from the rough_cut timeline), with throttled progress on the job (reusing the `progress_json` + import-status pattern). A new `ExportView` lists finished exports as `MediaCard`s with a format picker; interchange exports (EDL/OTIO/FCPXML/SRT) already exist and are listed alongside.

**Tech Stack:** Python 3.11 (FastAPI, ffmpeg subprocess, the job runner), pytest; React 18 + TS strict, Tailwind, vitest.

**Working dir:** Laura. Backend: `services/local-api` (`uv run --no-sync`). Frontend: `apps/desktop` (`npm --prefix apps/desktop`). Branch: a fresh `feat/pipeline-export` off the current pipeline branch. Git hygiene: commit only listed files; nothing under unrelated paths. If `No space left on device`, STOP and report BLOCKED.

**Spec:** [`docs/superpowers/specs/2026-06-03-editorial-pipeline-architecture-design.md`](../specs/2026-06-03-editorial-pipeline-architecture-design.md)

**Reuses (existing):** the job runner + `progress_json` + import-status pattern; `ffmpeg.run_ffmpeg`; timelines + clips + OTIO; interchange exporters (`/timelines/{id}/exports`); `MediaCard`, `ImportProgress`, `useImportStatus`.

**Decision (recorded):** Stages 3–5 reuse the existing Scene-Workbench editor; this plan covers only Stage 6 (Export). The nested scenes-as-sub-timelines model is deferred to a later enhancement spec.

---

## File Structure
- Modify: `services/local-api/src/laura/db/migrations/` — new `NNNN_exports.sql` (an `exports` table).
- Create: `services/local-api/src/laura/render/mp4.py` — `render_timeline_mp4(...)` (ffmpeg concat).
- Modify: `services/local-api/src/laura/ingest/handlers.py` (or a new `render/handlers.py`) — `handle_render` job.
- Modify: `services/local-api/src/laura/jobs/queues.py` — route `export.render`.
- Modify: `services/local-api/src/laura/db/repos.py` — `create_export`, `list_exports`, `set_export_done`.
- Modify: `services/local-api/src/laura/api/timelines.py` (or `exports.py`) — `POST /timelines/{id}/render`, `GET /projects/{id}/exports`.
- Modify: `services/local-api/src/laura/api/models.py` — `RenderRequest`, `ExportOut`.
- Create: `apps/desktop/src/components/ExportView.tsx` (+ test).
- Modify: `apps/desktop/src/api.ts` — `renderTimeline`, `listExports`, `Export` type.

---

## Task 1: `exports` table + repo helpers

**Files:** migration `NNNN_exports.sql`, `repos.py`, test `tests/test_exports.py`.

- [ ] **Step 1: Discover next migration number**
Run from `services/local-api`: `ls src/laura/db/migrations/` → use the next free `NNNN`.

- [ ] **Step 2: Migration** `NNNN_exports.sql`:
```sql
CREATE TABLE exports (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    timeline_id   TEXT,
    format        TEXT NOT NULL,        -- mp4 | edl | otio | fcpxml | srt
    status        TEXT NOT NULL DEFAULT 'rendering',  -- rendering | ready | error
    path          TEXT,
    size_bytes    INTEGER,
    error         TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX idx_exports_project ON exports(project_id);
```

- [ ] **Step 3: Write the failing test** — `tests/test_exports.py`:
```python
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase


def _db(tmp_path):
    db = SqliteDatabase(Settings(workspace_root=tmp_path).db_path); db.migrate(); return db


def test_create_list_and_finish_export(tmp_path):
    db = _db(tmp_path)
    e = repos.create_export(db, project_id="p1", timeline_id="t1", format="mp4")
    assert e["status"] == "rendering"
    repos.set_export_done(db, e["id"], path="/x/out.mp4", size_bytes=1234)
    rows = repos.list_exports(db, "p1")
    assert len(rows) == 1 and rows[0]["status"] == "ready" and rows[0]["size_bytes"] == 1234
```

- [ ] **Step 4: Run → fail.** `uv run --no-sync pytest tests/test_exports.py -v`.

- [ ] **Step 5: Implement repo helpers** in `repos.py`:
```python
def create_export(db: Database, *, project_id: str, timeline_id: str | None, format: str) -> dict[str, Any]:
    eid = new_id(); now = utcnow_iso()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO exports (id, project_id, timeline_id, format, status, created_at) "
            "VALUES (?, ?, ?, ?, 'rendering', ?)", (eid, project_id, timeline_id, format, now))
    row = get_export(db, eid); assert row is not None; return row

def get_export(db: Database, export_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        r = conn.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone()
        return dict(r) if r else None

def list_exports(db: Database, project_id: str) -> list[dict[str, Any]]:
    with db.connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM exports WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall()]

def set_export_done(db: Database, export_id: str, *, path: str, size_bytes: int) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE exports SET status='ready', path=?, size_bytes=? WHERE id=?",
                     (path, size_bytes, export_id))

def set_export_error(db: Database, export_id: str, error: str) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE exports SET status='error', error=? WHERE id=?", (error, export_id))
```
(Confirm `new_id`, `utcnow_iso`, `Database`, `Any` are imported in repos.py.)

- [ ] **Step 6: Run → pass; mypy; commit.**
```
uv run --no-sync pytest tests/test_exports.py -v
uv run --no-sync mypy src/laura/db/repos.py
git add services/local-api/src/laura/db/migrations/*_exports.sql services/local-api/src/laura/db/repos.py services/local-api/tests/test_exports.py
git commit -m "$(printf 'feat(db): exports table + repo helpers\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 2: MP4 render (ffmpeg concat of timeline clips)

**Files:** `services/local-api/src/laura/render/mp4.py`, test `tests/test_render_mp4.py` (real ffmpeg, skip if absent).

- [ ] **Step 1: Write the failing test**
```python
import os, shutil
from pathlib import Path
import pytest
from laura.ingest.ffmpeg import run_ffmpeg
from laura.render.mp4 import render_clips_mp4

pytestmark = pytest.mark.skipif(shutil.which(os.environ.get("LAURA_FFMPEG","ffmpeg")) is None, reason="ffmpeg")

def _clip(tmp_path, name, secs):
    p = tmp_path / name
    run_ffmpeg(["-f","lavfi","-i",f"testsrc=duration={secs}:size=320x240:rate=30","-c:v","libx264","-pix_fmt","yuv420p",str(p)])
    return p

def test_render_concats_clips(tmp_path):
    a = _clip(tmp_path, "a.mp4", 1); b = _clip(tmp_path, "b.mp4", 1)
    out = tmp_path / "seq.mp4"
    render_clips_mp4([(a,0,30),(b,0,30)], out, rate_num=30, rate_den=1)  # (src, in_frame, out_excl)
    assert out.exists() and out.stat().st_size > 0
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** `render/mp4.py`:
```python
"""Render a list of timeline clip segments into one MP4 via ffmpeg (trim + concat)."""
from __future__ import annotations
from pathlib import Path
from ..ingest.ffmpeg import run_ffmpeg

def render_clips_mp4(clips: list[tuple[Path, int, int]], dest: Path, *, rate_num: int, rate_den: int) -> None:
    """clips = [(source, in_frame, out_frame_exclusive)]. Trims each by frame range and
    concatenates them in order with the filter_complex concat filter (re-encode → safe across
    mixed sources)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fps = rate_num / rate_den
    inputs: list[str] = []
    filt: list[str] = []
    for i, (src, fin, fout) in enumerate(clips):
        ss = fin / fps
        to = fout / fps
        inputs += ["-i", str(src)]
        filt.append(f"[{i}:v]trim=start={ss:.4f}:end={to:.4f},setpts=PTS-STARTPTS[v{i}]")
    concat_in = "".join(f"[v{i}]" for i in range(len(clips)))
    filter_complex = ";".join(filt) + f";{concat_in}concat=n={len(clips)}:v=1:a=0[out]"
    run_ffmpeg([*inputs, "-filter_complex", filter_complex, "-map", "[out]",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", f"{rate_num}/{rate_den}", str(dest)])
```
(v1 = video-only concat; audio is a follow-up. Re-encode keeps it robust across proxy/original mixes.)

- [ ] **Step 4: Run → pass; mypy; commit.**
```
uv run --no-sync pytest tests/test_render_mp4.py -v
uv run --no-sync mypy src/laura/render/mp4.py
git add services/local-api/src/laura/render/mp4.py services/local-api/tests/test_render_mp4.py
git commit -m "$(printf 'feat(render): ffmpeg trim+concat timeline -> mp4\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 3: `export.render` job + queue

**Files:** `services/local-api/src/laura/render/handlers.py`, `jobs/queues.py`, test in `tests/test_exports.py`.

- [ ] **Step 1: Write the failing integration test** (real ffmpeg; builds a tiny project+timeline, enqueues render, drains, asserts export ready + file exists). Mirror `tests/test_fetch.py`'s drain pattern; read a timeline's clips via `repos.list_timeline_clips`, map to `(source_path, src_in_frame, src_out_frame_exclusive)`, render. Assert `repos.get_export(...)["status"] == "ready"`.

- [ ] **Step 2: Implement** `render/handlers.py` `handle_render(ctx)`:
```python
def handle_render(ctx: JobContext) -> dict[str, Any]:
    export_id = ctx.payload["export_id"]
    exp = repos.get_export(ctx.db, export_id)
    if exp is None: raise ValueError(f"export not found: {export_id}")
    tl = repos.get_timeline(ctx.db, exp["timeline_id"])
    if tl is None: raise ValueError("timeline not found")
    project = repos.get_project(ctx.db, exp["project_id"])
    clips_rows = repos.list_timeline_clips(ctx.db, exp["timeline_id"])
    clips: list[tuple[Path, int, int]] = []
    for c in clips_rows:
        a = repos.get_asset(ctx.db, c["asset_id"]); assert a is not None
        clips.append((Path(a["source_path"]), c["src_in_frame"], c["src_out_frame_exclusive"]))
    if not clips:
        repos.set_export_error(ctx.db, export_id, "timeline has no clips"); raise ValueError("no clips")
    dest = Path(project["workspace_root"]) / "exports" / f"{export_id}.mp4"
    try:
        render_clips_mp4(clips, dest, rate_num=project["sequence_rate_num"], rate_den=project["sequence_rate_den"])
    except FFmpegError as e:
        repos.set_export_error(ctx.db, export_id, str(e)[-500:]); raise
    repos.set_export_done(ctx.db, export_id, path=str(dest), size_bytes=os.path.getsize(dest))
    return {"export_id": export_id, "path": str(dest)}
```
Register `registry["export.render"] = handle_render`; add `"export.render": QUEUE_EXPORT` to `_STAGE_QUEUE` (QUEUE_EXPORT exists).

- [ ] **Step 3: Run → pass; mypy/ruff; commit** (files: handlers, queues, test).

---

## Task 4: Render + exports API

**Files:** `api/timelines.py` (or new `api/exports.py`), `api/models.py`, test `tests/test_exports_api.py`.

- [ ] **Step 1: Failing API test** — `POST /timelines/{id}/render {format:"mp4"}` → 202 + export_id; `GET /projects/{id}/exports` lists it; status starts `rendering`. (Use TestClient + the existing `_make_project` style.)

- [ ] **Step 2: Models** (`models.py`):
```python
class RenderRequest(BaseModel):
    format: str = "mp4"

class ExportOut(BaseModel):
    id: str
    project_id: str
    timeline_id: str | None
    format: str
    status: str
    path: str | None = None
    size_bytes: int | None = None
    error: str | None = None
```

- [ ] **Step 3: Routes** (`api/timelines.py`):
```python
@router.post("/timelines/{timeline_id}/render", status_code=status.HTTP_202_ACCEPTED)
def render_timeline(timeline_id: str, body: RenderRequest, request: Request) -> dict[str, str]:
    db = _db(request)
    tl = repos.get_timeline(db, timeline_id)
    if tl is None: raise HTTPException(404, "timeline not found")
    exp = repos.create_export(db, project_id=tl["project_id"], timeline_id=timeline_id, format=body.format)
    job_id = enqueue(db, queue="export", kind="export.render",
                     payload={"export_id": exp["id"]}, idempotency_key=f"render:{exp['id']}")
    return {"export_id": exp["id"], "job_id": job_id}

@router.get("/projects/{project_id}/exports", response_model=list[ExportOut])
def list_project_exports(project_id: str, request: Request) -> list[ExportOut]:
    db = _db(request)
    return [ExportOut(**e) for e in repos.list_exports(db, project_id)]
```

- [ ] **Step 4: Run → pass; mypy/ruff; commit.**

---

## Task 5: `ExportView` (frontend)

**Files:** `apps/desktop/src/api.ts`, `apps/desktop/src/components/ExportView.tsx` (+ test).

- [ ] **Step 1: API client** — add to `api.ts`:
```ts
export interface Export {
  id: string; project_id: string; timeline_id: string | null;
  format: string; status: "rendering" | "ready" | "error";
  path: string | null; size_bytes: number | null; error: string | null;
}
```
and methods `renderTimeline(timelineId, format)` (POST) + `listExports(projectId)` (GET). Add a vitest case (fetch mocked) mirroring the existing `api.test.ts` style.

- [ ] **Step 2: `ExportView.tsx`** — header: format `<select>` (mp4/edl/otio/fcpxml/srt) + "Exportieren" (calls `renderTimeline`); body: `MediaCard` grid of `listExports` results (poll while any `rendering`), `rendering` cards show an indeterminate bar, `ready` cards show size + an open/download affordance, `error` shows the reason. Reuse `MediaCard`. Write a vitest test: given a `ready` export, renders its name/format; given `rendering`, shows a progress hint.

- [ ] **Step 3: Typecheck + test + commit.**

---

## Task 6: Wire Export into the nav

**Files:** `apps/desktop/src/App.tsx` (the stage switch from the Foundation plan).

- [ ] **Step 1:** In the App stage switch (Foundation Task 6), replace the `export` stub with `<ExportView client={client} projectId={selectedProjectId} timelineId={roughCut?.id ?? null} />`. Adapt prop names to the actual App state. Typecheck + test + commit.

---

## Notes
- **Stages 3–5** are the existing Scene-Workbench editor, routed by the nav (Foundation plan). This plan adds only Stage 6.
- **Audio in MP4 render** and **scene-aware (nested) rendering** are follow-ups.
- The render job reuses the export queue + job-runner retry; progress is coarse (rendering→ready) in v1 — byte/percent render progress is a follow-up (ffmpeg `-progress` parsing, like the aria2 parser).
