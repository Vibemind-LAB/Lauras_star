# Feinschnitt Undo/Redo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Multi-step Undo/Redo across every Feinschnitt editor action, via a durable backend checkpoint stack.

**Architecture:** Before each timeline mutation a context manager snapshots the timeline's full editorial DB state (clips, scenes, audio-clips, transition-reviews) onto an `undo` stack and clears the `redo` stack. Undo/Redo restore the neighbouring snapshot in one atomic transaction (full-column round-trip), then regenerate the `otio_json` cache as a separate idempotent step. In-flight AI jobs (VO/Lipsync/Reenact) are cancel-requested on undo and the handlers abort before their final write, so undo stays immediate (seamless).

**Tech Stack:** Python 3.11 (uv, ruff, mypy, pytest), SQLite/Postgres via the repo's `Database` wrapper, FastAPI; TypeScript strict + React + Tailwind (Electron renderer), vitest.

## Global Constraints

- Python 3.11; `uv run pytest` green, `uv run ruff check`/`format` clean, `uv run mypy` clean. No `print` — use the project logger.
- TypeScript `strict`, **never** `any` (→ `unknown` + narrow). Tailwind tokens only — `accent`/`surface`/`content`, **no** raw `sky-*`. No `console.log` (project logger).
- **Conventional Commits.** Each task commits with an **explicit** `git add <paths>` — **never** `git add -A`/`-am`. The working tree carries foreign uncommitted changes (`services/local-api/uv.lock`) and untracked `.superpowers/`; they must **never** enter a commit.
- **Do not touch** `services/ai-runtimes/`, `services/local-api/src/laura/ai/runtime_*`, `services/local-api/src/laura/api/ai_runtimes.py` (a parallel codex subtree).
- Invariants: integer frames relative to the sequence; ranges end-exclusive (`seq_out_frame_exclusive`); audio in samples (`audio_offset_samples`); **`timeline_clips` is the source of truth**, `timelines.otio_json` is a regenerated cache (`rebuild_otio`).
- New backend code lives under `services/local-api/src/laura/`; tests under `services/local-api/tests/`. Run backend commands from `services/local-api`.

---

### Task 1: Verify the single-timeline assumption (read-only spike)

The whole design assumes the Feinschnitt edits exactly **one** rough-cut `timeline_id` and never a per-scene `scene_timeline_id` sub-timeline (otherwise a single-timeline snapshot is the wrong aggregate). Confirm before building.

**Files:** none modified (investigation only).

