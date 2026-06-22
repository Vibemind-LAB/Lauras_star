# R3-C — Reenact-Skelett — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Checkbox-getrackt.
> **Kollisions-Regeln:** Pathspec-Commits nur mit Pfad; **niemals `api/timelines.py`, `RoughCutView.tsx`,
> `FineCutView.tsx`, `Player.tsx`, `analysis/refine.py`, `shots.py`**; uv.lock/.claude/build nie stagen;
> Subagenten committen NICHT. Design: `docs/superpowers/specs/2026-06-09-r3c-reenact-design.md`.

**Goal:** Konsentierter, gekennzeichneter Reenact-Job mit **Stub-Backend** (dep-frei): Driving-Range +
Ziel-Portrait → synthetisches Asset → als Replace-Overlay platziert. LivePortrait dockt später als Adapter.

**Architecture:** `ai.reenact`-Job, hart gegated über `consent_records`; pluggbares `ReenactBackend`
(Stub default); Platzierung über die fertige Replace-Overlay-Primitive (`add_timeline_clip role='replace'`).
Frame-genaue Black-Box-Grenze (Range+fps rein, MP4 raus, re-probe).

**Tech Stack:** Python 3.11/uv/pytest/ffmpeg · SQLite-Migration · FastAPI · React/TS · Verifikation:
`uv run pytest` + echter ffprobe + tsc.

**Verifizierte Anker:** `repos.add_timeline_clip(..., lane, role)` (RL1), `resolve_clip_rows` (RL3),
`render_clips_mp4(clips,dest,*,rate_num,rate_den,…)`, `create_asset(db,*,project_id,type,display_name,source_path,…)`,
JobRunner/`enqueue`/`register_*_handlers`, Router-Muster `api/overlays.py`/`api/reels.py` (mount in `main.py`).

---

## File Structure
- **Create:** `db/migrations/0015_synthetic_consent.sql` · `src/laura/ai/__init__.py` ·
  `src/laura/ai/reenact_backend.py` (Protocol + Stub + resolver) · `src/laura/ai/handlers.py`
  (`handle_reenact` + register) · `src/laura/api/reenact.py` (Router) · `apps/desktop/src/components/ReenactPanel.tsx` ·
  Tests: `test_consent_repos.py`, `test_reenact_backend.py`, `test_reenact_job.py`, `test_reenact_api.py`
- **Modify (meins):** `db/repos.py` (consent CRUD + `create_asset(synthetic,ai_effect)` + `set_asset_synthetic`) ·
  `api/models.py` · `main.py` (Router + Handler-Registrierung) · `apps/desktop/src/api.ts` · `AssembleView.tsx`

---

## Task RC1 — Migration `synthetic`/`ai_effect` + `consent_records` + Repos
**Files:** Create `0015_synthetic_consent.sql`; Modify `db/repos.py`; Create `tests/test_consent_repos.py`

- [ ] **Step 1 — Failing test:** `create_consent_record(db, project_id=…, subject_label="Anna", confirmed_by="user")`
  → `get_consent_record` liefert es (subject_label, confirmed_at gesetzt). `create_asset(..., synthetic=True, ai_effect="reenact")`
  → `get_asset` zeigt `synthetic==1`/truthy + `ai_effect=="reenact"`. Default-Asset → `synthetic` falsy, `ai_effect` None.
- [ ] **Step 2 — Run, expect fail.**
- [ ] **Step 3 — Implement:** Migration:
  `ALTER TABLE media_assets ADD COLUMN synthetic INTEGER NOT NULL DEFAULT 0;`
  `ALTER TABLE media_assets ADD COLUMN ai_effect TEXT;`
  `CREATE TABLE consent_records (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, subject_label TEXT NOT NULL,
   source_asset_id TEXT, confirmed_by TEXT, confirmed_at TEXT NOT NULL, note TEXT);`
  Repos: `create_consent_record`, `get_consent_record`, `list_consent_records(project_id)`;
  `create_asset(..., synthetic: bool=False, ai_effect: str|None=None)` (INSERT erweitern, Defaults additiv);
  `set_asset_synthetic(db, asset_id, ai_effect)`. `get_asset` liefert die Spalten via SELECT *.
- [ ] **Step 4/5 — pass + commit.**

