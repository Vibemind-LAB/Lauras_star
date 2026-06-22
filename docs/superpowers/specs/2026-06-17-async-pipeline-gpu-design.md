# Async-Pipeline + GPU-Detection (kein Downscale) — Design

> Performance-Umbau der Ingest-/Analyse-Pipeline. Auslöser: ein 29-Min-1080p60-Video
> brauchte „viele Minuten", weil alles **seriell auf einem Worker** lief, der Proxy mit
> **CPU-libx264** encodiert wurde und **Whisper auf der CPU** lief — obwohl die Maschine
> eine RTX 3060 + NVENC + CUDA-fähiges ctranslate2/torch hat. Entscheidung (2026-06-17):
> **async (Multi-Worker)** · **GPU-Detection** (Proxy/ASR/Scene) · **nicht runterskalieren**
> (Proxy bleibt voll 1080p).

## Kernprinzip

Schneller werden durch **Parallelität + GPU-Beschleunigung**, ohne die Proxy-Auflösung zu
senken. Die Queue-Architektur (`ingest.io / proxy.cpu / analysis.scene / analysis.gpu /
export`) und das **atomare `claim_job`** existieren bereits — sie werden nur noch nicht
ausgenutzt, weil ein einziger Runner-Thread alle Queues seriell abarbeitet.

## Nicht verhandelbare Invarianten (bleiben)

- `PROXY_MAX_HEIGHT = 1080` **unverändert** — kein Downscale.
- Schwere Modelle bleiben optionale Extras; Backend startet und läuft ohne GPU/Modelle
  (sauberer CPU/libx264-Fallback).
- Zeit-/Interchange-Invarianten unberührt (reiner Worker-/Encoder-/Device-Change).

## Architektur

```
N Worker-Threads (Default 3) — alle konsumieren ALLE Queues
  claim_job (atomar, immediate-Transaction)  → kein Doppel-Claim
  pro laufendem Handler: Auto-Heartbeat-Thread hält die Lease frisch
    → proxy.build ‖ audio.extract laufen parallel; Cross-Asset parallel
    → jeder Schritt GPU-beschleunigt wo möglich
GPU-Detection (gecacht, Env-Override):
  nvenc_available()      → Proxy via h264_nvenc (voll 1080p) sonst libx264
  asr_cuda_available()   → Whisper cuda+float16 sonst cpu+int8 (geloggt)
  torch_cuda_available() → TransNet-Scene auf cuda (Default-Detektor adaptive bleibt CPU)
```

## Komponenten

### 1. Multi-Worker-Pool — `jobs/runner.py`, `main.py`, `config.py`

- `JobRunner` bekommt einen `concurrency: int = 1`-Parameter. `start()` startet `concurrency`
  Loop-Threads (statt einen), die sich Registry/DB teilen. Jeder Thread: `reap → claim →
  execute`. Da `claim_job` write-locked, claimt jeder Worker einen **disjunkten** Job.
- `stop()` setzt das Stop-Event und joint **alle** Threads (`self._threads: list`).
- Config: `Settings.worker_concurrency` (env `LAURA_WORKERS`, Default **3**). `main.py`
  übergibt es an den `JobRunner`.
- **SQLite WAL**: in `db/sqlite.py` `PRAGMA journal_mode=WAL` setzen (falls nicht aktiv),
  `busy_timeout` bleibt (5000 ms) — erlaubt nebenläufige Reader während Writer claimen/heartbeaten.

### 2. Runner-Auto-Heartbeat — `jobs/runner.py` (kritischer Enabler)

- In `_execute`: vor `handler(ctx)` einen Daemon-Thread starten, der alle
  `max(5, lease_seconds // 2)` s `ctx.heartbeat()` ruft, bis ein `threading.Event`
  gesetzt ist. Nach Rückkehr/Exception des Handlers: Event setzen + Thread joinen
  (kurzes Timeout).
- Wirkung: jeder beliebig lange Job (Proxy ~Min, ASR Min) hält seine Lease → der
  60-s-Reaper eines **anderen** Workers requeued/failt ihn nicht mehr. Ohne diesen
  Mechanismus würde Multi-Worker die alten Timeouts als *Backend*-Fehler zurückbringen.
- Handler bleiben unangetastet (bestehende manuelle Heartbeats in `handle_fetch` werden
  redundant, aber harmlos — bleiben).

### 3. GPU-Detection-Util — `gpu.py` (neu, z. B. `src/laura/gpu.py`)

- `@lru_cache nvenc_available() -> bool`: ruft `ffmpeg -hide_banner -encoders` (über den
  `LAURA_FFMPEG`-Pfad aus `ingest/ffmpeg.py`) und prüft auf `h264_nvenc`.
- `@lru_cache asr_cuda_available() -> bool`: `import ctranslate2; get_cuda_device_count() > 0`
  (tolerant — jede Exception ⇒ False).
- `@lru_cache torch_cuda_available() -> bool`: `import torch; torch.cuda.is_available()`
  (tolerant ⇒ False).
- Alle drei fangen ImportError/Laufzeitfehler ab und liefern False (Backend ohne Extras OK).

### 4. Proxy-Encoder — `ingest/proxy.py`

