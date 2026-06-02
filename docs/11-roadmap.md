# 11 — Roadmap (12 Wochen)

Hart fokussiert. Ziel: **eine starke Pro-Version**, nicht „alles". Quelle der Wahrheit für den
laufenden Stand ist [`../tasks/todo.md`](../tasks/todo.md).

## Phasen

| Phase | Wochen | Fokus | Deliverables | PW |
|---|---|---|---|---|
| **Foundations** | 1–2 | Desktop-App läuft, lokaler Service, Workspace, Ingest | Electron Shell, FastAPI, ffprobe-Ingest, Proxy/Waveform-Jobs, Schema v1, **Zeitkern + Tests** | 10–12 |
| **Analysekern** | 3–4 | Shots, ASR, Wort-Alignment, Sprecher | PySceneDetect/TransNetV2, faster-whisper, WhisperX, pyannote, Analyse-Manifest | 10–12 |
| **Rough-Cut UX** | 5–6 | transcript-first Selects & Timeline-Ops | Analyse-View, Transcript-Search, Selects-Bin, Rough-Cut-Timeline, frame-aware Trim | 9–11 |
| **Interchange** | 7–8 | Exporte & Preflight | OTIO R/W, EDL, FCP7-XML (Premiere), SRT/VTT, Export-Diagnostics | 8–10 |
| **Härtung** | 9–10 | Playback, Performance, Recovery | libmpv-Integration, Crash-Recovery, Golden Fixtures, Timecode-Tests, Perf-Dashboard | 9–11 |
| **Release Candidate** | 11–12 | Packaging, Signing, Pilot | Electron-Forge-Build, macOS-Notarization, Windows-Signing, Docs, Pilot-Build | 9–12 |

## Exit-Kriterien je Phase

| Phase | Exit-Kriterium |
|---|---|
| Foundations | Beliebige Medien lassen sich importieren, prüfen, proxien und **wieder öffnen** |
| Analysekern | 60-min Material erzeugt Shots, Wörter & Speaker **ohne** manuellen Eingriff |
| Rough Cut | Textoperationen erzeugen **deterministische** Timeline-Deltas |
| Interchange | Premiere-kompatibler FCP7-XML + OTIO/EDL/SRT/VTT laufen gegen Golden Fixtures |
| Härtung | **Keine** Timecode-Drifts im Testkorpus, Playback stabil, Recovery funktioniert |
| Release Candidate | Signierte Builds, reproduzierbare Demo-Projekte, Pilot-handoff-fähig |

## Reihenfolge-Doktrin (Endempfehlung des Reports)

> präzise Zeitbasis → zuverlässige Analyse → transcript-first editing → belastbarer Export → Kollaboration

In Phase 1–4 **kompromisslos** den frame-/sample-genauen Analyse- und Rough-Cut-Kern bauen.
Nicht zu früh in „noch eine NLE" driften (UI-/Playback-/Effekt-Komplexität verbrennt den Vorsprung).

## Offene Fragen / Grenzen

| Thema | Grenze |
|---|---|
| FCPXML-Stabilität | OSS-Adapter nützlich, aber nicht blind vertrauenswürdig → eigene Fixture-Suite Pflicht |
| WhisperX-Betrieb | funktional stark, dependency-sensibel → isolierte Runtime, Release-Pinning |
| Browser-Playback | WebCodecs vielversprechend, als alleiniger Pro-Player noch zu riskant |
| „Szenen" vs. Shots | OSS erkennt primär Shot-Grenzen; echte Szenenbildung heuristisch/ML |
| Multicam/komplexe Effekte | bewusst **nicht** MVP |
| Self-hosted Supabase | brauchbar, aber nicht feature-identisch zur gehosteten Variante |
