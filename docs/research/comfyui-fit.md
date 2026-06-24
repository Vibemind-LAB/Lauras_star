# Erweiterung: ComfyUI als generatives AI-Backend für Laura

- **Datum:** 2026-06-23
- **Status:** Recherche / geplante Erweiterung (kein Code)
- **Frage:** Passt ComfyUI (`Comfy-Org/ComfyUI`, GPL-3.0) als generatives Backend in Laura, und wofür?

## Urteil

**Starker, sauberer Fit — aber als *optionaler externer HTTP-Runtime*, niemals gebündelt.** Lauras
Architektur ist praktisch dafür gebaut: es existiert bereits eine vollständige zweischichtige
Fremd-Engine-Abstraktion, und ComfyUIs einzige saubere Einbindungsart (Out-of-Process über HTTP)
deckt sich exakt mit Lauras Mustern und Invarianten.

## Warum es passt (die Steckdose existiert schon)

- **AI-Runtime-Registry** (`ai_runtimes`, Migration `0025_ai_runtime_registry.sql`): DB-Zeile + REST-CRUD
  (`api/ai_runtimes.py`) + Docker-Lifecycle (`ai/docker_runtime.py`, `--gpus all`) + Health/Capabilities-
  Vertrag (`ai/runtime_manager.py`). Kinds: `stub | external_http | container`. ComfyUI = eine
  `external_http`-Zeile (`base_url=http://127.0.0.1:8188`) oder `container`.
- **Per-Effekt-Backend-Muster** (`ai/{reenact,lipsync,voiceover}_backend.py`): `Protocol` + Resolver +
  Adapter, Transport via **stdlib `urllib`** (keine neue Dependency). Ein `ComfyUIBackend` ist ein
  Resolver-Branch + eine Klasse.
- **Optional-Extras-Invariante** (CLAUDE.md): kein Top-Level-Heavy-Import, `available()`-via-`/healthz`-
  Probe, **Stub-Fallback** → Backend startet ohne GPU/Modelle.
- **Job-System** (`jobs/runner.py`): Handler dürfen minutenlang blockieren (Auto-Heartbeat,
  `max_runtime` 3600s) — passt zur GPU-gebundenen Generierung.
- **Präzedenz**: Der Ollama-VLM-Opt-in (`LAURA_VLM_MODEL`/`LAURA_VLM=1`, sonst deaktiviert;
  `analysis/transition_review.py:361`) ist exakt das „off-by-default, env-getoggelt, local-first" Muster.

## Wo es andockt (Fit-Rating)

| Slot | Heute | ComfyUI-Fit |
|---|---|---|
| **`reenact`** (Portrait-/Performance-Reenactment) | **Stub** (Drawtext-Platzhalter); „echt" nur via nicht-gebündeltem LivePortrait-Sidecar | **HOCH** — der eine echte Video-Gen-Slot; Plumbing (synthetic Asset → Provenance → Lane-1-`replace`-Clip → Consent-Gate) existiert schon. Vertrag (driving-MP4 + Portrait → MP4) mappt sauber. |
| **Neue B-Roll / Title-Cards / Stills** | **existiert gar nicht** (Poster/Thumbs sind ffmpeg-Frame-Grabs) | **HOCH (Greenfield)** — ComfyUIs Kernstärke (Bild + Video). Höchster *einzigartiger* Mehrwert, aber neuer Effekt + UI nötig. |
| **`lipsync`** | Stub; echt via Wav2Lip/MuseTalk-Sidecar | **MITTEL** — machbar, aber dedizierter Wav2Lip-Sidecar ist der klassischere Host. |
| **`transition_review`** (VLM) | Real-but-off (Ollama `qwen3-vl`) | **KEINE** — Vision-*Language*-Urteil, kein Pixel-Gen. |
| **`voiceover`** (TTS) | SAPI/Stub | **KEINE** — Audio, außerhalb ComfyUIs Scope. |

## Wie (kleinste Änderung, passt aufs vorhandene Muster)

1. Effekt zu `RuntimeEffect` (`api/models.py:895`) — `faceswap`/`restore` sind reserviert, sonst `generate`.
2. ComfyUI-Runtime-Zeile registrieren (`POST /ai/runtimes`, `external_http` @ :8188, oder `container`).
3. `ComfyUIBackend` (stdlib `urllib`): `available()` via `/system_stats`, generieren via
   **`POST /prompt` → Poll `/history/{id}` → Fetch `/view`**, mit **vorab-authorten, versions-gepinnten
   API-Format-Workflows** (nur variable Inputs pro Job patchen). Plus Stub-Backend für den GPU-freien
   Pfad + Resolver-Branch.
4. `handle_<effekt>` in `ai/handlers.py` via `_backend_config_from_runtime(...)`, registrieren (`:319`),
   Kind in `jobs/queues.py:24` auf `analysis.gpu` routen.
5. ComfyUI **nicht** in `pyproject.toml` — nur über HTTP erreicht.

