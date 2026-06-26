# Analyse-Modelle in GPU-Container auslagern (Design)

_Datum: 2026-06-26 · Status: Entwurf, wartet auf Review_

## Ziel

Die schweren Analyse-Modelle (ASR/Whisper, Szenenerkennung/TransNetV2, visuelle
Embeddings/CLIP) laufen heute **in-process** im lokalen FastAPI-Backend auf der **Host-CPU**.
Auf einer ausgelasteten Maschine (parallele Container-Stacks + ComfyUI) führt das zum
beobachteten Absturz `RuntimeError: mkl_malloc: failed to allocate memory` — die ASR scheitert
still, das Transkript fehlt.

Dieses Design lagert die Inferenz in **einen gemeinsamen GPU-Container** aus, den das Backend
per HTTP anspricht. Das behebt den OOM, beschleunigt die ASR um ~5–15× (GPU float16 statt
CPU int8) und vollendet das bereits etablierte Sidecar-Muster (Voice/Reenact/Lipsync laufen
schon als Container, der VLM schon remote via Ollama).

## Nicht-Ziele

- **Kein** Umbau des Electron-Startflows: das Backend bleibt ein Host-Prozess, der vom
  Electron-Main gespawnt wird. Nur die Modell-Inferenz wandert in den Container.
- **Kein** Eingriff in den `services/ai-runtimes/`-Subtree (Voice/Reenact/Lipsync) — dieser
  wird von einer parallelen Session gepflegt und bleibt für dieses Vorhaben **read-only**.
- **Kein** Zwang zur GPU: ohne Container/GPU muss alles wie heute weiterlaufen.

## Invarianten (nicht verhandelbar)

1. **Backend startet und arbeitet ohne Container.** Ist der Worker nicht erreichbar, fällt jede
   Stufe auf den heutigen In-Process-Pfad zurück (ASR/Scene) bzw. überspringt sauber (Embed).
   Heavy-Models bleiben optionale Extras.
2. **Determinismus bleibt erhalten** (temperature=0, fixe Seeds), damit der Cache-/Idempotenz-
   Schlüssel `(input, pipeline_version)` gültig bleibt — egal ob in-process oder im Container
   gerechnet wurde. Das Containerergebnis muss formgleich zum In-Process-Ergebnis sein.
3. **Zeit-/Sample-Invarianten unberührt:** der Container liefert dieselben Datenstrukturen
   (`SegmentResult`/`WordResult` in Sekunden; Boundaries als Frames; Vektoren als float-Listen).
   Das Mapping auf Frames/Samples passiert weiterhin im Backend (`mapping.py`).

## Architektur

```
Electron ── spawnt ──▶ local-api (Host-Prozess, :8765)
                          │
                          │  analysis.run Job
                          ▼
                    Analyse-Stufen
              scene │ asr │ embed
                          │   resolve_*_backend()  (env-gated, health-checked)
              ┌───────────┴───────────┐
        Container gesund?          sonst
              │ HTTP (JSON)          │ In-Process (heute) / skip
              ▼                      ▼
   laura-analysis-runtime      faster-whisper / transnet / fastembed
   (GPU, :8896)                im Backend-Prozess
   /transcribe /scenes /embed /healthz
```

## Komponenten

### 1. Container-Service `services/analysis-runtime/` (neu, eigener Subtree)

Ein kleiner GPU-Worker, **getrennt von `services/ai-runtimes/`** (keine Kollision mit der
Parallel-Session; das Muster wird gespiegelt, nicht geteilt).

- **`server.py`** — FastAPI-App, Endpoints:
  - `POST /transcribe` — Body: WAV-Bytes (oder Pfad im gemounteten Workspace) + `{model_size,
    language}` → `{segments:[{text,start_sec,end_sec,confidence,words:[…]}]}`.
  - `POST /scenes` — Body: Proxy-Video (Pfad) → `{boundaries:[frame,…], detector}`.
  - `POST /embed` — Body: Frame-Bilder (PNG-Bytes oder Pfade) → `{vectors:[[float,…],…], dim}`.
  - `GET /healthz` — `{status:"ok", device, models_loaded:[…], vram_free_mb}`.
- **Lazy-Load + LRU-Unload:** Modelle werden bei erstem Gebrauch geladen und unter VRAM-Druck
  wieder freigegeben (Budget ~6.9 GB frei auf der geteilten RTX 3060). Da die Stufen pro
  Analyse-Lauf **sequenziell** laufen, genügt ein residentes Modell zur Zeit.
- **`Dockerfile`** — Basis `nvidia/cuda:12.x-base` (vorhanden), installiert faster-whisper,
  transnetv2-pytorch, fastembed; Modelle/Weights read-only gemountet (`/models`), Workspace
  gemountet (`/workspace`) für Datei-I/O statt großer HTTP-Bodies.
- **`deploy/analysis-runtime/compose.yml`** — Service mit `gpus: all` (nvidia-Default-Runtime
  ist gesetzt), `restart: unless-stopped`, Healthcheck auf `/healthz`, Port `8896`.

### 2. Backend-Adapter `services/local-api/src/laura/analysis/sidecar.py` (neu)