- [ ] **Step 1: Trace the mutation paths.** Read `apps/desktop/src/hooks/useRoughCutTranscript.ts` and confirm `deleteRange`/`cutAt`/`replaceSpanText` all call APIs with `roughCutId` (never a scene's `scene_timeline_id`). Read `apps/desktop/src/components/FineCutView.tsx` and confirm the scene list only **navigates** (seeks), never `openScene`/materialise. Grep the backend for `scene_timeline_id`:

Run: `cd services/local-api && rg -n "scene_timeline_id" src/laura`
Expected: writes to `scene_timeline_id` only in the legacy `open_scene`/materialise path (`api/scenes.py` `open_scene`, `repos.set_scene_timeline`), **not** in `delete_words`/`cut_at_frame`/`voiceover`/audio/overlay/scene-music routes.

- [ ] **Step 2: Record the verdict** in `.superpowers/sdd/progress.md` (one line): either "CONFIRMED single-timeline" or "BLOCKED: <path that edits a sub-timeline>". If BLOCKED, stop and escalate — the snapshot scope (Task 3/4) must widen first.

- [ ] **Step 3: No commit** (no file changes). Proceed to Task 2 only on CONFIRMED.

---

### Task 2: Migration 0029 — `timeline_history` table

**Files:**
- Create: `services/local-api/src/laura/db/migrations/0029_timeline_history.sql`
- Test: `services/local-api/tests/test_migration_0029_timeline_history.py`

**Interfaces:**
- Produces: table `timeline_history(id, timeline_id, seq_no, stack, label, payload_json, created_at)`; `schema_version` → 29.

- [ ] **Step 1: Write the failing test**

```python
# services/local-api/tests/test_migration_0029_timeline_history.py
from laura.db.sqlite import SqliteDatabase
from laura.db import base


def test_timeline_history_table_and_schema_version(tmp_path):
    db = SqliteDatabase(str(tmp_path / "t.db"))
    base.migrate(db)  # applies all migrations
    with db.connection() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(timeline_history)").fetchall()}
        ver = conn.execute("SELECT MAX(version) AS v FROM schema_meta").fetchone()["v"]
    assert cols == {"id", "timeline_id", "seq_no", "stack", "label", "payload_json", "created_at"}
    assert ver >= 29
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/local-api && uv run pytest tests/test_migration_0029_timeline_history.py -v`
Expected: FAIL (table missing / version < 29). *If the migrate/schema_meta API differs, adjust the test to the project's real migration entrypoint discovered in `db/base.py` — keep the column + version assertions.*

- [ ] **Step 3: Write the migration**

```sql
-- services/local-api/src/laura/db/migrations/0029_timeline_history.sql
-- Per-timeline undo/redo checkpoint stacks. payload_json = full editorial snapshot
-- (clips/scenes/audio_clips/transitions). ON DELETE CASCADE mirrors transition_reviews (0024).
CREATE TABLE timeline_history (
    id            TEXT PRIMARY KEY,
    timeline_id   TEXT NOT NULL REFERENCES timelines(id) ON DELETE CASCADE,
    seq_no        INTEGER NOT NULL,
    stack         TEXT NOT NULL,
    label         TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    CHECK (stack IN ('undo','redo'))
);
CREATE INDEX idx_timeline_history_lookup ON timeline_history(timeline_id, stack, seq_no);
```

Confirm the migration runner picks up `0029_*.sql` and bumps `schema_version` to 29 (follow exactly how `0028_asset_policies.sql` is registered — same mechanism).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/local-api && uv run pytest tests/test_migration_0029_timeline_history.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/db/migrations/0029_timeline_history.sql services/local-api/tests/test_migration_0029_timeline_history.py
git commit -m "feat(history): timeline_history migration 0029"
```

---

### Task 3: Snapshot capture + column-completeness guard

**Files:**
- Modify: `services/local-api/src/laura/db/repos.py` (add `capture_timeline_snapshot`)
- Test: `services/local-api/tests/test_timeline_snapshot_capture.py`

**Interfaces:**
- Consumes: existing `list_timeline_clips(db, timeline_id)`, `list_scenes(db, source_timeline_id)`, `list_timeline_audio_clips(db, timeline_id)`, `list_transition_reviews(db, timeline_id)` — all return `list[dict]` from `SELECT *` (full columns).
- Produces: `capture_timeline_snapshot(db: Database, timeline_id: str) -> dict[str, list[dict[str, Any]]]` with keys `clips`, `scenes`, `audio_clips`, `transitions`. For the rough-cut, `scenes.source_timeline_id == timeline_id` (verified Task 1).

- [ ] **Step 1: Write the failing test**

```python
# services/local-api/tests/test_timeline_snapshot_capture.py
from laura.db import repos
from tests.helpers import make_db_with_rough_cut  # existing fixture-style helper; if absent, build inline


def test_capture_has_four_groups_and_full_columns(seeded_timeline):
    db, timeline_id = seeded_timeline
    snap = repos.capture_timeline_snapshot(db, timeline_id)
    assert set(snap) == {"clips", "scenes", "audio_clips", "transitions"}
    # Column-completeness: captured clip keys must equal the live table's columns.
    with db.connection() as conn:
        clip_cols = {r["name"] for r in conn.execute("PRAGMA table_info(timeline_clips)").fetchall()}
        scene_cols = {r["name"] for r in conn.execute("PRAGMA table_info(scenes)").fetchall()}
    assert set(snap["clips"][0]) == clip_cols          # catches a future ADD COLUMN not captured
    assert set(snap["scenes"][0]) == scene_cols
    # role/transition/linked columns must be present (the bug replace_timeline_clips has)
    assert {"role", "transition_after_kind", "transition_after_frames", "linked_audio_group"} <= clip_cols
```

Use an existing seeding helper if the suite has one (look in `tests/` for a rough-cut fixture). Otherwise seed: a project, asset, timeline, ≥1 clip via `repos.add_timeline_clip`, ≥1 scene via `repos.replace_scenes`, ≥1 audio clip via `repos.add_timeline_audio_clip`.

- [ ] **Step 2: Run to verify it fails**

Run: `cd services/local-api && uv run pytest tests/test_timeline_snapshot_capture.py -v`
Expected: FAIL (`capture_timeline_snapshot` undefined).

- [ ] **Step 3: Implement**

```python
# repos.py — near the other timeline read helpers
def capture_timeline_snapshot(db: Database, timeline_id: str) -> dict[str, list[dict[str, Any]]]:
    """Full editorial state of one rough-cut timeline (all columns, raw)."""
    return {
        "clips": list_timeline_clips(db, timeline_id),
        "scenes": list_scenes(db, timeline_id),  # scenes scoped by source_timeline_id == timeline_id
        "audio_clips": list_timeline_audio_clips(db, timeline_id),
        "transitions": list_transition_reviews(db, timeline_id),
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd services/local-api && uv run pytest tests/test_timeline_snapshot_capture.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/db/repos.py services/local-api/tests/test_timeline_snapshot_capture.py
git commit -m "feat(history): capture_timeline_snapshot (full-column, 4 groups)"
```

---

### Task 4: Atomic, full-column restore

**Files:**
- Modify: `services/local-api/src/laura/db/repos.py` (add `restore_timeline_snapshot` + `_restore_into`)
- Test: `services/local-api/tests/test_timeline_snapshot_restore.py`

**Interfaces:**
- Consumes: `Database.transaction(*, immediate=False)` → context manager yielding `conn` with `conn.execute(sql, params)`.
- Produces: `restore_timeline_snapshot(db: Database, timeline_id: str, snapshot: dict, *, conn: Any | None = None) -> None`. With `conn` given, runs in the caller's transaction (used by Task 8); otherwise opens its own `immediate=True` transaction. Restores all four groups byte-identically via dynamic full-column INSERTs.

- [ ] **Step 1: Write the failing tests**

```python
# services/local-api/tests/test_timeline_snapshot_restore.py
from laura.db import repos


def test_restore_roundtrip_is_byte_identical(seeded_timeline):
    db, tl = seeded_timeline
    before = repos.capture_timeline_snapshot(db, tl)
    # Mutate: drop a clip + change a scene's music + add an audio clip.
    repos.replace_timeline_clips(db, tl, [])          # wipe clips
    snap_after_wipe = repos.capture_timeline_snapshot(db, tl)
    assert snap_after_wipe["clips"] == []
    # Restore the original snapshot.
    repos.restore_timeline_snapshot(db, tl, before)
    after = repos.capture_timeline_snapshot(db, tl)
    assert after == before  # incl. role/transition_after_*/linked_audio_group/music_* columns


def test_restore_is_atomic_on_bad_row(seeded_timeline):
    db, tl = seeded_timeline
    good = repos.capture_timeline_snapshot(db, tl)
    bad = {**good, "audio_clips": [{"id": "x", "nonexistent_col": 1}]}  # will fail INSERT
    try:
        repos.restore_timeline_snapshot(db, tl, bad)
    except Exception:
        pass
    # Clips must be untouched (transaction rolled back) — no partial restore.
    assert repos.capture_timeline_snapshot(db, tl)["clips"] == good["clips"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd services/local-api && uv run pytest tests/test_timeline_snapshot_restore.py -v`
Expected: FAIL (`restore_timeline_snapshot` undefined).

- [ ] **Step 3: Implement**

```python
# repos.py
def _insert_rows(conn: Any, table: str, rows: list[dict[str, Any]]) -> None:
    # table + column names are internal (from our own SELECT *), never user input.
    for r in rows:
        cols = list(r.keys())
        collist = ", ".join(cols)
        placeholders = ", ".join("?" for _ in cols)
        conn.execute(
            f"INSERT INTO {table} ({collist}) VALUES ({placeholders})",  # noqa: S608 (trusted cols)
            tuple(r[c] for c in cols),
        )


def _restore_into(conn: Any, timeline_id: str, snapshot: dict[str, list[dict[str, Any]]]) -> None:
    conn.execute("DELETE FROM timeline_clips WHERE timeline_id=?", (timeline_id,))
    conn.execute("DELETE FROM timeline_audio_clips WHERE timeline_id=?", (timeline_id,))
    conn.execute("DELETE FROM transition_reviews WHERE timeline_id=?", (timeline_id,))
    conn.execute("DELETE FROM scenes WHERE source_timeline_id=?", (timeline_id,))
    _insert_rows(conn, "timeline_clips", snapshot["clips"])
    _insert_rows(conn, "timeline_audio_clips", snapshot["audio_clips"])
    _insert_rows(conn, "transition_reviews", snapshot["transitions"])
    _insert_rows(conn, "scenes", snapshot["scenes"])


def restore_timeline_snapshot(
    db: Database, timeline_id: str, snapshot: dict[str, list[dict[str, Any]]], *, conn: Any | None = None
) -> None:
    if conn is not None:
        _restore_into(conn, timeline_id, snapshot)
        return
    with db.transaction(immediate=True) as own:
        _restore_into(own, timeline_id, snapshot)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd services/local-api && uv run pytest tests/test_timeline_snapshot_restore.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/db/repos.py services/local-api/tests/test_timeline_snapshot_restore.py
git commit -m "feat(history): atomic full-column restore_timeline_snapshot"
```

---

### Task 5: History-stack repos (push / state / depth-cap)

**Files:**
- Modify: `services/local-api/src/laura/db/repos.py` (add stack helpers + `_UNDO_DEPTH = 50`)
- Test: `services/local-api/tests/test_timeline_history_stack.py`

**Interfaces:**
- Produces:
  - `push_undo_checkpoint(db, timeline_id, label: str) -> None` — captures live state, inserts an `undo` row (next `seq_no`), **clears the redo stack**, caps undo depth at 50.
  - `get_history_state(db, timeline_id) -> dict` → `{"can_undo": bool, "can_redo": bool, "undo_label": str|None, "redo_label": str|None}`.
  - (used by Task 8) `pop_top(conn, timeline_id, stack) -> dict|None` returning `{"id","seq_no","label","payload"}`; `push_row(conn, timeline_id, stack, label, snapshot) -> None`.
- Consumes: `capture_timeline_snapshot` (Task 3); an id/time helper — use the same pattern existing repos use (`uuid4().hex` and `utcnow_iso()`; confirm by reading a neighbouring repo writer).

- [ ] **Step 1: Write the failing tests**

```python
# services/local-api/tests/test_timeline_history_stack.py
from laura.db import repos


def test_push_sets_undo_and_clears_redo(seeded_timeline):
    db, tl = seeded_timeline
    repos.push_undo_checkpoint(db, tl, "Edit A")
    st = repos.get_history_state(db, tl)
    assert st["can_undo"] is True and st["undo_label"] == "Edit A" and st["can_redo"] is False


def test_depth_cap_keeps_newest_50(seeded_timeline):
    db, tl = seeded_timeline
    for i in range(55):
        repos.push_undo_checkpoint(db, tl, f"Edit {i}")
    with db.connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM timeline_history WHERE timeline_id=? AND stack='undo'", (tl,)
        ).fetchone()["c"]
    assert n == 50
    assert repos.get_history_state(db, tl)["undo_label"] == "Edit 54"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd services/local-api && uv run pytest tests/test_timeline_history_stack.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# repos.py
import json
from uuid import uuid4

_UNDO_DEPTH = 50


def push_row(conn: Any, timeline_id: str, stack: str, label: str, snapshot: dict[str, Any]) -> None:
    seq = conn.execute(
        "SELECT COALESCE(MAX(seq_no), 0) + 1 AS n FROM timeline_history WHERE timeline_id=?",
        (timeline_id,),
    ).fetchone()["n"]
    conn.execute(
        "INSERT INTO timeline_history (id, timeline_id, seq_no, stack, label, payload_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (uuid4().hex, timeline_id, seq, stack, label, json.dumps(snapshot), utcnow_iso()),
    )


def pop_top(conn: Any, timeline_id: str, stack: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, seq_no, label, payload_json FROM timeline_history "
        "WHERE timeline_id=? AND stack=? ORDER BY seq_no DESC LIMIT 1",
        (timeline_id, stack),
    ).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM timeline_history WHERE id=?", (row["id"],))
    return {"id": row["id"], "seq_no": row["seq_no"], "label": row["label"], "payload": json.loads(row["payload_json"])}


def push_undo_checkpoint(db: Database, timeline_id: str, label: str) -> None:
    snapshot = capture_timeline_snapshot(db, timeline_id)
    with db.transaction() as conn:
        push_row(conn, timeline_id, "undo", label, snapshot)
        conn.execute("DELETE FROM timeline_history WHERE timeline_id=? AND stack='redo'", (timeline_id,))
        cnt = conn.execute(
            "SELECT COUNT(*) AS c FROM timeline_history WHERE timeline_id=? AND stack='undo'", (timeline_id,)
        ).fetchone()["c"]
        if cnt > _UNDO_DEPTH:
            conn.execute(
                "DELETE FROM timeline_history WHERE id IN ("
                "SELECT id FROM timeline_history WHERE timeline_id=? AND stack='undo' "
                "ORDER BY seq_no ASC LIMIT ?)",
                (timeline_id, cnt - _UNDO_DEPTH),
            )


def get_history_state(db: Database, timeline_id: str) -> dict[str, Any]:
    with db.connection() as conn:
        u = conn.execute(
            "SELECT label FROM timeline_history WHERE timeline_id=? AND stack='undo' ORDER BY seq_no DESC LIMIT 1",
            (timeline_id,),
        ).fetchone()
        r = conn.execute(
            "SELECT label FROM timeline_history WHERE timeline_id=? AND stack='redo' ORDER BY seq_no DESC LIMIT 1",
            (timeline_id,),
        ).fetchone()
    return {
        "can_undo": u is not None,
        "can_redo": r is not None,
        "undo_label": u["label"] if u is not None else None,
        "redo_label": r["label"] if r is not None else None,
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd services/local-api && uv run pytest tests/test_timeline_history_stack.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/db/repos.py services/local-api/tests/test_timeline_history_stack.py
git commit -m "feat(history): undo/redo stack repos with depth cap"
```

---

### Task 6: `timeline_checkpoint` wrapper + wire every sync mutation route + coverage test

**Files:**
- Create: `services/local-api/src/laura/editing/history.py` (`timeline_checkpoint`)
- Modify: `services/local-api/src/laura/api/timelines.py` (apply_operation, set_clip_transition, set_timeline_clips), `api/scenes.py` (cut_at_frame, generate, split, merge, rename, set/clear music), `api/audio.py` (create/update/delete), `api/overlays.py` (add/remove)
- Test: `services/local-api/tests/test_checkpoint_coverage.py`, `tests/test_timeline_checkpoint.py`

**Interfaces:**
- Produces: `@contextmanager def timeline_checkpoint(db, timeline_id, label) -> Iterator[None]` — calls `repos.push_undo_checkpoint(db, timeline_id, label)` on enter, yields. (No post-yield work; the wrapped body performs the mutation.)
- Consumes: every wrapped route already has `db` + a `timeline_id` (for scene-music routes, resolve via `scene["source_timeline_id"]`).

- [ ] **Step 1: Write the wrapper test (failing)**

```python
# services/local-api/tests/test_timeline_checkpoint.py
from laura.editing.history import timeline_checkpoint
from laura.db import repos


def test_checkpoint_pushes_pre_edit_snapshot(seeded_timeline):
    db, tl = seeded_timeline
    before = repos.capture_timeline_snapshot(db, tl)
    with timeline_checkpoint(db, tl, "Wörter gelöscht"):
        repos.replace_timeline_clips(db, tl, [])  # the "edit"
    st = repos.get_history_state(db, tl)
    assert st["can_undo"] and st["undo_label"] == "Wörter gelöscht"
    # The pushed snapshot is the PRE-edit state.
    with db.connection() as conn:
        payload = conn.execute(
            "SELECT payload_json FROM timeline_history WHERE timeline_id=? ORDER BY seq_no DESC LIMIT 1", (tl,)
        ).fetchone()["payload_json"]
    import json
    assert json.loads(payload)["clips"] == before["clips"]
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/test_timeline_checkpoint.py -v` → FAIL.

- [ ] **Step 3: Implement the wrapper**

```python
# services/local-api/src/laura/editing/history.py
from __future__ import annotations
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from laura.db import repos


@contextmanager
def timeline_checkpoint(db: Any, timeline_id: str, label: str) -> Iterator[None]:
    """Snapshot the timeline's pre-edit editorial state, then run the mutation."""
    repos.push_undo_checkpoint(db, timeline_id, label)
    yield
```

- [ ] **Step 4: Wrap each sync mutation route.** In each handler, wrap the existing mutation body. Examples (apply the same shape to all rows of §4.3's coverage table):

```python
# api/timelines.py  apply_operation (~944): wrap the mutate+persist body
with timeline_checkpoint(db, timeline_id, _op_label(body.op)):
    ...  # existing _apply + replace_timeline_clips + update_timeline_otio

# api/timelines.py  set_clip_transition (~1029)
with timeline_checkpoint(db, timeline_id, "Übergang geändert"):
    ...

# api/scenes.py  cut_at_frame (~115)
with timeline_checkpoint(db, timeline_id, "Schnitt gesetzt"):
    ...

# api/audio.py  create/update/delete (~80/110/131)
with timeline_checkpoint(db, timeline_id, "Audio geändert"):
    ...

# api/overlays.py  add/remove (~24/89)
with timeline_checkpoint(db, timeline_id, "Overlay geändert"):
    ...

# api/scenes.py  set_scene_music / clear_scene_music (~246/260) — scene_id only:
scene = repos.get_scene(db, scene_id)            # 404 if None (mirror existing guard)
with timeline_checkpoint(db, scene["source_timeline_id"], "Musik geändert"):
    ...
```

Add a small `_op_label(op: str) -> str` map in `timelines.py` (e.g. `delete_words → "Wörter gelöscht"`, `split → "Clip geteilt"`, default `"Bearbeitet"`). Also wrap `set_timeline_clips` (PUT /clips) with label `"Clips ersetzt"` **only if** it is a user edit path — it is **also** the restore primitive's HTTP twin; the undo/redo orchestration (Task 8) calls `repos.restore_timeline_snapshot` directly (not this route), so wrapping the route is safe.

- [ ] **Step 5: Write the coverage test (the structural "Alles" guarantee)**

```python
# services/local-api/tests/test_checkpoint_coverage.py
import ast, pathlib

API = pathlib.Path("src/laura/api")
WRITE_FNS = {"replace_timeline_clips", "add_timeline_clip", "delete_timeline_clip",
             "replace_scenes", "update_scene_name", "set_scene_music", "clear_scene_music",
             "add_timeline_audio_clip", "update_timeline_audio_clip", "delete_timeline_audio_clip"}
# Intentionally outside the synchronous wrapper (covered by live-diff / are the restore primitive):
ALLOW_FILES = {"src/laura/ai/handlers.py"}  # async job writes (§6)


def test_every_api_timeline_write_is_in_a_checkpoint():
    offenders = []
    for path in API.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        # crude but effective: any handler function that calls a WRITE_FN must also mention timeline_checkpoint
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                body_src = ast.get_source_segment(src, node) or ""
                calls = {n.func.attr for n in ast.walk(node)
                         if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
                if calls & WRITE_FNS and "timeline_checkpoint" not in body_src:
                    offenders.append(f"{path}::{node.name}")
    assert offenders == [], f"timeline writes outside a checkpoint: {offenders}"
```

(Refine the heuristic if it flags a legitimate non-edit reader; keep it red for a genuinely unwrapped mutator.)

- [ ] **Step 6: Run tests** — `uv run pytest tests/test_timeline_checkpoint.py tests/test_checkpoint_coverage.py -v` → PASS. Then the existing API test suites for these routes must still pass: `uv run pytest tests/ -k "timeline or scene or audio or overlay" -q`.

- [ ] **Step 7: Commit**

```bash
git add services/local-api/src/laura/editing/history.py services/local-api/src/laura/api/timelines.py services/local-api/src/laura/api/scenes.py services/local-api/src/laura/api/audio.py services/local-api/src/laura/api/overlays.py services/local-api/tests/test_timeline_checkpoint.py services/local-api/tests/test_checkpoint_coverage.py
git commit -m "feat(history): checkpoint wrapper on all sync timeline mutations + coverage test"
```

---

### Task 7: Cooperative job-cancel (repos + AI handlers)

**Files:**
- Modify: `services/local-api/src/laura/db/repos.py` (`is_job_cancel_requested`, `request_timeline_jobs_cancel`)
- Modify: `services/local-api/src/laura/ai/handlers.py` (cancel check before the final write in `handle_voiceover`, `handle_lipsync`, `handle_reenact`)
- Test: `services/local-api/tests/test_job_cancel_cooperative.py`

**Interfaces:**
- Produces: `is_job_cancel_requested(db, job_id) -> bool`; `request_timeline_jobs_cancel(db, timeline_id) -> list[str]` (flags every non-terminal `ai.*` job whose `payload_json.timeline_id` matches; returns the ids).
- Consumes: existing `cancel_job(db, job_id)` (queued→cancelled, running→`cancel_requested=1`); `JobContext.job_id`/`.db` in handlers.

- [ ] **Step 1: Write the failing test**

```python
# services/local-api/tests/test_job_cancel_cooperative.py
from laura.db import repos


def test_request_cancel_flags_timeline_jobs(seeded_timeline, enqueue_ai_job):
    db, tl = seeded_timeline
    jid = enqueue_ai_job("ai.voiceover", {"timeline_id": tl, "text": "hi"})  # helper enqueues a queued job
    flagged = repos.request_timeline_jobs_cancel(db, tl)
    assert jid in flagged
    # queued job → cancel_job marks it cancelled (terminal)
    with db.connection() as conn:
        status = conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()["status"]
    assert status in {"canceled", "cancelled"}


def test_handle_voiceover_aborts_when_cancel_requested(monkeypatch, vo_job_ctx):
    # vo_job_ctx: a JobContext whose job row has cancel_requested=1, db seeded so the handler reaches its write.
    from laura.ai import handlers
    before = handlers.repos.list_timeline_audio_clips(vo_job_ctx.db, vo_job_ctx.payload["timeline_id"])
    handlers.handle_voiceover(vo_job_ctx)
    after = handlers.repos.list_timeline_audio_clips(vo_job_ctx.db, vo_job_ctx.payload["timeline_id"])
    assert after == before  # no clip appended after a cancel
```

*If a full `handle_voiceover` ctx is heavy to build, narrow the second test to assert the cancel-guard returns before `add_timeline_audio_clip` (e.g. monkeypatch `add_timeline_audio_clip` to raise, set `cancel_requested`, assert it is NOT called).*

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/test_job_cancel_cooperative.py -v` → FAIL.

- [ ] **Step 3: Implement repos**

```python
# repos.py
_NON_TERMINAL = ("queued", "leased", "running")
_AI_KINDS = ("ai.voiceover", "ai.lipsync", "ai.reenact")


def is_job_cancel_requested(db: Database, job_id: str) -> bool:
    with db.connection() as conn:
        row = conn.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
    return bool(row["cancel_requested"]) if row is not None else False


def request_timeline_jobs_cancel(db: Database, timeline_id: str) -> list[str]:
    placeholders = ",".join("?" for _ in _AI_KINDS)
    status_ph = ",".join("?" for _ in _NON_TERMINAL)
    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT id FROM jobs WHERE kind IN ({placeholders}) AND status IN ({status_ph}) "
            "AND json_extract(payload_json, '$.timeline_id') = ?",
            (*_AI_KINDS, *_NON_TERMINAL, timeline_id),
        ).fetchall()
    ids = [r["id"] for r in rows]
    for jid in ids:
        cancel_job(db, jid)
    return ids
```

- [ ] **Step 4: Insert the cancel guard in each AI handler** — immediately before the final timeline write:

```python
# ai/handlers.py  handle_voiceover — before delete_timeline_audio_clips_overlapping/add_timeline_audio_clip (~553)
if repos.is_job_cancel_requested(ctx.db, ctx.job_id):
    return {"status": "cancelled", "reason": "undo"}
# handle_lipsync — before add_timeline_clip (~811): same guard, timeline=timeline["id"]
# handle_reenact — before add_timeline_clip (~280): same guard, timeline=tl["id"]
```

Match each handler's existing return type (they return `dict[str, Any]`). Keep the guard a no-write early-return.

- [ ] **Step 5: Run to verify it passes** — `uv run pytest tests/test_job_cancel_cooperative.py -v` → PASS. Plus existing AI handler tests: `uv run pytest tests/ -k "voiceover or lipsync or reenact" -q`.

- [ ] **Step 6: Commit**

```bash
git add services/local-api/src/laura/db/repos.py services/local-api/src/laura/ai/handlers.py services/local-api/tests/test_job_cancel_cooperative.py
git commit -m "feat(history): cooperative job-cancel + AI handler abort-before-write"
```

---

### Task 8: Undo/Redo orchestration (`perform_undo` / `perform_redo`)

**Files:**
- Modify: `services/local-api/src/laura/editing/history.py`
- Test: `services/local-api/tests/test_perform_undo_redo.py`

**Interfaces:**
- Produces: `perform_undo(db, timeline_id) -> tuple[list[dict], list[dict]]` and `perform_redo(...)` returning `(clips, scenes)`; `class HistoryEmpty(Exception)`.
- Behaviour: cancel in-flight jobs → in ONE transaction {push live→other stack, pop top of this stack, restore its snapshot} → `rebuild_otio` (soft-fail) → return fresh `(clips, scenes)`.
- Consumes: Task 4 `restore_timeline_snapshot(..., conn=...)`, Task 5 `push_row`/`pop_top`, Task 7 `request_timeline_jobs_cancel`, `otio_sync.rebuild_otio`.

- [ ] **Step 1: Write the failing tests**

```python
# services/local-api/tests/test_perform_undo_redo.py
import pytest
from laura.editing import history
from laura.db import repos


def test_undo_then_redo_round_trips(seeded_timeline):
    db, tl = seeded_timeline
    a = repos.capture_timeline_snapshot(db, tl)
    with history.timeline_checkpoint(db, tl, "Edit"):
        repos.replace_timeline_clips(db, tl, [])     # state B (no clips)
    b = repos.capture_timeline_snapshot(db, tl)
    history.perform_undo(db, tl)
    assert repos.capture_timeline_snapshot(db, tl)["clips"] == a["clips"]
    history.perform_redo(db, tl)
    assert repos.capture_timeline_snapshot(db, tl)["clips"] == b["clips"]


def test_undo_on_empty_stack_raises(seeded_timeline):
    db, tl = seeded_timeline
    with pytest.raises(history.HistoryEmpty):
        history.perform_undo(db, tl)


def test_new_edit_clears_redo(seeded_timeline):
    db, tl = seeded_timeline
    with history.timeline_checkpoint(db, tl, "E1"):
        repos.replace_timeline_clips(db, tl, [])
    history.perform_undo(db, tl)
    assert repos.get_history_state(db, tl)["can_redo"] is True
    with history.timeline_checkpoint(db, tl, "E2"):
        repos.replace_timeline_clips(db, tl, [])
    assert repos.get_history_state(db, tl)["can_redo"] is False
```

- [ ] **Step 2: Run to verify it fails** — FAIL.

- [ ] **Step 3: Implement**

```python
# editing/history.py
import logging
from laura.editing.otio_sync import rebuild_otio

logger = logging.getLogger(__name__)


class HistoryEmpty(Exception):
    pass


def _perform(db: Any, timeline_id: str, from_stack: str, to_stack: str) -> tuple[list, list]:
    repos.request_timeline_jobs_cancel(db, timeline_id)           # seamless: cancel in-flight (§6)
    live = repos.capture_timeline_snapshot(db, timeline_id)
    with db.transaction(immediate=True) as conn:
        top = repos.pop_top(conn, timeline_id, from_stack)
        if top is None:
            raise HistoryEmpty()
        repos.push_row(conn, timeline_id, to_stack, top["label"], live)
        repos.restore_timeline_snapshot(db, timeline_id, top["payload"], conn=conn)
    try:
        rebuild_otio(db, timeline_id)
    except Exception as exc:  # rows restored; otio cache stale-but-regenerable
        logger.warning("rebuild_otio after %s failed (otio cache stale): %s", from_stack, exc)
    return repos.list_timeline_clips(db, timeline_id), repos.list_scenes(db, timeline_id)


def perform_undo(db: Any, timeline_id: str) -> tuple[list, list]:
    return _perform(db, timeline_id, "undo", "redo")


def perform_redo(db: Any, timeline_id: str) -> tuple[list, list]:
    return _perform(db, timeline_id, "redo", "undo")
```

> Note: `pop_top`/`push_row` take a `conn`; ensure `restore_timeline_snapshot(..., conn=conn)` runs in the same transaction so the whole bookkeeping+restore is atomic.

- [ ] **Step 4: Run to verify it passes** — `uv run pytest tests/test_perform_undo_redo.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/editing/history.py services/local-api/tests/test_perform_undo_redo.py
git commit -m "feat(history): perform_undo/perform_redo orchestration"
```

---

### Task 9: API endpoints (undo / redo / history)

**Files:**
- Modify: `services/local-api/src/laura/api/timelines.py` (3 routes), `api/models.py` (response models)
- Test: `services/local-api/tests/test_history_api.py`

**Interfaces:**
- `POST /timelines/{id}/undo` → `{"clips": [...], "scenes": [...]}`; `409` on `HistoryEmpty`.
- `POST /timelines/{id}/redo` → same; `409` on `HistoryEmpty`.
- `GET /timelines/{id}/history` → `{"can_undo","can_redo","undo_label","redo_label"}`.
- Mirror `cut_at_frame`'s `{clips, scenes}` dict response (so the existing `ClipOut`/`SceneOut` serialisation is reused).

- [ ] **Step 1: Write the failing test**

```python
# services/local-api/tests/test_history_api.py
def test_undo_redo_history_endpoints(client, seeded_timeline_via_api):
    tl = seeded_timeline_via_api
    # an edit creates an undo checkpoint
    client.post(f"/timelines/{tl}/cut-at-frame", json={"at_seq_frame": 5})
    assert client.get(f"/timelines/{tl}/history").json()["can_undo"] is True
    r = client.post(f"/timelines/{tl}/undo")
    assert r.status_code == 200 and "clips" in r.json() and "scenes" in r.json()
    assert client.get(f"/timelines/{tl}/history").json()["can_redo"] is True
    assert client.post(f"/timelines/{tl}/redo").status_code == 200


def test_undo_empty_is_409(client, seeded_timeline_via_api):
    tl = seeded_timeline_via_api
    assert client.post(f"/timelines/{tl}/undo").status_code == 409
```

- [ ] **Step 2: Run to verify it fails** — FAIL.

- [ ] **Step 3: Implement models + routes**

```python
# api/models.py
class HistoryStateOut(BaseModel):
    can_undo: bool
    can_redo: bool
    undo_label: str | None = None
    redo_label: str | None = None
```

```python
# api/timelines.py  (mirror the cut_at_frame {clips, scenes} response builders)
from fastapi import HTTPException
from laura.editing import history

@router.post("/timelines/{timeline_id}/undo")
def undo_timeline(timeline_id: str, request: Request) -> dict[str, Any]:
    db = _db(request)
    if repos.get_timeline(db, timeline_id) is None:
        raise HTTPException(404, "timeline not found")
    try:
        clips, scenes = history.perform_undo(db, timeline_id)
    except history.HistoryEmpty:
        raise HTTPException(409, "nothing to undo")
    return {"clips": [ClipOut.from_row(c).model_dump() for c in clips],
            "scenes": [SceneOut.from_row(s).model_dump() for s in scenes]}

@router.post("/timelines/{timeline_id}/redo")
def redo_timeline(timeline_id: str, request: Request) -> dict[str, Any]:
    db = _db(request)
    if repos.get_timeline(db, timeline_id) is None:
        raise HTTPException(404, "timeline not found")
    try:
        clips, scenes = history.perform_redo(db, timeline_id)
    except history.HistoryEmpty:
        raise HTTPException(409, "nothing to redo")
    return {"clips": [ClipOut.from_row(c).model_dump() for c in clips],
            "scenes": [SceneOut.from_row(s).model_dump() for s in scenes]}

@router.get("/timelines/{timeline_id}/history", response_model=HistoryStateOut)
def get_timeline_history(timeline_id: str, request: Request) -> HistoryStateOut:
    db = _db(request)
    if repos.get_timeline(db, timeline_id) is None:
        raise HTTPException(404, "timeline not found")
    return HistoryStateOut(**repos.get_history_state(db, timeline_id))
```

Use the **exact** `ClipOut`/`SceneOut` construction the existing `cut_at_frame` handler uses (read `scenes.py:115-186` and copy its serialisation, whether `.from_row(...)` or direct field mapping).

- [ ] **Step 4: Run to verify it passes** — `uv run pytest tests/test_history_api.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/api/timelines.py services/local-api/src/laura/api/models.py services/local-api/tests/test_history_api.py
git commit -m "feat(history): undo/redo/history API endpoints"
```

---

### Task 10: Frontend API client methods

**Files:**
- Modify: `apps/desktop/src/api.ts`
- Test: `apps/desktop/src/api.history.test.ts`

**Interfaces:**
- Produces (on `LauraClient`): `undo(timelineId) → Promise<{clips: TimelineClip[]; scenes: Scene[]}>`, `redo(...)` same, `getHistory(timelineId) → Promise<HistoryState>` where `interface HistoryState { can_undo: boolean; can_redo: boolean; undo_label: string | null; redo_label: string | null }`.

- [ ] **Step 1: Write the failing test** (vitest, mock `fetch` like the existing api tests do — follow an existing `*.test.ts` in `apps/desktop/src`).

```ts
// apps/desktop/src/api.history.test.ts
import { describe, it, expect, vi } from "vitest";
import { LauraClient } from "./api";

describe("history api", () => {
  it("undo posts and returns clips+scenes", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ clips: [], scenes: [] }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const c = new LauraClient("http://x", "t");
    const r = await c.undo("tl1");
    expect(r).toEqual({ clips: [], scenes: [] });
    expect(fetchMock).toHaveBeenCalledWith("http://x/timelines/tl1/undo", expect.objectContaining({ method: "POST" }));
  });
});
```

- [ ] **Step 2: Run to verify it fails** — `cd apps/desktop && npx vitest run src/api.history.test.ts` → FAIL.

- [ ] **Step 3: Implement** (mirror `cutAtFrame` at `api.ts:1110`)

```ts
export interface HistoryState {
  can_undo: boolean;
  can_redo: boolean;
  undo_label: string | null;
  redo_label: string | null;
}

// inside LauraClient:
undo(timelineId: string): Promise<{ clips: TimelineClip[]; scenes: Scene[] }> {
  return this.request(`/timelines/${timelineId}/undo`, { method: "POST" });
}
redo(timelineId: string): Promise<{ clips: TimelineClip[]; scenes: Scene[] }> {
  return this.request(`/timelines/${timelineId}/redo`, { method: "POST" });
}
getHistory(timelineId: string): Promise<HistoryState> {
  return this.request(`/timelines/${timelineId}/history`);
}
```

- [ ] **Step 4: Run to verify it passes** — PASS. Then `npx tsc --noEmit`.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/api.ts apps/desktop/src/api.history.test.ts
git commit -m "feat(history): api.ts undo/redo/getHistory"
```

---

### Task 11: Hook — `useRoughCutTranscript` undo/redo state

**Files:**
- Modify: `apps/desktop/src/hooks/useRoughCutTranscript.ts`
- Test: `apps/desktop/src/hooks/useRoughCutTranscript.history.test.tsx`

**Interfaces:**
- Adds to `RoughCutTranscriptController`: `undo: () => Promise<void>`, `redo: () => Promise<void>`, `canUndo: boolean`, `canRedo: boolean`, `undoLabel: string | null`, `redoLabel: string | null`.
- After every mutating method (`deleteRange`, `cutAt`, `replaceSpanText`) **and** after `undo`/`redo`, refresh history via `client.getHistory(roughCutId)`. `undo`/`redo` call the API then apply `{clips, scenes}` (reuse `reload()` for simplicity).

- [ ] **Step 1: Write the failing test** — render the hook with a mocked client whose `getHistory` returns `{can_undo:true,...}`; assert `canUndo` becomes true after a mutation, and `undo()` calls `client.undo` then `reload`.

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement** — add state + a `refreshHistory` callback:

```ts
const [history, setHistory] = useState<HistoryState>({ can_undo: false, can_redo: false, undo_label: null, redo_label: null });

const refreshHistory = useCallback(async () => {
  if (!client || !roughCutId) return;
  try { setHistory(await client.getHistory(roughCutId)); } catch { /* non-fatal */ }
}, [client, roughCutId]);

const undo = useCallback(async () => {
  if (!client || !roughCutId) return;
  try { await client.undo(roughCutId); await reload(); await refreshHistory(); }
  catch (e) { setError(String(e)); }
}, [client, roughCutId, reload, refreshHistory]);
// redo: identical with client.redo
```

Call `void refreshHistory()` at the end of `reload()` (covers initial load + post-mutation, since each mutator already calls `reload()`/`setClips`). Return the new fields (`canUndo: history.can_undo`, etc.).

- [ ] **Step 4: Run to verify it passes** — PASS; `npx tsc --noEmit`.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/hooks/useRoughCutTranscript.ts apps/desktop/src/hooks/useRoughCutTranscript.history.test.tsx
git commit -m "feat(history): hook undo/redo state + refresh"
```

---

### Task 12: UI — buttons in EditorialToolsBar + keyboard in FineCutView

**Files:**
- Modify: `apps/desktop/src/components/EditorialToolsBar.tsx`, `apps/desktop/src/components/FineCutView.tsx`
- Test: `apps/desktop/src/components/EditorialToolsBar.history.test.tsx`

**Interfaces:**
- `EditorialToolsBarProps` gains: `canUndo?: boolean`, `canRedo?: boolean`, `undoLabel?: string | null`, `redoLabel?: string | null`, `onUndo?(): void`, `onRedo?(): void`.

- [ ] **Step 1: Write the failing test** — render `EditorialToolsBar` with `canUndo={false}`; assert the ↶ button is `disabled`; with `canUndo` true + `onUndo` spy, click fires it.

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Add the two buttons** (mirror TimelineBar JSX, accent tokens):

```tsx
<button type="button" onClick={onUndo ?? (() => undefined)} disabled={!canUndo}
  title={undoLabel ? `Rückgängig: ${undoLabel}` : "Rückgängig"}
  className="rounded bg-accent px-3 py-1 text-[11px] font-medium text-accent-ink hover:bg-accent-glow disabled:opacity-40">
  ↶ Rückgängig
</button>
<button type="button" onClick={onRedo ?? (() => undefined)} disabled={!canRedo}
  title={redoLabel ? `Wiederholen: ${redoLabel}` : "Wiederholen"}
  className="rounded bg-accent px-3 py-1 text-[11px] font-medium text-accent-ink hover:bg-accent-glow disabled:opacity-40">
  ↷ Wiederholen
</button>
```

- [ ] **Step 4: Wire FineCutView** — pass `canUndo/canRedo/undoLabel/redoLabel/onUndo/onRedo` from `rc` into `<EditorialToolsBar>`, and add a window keydown handler with the focus-guard:

```tsx
useEffect(() => {
  function onKey(e: KeyboardEvent): void {
    const t = e.target;
    if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement) return; // ContinuousTranscript input
    const mod = e.ctrlKey || e.metaKey;
    if (!mod) return;
    const k = e.key.toLowerCase();
    if (k === "z" && !e.shiftKey) { e.preventDefault(); void rc.undo(); }
    else if ((k === "z" && e.shiftKey) || k === "y") { e.preventDefault(); void rc.redo(); }
  }
  window.addEventListener("keydown", onKey);
  return () => window.removeEventListener("keydown", onKey);
}, [rc]);
```

- [ ] **Step 5: Run to verify it passes** — vitest PASS; `npx tsc --noEmit`.

- [ ] **Step 6: Commit**

```bash
git add apps/desktop/src/components/EditorialToolsBar.tsx apps/desktop/src/components/FineCutView.tsx apps/desktop/src/components/EditorialToolsBar.history.test.tsx
git commit -m "feat(history): undo/redo buttons + Ctrl+Z/Ctrl+Shift+Z in Feinschnitt"
```

---

### Task 13: Full verification

**Files:** none (verification + fixups only).

- [ ] **Step 1: Backend** — `cd services/local-api && uv run ruff check . && uv run ruff format --check . && uv run mypy src/laura && uv run pytest -q`. All green. Fix any lint/type/test fallout inline (commit as `chore`/`fix` with explicit `git add`).
- [ ] **Step 2: Frontend** — `cd apps/desktop && npx tsc --noEmit && npx vitest run`. All green.
- [ ] **Step 3: Confirm no stray writes** — re-run `uv run pytest tests/test_checkpoint_coverage.py -v`. Green = the "Alles" guarantee holds.
- [ ] **Step 4: Ledger** — append the final status line to `.superpowers/sdd/progress.md`.

---

## Self-Review

**Spec coverage:** §4.1 → T2; §4.2 capture + full-column → T3; §4.2/§5 restore + atomicity → T4; depth/stack/redo-clear (§7) → T5; wrapper + coverage (§4.3) → T6; cooperative cancel (§6) → T7; undo/redo semantics (§5) → T8; API (§8) → T9; frontend (§9) → T10–T12; tests (§10) distributed; verification → T13. Single-timeline assumption (§2) → T1. **No gap.**

**Type consistency:** `capture_timeline_snapshot`/`restore_timeline_snapshot`/`push_undo_checkpoint`/`get_history_state`/`pop_top`/`push_row`/`is_job_cancel_requested`/`request_timeline_jobs_cancel`/`perform_undo`/`perform_redo`/`timeline_checkpoint` names used identically across tasks. Frontend `HistoryState` shape (`can_undo`/`can_redo`/`undo_label`/`redo_label`) matches backend `HistoryStateOut` and `get_history_state` keys.

**Placeholder scan:** none — every code step carries real code. Heuristic-test refinement notes (T6) are explicit, not placeholders.
