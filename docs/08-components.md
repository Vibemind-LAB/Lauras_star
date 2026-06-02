# 08 — Komponenten-Landschaft

Mapping-Liste für den Stack. **Maturity** = Architektur-Einschätzung aus Release-/Doku-Lage und
Integrationsrisiko (nicht Code-Qualitätsurteil).

## Open-Source-Kern

| Komponente | Projekt | Lizenz | Maturity | Rolle | Integrationshinweis |
|---|---|---|---|---|---|
| Media I/O | FFmpeg | LGPL2.1+/optional GPL | hoch | **Pflichtkern** | LGPL-konforme Builds; getrennte Export-Profile |
| Media Probe | ffprobe | Teil v. FFmpeg | hoch | **Pflichtkern** | JSON-Output direkt in Ingest/Validierung |
| Playback | libmpv | LGPL2.1+/gemischt | hoch | **primärer Player** | nativ einbetten, **nicht** Browser-Video als Primär |
| Shot Detection | PySceneDetect | BSD-3 | hoch | **MVP-Pflicht** | erste Cut-Pass-Erkennung, deterministisch |
| Shot Refinement | TransNetV2 | MIT | mittel | optionaler Booster | 2. Pass nur für strittige Grenzen |
| ASR | faster-whisper | MIT | hoch | **ASR-Kern** | schnell, quantisierbar, CPU/GPU-Fallback |
| Forced Alignment | WhisperX | BSD-2 | mittel | Alignment-Layer | **isolierte Runtime, hart gepinnt** (Version-Churn) |
| Diarization | pyannote.audio | MIT | hoch | Diarization | eigener Worker + Cache; Token-Handling beachten |
| Interchange | OpenTimelineIO | Apache-2.0 | hoch | **Pflichtkern** | internes Canonical Model anlehnen |
| OTIO Plugins | OTIO-Plugins | Apache-2.0 | mittel | **Export-Pflicht** | `cmx_3600`/`fcp_xml`/`fcpx_xml`; per Golden Tests absichern |
| FCPXML | otio-fcpx-xml-adapter | Apache-2.0 | mittel-niedrig | **guarded/später** | Maintainer-Risiko → Warnstatus + Fixtures |
| Packaging | Electron Forge | MIT | hoch | **empfohlen** | Packaging/Signing/Publishing |
| Secrets | Electron safeStorage | Electron | hoch | **Pflicht (Secrets)** | OS-Keychain/DPAPI; nur für Secrets |
| Relationale DB | PostgreSQL | PostgreSQL | hoch | Server-Schema-Basis | lokal SQLite, Schema PG-kompatibel |
| Vector Search | Qdrant | Apache-2.0 | hoch | optional ab Phase 2 | erst ab semantischer Suche/Team |
| Queue | Celery | BSD | hoch | Server-Queue | Desktop: DB-Runner; Server: Celery/Redis |
| Queue Light | RQ | BSD | mittel-hoch | Alternative | simpler, weniger Workflow-Mächtigkeit |
| macOS-Turbo | MLX Whisper | MIT | mittel | macOS-Optimierung | Apple-Silicon-Sonderpfad, nur Mac-Builds |

## Referenzprojekte (Inspiration, **nicht** als Core-Dependency)

- **CutScript** (MIT) — sehr nah am Zielstack (Electron + React + FastAPI + WhisperX + FFmpeg).
  Gute Referenz für Packaging & Transkript-UX.
- **StoryToolkitAI** — Film-Editing-AI, Suche, EDL/XML-Export. Lizenz/Architektur vor Nutzung prüfen.
  Produktreferenz, kein eingebetteter Baustein.

## Kommerziell — Benchmarks & optionale Integrationen

| Produkt | Rolle |
|---|---|
| Adobe Premiere Pro | **UX-Benchmark** (transcript-first, lokale Media-Intelligence) + Interop-Ziel (FCP7-XML) |
| DaVinci Resolve | Benchmark/Export-Ziel (Pro-Workflows) |
| Avid ScriptSync | Benchmark (script-/transcript-linked editing) |
| Frame.io | optionales Review-/Delivery-Ziel (API, searchable transcripts) |
| Google STT / Amazon Transcribe | optionaler Cloud-**Fallback** (nicht Kern) |
| Azure AI Video Indexer | Enterprise Add-on |
| Qdrant Cloud | später (Team-Betrieb) |

## Optionalitäts-Prinzip

Schwere ML-Komponenten (WhisperX/pyannote/TransNetV2/MLX) sind **Extras**, keine harten Defaults.
Das Backend muss **ohne** GPU/Modelle starten und ingest/probe/proxy/waveform/export leisten.
Modelle werden lazy geladen, gecacht und über Pipeline-Version gepinnt.
