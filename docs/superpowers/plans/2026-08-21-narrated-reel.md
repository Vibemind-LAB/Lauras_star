# Narrated Reel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der Workflow „narrated product reel" (Beat-Liste → Collage-Timeline mit Clone-Voice
und Karaoke-Captions) läuft komplett über Lauras API/MCP.

**Architecture:** Spec [2026-08-21-narrated-reel-design.md](../specs/2026-08-21-narrated-reel-design.md)
— sie ist der bindende Vertrag (Feldnamen, Fehlerfälle, Testliste). Alles dockt an
Bestehendes an: `ai/voiceover_backend.py` (Backends), `ai/handlers.py::handle_voiceover`
(Synthese+Platzierung), `render/captions_source.py` + `render/handlers.py`
(Karaoke-Pfad `opts["captions"]`), Job-Registry, MCP-Server.

**Tech Stack:** Python 3.11, FastAPI, pytest; kein neues Package im Backend
(ElevenLabs via urllib, Whisper nur wenn installiert; Chatterbox als externer Sidecar).

## Global Constraints

- Zeit-Invarianten: Ganzzahl-Frames, end-exclusive Ranges, NDF intern.
- Schwere Modelle bleiben optionale Extras — kein neuer Import-Zwang im Core.
- Kein `print`; projektlokaler Logger. Typing strikt (bare `uv run mypy` ist das Gate,
  nicht `mypy src`). Tests: `uv run pytest`. Lint: `uv run ruff check src tests`.
- API-Keys erscheinen in keiner Log-/Fehlermeldung.
- Conventional Commits, explizite `git add <paths>` (nie `-A`).
- Arbeitsbranch: `feat/generate-ui`.

---

### Task 1: `fit_to_slot`-Protokoll + ElevenLabs-Backend

**Files:**
- Modify: `services/local-api/src/laura/ai/voiceover_backend.py`
- Test: `services/local-api/tests/test_voiceover_backend.py` (existiert; erweitern)

**Interfaces:**
- Produces: `VoiceoverBackend.synthesize(..., fit_to_slot: bool = True)` (keyword-only,
  ans Ende der Signatur); `ElevenLabsVoiceoverBackend` (name `"elevenlabs"`);
  `resolve_voiceover_backend` kennt `{"elevenlabs", "el"}`.

- [ ] Failing Tests: (a) Registry löst `"elevenlabs"`/`"el"` auf; `"auto"` unverändert
      SAPI→Stub. (b) `available()` False ohne `LAURA_ELEVENLABS_API_KEY`, True mit.
      (c) Synthese mit gemocktem `urllib.request.urlopen` (MP3-Bytes-Fixture) + gemocktem
      `run_ffmpeg`: bei `fit_to_slot=True` enthält der ffmpeg-Aufruf `apad` und `-t`, bei
      `False` nicht. (d) HTTPError mit Body `{"detail":{"status":"payment_issue"}}` →
      RuntimeError-Message enthält `payment_issue`, aber NICHT den Key. (e) SAPI mit
      `fit_to_slot=False`: ffmpeg-Args ohne `apad`/`-t` (SAPI-Test-Muster der Datei
      wiederverwenden). (f) Stub ignoriert das Flag (Sample-Count unverändert).
- [ ] Implementierung: Flag in Protokoll + alle fünf Backends (Stub/SAPI/Sidecar/
      Unavailable/EL). Sidecar legt `"fit_to_slot"` ins JSON-Payload. EL: Env-Kontrakt und
      MP3→WAV-Pfad exakt wie in der Spec §1; ffmpeg via `..ingest.ffmpeg.run_ffmpeg`
      (Lazy-Import wie SAPI).
- [ ] `uv run pytest tests/test_voiceover_backend.py -q` grün; Commit
      `feat(voiceover): elevenlabs backend + fit_to_slot protocol`.

### Task 2: Natural-Fit im Voiceover-Job

