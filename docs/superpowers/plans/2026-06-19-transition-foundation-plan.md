# Transition-Fundament (Plan A) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein echtes Crossfade (`xfade`) im Renderer + Transition-Felder auf allen Timeline-Arten, sodass ein Jump-Cut-Fix ein echtes Dissolve wird (rough_cut/scene/sequence), nicht eine Abblende durch Schwarz.

**Architecture:** (1) Migration 0024 ergänzt `timeline_clips.transition_after_kind/frames` (wie 0021 für `sequence_items`). (2) Der Render-Video-Zusammenbau in `render_clips_mp4` wird von „concat-dann-Fade" auf einen **paarweisen Fold** umgestellt, der pro Grenze entweder `concat` (hard/fade) oder echtes `xfade`/`acrossfade` (crossfade, mit Reserve-Overlap, Gesamtlänge bleibt = Σ Cliplängen) macht. (3) `handle_render` baut Transitions auch für rough_cut/scene aus den Clip-Feldern; Repos + API zum Setzen einer Clip-Transition.

**Tech Stack:** Python 3.11 (uv, pytest, ruff, mypy), ffmpeg-Filtergraph (`xfade`, `acrossfade`, `concat`), SQLite-Migrationen.

## Global Constraints

- **Frames sind Ganzzahlen, Ranges end-exclusive** (Invarianten #1/#2). Nie Float-Sekunden als Zustand.
- **Determinismus (#7):** Render byte-stabil bei gleichem Input; keine Zufalls-/Zeitabhängigkeit.
- **Kein `print`** → projektlokaler Logger. **Typing strikt** (mypy), **ruff** sauber.
- **Backwards-kompatibel:** Ohne Transitions (`hard`) muss der Filtergraph **byte-identisch** zum heutigen `concat`-Pfad bleiben (bestehende Render-Tests dürfen nicht brechen).
- **Migrationsnummer:** Plan A baut zuerst → **`0023_clip_transitions.sql`** (höchste aktuell: `0022_demo_drafts.sql`). Plan B bekommt `0024_transition_reviews.sql`. Der Runner ([`db/base.py`](../../../services/local-api/src/laura/db/base.py)) trackt **pro Version** in `schema_meta` (kein Max-Integer) → Lücken/Reihenfolge unkritisch, Nummern müssen nur eindeutig sein; wir halten sie lückenlos.

---

### Task 1: Migration — Transition-Felder auf `timeline_clips`

**Files:**
- Create: `services/local-api/src/laura/db/migrations/0023_clip_transitions.sql`
- Test: `services/local-api/tests/test_migrations.py` (falls vorhanden; sonst ein neuer `test_clip_transitions_migration.py`)

**Interfaces:**
- Produces: Spalten `timeline_clips.transition_after_kind TEXT NOT NULL DEFAULT 'hard'`, `timeline_clips.transition_after_frames INTEGER NOT NULL DEFAULT 0`.

- [ ] **Step 1: Failing test** — frisch migrierte DB hat die neuen Spalten auf `timeline_clips`.

```python
# tests/test_clip_transitions_migration.py
from laura.db.database import Database

def test_timeline_clips_has_transition_columns(tmp_path):
    db = Database(str(tmp_path / "t.db")); db.migrate()
    cols = {r["name"] for r in db.query("PRAGMA table_info(timeline_clips)")}
    assert {"transition_after_kind", "transition_after_frames"} <= cols
```
> Vor dem Schreiben: echte API von `Database` prüfen (`db.migrate()`/`db.query()` ggf. anpassen — Muster aus bestehenden Migrationstests übernehmen).

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_clip_transitions_migration.py -v` → fehlende Spalten.

- [ ] **Step 3: Migration schreiben**

```sql
-- 0023_clip_transitions.sql
ALTER TABLE timeline_clips ADD COLUMN transition_after_kind TEXT NOT NULL DEFAULT 'hard';
ALTER TABLE timeline_clips ADD COLUMN transition_after_frames INTEGER NOT NULL DEFAULT 0;
```

- [ ] **Step 4: Run, expect PASS.**

- [ ] **Step 5: Commit** — `git commit -m "feat(render): clip-level transition columns (migration 0023)"`

---

### Task 2: Repo — Clip-Transition lesen/setzen

**Files:**
- Modify: `services/local-api/src/laura/db/repos.py` (nahe `list_timeline_clips` / der `sequence_items`-Transition-Setter um Zeile 1285)
- Test: `services/local-api/tests/test_repos_clip_transition.py`

**Interfaces:**
- Consumes: bestehende `add_timeline_clip`, `list_timeline_clips`.
- Produces:
  - `set_clip_transition(db, *, clip_id: str, kind: str, frames: int) -> None`
  - `list_timeline_clips(...)` liefert die neuen Felder mit (sie selektiert `*` oder muss erweitert werden — prüfen).

- [ ] **Step 1: Failing test**

```python
def test_set_and_read_clip_transition(tmp_path):
    db = _seed_db(tmp_path)                       # Helper: Projekt+Asset+rough_cut-Timeline+1 Clip
    tl_id, clip_id = _first_clip(db)
    repos.set_clip_transition(db, clip_id=clip_id, kind="crossfade", frames=12)
    row = next(c for c in repos.list_timeline_clips(db, tl_id) if c["id"] == clip_id)
    assert row["transition_after_kind"] == "crossfade"
    assert row["transition_after_frames"] == 12
```

- [ ] **Step 2: Run, expect FAIL** (Funktion fehlt / Feld fehlt).

- [ ] **Step 3: Implementieren** — `set_clip_transition` (UPDATE analog zum `sequence_items`-Setter bei repos.py:1285); sicherstellen, dass `list_timeline_clips` die Spalten zurückgibt.

```python
def set_clip_transition(db, *, clip_id: str, kind: str, frames: int) -> None:
    db.execute(
        "UPDATE timeline_clips SET transition_after_kind=?, transition_after_frames=? WHERE id=?",
        (kind, int(frames), clip_id),
    )
```

- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(render): set_clip_transition repo + expose fields"`

---

### Task 3: Render — echtes `xfade`/`acrossfade` im Video-Zusammenbau

**Files:**
- Modify: `services/local-api/src/laura/render/mp4.py` (`render_clips_mp4`, neuer Builder; `_video_transition_chain` wird für crossfade ersetzt/ergänzt)
- Test: `services/local-api/tests/test_render_xfade.py`

**Interfaces:**
- Consumes: `clips: list[tuple[Path, int, int]]`, `video_transitions: list[VideoTransition]` (kind ∈ {hard, fade, crossfade}).
- Produces: korrekt zusammengesetzter Filtergraph; bei `crossfade` echter Querblende mit Reserve-Overlap; Gesamtlänge = Σ (src_out−src_in) (Sync-Invariante, `assert_or_fix_media_sync` bleibt grün).

**Design (Kern):** Heute: alle Clips `concat` → danach `fade`. Neu: **paarweiser Fold** über die Clips. Für jede Grenze i→i+1:
- `hard` → `concat=n=2:v=1` (Video) bzw. Audio-Mitführung wie bisher.
- `crossfade(D)` → Clip i wird beim Trim um **D Reserve-Frames verlängert** (`end_frame = src_out_i + D`, nur wenn die Quelle die Reserve hat — sonst Fallback auf `fade`, geloggt). `xfade=transition=fade:duration=D/fps:offset=content_len_so_far/fps`. `content_len_so_far` = Σ Originallängen L_0..L_i (NICHT inkl. Reserve). Output-Länge = Σ L. Audio analog mit `acrossfade=d=D/fps`.
- `fade` → wie heute (Abblende durch Schwarz auf dem laufenden Stream) als Alternativstil.

> **Warum Reserve statt Content-Eat:** würde der Crossfade Content fressen, sänke die Gesamtlänge um D pro Übergang → `assert_or_fix_media_sync` (erwartet Σ Cliplängen) bräche, Caption/Music-Offsets verrutschten. Reserve-Overlap hält die Länge exakt.

- [ ] **Step 1: Failing test (hard-Pfad bleibt identisch)** — Regressionsschutz: bei nur `hard` ist der Filtergraph byte-identisch zum Status quo.

```python
def test_hard_only_filtergraph_unchanged(monkeypatch, tmp_path):
    calls = _capture_ffmpeg_args(monkeypatch)         # patch run_ffmpeg, record args
    render_clips_mp4(_two_clips(tmp_path), tmp_path/"o.mp4", rate_num=30, rate_den=1)
    fc = _filter_complex(calls[-1])
    assert "concat=n=2:v=1" in fc and "xfade" not in fc
```

- [ ] **Step 2: Failing test (crossfade baut xfade)** — mit einem crossfade-Übergang taucht `xfade` + korrekter `offset` auf, und Clip A wird um D verlängert.

```python
def test_crossfade_builds_xfade(monkeypatch, tmp_path):
    calls = _capture_ffmpeg_args(monkeypatch)
    clips = _two_clips(tmp_path)                       # je 30 frames, Quelle hat Reserve
    tr = [VideoTransition(kind="crossfade", boundary_frame=30, duration_frames=6)]
    render_clips_mp4(clips, tmp_path/"o.mp4", rate_num=30, rate_den=1, video_transitions=tr)
    fc = _filter_complex(calls[-1])
    assert "xfade=transition=fade:duration=0.2:offset=" in fc      # 6/30 = 0.2s
    assert "end_frame=36" in fc                                    # A: 30 + 6 reserve
```

- [ ] **Step 3: Failing test (Reserve fehlt → fade-Fallback)** — wenn die Quelle keine D Reserve-Frames hat, fällt der Übergang auf `fade` zurück (kein `xfade`, geloggt).

```python
def test_crossfade_without_reserve_falls_back_to_fade(monkeypatch, tmp_path):
    calls = _capture_ffmpeg_args(monkeypatch)
    clips = _two_clips_no_reserve(tmp_path)           # Clip A endet am Quell-Ende
    tr = [VideoTransition(kind="crossfade", boundary_frame=30, duration_frames=6)]
    render_clips_mp4(clips, tmp_path/"o.mp4", rate_num=30, rate_den=1, video_transitions=tr)
    fc = _filter_complex(calls[-1])
    assert "xfade" not in fc and "fade=t=out" in fc
```

- [ ] **Step 4: Run alle drei, expect FAIL.**

- [ ] **Step 5: Implementieren** — `render_clips_mp4` Video/Audio-Zusammenbau auf paarweisen Fold umstellen. Reserve = D pro crossfade-Clip, via `probe`/Asset-Länge prüfen; `xfade`/`acrossfade` bzw. `concat`; `content_len_so_far`-Offset. `hard`-only-Pfad MUSS den alten Graphen exakt reproduzieren (Branch: wenn keine crossfade-Transition → bestehender Code unverändert).

> Implementierungs-Detail: Den Fold in einen reinen Helfer `_build_video_audio_graph(clips, transitions, rate) -> (parts, out_label, extended_endframes)` ziehen (testbar ohne ffmpeg). `render_clips_mp4` ruft ihn auf. Reserve-Verfügbarkeit über die Quelllänge (Asset `frame_count` / `probe`) bestimmen.

- [ ] **Step 6: Run, expect PASS** (+ bestehende Render-Tests grün: `uv run pytest tests/ -k render`).

- [ ] **Step 7: Echter ffmpeg-Smoke** (nicht gemockt) — ein 2-Clip-Crossfade gegen ein Fixture rendern, `probe` prüft Frame-Zahl == Σ Cliplängen.

- [ ] **Step 8: Commit** — `git commit -m "feat(render): real xfade/acrossfade crossfade with reserve overlap"`

---

### Task 4: Render-Handler — Transitions für rough_cut/scene aus Clip-Feldern

**Files:**
- Modify: `services/local-api/src/laura/render/handlers.py` (neuer `_clip_video_transitions`, Wiring in `handle_render` Zeile ~218)
- Test: `services/local-api/tests/test_clip_video_transitions.py`

**Interfaces:**
- Consumes: `repos.list_timeline_clips`, `VideoTransition`.
- Produces: `_clip_video_transitions(db, timeline_id) -> list[VideoTransition]` (Boundary = kumulierte `seq_out_frame_exclusive`; Transition aus `transition_after_*` jedes Clips außer dem letzten).

- [ ] **Step 1: Failing test** — rough_cut mit 3 Clips, mittlerer hat `crossfade`/12 → genau eine `VideoTransition` mit Boundary = seq_out des mittleren Clips.

```python
def test_clip_video_transitions_for_rough_cut(tmp_path):
    db = _seed_rough_cut_3_clips(tmp_path)            # seq 0-100,100-160,160-240
    tl_id, clips = _clips(db)
    repos.set_clip_transition(db, clip_id=clips[0]["id"], kind="crossfade", frames=12)
    tr = _clip_video_transitions(db, tl_id)
    assert len(tr) == 1 and tr[0].kind == "crossfade"
    assert tr[0].boundary_frame == 100 and tr[0].duration_frames == 12
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implementieren** `_clip_video_transitions` (mirror `_sequence_video_transitions`, aber über Clips) + Wiring:

```python
video_transitions = (
    _sequence_video_transitions(ctx.db, exp["timeline_id"])
    if tl["kind"] == "sequence"
    else _clip_video_transitions(ctx.db, exp["timeline_id"])
)
```

- [ ] **Step 4: Run, expect PASS** (+ Sequenz-Pfad-Tests bleiben grün).
- [ ] **Step 5: Commit** — `git commit -m "feat(render): build video transitions for rough_cut/scene from clip fields"`

---

### Task 5: API — Clip-Transition setzen + Modelle

**Files:**
- Modify: `services/local-api/src/laura/api/timelines.py` (neuer Endpoint), `services/local-api/src/laura/api/models.py` (Request-Modell)
- Modify: `apps/desktop/src/api.ts` (Methode + Typ)
- Test: `services/local-api/tests/test_api_clip_transition.py`

**Interfaces:**
- Produces: `POST /timelines/{id}/clips/{clip_id}/transition` Body `{kind: "hard"|"fade"|"crossfade", frames: int}` → `{status:"ok"}`. `api.ts`: `setClipTransition(timelineId, clipId, kind, frames)`.

- [ ] **Step 1: Failing test (TestClient)** — POST setzt die Felder; ungültiges `kind` → 422.

```python
def test_set_clip_transition_endpoint(client, tmp_path):
    tl_id, clip_id = _seed_clip(client)
    r = client.post(f"/timelines/{tl_id}/clips/{clip_id}/transition",
                    json={"kind": "crossfade", "frames": 12}, headers=_auth())
    assert r.status_code == 200
    # verify via list endpoint
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implementieren** — Pydantic-Modell (`kind: Literal["hard","fade","crossfade"]`, `frames: int >= 0`), Endpoint ruft `repos.set_clip_transition`. `api.ts`-Methode + Typ (kein `any`).
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: `npm --prefix apps/desktop run typecheck`** (api.ts strict).
- [ ] **Step 6: Commit** — `git commit -m "feat(api): set clip transition endpoint + api.ts method"`

---

### Task 6: Plan-A-Verifikation (Integration)

- [ ] **Step 1:** `uv run pytest` (volle Backend-Suite grün).
- [ ] **Step 2:** `uv run ruff check . && uv run mypy src` (sauber).
- [ ] **Step 3:** Echter End-to-End-Render: rough_cut mit gesetztem Crossfade exportieren, `probe` → Frame-Zahl == Σ Cliplängen, Sichtprüfung 1 Frame im Übergang (manuell markiert).
- [ ] **Step 4:** `npm --prefix apps/desktop run typecheck && npm --prefix apps/desktop run lint`.
- [ ] **Step 5: Commit** etwaiger Nacharbeiten. Plan A abgeschlossen → weiter mit Plan B.

## Self-Review (Spec-Abdeckung Plan A)

- Spec §4.7 (Migration Clip-Transitions) → Task 1. ✓
- Spec §5 (`transition`-Fix, crossfade, alle kinds, fade-Fallback) → Tasks 3+4+5. ✓
- Spec §10 (xfade braucht Overlap/Reserve; Fallback) → Task 3 Steps 2–3. ✓
- Spec §11 (echter xfade im Renderer drin) → Task 3. ✓
- Determinismus/Sync (Σ-Länge erhalten) → Task 3 Step 7 + Task 6 Step 3. ✓
- Backwards-Kompat (hard byte-identisch) → Task 3 Step 1. ✓
