# Replacement-Lane (Source-Replace-Primitive) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Checkbox-getrackt.
> **Kollisions-Regeln:** Pathspec-Commits nur mit explizitem Pfad; **niemals `api/timelines.py`,
> `RoughCutView.tsx`, `FineCutView.tsx`, `analysis/refine.py`, `shots.py`, `Player.tsx`** anfassen;
> uv.lock/.claude/build nie stagen; Subagenten committen NICHT. Design: `docs/superpowers/specs/2026-06-09-replacement-lane-design.md`.

**Goal:** Ein Clip mit `role='replace'` auf `lane ≥ 1` ersetzt beim Render die Base-Lane in seiner
Frame-Range — nicht-destruktiv, frame-genau, in Szene- **und** Sequenz-Timeline.

**Architecture:** Eine reine Präzedenz-Funktion splittet Base-Clips an Overlay-Range-Grenzen und setzt
das Overlay-Asset ein; eingehängt am **einzigen Choke-Point** `resolve_clip_rows` (beide Tabs laufen
dort durch). Kein neuer Filtergraph (opakes Voll-Frame-Replace ⇒ bestehender Trim+Concat-Pfad).

**Tech Stack:** Python 3.11/uv/pytest/ffmpeg · SQLite-Migration · FastAPI · React/TS/Tailwind ·
Verifikation: `uv run pytest` + echter ffprobe + tsc + CDP.

**Verifizierte Schema-Fakten (Exploration):**
- `timeline_clips`-Spalten: `id, timeline_id, asset_id, src_in_frame, src_out_frame_exclusive,
  seq_in_frame, seq_out_frame_exclusive, lane(0), speed_num(1), speed_den(1), audio_offset_samples(0)`, … — **kein `role`**.
- Sequenz = `timelines`-Row `kind='sequence'`; `flatten_sequence(db, seq_tl_id)` liefert transiente Base-Rows
  (Szenen-Clips, lane 0, seq-rebased). Sequenz-Overlays werden als `timeline_clips` auf der Sequenz-Timeline gespeichert.
- `resolve_clip_rows(db, timeline_row)` in `editing/otio_sync.py:23-32` ist der einzige Choke-Point.
- Render (`handle_render`) baut `clips=[(Path(src), src_in, src_out), …]` aus den Rows **in Reihenfolge**
  und concatet → Präzedenz muss nach `seq_in` sortierte, gesplittete Rows liefern.
- **Annahme v1:** Base-Clip unter dem Overlay hat `speed_num/den == 1` (Retiming unter Overlay = später).

---

## File Structure
- **Create:** `services/local-api/src/laura/db/migrations/0014_clip_role.sql` · `src/laura/editing/overlays.py`
  (reine Präzedenz) · `tests/test_overlay_precedence.py` · `tests/test_overlay_render.py` (ffprobe) ·
  `src/laura/api/overlays.py` (Router) · `tests/test_overlays_api.py`
- **Modify (alles meins):** `db/repos.py` (`add_timeline_clip(role=…)`, `update_timeline_clip_role`, `delete_timeline_clip`) ·
  `editing/otio_sync.py` (`resolve_clip_rows` ruft Präzedenz) · `api/models.py` (OverlayRequest + role in Clip-Out) ·
  `main.py` (Router mounten) · `apps/desktop/src/api.ts` (`setOverlay`/`removeOverlay`) ·
  `apps/desktop/src/components/TimelineBar.tsx` (zweite Lane) · `FineCutView.tsx`/`AssembleView.tsx` **NICHT** —
  stattdessen die Overlay-Steuerung in eine **neue** kleine Komponente, von beiden eingebunden (kollisionssicher).

> **Achtung Kollision:** `FineCutView.tsx` ist verboten. Die zweite Lane lebt in `TimelineBar.tsx` (erlaubt,
> datengetrieben) — das genügt für die Anzeige in BEIDEN Tabs, da beide dieselbe TimelineBar nutzen. Die
> Set/Remove-UI kommt als eigene Komponente `OverlayControls.tsx`, die AssembleView einbindet (erlaubt); für
> FineCut reicht v1 die Anzeige + Remove-Klick in der TimelineBar (kein FineCutView-Edit nötig).