## ComfyUI — technische Eckdaten

- **Modalitäten (nativ):** Bild (SDXL, Flux/Flux 2, Qwen Image, …), **Video** (LTX-Video, Hunyuan Video,
  Wan 2.1/2.2, SVD, Mochi), Audio (Stable Audio, ACE Step), 3D (Hunyuan3D).
- **API:** langlaufender HTTP-Server (`:8188`). `POST /prompt` (Workflow-JSON im „API-Format") →
  `GET /history/{id}` → `GET /view`. Websocket `/ws` für Progress/Preview. `GET /object_info` =
  Node-Schema (zur Validierung). Kein offizielles pip-SDK → dünner `urllib`-Client.
- **API-Stabilität:** Endpunkte stabil; **Workflow-JSON-Format ohne offizielles Schema/Versioning**
  (Issue #8899) → Workflows als versionierte Assets pinnen, ComfyUI-Commit + Custom-Node-Versionen
  pinnen, beim Upgrade gegen `/object_info` validieren.

## GPU-Bedarf (VRAM ist die bindende Größe; NVIDIA/CUDA bevorzugt)

| Feature | Modellklasse | VRAM (real) | Beispiel-Karte |
|---|---|---|---|
| Stills / Title-Cards | SDXL | ~8 GB | RTX 3060 12 GB · 4060 |
| | Flux (voll / GGUF) | ~12 GB / ~8 GB | 4070 · 4060 Ti 16 GB |
| `reenact` | LivePortrait (kein Diffusion-Video) | ~6–8 GB | 3060 · 4060 |
| | Reenact via Video-Diffusion | 16–24 GB | s. unten |
| `lipsync` | Wav2Lip / MuseTalk | ~4–8 GB | fast jede moderne Karte |
| B-Roll-Video | LTX-Video (leicht/schnell) | ~16 GB | 4070 Ti Super 16 GB · 4080 |
| | Wan 2.2 / HunyuanVideo (Qualität) | ~24 GB | RTX 3090 · 4090 · A6000 |

- **Smart-Offloading** streckt größere Modelle auf kleinerer VRAM (→ System-RAM), spürbar langsamer.
  Zusätzlich **32 GB+ System-RAM** für Video, **Disk: zig–hunderte GB** Gewichte.
- **Wer braucht die GPU:** nur der optionale ComfyUI-Sidecar (Nutzer-Maschine oder zentrale/Cloud-
  Instanz). **Laura selbst läuft GPU-frei** (Stub). Dev/CI: keine GPU (Stub + `--cpu`).
- **Kein-GPU-Pfad:** **Comfy Cloud** ($20–100/mo, gleiche `/prompt`-API, drop-in) oder Stub behalten.
- **Faustregel:** erster echter Mehrwert (Reenact statt Platzhalter) → **12-GB-NVIDIA** reicht;
  ernsthaftes generatives KI-Video → **24 GB**.

## Risiken / Abwägung

- **GPL-3.0** → **nur Out-of-Process** (HTTP-Sidecar). Niemals ComfyUI-Module importieren/bündeln/als
  kombiniertes Werk ausliefern (Copyleft). *Engineering-Leitlinie, keine Rechtsberatung.*
- **GPU/VRAM**: viele Nutzer haben kein 24-GB-Setup → Stub + Comfy-Cloud-Fallback; „startet ohne GPU"-
  Invariante bleibt intakt.
- **Workflow-Format instabil** → pinnen + `/object_info`-Validierung.
- **Custom-Node-Supply-Chain**: beliebiges in-process-Python aus GitHub, reale Malware-Vorfälle → nur
  Core-Nodes + Allowlist, kein Runtime-Auto-Install, Registry untrusted.
- **EU-AI-Act**: greift automatisch — generierte Clips sind `synthetic=True` + Provenance (reenact macht
  das bereits). Bleibt konsistent.

## Empfehlung

Opt-in externer Runtime. **Kleinster erster Schritt:** `reenact`-Stub durch ein `ComfyUIBackend` ersetzen
(Plumbing existiert, sofortiger echter Mehrwert). Mittelfristiger höchster *einzigartiger* Hebel:
**generative B-Roll / Title-Cards** als neuer Effekt (Greenfield).

**Caveat:** Der AI-Runtime-Subtree (`services/ai-runtimes/`, `ai/runtime_*`, `api/ai_runtimes.py`) ist
parallele codex-Arbeit — eine echte Umsetzung muss damit koordiniert werden.

## Quellen

- https://github.com/Comfy-Org/ComfyUI (README: Modalitäten, GPL-3.0, VRAM/`--cpu`)
- https://docs.comfy.org/development/comfyui-server/comms_routes (Endpunkte)
- https://github.com/comfyanonymous/ComfyUI/issues/8899 (kein JSON-Schema/Versioning)
- https://comfy.org/cloud/pricing/ (Comfy-Cloud-Tiers)
