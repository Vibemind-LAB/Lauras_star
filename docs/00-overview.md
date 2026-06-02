# 00 — Überblick & Doku-Index

> Destillat der Deep-Research für die Implementierung. Original (unverändert):
> [`research/deep-research-report.md`](research/deep-research-report.md).

## Produktthese

**Analyse zuerst, Schnittentscheidungen zweitens, NLE-Export drittens.** Laura versteht
Rohmaterial lokal (Shots, Transkript auf Wortebene, Sprecher, semantischer Index), leitet
daraus schnell Selects/Rough Cuts/Radio Edits/Subtitles ab und exportiert zuverlässig in
bestehende Profi-Workflows (OTIO, EDL, FCP7-XML, FCPXML, SRT/VTT).

Bewusst **kein** vollwertiges NLE-/Effekt-/Compositing-System. Differenzierung = frame-/sample-genaue
Zeitbasis + transcript-first Editing + belastbarer Interchange.

## Die vier Kerne

1. **Media I/O & Rendering** — FFmpeg/ffprobe (Probe, Proxy, Audio-Extraktion, Transcode, Export).
2. **Playback/Scrub** — libmpv (eingebettet), WebCodecs nur ergänzend.
3. **Kanonisches Timeline-/Interchange-Modell** — OpenTimelineIO als Source of Truth.
4. **AI-Analyse-Stack** — Shot Detection, ASR, Forced Alignment, Speaker Diarization, semantische Suche.

## Größte Risiken (wo die Zeit wirklich draufgeht)

Laut Report **nicht** STT/Shot-Detection, sondern:

- **Frame-/Timecode-Konsistenz** (DF/NDF, VFR, Speed Changes, Sample↔Frame). → [`03-time-model`](03-time-model.md)
- **Scrubbing/Playback** (libmpv-Embedding in Electron). → [`ADR-0002`](adr/0002-libmpv-primary-playback.md)
- **Interchange-Kompatibilität** (FCPXML-Adapter-Risiko). → [`07-interchange`](07-interchange.md)
- **Export-Validierung** (Capability/Degradation-Modell, Preflight). → [`07-interchange`](07-interchange.md)

## Doku-Index

| Datei | Inhalt |
|---|---|
| [00-overview](00-overview.md) | dieses Dokument |
| [01-architecture](01-architecture.md) | Referenzarchitektur, Komponenten, Prozessmodell |
| [02-data-model](02-data-model.md) | DB-Schema v1 (SQLite/Postgres-kompatibel) |
| [03-time-model](03-time-model.md) | **Frame-/Sample-genaue Zeitbasis (Kern-Risiko)** |
| [04-api](04-api.md) | Lokale FastAPI-Oberfläche |
| [05-workers-queue](05-workers-queue.md) | Pipeline, Queues, Job-Lebenszyklus |
| [06-storage](06-storage.md) | Workspace-Layout auf Disk |
| [07-interchange](07-interchange.md) | Exportformate & Interchange-Strategie |
| [08-components](08-components.md) | OSS-/kommerzielle Komponenten-Map |
| [09-security](09-security.md) | Secrets, Datenschutz, Team-Rollen |
| [10-testing-observability](10-testing-observability.md) | Testpyramide, Golden Tests, Metriken |
| [11-roadmap](11-roadmap.md) | 12-Wochen-Plan, Phasen, Exit-Kriterien |
| [12-performance-hardware](12-performance-hardware.md) | Performance-Ziele, Hardware-Klassen |
| [13-packaging](13-packaging.md) | Packaging, Signing, Release |
| [14-enterprise](14-enterprise.md) | Mandanten, RBAC, Audit, Observability, Deployment |
| [15-gap-closure-plan](15-gap-closure-plan.md) | Plan für alle offenen Lücken (Wave A/B/C) |
| [adr/](adr/) | Architecture Decision Records |

## Arbeitsannahmen (aus dem Report)

| Thema | Annahme |
|---|---|
| Ziel-OS | macOS & Windows zuerst, Linux später |
| Frame Rates | 23.976, 24, 25, 29.97 DF/NDF, 30, 50, 59.94 DF/NDF, 60 |
| Einsatzmodus | Einzelplatz lokal zuerst, Team-Sync/Review/Shared-Search danach |
| Medienarten | dialog-/szenengetrieben (Doku, Interview, Narrativ); Social/Shorts Nebenfall |

## Zielgruppen (Priorität)

1. Editor:innen in Doku/Interview/Narrativ — schnell Selects & Radio Edits aus viel Material.
2. Filmemacher:innen / Assistenzschnitt — Material verstehen & vorsortieren ohne NLE-Tuning.
3. Post-Teams mit Premiere/Resolve/Avid — starker AI-Vorlauf + sauberer Interchange.
