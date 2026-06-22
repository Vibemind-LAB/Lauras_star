# R0 — Reel-Render-Skelett — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (oder
> executing-plans). Schritte sind Checkbox-getrackt. **Kollisions-Regeln:** Pathspec-Commits nur
> mit explizitem Pfad; **niemals `api/timelines.py`, `RoughCutView.tsx`, `FineCutView.tsx`,
> `analysis/refine.py`, `shots.py` anfassen**; uv.lock/.claude/build nie stagen; Subagenten committen nicht.

**Goal:** Aus einer bestehenden Sequenz ein **vertikales 9:16-MP4** exportieren, mit eingebranntem
**Hook-Text** und **Disclosure-Label** — ohne KI, ohne neue schwere Deps. Liefert das erste fertige,
gekennzeichnete Reel.

**Architecture:** Reine, additive Erweiterung des bestehenden Render-Pfads. `render_clips_mp4` bekommt
optionale Reel-Filter (crop→scale→drawtext) nach dem Concat. Reel-Parameter reisen über eine neue
`options`-Spalte am `exports`-Record (nicht über `timelines.py`). Ein **neuer** Endpoint
`POST /timelines/{id}/render-reel` (eigener Router, kollisionsfrei) legt den Export mit Reel-Optionen an
und enqueued den bestehenden `export.render`-Job. `handle_render` liest die Optionen und reicht sie durch.
Frontend: ExportView bekommt ein „Reel 9:16"-Preset + Hook-Feld + Disclosure-Toggle.

**Tech Stack:** Python 3.11/uv/pytest/ffmpeg (`crop`,`scale`,`drawtext`) · SQLite-Migration · FastAPI ·
React/TS/Tailwind/Electron · Verifikation: `uv run pytest` + echter `ffprobe` + CDP-Screenshot.

**Invarianten:** Crop-/Text-Geometrie als Ganzzahl-Frames bzw. feste Pixel; OTIO = Wahrheit
(Reel-Optionen sind Export-Parameter, nie Projektzustand); schwere Modelle bleiben außen vor (R0 ist GPU-frei);
Idempotenz über den bestehenden `render:{export_id}`-Key.

---

## File Structure
- **Create:** `services/local-api/src/laura/render/reel.py` (reine Filter-Builder) · `tests/test_reel_filters.py` ·
  `tests/test_reel_render.py` (ffprobe) · `services/local-api/src/laura/api/reels.py` (neuer Router) ·
  `tests/test_reel_render_api.py` · `services/local-api/src/laura/db/migrations/0013_export_options.sql`
- **Modify (alles meins):** `render/mp4.py` (Reel-Kwargs) · `render/handlers.py` (Optionen lesen) ·
  `db/repos.py` (`create_export(options=)`, `get_export` parst) · `api/models.py` (`ReelRenderRequest`) ·
  `api/__init__.py` oder App-Setup (Router mounten) · `apps/desktop/src/api.ts` (`renderReel`) ·
  `apps/desktop/src/components/ExportView.tsx` (Preset + Hook + Disclosure)

---

## Task R0.1 — Reiner Reel-Filter-Builder (vertikal + drawtext)
**Files:** Create `render/reel.py`, `tests/test_reel_filters.py`

- [ ] **Step 1 — Failing test** (`tests/test_reel_filters.py`):
```python
from laura.render.reel import reel_video_chain

def test_vertical_crop_scale():
    chain = reel_video_chain(vertical=True, hook_text=None, disclosure_text=None, font="X")
    assert "crop=ih*9/16:ih" in chain and "scale=1080:1920" in chain

def test_hook_and_disclosure_drawtext_escaped():
    chain = reel_video_chain(vertical=True, hook_text="Hi: 50%", disclosure_text="KI", font="X")
    assert chain.count("drawtext=") == 2
    assert r"Hi\: 50\%" in chain        # ':' and '%' escaped for drawtext

def test_empty_is_noop():
    assert reel_video_chain(vertical=False, hook_text=None, disclosure_text=None, font="X") == ""
```
- [ ] **Step 2 — Run, expect fail:** `cd services/local-api && uv run pytest tests/test_reel_filters.py -q` → ImportError.
- [ ] **Step 3 — Implement** `render/reel.py`: pure function returning a comma-joined ffmpeg video-filter
  string (no leading/trailing comma). `crop=ih*9/16:ih,scale=1080:1920` when `vertical`; hook = centered
  near top (larger, boxed); disclosure = bottom-right small box. Escape drawtext text via a helper that
  backslash-escapes `: \ % '` and newlines. `font` is a resolved `fontfile` path (see R0.2). Return `""`
  when nothing requested.