**Files:**
- Modify: `services/local-api/src/laura/api/models.py` (VoiceoverRequest),
  `services/local-api/src/laura/api/voiceover.py` (Payload-Durchreiche),
  `services/local-api/src/laura/ai/handlers.py` (handle_voiceover)
- Test: `services/local-api/tests/test_ai_voiceover_handler.py` (existierendes
  Handler-Testfile finden; sonst neu)

**Interfaces:**
- Consumes: Task 1 (`fit_to_slot`).
- Produces: `VoiceoverRequest.fit: Literal["slot","natural"] = "slot"`,
  `pad_frames: int = 12` (ge=0, le=120); Refactor
  `_synthesize_voiceover_asset(ctx, *, project, timeline, text, backend_config, language,
  voice_id, duration_frames, fit_to_slot) -> tuple[asset: dict, measured_frames: int,
  out_path: Path]` — kapselt Synthese, Sync-Fix (nur slot-Modus), Asset+Provenance+Probe.
  Job-Ergebnis um `measured_frames` und effektives `seq_out_frame_exclusive` erweitert.

- [ ] Failing Tests (Stub-Backend, In-Memory-DB nach Muster der bestehenden Handler-Tests):
      (a) `fit="natural"`, Stub liefert 45 Frames, pad 12, Slot 300 → Audio-Clip endet bei
      `seq_in+57`; Ergebnis nennt `measured_frames=45`. (b) Sprache länger als Slot →
      Clip endet exakt am Slot-Ende (Obergrenze). (c) `fit="slot"` byte-identisches
      Verhalten zu heute (Regression: bestehende Tests bleiben grün). (d) Overlap-Delete
      läuft mit dem effektiven Span.
- [ ] Implementierung per Spec §3; Frames messen über die vorhandene Probe-Hilfe
      (`assert_or_fix_media_sync`-Umfeld nutzt ffprobe — gleiche Quelle verwenden,
      aufrunden). `fit`/`pad_frames` durch Endpoint-Payload und Idempotenz-Key führen.
- [ ] Volle Testdatei + betroffene Bestandstests grün; Commit
      `feat(voiceover): natural-length fit derives the clip span from speech`.

### Task 3: Wort-Timing-Sidecar

**Files:**
- Create: `services/local-api/src/laura/ai/vo_words.py`
- Modify: `services/local-api/src/laura/ai/handlers.py` (Aufruf nach Synthese)
- Test: `services/local-api/tests/test_vo_words.py`

**Interfaces:**
- Produces: `write_word_sidecar(wav_path: Path, *, text: str, measured_frames: int,
  rate_num: int, rate_den: int, language: str | None) -> Path | None` — schreibt
  `<wav>.words.json` (Schema Spec §4), nie raising (Warnung + None bei Fehlern);
  `authored_words(text: str) -> list[str]`; `map_words_to_slots(words, slots) -> list[...]`
  (1:1 / proportional).

- [ ] Failing Tests: (a) authored_words entfernt Einzel-`-`-Token, behält Interpunktion am
      Wort. (b) Mapping 1:1 und proportional (n_a≠n_w) — Formel `min(n_w-1, j*n_w//n_a)`.
      (c) Whisper gemockt (monkeypatch eines Modul-Level-Loaders) → Sidecar
      `source:"whisper"`, Frames aus Timings. (d) Whisper-Import schlägt fehl →
      `source:"even"`, Slots gleichverteilt über `measured_frames`. (e) Exception im
      Whisper-Pfad → even-Fallback, kein Raise.
- [ ] Implementierung; Handler-Aufruf in `_synthesize_voiceover_asset` (beide Fit-Modi).
- [ ] Grün; Commit `feat(voiceover): word-timing sidecar for narration captions`.

### Task 4: Caption-Quelle Voiceover + Render-Wiring

**Files:**
- Modify: `services/local-api/src/laura/render/captions_source.py`,
  `services/local-api/src/laura/render/handlers.py`,
  `services/local-api/src/laura/api/models.py` (RenderRequest),
  `services/local-api/src/laura/api/timelines.py` (Options-Durchreiche)