---

## Task RL1 — Migration `role` + Repos
**Files:** Create `db/migrations/0014_clip_role.sql`; Modify `db/repos.py`; Create/extend test

- [ ] **Step 1 — Failing test** (`tests/test_overlay_precedence.py`, repos-Teil): erst nur Repos:
```python
from laura.db.database import SqliteDatabase
from laura.db import repos

def _mk(tmp_path):
    db = SqliteDatabase(tmp_path / "t.db"); db.migrate()
    p = repos.create_project(db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root=str(tmp_path))
    a = repos.create_asset(db, project_id=p["id"], type="video", display_name="a", source_path="a.mp4")
    tl = repos.create_timeline(db, project_id=p["id"], name="s", kind="rough_cut")
    return db, p, a, tl

def test_clip_role_defaults_base(tmp_path):
    db, p, a, tl = _mk(tmp_path)
    repos.add_timeline_clip(db, timeline_id=tl["id"], asset_id=a["id"], src_in_frame=0, src_out_frame_exclusive=30, seq_in_frame=0, seq_out_frame_exclusive=30)
    rows = repos.list_timeline_clips(db, tl["id"])
    assert rows[0]["role"] == "base"

def test_add_clip_role_replace_and_update(tmp_path):
    db, p, a, tl = _mk(tmp_path)
    c = repos.add_timeline_clip(db, timeline_id=tl["id"], asset_id=a["id"], src_in_frame=0, src_out_frame_exclusive=10, seq_in_frame=5, seq_out_frame_exclusive=15, lane=1, role="replace")
    assert c["role"] == "replace" and c["lane"] == 1
    repos.update_timeline_clip_role(db, c["id"], "base")
    assert repos.list_timeline_clips(db, tl["id"])[0]["role"] == "base"
    repos.delete_timeline_clip(db, c["id"])
    assert repos.list_timeline_clips(db, tl["id"]) == []
```
- [ ] **Step 2 — Run, expect fail.**
- [ ] **Step 3 — Implement:** Migration `ALTER TABLE timeline_clips ADD COLUMN role TEXT NOT NULL DEFAULT 'base';`
  (additiv; bestehende Clips 'base'). In repos: `add_timeline_clip(..., lane: int = 0, role: str = "base")` (INSERT
  benennt `role`); neue `update_timeline_clip_role(db, clip_id, role)` und `delete_timeline_clip(db, clip_id)`
  (falls noch nicht vorhanden — sonst wiederverwenden). `list_timeline_clips` liefert `role` automatisch (SELECT *).
- [ ] **Step 4 — Run, expect pass.**
- [ ] **Step 5 — Commit:** `… -- services/local-api/src/laura/db/migrations/0014_clip_role.sql services/local-api/src/laura/db/repos.py services/local-api/tests/test_overlay_precedence.py`

## Task RL2 — Reine Präzedenz-Auflösung
**Files:** Create `src/laura/editing/overlays.py`; extend `tests/test_overlay_precedence.py`

- [ ] **Step 1 — Failing test** (pure, keine DB): ein Base-Clip seq[0,30) src[100,130) + ein Replace-Overlay
  seq[10,20) src[0,10) (anderes Asset) → 3 Render-Rows in seq-Reihenfolge:
