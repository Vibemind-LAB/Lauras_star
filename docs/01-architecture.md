# 01 — Architektur

## Doppelt geschichtet

- **Local Runtime Plane** — alles, was auf dem Schnittrechner laufen muss (offline-fähig).
- **Optional Collaboration Plane** — Team, Review, zentrale Suche (später, opt-in).

```
Desktop (Local Runtime Plane)
  UI       Electron + React (Vite)         ── IPC/HTTP ──┐
  Player   libmpv Playback Engine                        │
  API      FastAPI Local Service (127.0.0.1:8765)  ◄─────┘
  Worker   Python Worker Pool (DB-Job-Runner)
  DB       SQLite lokal (Postgres-kompatibles Schema)
  Vec      Qdrant (optional, Phase 2+)
  FS       Workspace Files (workspace/)

Optional Collaboration Plane (später)
  PostgreSQL / Supabase · Qdrant Cloud · S3/MinIO/NAS · Redis/Celery · Review/API
```

UI → API (HTTP) und UI → Player (nativ). API → DB + Worker. Worker → FS + DB + (Vec).
Cloud-Sync, Objektstore und Celery/Redis sind **optionale** Kanten im Servermodus.

## Prozessmodell (Desktop)

| Prozess | Rolle | Tech |
|---|---|---|
| **Main** (Electron) | Fenster, Auto-Update, libmpv-Host, Lifecycle des lokalen Service | Node/Electron |
| **Renderer** | UI, Transcript/Timeline, Such-UX | React + Vite, `contextIsolation`, typed Preload-Bridge |
| **Local API** | HTTP-Service, orchestriert Jobs, hält DB | Python/FastAPI (gebündelte Runtime) |
| **Worker(s)** | Heavy Lifts (probe/proxy/waveform/analysis/export) | Python, idempotente Stufen |

Der lokale Service wird vom Main-Prozess als Child gestartet/gestoppt. Renderer spricht **nie**
direkt mit Dateisystem/Modellen — alles über die API (Audit, Reproduzierbarkeit, Sicherheit).

## Warum eigener Timeline-Kern (nicht MLT/GES)

MLT (Kdenlive/Shotcut) und GStreamer Editing Services sind legitim, erzeugen aber früh viel
Komplexität im Render-/Timeline-Modell, **bevor** der AI-Mehrwert steht. Für einen AI-first
Editorial Assistant mit starkem Export ist **eigener Timeline-Kern + OTIO + FFmpeg/libmpv**
der schnellere Weg. MLT/GES bleiben für späteres volles NLE-Authoring re-evaluierbar.
→ [`ADR-0004`](adr/0004-own-timeline-core.md)

## Komponenten-Kurzmap (Details in 08-components)

| Aufgabe | Komponente | Rolle |
|---|---|---|
| Media I/O | FFmpeg / ffprobe | Pflichtkern |
| Playback | libmpv | primärer Playback-Layer |
| Shot Detection | PySceneDetect (+ TransNetV2 Refinement) | MVP-Pflicht / optionaler Booster |
| ASR | faster-whisper | empfohlener ASR-Kern |
| Word Alignment | WhisperX | isoliert, hart gepinnt |
| Diarization | pyannote.audio | eigener Worker + Cache |
| Interchange | OpenTimelineIO (+ Plugins) | kanonisch + Export |
| Store | SQLite → PostgreSQL-Schema; Qdrant optional | Runtime / Server / Vektor |
| Queue | DB-Runner (Desktop) → Celery/Redis (Server) | Jobs |
| Packaging | Electron Forge | Build/Sign/Publish |
| Secrets | Electron safeStorage | OS-Keychain/DPAPI |

## Datenflüsse (High-Level)

**Ingest:** `POST /assets/import` → `ingest.io` (probe, checksum, poster) → `proxy.cpu`
(proxy, audio extract, waveform) → DB-Metadaten + FS-Artefakte.

**Analyse:** `POST /assets/{id}/analysis` → `analysis.scene` → `analysis.asr` →
`analysis.align` → `analysis.diarize` → (`analysis.embed`) → Manifest + DB.

**Rough Cut:** Transcript-Operation → `POST /timelines/{id}/operations` → kanonische
OTIO-Mutation in DB.

**Export:** `POST /timelines/{id}/exports` → Preflight (`/interop/validate`) →
`export.interchange` → FS (otio/xml/edl/srt/vtt) + Diagnostics.
