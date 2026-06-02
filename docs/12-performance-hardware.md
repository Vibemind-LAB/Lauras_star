# 12 — Performance-Ziele & Hardware

Werte sind **Produktziele**, keine gemessenen Zusagen.

## Nichtfunktionale Zielwerte

| Kategorie | Zielwert |
|---|---|
| Timeline-Präzision | alle Edits intern als **Ganzzahl-Frames** relativ zur Sequence |
| Audio-Präzision | Alignment zusätzlich in **Samples** |
| UI-Reaktivität | Projekt öffnen < 5 s (lokaler Cache); Sucheingabe-Echo < 150 ms |
| Playback | Scrub-Start < 100 ms aus Proxy-Cache; Sprung auf beliebigen Cut < 300 ms |
| Analyse-Idempotenz | gleicher Input + gleiche Pipeline-Version → gleicher Zustand |
| Offline | voller Ingest-/Analyse-/Rough-Cut-/Export-Pfad ohne Internet |
| Interchange | Round-trip-Tests OTIO/EDL/XML gegen Golden Fixtures |
| Auditierbarkeit | jede Analyse/jeder Export versioniert, reproduzierbar, mit Provenance |

## Pragmatische Performance-Ziele

| Workload | Ziel |
|---|---|
| Ingest + Probe pro Clip | < 5 s für Metadaten |
| Waveform aus 60 min Audio | < 20 s |
| Shot Detection auf Proxy | < 0,25× Clipdauer |
| ASR + Alignment 60 min | < 1× Realtime auf Produktions-GPU |
| Timeline-Suche | < 150 ms UI-Feedback |
| Export OTIO/EDL/SRT/VTT | < 5 s für typische Projekte |
| XML-Export | < 20 s inkl. Preflight |

## Hardware-Klassen

| Klasse | Minimum | Produktion |
|---|---|---|
| Laptop lokal | 8 Cores, 32 GB, 1 TB NVMe, M3 Pro / RTX 4060 8 GB | 12+ Cores, 48–64 GB, 2 TB NVMe, M4 Max / RTX 4070–4080 |
| Desktop lokal | 12 Cores, 64 GB, 2 TB NVMe, RTX 4070 12 GB | 16+ Cores, 128 GB, 4 TB NVMe, RTX 4080/4090 oder L4/A10 |
| Server On-Prem | 16–24 Cores, 128 GB, 8 TB NVMe, 1× A10/L4 | 32 Cores, 256 GB, 16 TB NVMe, 1–2× L40S |
| Cloud | L4/A10 für Analyse, CPU-Pool für Probe/Export | L40S für Batch/Hochdurchsatz |

## Kostengrößenordnung

- Rein lokal/Einzelplatz: praktisch keine laufenden Serverkosten.
- Backup in Objektstore: S3 Standard ≈ 0,023 USD/GB-Monat (~23 USD/TB-Monat).
- Semantische Suche managed: Qdrant Free Tier 1 GB RAM / 4 GB Disk, danach usage-based.
- Serverless-GPU klein: Cloud Run L4 ≈ 0,0001867 USD/s (~0,67 USD/h GPU-only).
- Kleine Pilot-Cloud: ~100–400 USD/Monat ohne große Transcoding-Last.
- On-Prem Workstation: grob 4.000–8.000 EUR einmalig je starker Analyse-/Editor-Maschine.

## Aufwand (Schätzung)

- MVP Pro in 12 Wochen: **55–75 Person-Wochen**.
- Produktionshart (breite XML/EDL-Testbank, Kollaboration, mehrsprachig): **90–130 PW**.
- Rollen: Product/Tech-Lead 0,5–1 · Desktop/UI 1,5 · Backend/Workers 1,5 · Media/Interop 1 · QA 0,5–1 FTE.