```python
from laura.editing.overlays import apply_overlay_precedence
def test_overlay_splits_base_into_three():
    base = [{"asset_id":"A","src_in_frame":100,"src_out_frame_exclusive":130,"seq_in_frame":0,"seq_out_frame_exclusive":30,"lane":0,"role":"base","speed_num":1,"speed_den":1}]
    ov   = [{"asset_id":"B","src_in_frame":0,"src_out_frame_exclusive":10,"seq_in_frame":10,"seq_out_frame_exclusive":20,"lane":1,"role":"replace","speed_num":1,"speed_den":1}]
    out = apply_overlay_precedence(base, ov)
    assert [(r["asset_id"], r["src_in_frame"], r["src_out_frame_exclusive"], r["seq_in_frame"], r["seq_out_frame_exclusive"]) for r in out] == [
        ("A",100,110,0,10), ("B",0,10,10,20), ("A",120,130,20,30)]  # rechts: src_in=100+(20-0)=120 (zeit-aligned)
def test_overlay_full_clip_replaces_whole(): ...   # overlay == base range -> nur Overlay
def test_no_overlays_returns_base_unchanged(): ...
def test_overlay_at_clip_start_and_end(): ...      # Grenzfälle: kein Null-Längen-Segment
```
- [ ] **Step 2 — Run, expect fail** (ImportError).
- [ ] **Step 3 — Implement** `apply_overlay_precedence(base_rows, overlay_rows) -> list[dict]`:
  für jeden Base-Row die überlappenden Overlays (nach `seq_in`) heraussplitten: linkes Base-Stück
  `seq[b_in, o_in]` mit `src[b_src_in, b_src_in+(o_in-b_in)]`, dann das Overlay-Row unverändert, dann
  rechtes Base-Stück `seq[o_out, b_out]` mit `src[b_src_in+(o_out-b_in), b_src_out]`. Mehrere Overlays je
  Base nacheinander. **Null-Längen-Segmente weglassen.** Base-Rows ohne Overlay unverändert. Ergebnis nach
  `seq_in_frame` sortiert. Ganzzahl-Frames, end-exclusive. (Annahme `speed==1` unter Overlay; bei `speed!=1`
  am Base-Stück: v1 → den Base-Clip NICHT splitten, Overlay trotzdem einsetzen + Warn-Log; sauber dokumentieren.)
- [ ] **Step 4 — Run, expect pass.**
- [ ] **Step 5 — Commit** (`editing/overlays.py`, Test).

## Task RL3 — `resolve_clip_rows` → Präzedenz (beide Tabs) + Render-Test
**Files:** Modify `editing/otio_sync.py`; Create `tests/test_overlay_render.py`

- [ ] **Step 1 — Failing integration test** (`tests/test_overlay_render.py`, skip ohne ffmpeg/ffprobe): zwei
  unterscheidbare Test-Clips (z. B. `testsrc` vs `testsrc2`/andere Farbe), Szene-Timeline mit Base-Clip,
  ein Replace-Overlay (zweites Asset) über eine Sub-Range; `resolve_clip_rows` → render → ffprobe Länge stimmt;
  **Frame-Mitte der Overlay-Range** mit `ffmpeg -ss … -frames:v 1` extrahieren und prüfen, dass sie vom
  Overlay-Asset stammt (z. B. dominante Farbe ≠ Base). Plus: ohne Overlay → identische Rows wie `list_timeline_clips`.
- [ ] **Step 2 — Run, expect fail.**
- [ ] **Step 3 — Implement** in `editing/otio_sync.py`:
```python
def resolve_clip_rows(db, timeline_row):
    if timeline_row.get("kind") == "sequence":
        base = flatten_sequence(db, timeline_row["id"])
        overlays = [c for c in repos.list_timeline_clips(db, timeline_row["id"]) if c.get("role") == "replace"]
    else:
        rows = repos.list_timeline_clips(db, timeline_row["id"])
        base = [c for c in rows if c.get("role", "base") != "replace"]
        overlays = [c for c in rows if c.get("role") == "replace"]
    if not overlays:
        return base  # regression-safe: identisch zu vorher
    from .overlays import apply_overlay_precedence
    return apply_overlay_precedence(base, overlays)
```
- [ ] **Step 4 — Run, expect pass** (echter ffprobe + Farbprobe). Regression: `test_sequence_render`/`test_render_job` grün.
- [ ] **Step 5 — Commit** (`editing/otio_sync.py`, `tests/test_overlay_render.py`).

## Task RL4 — Overlay-API (eigener Router)
**Files:** Create `api/overlays.py`, `tests/test_overlays_api.py`; Modify `api/models.py`, `main.py`