- Test: `services/local-api/tests/test_captions_source.py` (erweitern),
  Render-Handler-Test (bestehendes File erweitern)

**Interfaces:**
- Consumes: Sidecar-Schema aus Task 3.
- Produces: `voiceover_caption_words(db, timeline_id) -> list[Word]`;
  RenderRequest-Felder `captions/caption_source/caption_preset` (Spec §5);
  Render-Handler wählt bei `captions` die Quelle nach `caption_source` (auto: VO-Wörter
  falls nicht leer, sonst Transkript); Preset `wide` → fontsize 54, margin_v 48 Defaults.

- [ ] Failing Tests: (a) VO-Wörter: Mapping `seq_in + (start - asset_in)`, Clipping auf den
      Clip-Span, Ordnung über mehrere Clips. (b) Clip ohne Sidecar wird übersprungen.
      (c) Render-Handler `captions=True, caption_source=auto`: mit VO-Sidecar entsteht
      `caption_ass` aus Autoren-Wörtern; ohne Sidecar Fallback Transkript-Pfad
      (bestehende Fixture). (d) Endpoint reicht die drei Felder in `options` durch.
- [ ] Implementierung per Spec §5.
- [ ] Grün; Commit `feat(render): narration captions from voiceover word sidecars`.

### Task 5: Collage-Builder `ai.narrated_reel`

**Files:**
- Create: `services/local-api/src/laura/api/narrated_reel.py` (Router; in `app.py`
  registrieren — Muster der anderen Router)
- Modify: `services/local-api/src/laura/ai/handlers.py` (Handler + Registry),
  `services/local-api/src/laura/api/models.py` (Request/Accepted-Modelle)
- Test: `services/local-api/tests/test_narrated_reel.py`

**Interfaces:**
- Consumes: `_synthesize_voiceover_asset` (Task 2), Sidecar (Task 3), Render-Options
  (Task 4).
- Produces: `POST /projects/{project_id}/narrated-reel` → 202
  `{"timeline_id": str, "job_id": str}`; Job-Kind `ai.narrated_reel` (Queue `ai`),
  Ergebnis `{"beats": [{"clip_id","voice_asset_id","measured_frames","seq_in","seq_out"}],
  "export_id": str | None, "warnings": [str]}`; Request-Modell exakt Spec §6.

- [ ] Failing Tests: (a) Endpoint-Validierung: fremdes Asset → 422, leere Beats → 422,
      unbekanntes Projekt → 404; Erfolg legt Timeline an (kind bleibt Standard) und
      enqueued den Job. (b) Handler mit Stub-Backend, 3 Beats über 2 Fixture-Assets:
      Clips in Reihenfolge mit `src_out = src_in + measured + pad`, Crossfade 8 auf Clips
      0..n-2, Fade 12 auf letztem, drei VO-Audio-Clips `replace_original` mit
      Ducking 100/Fades 3/4, Sidecars existieren. (c) `render=True` → Export angelegt +
      `export.render` enqueued mit `captions/caption_source=voiceover/caption_preset`.
      (d) Beat über Asset-Ende → Clip endet am Asset-Ende + Warning. (e) Cancel zwischen
      Beat 1 und 2 → sauberer Abbruch (`status:"cancelled"`), keine halbe Voice-Spur.
- [ ] Implementierung per Spec §6 (repos-Ebene für append/transition wie der
      Operations-Endpoint; ein `timeline_checkpoint` um den gesamten Aufbau).
- [ ] Grün; Commit `feat(api): narrated-reel collage builder endpoint + job`.

### Task 6: MCP-Tool `build_narrated_reel`

**Files:**
- Modify: `services/local-api/src/laura/mcp/tools.py`,
  `services/local-api/src/laura/mcp/server.py`
- Test: `services/local-api/tests/test_mcp_tools.py` (Muster bestehender Tool-Tests)