- Encoder-Wahl: `LAURA_PROXY_ENCODER` (`auto`|`nvenc`|`libx264`, Default `auto`).
  `auto` ⇒ `h264_nvenc` wenn `nvenc_available()`, sonst `libx264`.
- NVENC-Args: `-c:v h264_nvenc -preset p5 -cq 23 -pix_fmt yuv420p -movflags +faststart`
  (Qualitäts-Pendant zu libx264 crf 20). **`scale=-2:1080` bleibt** (kein Downscale; bei
  1080p-Quelle ein No-Op, bei >1080p der bestehende Cap).
- libx264-Pfad unverändert als Fallback. Gewählter Encoder wird geloggt.

### 5. ASR-Device — `analysis/asr.py`

- `_run`/`transcribe`: Device `auto` ⇒ `cuda` + `compute_type="float16"` wenn
  `asr_cuda_available()`, sonst `cpu` + `compute_type="int8"`. Gewähltes Device **geloggt**
  (kein stiller Fallback mehr). Bestehender try/except-CPU-Fallback bleibt als Sicherheitsnetz.
- `LAURA_ASR_DEVICE` (existiert) überschreibt weiterhin.

### 6. Scene-Device — `analysis/transnet.py` (minimal)

- Der TransNet-Pfad lädt das Modell auf `cuda` wenn `torch_cuda_available()`, sonst CPU.
- Default-Detektor `adaptive` (PySceneDetect/OpenCV) bleibt CPU — kann keine GPU; **nicht**
  geändert. Kein Wechsel des Default-Detektors.

## Daten-/Kontrollfluss (ein langes Video, nachher)

1. `ingest.fetch` (Download) → online.
2. `ingest.probe` → enqueued `proxy.build` + `audio.extract`.
3. Zwei Worker ziehen **gleichzeitig** `proxy.build` (NVENC) und `audio.extract`.
4. Nach beiden: `analysis.run` (Scene CPU + Whisper **GPU**). Auto-Heartbeat hält die Lease.
5. Resultat: der ~5,5-Min-Proxy-Schritt + Minuten-ASR schrumpfen auf GPU-Tempo und
   überlappen mit dem Audio-Extract.

## Error-Handling

- Jede GPU-Probe ⇒ False bei Fehler ⇒ CPU/libx264-Pfad. NVENC-ffmpeg-Fehler werden vom
  bestehenden `FFmpegError`-Pfad behandelt; zusätzlich Encoder-Fallback-Log.
- Worker-Loop fängt bereits alle Handler-Exceptions (`_finish_fail`/Retry).
- Auto-Heartbeat-Thread ist best-effort; ein DB-Fehler beim Heartbeat darf den Handler
  nicht crashen (try/except im Heartbeat-Loop).

## Testing / Verifikation

- **gpu.py**: Probes gemockt (ffmpeg-`-encoders`-Output mit/ohne nvenc; ctranslate2/torch
  vorhanden/fehlend) → korrektes bool; Exception ⇒ False.
- **proxy.py**: `LAURA_PROXY_ENCODER`/`nvenc_available` gemockt → Kommando enthält
  `h264_nvenc` bzw. `libx264`; **echter ffprobe** auf gebautem Proxy (NVENC falls vorhanden):
  Höhe == Quellhöhe (kein Downscale), Video-Stream vorhanden.
- **asr.py**: `asr_cuda_available` gemockt → Device/compute_type `cuda`/`float16` bzw.
  `cpu`/`int8`.
- **runner.py (Concurrency-Kerntest)**: N Worker + viele Jobs → jeder Job genau **einmal**
  ausgeführt (kein Doppel-Claim); **Auto-Heartbeat-Test**: ein Handler, der länger als
  `lease_seconds` blockiert, wird **nicht** vom Reaper requeued/gefailt und endet `succeeded`.
- `uv run pytest` grün · `ruff`/`mypy` clean.
- **Live-Re-Test**: 29-Min-Video erneut importieren+analysieren; Gesamtzeit messen (Ziel:
  von „viele Minuten" auf grob 1–2 Min) und bestätigen, dass Proxy NVENC + ASR cuda genutzt
  wurden (Logzeilen).

## Bewusst NICHT in diesem Spec (YAGNI)

- **ASR als eigener GPU-Job** (Scene‖ASR-Overlap innerhalb *eines* Videos) — der
  Orchestrator-Split lohnt nicht, sobald ASR GPU-schnell ist; späteres Teil falls nötig.
- Default-Detektor-Wechsel (adaptive→transnet).
- NVENC für den finalen Export-Render (separater Pfad; hier nur der Editorial-Proxy).

## Risiko

`runner.py`-Concurrency ist eine geteilte Kernkomponente. Höchstes Risiko: Race beim
Claim (durch atomare Transaction abgedeckt), SQLite-Writer-Contention (WAL + busy_timeout),
und der Reaper-vs-Heartbeat-Pfad. **Adversariale Code-Review Pflicht.** Kollisionsregeln:
`api/timelines.py`, `RoughCutView.tsx`, `Player.tsx`, `analysis/refine.py`, `shots.py`
nicht anfassen.