- [ ] **Step 1 — Failing API test:** `POST /timelines/{id}/overlays` `{lane:1, asset_id, seq_in_frame, seq_out_frame_exclusive, src_in_frame:0}`
  → 201, Body=Overlay-Clip mit `role='replace'`; `list_timeline_clips` zeigt ihn. `DELETE /timelines/{id}/overlays/{clip_id}` → 204, weg.
  Validierung: `seq_out>seq_in`; Asset-Range deckt die Länge (`src_out-src_in == seq_out-seq_in`) → sonst 400.
- [ ] **Step 3 — Implement:** `OverlayRequest(BaseModel)` {lane:int=1, asset_id:str, seq_in_frame:int, seq_out_frame_exclusive:int, src_in_frame:int=0} in models.py; Clip-Out-Modell um `role`/`lane` ergänzen (additiv).
  Router `api/overlays.py` (Depends require_token, `request.app.state.db`): POST validiert + `add_timeline_clip(..., lane=body.lane, role="replace", src_out_frame_exclusive=body.src_in_frame+(body.seq_out-body.seq_in))`; DELETE → `delete_timeline_clip`. In `main.py` mounten (neben den anderen, **nicht** in timelines.py).
- [ ] **Step 4/5 — pass + commit** (`api/overlays.py`, `api/models.py`, `main.py`, Test).

## Task RL5 — Frontend: zweite Lane + Set/Remove
**Files:** Modify `apps/desktop/src/api.ts`, `apps/desktop/src/components/TimelineBar.tsx`; Create `apps/desktop/src/components/OverlayControls.tsx`; Modify `AssembleView.tsx`

- [ ] **Step 1 — `api.ts`:** `setOverlay(timelineId, {lane, assetId, seqIn, seqOut, srcIn?}): Promise<Clip>` (POST) +
  `removeOverlay(timelineId, clipId): Promise<void>` (DELETE), Stil wie bestehende Client-Methoden (`this.request`).
- [ ] **Step 2 — `TimelineBar.tsx`:** Clips mit `lane >= 1` als **zweite Spur über V1** zeichnen (datengetrieben;
  Prop `clips` enthält `role`/`lane`). Replace-Overlay als Block; Klick → `onRemoveOverlay(clipId)` (neue optionale Prop).
  Da beide Tabs dieselbe TimelineBar nutzen, erscheint die Lane in Feinschnitt UND Zusammenfügen.
- [ ] **Step 3 — `OverlayControls.tsx`:** kleine Komponente (Range + Asset-Auswahl → `client.setOverlay`); in
  **AssembleView** eingebunden (FineCutView ist verboten — dort genügt Anzeige + Remove über die TimelineBar).
- [ ] **Step 4 — Verify:** `npm --prefix apps/desktop run typecheck` clean.
- [ ] **Step 5 — Commit** (api.ts, TimelineBar.tsx, OverlayControls.tsx, AssembleView.tsx).

## Task RL6 — Gesamt-Verifikation
- [ ] `cd services/local-api && uv run pytest -q` → grün (inkl. Overlay + Regression Szene/Sequenz-Render).
- [ ] `npm --prefix apps/desktop run typecheck` → clean.
- [ ] **Echt:** Szene mit Base + ein Replace-Overlay über Sub-Range → Render → ffprobe + Frame-Farbprobe; Sequenz analog.
- [ ] `tasks/todo.md` + `docs` aktualisieren.

---

## Risiken / De-Risk
- **Render concatet in Row-Reihenfolge** (ignoriert seq-Gaps) → Präzedenz liefert lückenlos-sortierte Rows; reine Funktion + Tests sichern Frame-Genauigkeit.
- **Retiming unter Overlay** (`speed != 1`): v1 splittet solche Base-Clips nicht (Overlay trotzdem gesetzt) + Warn-Log; sauber als Limit dokumentiert, Voll-Support später.
- **Sequenz-Overlays** auf der Sequenz-Timeline (`kind='sequence'`-Row) gespeichert; `flatten_sequence` bleibt Base-only → Merge in `resolve_clip_rows`. Regression-safe (keine Overlays ⇒ alter Pfad).
- **Kollision:** `FineCutView.tsx` unberührt — Anzeige/Remove via TimelineBar, Set-UI nur in AssembleView (+ Endpoint für reenact später programmatisch).