## Task RC2 — Backend-Interface + Stub + Resolver
**Files:** Create `src/laura/ai/__init__.py`, `src/laura/ai/reenact_backend.py`, `tests/test_reenact_backend.py`

- [ ] **Step 1 — Failing test:** baue ein 1s-Driving-Fixture (ffmpeg testsrc). `b = resolve_reenact_backend("stub")`;
  `b.available() is True`; `b.reenact(driving_path=fix, portrait_path=fix, out_path=out, fps_num=25, fps_den=1)`;
  ffprobe(out) → existiert, hat Video, ~gleiche Framezahl wie Driving.
- [ ] **Step 3 — Implement:** `class ReenactBackend(Protocol)` (`name`, `available()`, `reenact(*, driving_path,
  portrait_path, out_path, fps_num, fps_den) -> None`). `StubReenactBackend`: `available()->True`; `reenact` =
  `run_ffmpeg(["-i", driving, "-vf", "drawtext=fontfile=<resolve_font>:textfile=<basename>:…,
  eq=saturation=0.4", "-r", f"{fps_num}/{fps_den}", "-c:v","libx264","-pix_fmt","yuv420p", out], cwd=…)` — Text
  „REENACT (stub)" via textfile/cwd (R0.8-Mechanik), Längen-erhaltend, sichtbar synthetisch. `resolve_reenact_backend(name|None)`:
  env `LAURA_REENACT_BACKEND` → sonst Arg → Default `"stub"`; unbekannt/`"liveportrait"` ohne Extra →
  ein `UnavailableBackend` mit `available()->False` (kein Import schwerer Deps).
- [ ] **Step 4/5 — pass + commit.**

## Task RC3 — `ai.reenact`-Job (Consent-Gate → Driving-Render → Backend → synthetic Asset → Overlay)
**Files:** Create `src/laura/ai/handlers.py`, `tests/test_reenact_job.py`; Modify `main.py` (Handler registrieren)

- [ ] **Step 1 — Failing tests:** (a) Job mit fehlendem/ungültigem `consent_id` → Export/Job-Status Fehler, KEIN Asset
  erstellt. (b) Stub-e2e (echtes ffmpeg): Projekt+Asset+Szene-Timeline mit Base-Clip[0,30); Consent anlegen; Job
  `{timeline_id, seq_in_frame:5, seq_out_frame_exclusive:20, portrait_asset_id, consent_id, backend:"stub"}` →
  drain → ein neues Asset mit `synthetic` truthy + `ai_effect=="reenact"` existiert UND ein `role='replace'`-Clip
  auf `lane>=1` deckt seq[5,20); `resolve_clip_rows(tl)` enthält das synthetische Asset in der Range.
- [ ] **Step 3 — Implement `handle_reenact(ctx, payload)`:**
  1. `consent = get_consent_record(consent_id)`; if None → raise (Job failed), nichts anlegen.
  2. Driving extrahieren: `rows = resolve_clip_rows(db, timeline_row)`; die Base-Clips, die [seq_in,seq_out) überlappen,
     auf ihre src-Range beschneiden (gleiche Mapping-Formel wie overlays) → `render_clips_mp4(driving_clips, driving_tmp,
     rate_num, rate_den)` → driving.mp4 (Länge = seq_out-seq_in).
  3. `backend = resolve_reenact_backend(payload.get("backend"))`; if not `available()` → raise „backend not installed".
  4. `backend.reenact(driving_path=driving_tmp, portrait_path=get_asset(portrait_id).source_path, out_path=out, fps_num, fps_den)`.
  5. `ffprobe(out)`; Asset-Datei in den Workspace legen; `create_asset(..., synthetic=True, ai_effect="reenact")`.
  6. `add_timeline_clip(db, timeline_id=…, asset_id=out_asset, src_in_frame=0, src_out_frame_exclusive=(seq_out-seq_in),
     seq_in_frame=seq_in, seq_out_frame_exclusive=seq_out, lane=1, role="replace")`.
  Register als `ai.reenact` in `main.py`/Job-Registry (wo `register_render_handlers` etc. registriert werden).
- [ ] **Step 4/5 — pass + commit.**

## Task RC4 — Consent- + Reenact-API
**Files:** Create `src/laura/api/reenact.py`, `tests/test_reenact_api.py`; Modify `api/models.py`, `main.py`