- [ ] **Step 4 — Run, expect pass.**
- [ ] **Step 5 — Commit:** `git commit -m "feat(reel): pure reel video-filter builder (crop/scale/drawtext)" -- services/local-api/src/laura/render/reel.py services/local-api/tests/test_reel_filters.py`

## Task R0.2 — `render_clips_mp4`: Reel-Filter + Font-Resolution + ffprobe-Test
**Files:** Modify `render/mp4.py`; Create `tests/test_reel_render.py`

- [ ] **Step 1 — Failing integration test** (`tests/test_reel_render.py`): baue ein 1s-Test-Video mit ffmpeg
  (`testsrc 320x240`), rufe `render_clips_mp4([(fixture,0,25)], dest, rate_num=25, rate_den=1, vertical=True,
  hook_text="Hook", disclosure_text="KI")`, dann `ffprobe` → assert `width==1080 and height==1920`. (Skippen,
  wenn ffmpeg/ffprobe nicht auf PATH.)
- [ ] **Step 2 — Run, expect fail** (TypeError: unexpected kwarg `vertical`).
- [ ] **Step 3 — Implement:** Signatur erweitern um `vertical: bool=False, hook_text: str|None=None,
  disclosure_text: str|None=None`. Concat-Label `[out]`→`[vcat]`; wenn `reel_video_chain(...)` nicht leer:
  `parts += f";[vcat]{chain}[out]"`, sonst `parts += ";[vcat]anull?"`→ besser: Concat direkt `[out]` lassen
  und nur bei vorhandener Chain `[vcat]…[out]` einschieben. Font: `_resolve_font()` = `LAURA_FONT` env →
  sonst Bundle/`C:/Windows/Fonts/arialbd.ttf` (Drive-Colon im Filter escapen: `C\:/Windows/...`). Defaults
  off ⇒ unverändertes Verhalten (Backward-Compat).
- [ ] **Step 4 — Run, expect pass** (echter ffprobe: 1080×1920).
- [ ] **Step 5 — Commit** (Pfade `render/mp4.py`, `render/reel.py` wenn Font dort, `tests/test_reel_render.py`).

## Task R0.3 — `exports.options`-Migration + Repos
**Files:** Create `db/migrations/0013_export_options.sql`; Modify `db/repos.py`

- [ ] **Step 1 — Failing test** (in `tests/test_reel_render_api.py` o. repos-Test): `create_export(..., options={"vertical":True,"hook_text":"H"})` dann `get_export` → `options == {...}`.
- [ ] **Step 2 — Run, expect fail.**
- [ ] **Step 3 — Implement:** Migration `ALTER TABLE exports ADD COLUMN options TEXT;` (nullable, additiv,
  SQLite+PG-kompatibel). `create_export(..., options: dict|None=None)` → `json.dumps`. `get_export`/`list_exports`
  → `json.loads(options)` wenn vorhanden, sonst `{}`. Bestehende Aufrufer (deine `render_timeline`) bleiben
  unverändert (options default None).
- [ ] **Step 4/5 — Run pass + commit.**

## Task R0.4 — `handle_render`: Reel-Optionen lesen → durchreichen
**Files:** Modify `render/handlers.py`

- [ ] **Step 1 — Failing test:** Handler-Test (Export mit `options={"vertical":True,"hook_text":"H"}` + 1 Clip)
  → gerendertes MP4 ist 1080×1920 (ffprobe), bzw. Mock auf `render_clips_mp4` prüft die Kwargs.