Spiegelt das vorhandene `SidecarVoiceoverBackend`-Muster (`ai/voiceover_backend.py`, stdlib
`urllib`, Timeout, klare Fehler):

- `SidecarAsrBackend.transcribe(audio_path, *, model_size, language) -> list[SegmentResult]`
- `SidecarSceneBackend.detect(proxy_path) -> list[int]`  _(Phase 2)_
- `SidecarEmbedBackend.embed(frame_paths) -> list[list[float]]`  _(Phase 3)_
- `resolve_asr_backend()` → Sidecar (wenn `LAURA_ANALYSIS_URL` gesetzt **und** `/healthz` ok),
  sonst der heutige In-Process-Pfad, sonst Stub. Health wird kurz gecached.

### 3. Call-Sites in `handlers.py` (geteilt — vorsichtig, explizite Commits)

- `_run_transcript` (Zeile ~285): `transcribe(...)` → `resolve_asr_backend().transcribe(...)`.
- `_run_scene` (Zeile ~172/178): analog _(Phase 2)_.
- `handle_embed_frames`: analog _(Phase 3)_.
Jeweils mit Timeout → Fallback. Bestehende Diagnostics (`diagnostics["asr"]` etc.) bleiben,
ergänzt um `"backend": "sidecar"|"in_process"|"skipped"`.

### 4. Config

`services/local-api/src/laura/config.py` (`Settings`) + Env:
- `LAURA_ANALYSIS_URL` (z.B. `http://127.0.0.1:8896`) — leer ⇒ Sidecar aus (Default).
- `LAURA_ANALYSIS_TIMEOUT` (Sekunden, Default großzügig für lange Audios).
- Bestehende `LAURA_ASR_DEVICE`/`LAURA_ASR_VAD` bleiben für den In-Process-Fallback gültig.

## Datenkontrakte

Die JSON-Form spiegelt exakt `analysis/types.py` (`SegmentResult`, `WordResult`), damit
In-Process- und Sidecar-Pfad denselben Code zur Persistenz nutzen. Audio/Frames werden bevorzugt
als **Pfade im gemounteten Workspace** übergeben (kein Megabyte-HTTP-Body), Fallback: Bytes.

## Robustheit & Performance

- **Performance:** GPU-Offload ist der Hauptgewinn (kein Host-RAM-Wettbewerb, float16). Ein
  Worker teilt einen CUDA-Context; Lazy-Load/Unload hält das VRAM-Budget ein.
- **Robustheit:** Eine Fehlerdomäne, ein Healthcheck, `restart: unless-stopped`. Der
  In-Process-Fallback ist die eigentliche Ausfallsicherung — fällt der Container aus oder
  antwortet zu langsam (Timeout), läuft die Analyse lokal weiter. Getrennte Container brächten
  hier keinen Mehrwert (Stufen laufen sequenziell), kosteten aber mehr VRAM und Ops-Fläche.

## Inkrementeller Rollout

1. **Phase ASR** (fixt den OOM jetzt): Container-Service-Gerüst + `/transcribe` + `/healthz`,
   `SidecarAsrBackend`, `resolve_asr_backend`, Call-Site-Swap, Config, compose. Einzeln lauffähig.
2. **Phase Scene:** `/scenes` + `SidecarSceneBackend` + Call-Site.
3. **Phase Embed:** `/embed` + `SidecarEmbedBackend` + Call-Site.
4. **Optional später:** WhisperX-Align / pyannote-Diarize als weitere Endpoints.

## Tests

- **Adapter-Unit-Tests** (Mock-HTTP): Erfolg, Timeout→Fallback, Container-down→Fallback,
  formgleiche `SegmentResult`-Rückgabe.
- **Healthcheck-Gating-Test:** `LAURA_ANALYSIS_URL` gesetzt aber `/healthz` rot ⇒ In-Process.
- **Smoke-Test gegen den Container im Stub-Modus** (deterministische Dummy-Ausgabe), damit CI
  ohne GPU grün bleibt.
- **Bestehende Analyse-Tests** laufen unverändert über den Fallback-Pfad weiter.
- **Verifikation:** `uv run pytest` (Backend) + ein echter `/transcribe`-Lauf gegen den
  GPU-Container mit dem vorhandenen Talking-Head-Fixture (Transkript erscheint, kein OOM).

## Koordination

- Neuer Subtree `services/analysis-runtime/` + `deploy/analysis-runtime/` → **keine** Berührung
  von `services/ai-runtimes/`, `ai/runtime_*`, `api/ai_runtimes.py`.
- `handlers.py`/`config.py` sind geteilt → minimale, gezielte Änderungen, **explizite
  `git add <paths>`**, kleine Commits. `uv.lock` nicht committen.

## Offene Punkte (vor/in der Planung zu klären)

- Default-Whisper-Modellgröße im Container (`base` für VRAM-Schonung vs. `large-v3` für Qualität).
- Frame-Übergabe für `/embed`: Pfade (gemeinsamer Workspace-Mount) vs. Bytes — Pfade bevorzugt.
- Lifecycle: compose-manuell (`docker compose up`) für MVP; spätere Auto-Verwaltung wäre Sache
  des codex-eigenen `runtime_manager` und damit außerhalb dieses Subtrees.