- [ ] **Step 1 — Failing API test:** `POST /projects/{id}/consent {subject_label}` → 201 + id. `POST /timelines/{id}/reenact`
  ohne `consent_id` → 422; mit gültigem → 202 + `job_id`; `get_consent_record` zeigt den Record.
- [ ] **Step 3 — Implement:** Models `ConsentRequest{subject_label, source_asset_id?, note?}`,
  `ReenactRequest{seq_in_frame, seq_out_frame_exclusive, portrait_asset_id, consent_id, backend?}`. Router `api/reenact.py`:
  POST consent → `create_consent_record`; POST reenact → validiert Timeline/Consent existieren (404) →
  `enqueue("ai.reenact", payload, idempotency_key=f"reenact:{…}")`. Mount in `main.py` (nicht timelines.py).
- [ ] **Step 4/5 — pass + commit.**

## Task RC5 — Frontend: ReenactPanel
**Files:** Modify `apps/desktop/src/api.ts`, `AssembleView.tsx`; Create `apps/desktop/src/components/ReenactPanel.tsx`

- [ ] **Step 1 — api.ts:** `createConsent(projectId, {subjectLabel})` , `reenact(timelineId, {seqIn, seqOut, portraitAssetId, consentId})`.
- [ ] **Step 2 — ReenactPanel.tsx:** Driving-Range (Integer-Frame-Inputs, `Math.trunc`+`step=1`), Portrait-Asset-`<select>`,
  Subject-Label-Input + „Consent bestätigen" → createConsent → dann „Reenact (stub)" → reenact → onChange. Fehler via Projekt-Logger.
  Hinweis „LivePortrait nicht installiert — Stub-Ausgabe". In AssembleView einbinden (neben OverlayControls).
- [ ] **Step 3 — Verify:** tsc clean.
- [ ] **Step 4 — Commit.**

## Task RC6 — Gesamt-Verifikation / Eval (Skelett-Stufe)
- [ ] `uv run pytest -q` grün (inkl. consent-gate + stub-e2e). `npm --prefix apps/desktop run typecheck` clean.
- [ ] Stub-Reenact e2e real: Job → synthetic Asset + Replace-Overlay → Render zeigt Stub in der Range (ffprobe).
- [ ] `tasks/todo.md` + Doku. **STOPP an der LivePortrait-Wand** (Adapter-Tausch, User-Install).

## Task RC7 — LivePortrait-Sidecar-Adapter
- [x] `LivePortraitBackend` implementiert den bestehenden `ReenactBackend`-Contract ohne schwere Imports:
  `GET /healthz` prüft Verfügbarkeit; `POST /reenact` sendet multipart `driving`, `portrait`,
  `fps_num`, `fps_den` und schreibt die MP4-Bytes nach `out_path`.
- [x] Resolver: `backend:"liveportrait"` / `LAURA_REENACT_BACKEND=liveportrait` liefert den Adapter;
  URL via `LAURA_LIVEPORTRAIT_URL` (Default `http://127.0.0.1:8899`), Timeout via
  `LAURA_LIVEPORTRAIT_TIMEOUT`.
- [x] UI: `ReenactPanel` bietet Backend-Auswahl `Stub`/`LivePortrait Sidecar` und reicht `backend`
  an `client.reenact` weiter.
- [x] Tests: fake HTTP-Sidecar für Healthcheck + Render-POST, Renderer-Test für Backend-Auswahl.
- [ ] **Offen extern:** echter LivePortrait-Sidecar-Prozess + Modellgewichte installieren/starten.

---

## Risiken / De-Risk
- **Consent-Gate** ist der Kern — Test (a) erzwingt: ohne Consent kein Asset, kein Overlay.
- **Driving-Extraktion** über `render_clips_mp4` (bestehend) + Overlay-Mapping-Formel; v1 erwartet Base-Clips unter der Range.
- **Keine schweren Deps:** Stub nutzt nur ffmpeg; `LivePortraitBackend` spricht nur HTTP und importiert
  weder torch noch LivePortrait. Der echte Modellprozess lebt im Sidecar.
- **Kennzeichnung:** `synthetic=true` + R0-Burn-in beim Export; Pixel-Wasserzeichen = späteres Teil.