- [ ] **Step 3 — Implement:** nach `exp = get_export(...)`: `opts = exp.get("options") or {}`; an `render_clips_mp4`
  zusätzlich `vertical=opts.get("vertical", False), hook_text=opts.get("hook_text"), disclosure_text=opts.get("disclosure_text")` übergeben. (music_tracks unverändert.)
- [ ] **Step 4/5 — pass + commit.**

## Task R0.5 — Reel-Render-Endpoint (eigener Router, **nicht** timelines.py)
**Files:** Create `api/reels.py`, `tests/test_reel_render_api.py`; Modify `api/models.py` (+ Router mounten)

- [ ] **Step 1 — Failing API test:** `POST /timelines/{id}/render-reel` mit `{"hook_text":"H","disclosure_text":"KI"}`
  → 202, Body hat `export_id`; `get_export` zeigt `options.vertical==True, options.hook_text=="H"`.
- [ ] **Step 3 — Implement:** `ReelRenderRequest(BaseModel)` { `hook_text: str|None=None`, `disclosure_text: str|None="KI · synthetisch"`, `vertical: bool=True` } in models.py. Neuer Router `api/reels.py`
  (Depends require_token): Endpoint spiegelt `render_timeline`, aber `create_export(format="mp4",
  options={"vertical":body.vertical,"hook_text":body.hook_text,"disclosure_text":body.disclosure_text})` +
  `enqueue("export.render", payload={"export_id":exp["id"]}, idempotency_key=f"render:{exp['id']}")`. Router im
  App-Setup mounten (wo die anderen Router registriert werden — **nicht** in timelines.py).
- [ ] **Step 4/5 — pass + commit.**

## Task R0.6 — Frontend: `renderReel` + ExportView „Reel 9:16"
**Files:** Modify `apps/desktop/src/api.ts`, `apps/desktop/src/components/ExportView.tsx`

- [ ] **Step 1 — `api.ts`:** `renderReel(timelineId, { hookText, disclosureText }): Promise<{export_id:string}>`
  → POST `/timelines/${timelineId}/render-reel`.
- [ ] **Step 2 — `ExportView.tsx`:** neben dem Format-Select ein **„Reel 9:16"**-Button + ein Hook-Text-`input`
  + ein Disclosure-Toggle (Default an). Klick → `client.renderReel(timelineId, {hookText, disclosureText})`
  → `load()`. (Bestehender mp4/otio/edl-Pfad unangetastet.)
- [ ] **Step 3 — Verify:** `npm --prefix apps/desktop run typecheck` clean.
- [ ] **Step 4 — Commit** (Pfade api.ts + ExportView.tsx).

## Task R0.7 — Gesamt-Verifikation
- [ ] `cd services/local-api && uv run pytest -q -k "reel or export or render"` → grün.
- [ ] `npm --prefix apps/desktop run typecheck` → clean.
- [ ] **Echter Reel-Render:** über CDP in ExportView „Reel 9:16" auslösen → `ffprobe` auf das Output-MP4:
  **1080×1920**, Audio vorhanden falls Musik, Hook + Disclosure sichtbar (Screenshot).
- [ ] Doc/`tasks/todo.md` aktualisieren.

---

## Risiken / De-Risk
- **drawtext-Font auf Windows** ist der Hauptstolperstein (ohne `fontfile` schlägt `drawtext` fehl). R0.2
  löst das mit `_resolve_font()` (env → Bundle → System-Arial, Drive-Colon escapen). **Empfohlener erster
  Schritt vor dem Bau:** den exakten Filtergraph `crop=ih*9/16:ih,scale=1080:1920,drawtext=...:fontfile=…`
  einmal real gegen ein Fixture laufen lassen (proof) — dann ist R0.1/R0.2 risikofrei.
- Escaping (`:`, `%`, `'`, Backslash, Newline) im drawtext-Text — durch den Builder + Unit-Test abgedeckt.
- Center-Crop verliert Bildränder; Smart-Reframe ist bewusst **R2**, nicht R0.