**Interfaces:**
- Consumes: Endpoint aus Task 5 (Tool ruft die HTTP-API wie die übrigen Tools).
- Produces: Tool `build_narrated_reel(project_id, beats, name=None, backend=None,
  voice_id=None, language=None, render=True, caption_preset="wide", crossfade_frames=8,
  final_fade_frames=12, pad_frames=12)`; Docstring mit Beat-JSON-Beispiel und dem Hinweis
  „src-Fenster vorab frame-index-verifizieren (get_frame), nie Zeit-Seek".

- [ ] Failing Test: Tool-Registrierung + Durchreiche an den (gemockten) HTTP-Client;
      Fehlerpfad gibt LauraError-Format zurück (Muster der anderen Tools).
- [ ] Implementierung; Server-Registrierung + Tool-Count-Assertions der Tests anpassen.
- [ ] Grün; Commit `feat(mcp): build_narrated_reel tool`.

### Task 7: Chatterbox-Sidecar

**Files:**
- Create: `services/tts-sidecar/chatterbox_sidecar.py`, `services/tts-sidecar/README.md`

**Interfaces:**
- Produces: Sidecar-Kontrakt `GET /healthz` → 200; `POST /voiceover`
  (JSON: text, duration_frames, fps_num, fps_den, sample_rate, language, voice_id,
  fit_to_slot) → `audio/wav`-Bytes. Env: `CHATTERBOX_VOICE_REF`, `CHATTERBOX_DEVICE`
  (Default cuda), `HF_HOME`-Respekt. Kein Import in den Backend-Code.

- [ ] Implementierung: stdlib `http.server` (ThreadingHTTPServer), Lazy-Model-Load beim
      ersten Request, `voice_id` überschreibt `CHATTERBOX_VOICE_REF`-Pfad,
      Resample via ffmpeg-Subprozess auf mono `sample_rate`, `--port`-Arg (Default 8898).
      Fehler → 500 mit Klartext-Body.
- [ ] Kein Test-Gate im Backend (eigenes venv); README mit Start-Rezept und
      `LAURA_VOICEOVER_BACKEND=sidecar`-Kopplung. Smoke manuell in Task 9.
- [ ] Commit `feat(tts-sidecar): chatterbox voiceover sidecar`.

### Task 8: Härtung

**Files:**
- Modify: `services/local-api/src/laura/editing/history.py` (IMMEDIATE-Tx im
  Checkpoint-Schreibpfad, Muster Commit ae01228), `services/local-api/src/laura/api/models.py`
  (ProjectCreate-Defaults 30/1/False)
- Test: bestehende History-/Projects-Testfiles erweitern

- [ ] Failing Tests: (a) Twenty parallele `timeline_checkpoint`-Writer (Threads) ohne
      `database is locked` (Regressionstest nach dem Muster des ae01228-Tests).
      (b) `POST /projects` mit nur `{"name"}` → 201 mit 30/1/False.
- [ ] Implementierung.
- [ ] Grün; Commit `fix(history,api): immediate checkpoint tx + project rate defaults`.

### Task 9: Final Review + Live-Verifikation

- [ ] Gates: bare `uv run mypy`, `uv run pytest`, `uv run ruff check src tests`.
- [ ] Whole-Branch-Review (superpowers:requesting-code-review) über a41fa27..HEAD;
      Critical/Important fixen.
- [ ] Live: Chatterbox-Sidecar starten, Laura-Produktvideo-Beats (Projekt 82f9595f…,
      7 Beats aus dem Scratch-Build) über `build_narrated_reel` mit
      `backend="sidecar"`, `render=True` — Export prüfen: 7 Clips, Crossfades,
      Captions eingebrannt, Stimme = Clone. Beat-Mitten-Frames stichprobenartig
      gegen die Zeilen (Frame-Index-Rezept).
- [ ] lessons.md/Memory aktualisieren; Push nach Ansage.
