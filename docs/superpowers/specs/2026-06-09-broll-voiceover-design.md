# B-Roll & Voiceover — unabhängiger Spur-Tausch (Design)

**Ziel:** Im Feinschnitt pro Zeitbereich das **Bild** durch ein anderes Video ersetzen
(B-Roll, Originalton bleibt) bzw. den **Ton** durch anderes Audio ersetzen/überlagern
(Voiceover/Musik, Bild bleibt). Beide Spuren unabhängig.

## Aktuelles Modell (Ist)

- `timeline_clips`: **ein** Clip trägt Bild **und** Ton aus **einem** `asset_id`, mit
  `audio_offset_samples` (J/L-Versatz, Migration 0012).
- Aber: es gibt bereits eine **`lane INTEGER`-Spalte** (alles liegt aktuell auf lane 0) +
  `linked_audio_group`. Das Mehrspur-Fundament ist da.
- **Szenen-Musik** (FB) mischt schon Audio als Overlay (`adelay`+`volume`+`amix` in `mp4.py`).
  → Präzedenz für Ton-Ersatz.
- **Besitz:** `render/mp4.py` + `render/handlers.py` = **meins**. `api/timelines.py` = **deins**
  (L/J-Split, Editorial). `timeline/otio_sync.py` = meins.

## Ansätze

| | Idee | Pro | Contra |
|---|---|---|---|
| **A — Overlay-Lanes** ✅ | `lane` nutzen: lane 0 = Basis (Bild+Ton); **V2** = B-Roll (Bild ersetzt Bereich); **A2** = Voiceover/Musik (Ton ersetzt/mischt Bereich) | additiv; nutzt `lane` + meinen Musik-Render; Basis bleibt intakt; geringes Risiko; fast nur **meine** Dateien | Render muss Video-Overlay komponieren (neu) |
| B — Per-Clip-Override | Spalten `video_asset_id`/`audio_asset_id` am Basis-Clip | minimale Migration | an Clip-Grenzen gebunden, unflexibel, vermischt Belange |
| C — Volles V/A-Split | jeder Clip nur Bild **oder** Ton | „korrektes" NLE-Modell | größter Umbau; trifft OTIO/Render/UI **und deine `timelines.py`** stark; hohes Risiko |

**Empfehlung: A.** Kleinster Schritt zum Ziel, additiv, baut auf Vorhandenem, hält
`timelines.py` raus (neue, separate Endpoints).

## Design (Ansatz A)

- **Datenmodell** (Migration, additiv): Spalten `role TEXT DEFAULT 'base'` ('base'|'broll'|'vo')
  + `gain_percent INTEGER DEFAULT 100` an `timeline_clips`. Ein Overlay = Clip mit `role`,
  eigenem `asset_id` + Quell-Range + Seq-Range auf eigener `lane`.
- **Render** (`mp4.py`/`handlers.py`, meins):
  - **B-Roll:** dessen Bild über den Basis-Bereich legen (ffmpeg: Basis zerschneiden + B-Roll-
    Segment einsetzen, oder `overlay` mit `enable='between(t,…)'`), **Ton bleibt Basis**.
  - **Voiceover/Musik:** Audio in den Bereich mischen/ersetzen (Erweiterung meines `amix` —
    optional Basis-Ton ducken/ersetzen), **Bild bleibt**.
- **OTIO** (`otio_sync.py`, meins): Overlays als zusätzliche OTIO-Tracks → Source-of-Truth bleibt.
- **API** (neue Endpoints, **nicht** in `timelines.py`): `POST .../overlay` (B-Roll/VO-Asset
  über `[seq_in, seq_out)`, VO mit `gain`), `DELETE .../overlay/{id}`.
- **UI** (`TimelineBar`/`FineCutView`, meins): Overlay-Lanes **V2** (über V1) + **A2** (unter A1),
  **aligned wie die TX-Spur**. „Bild ersetzen"/„Ton ersetzen": Bereich/Clip wählen → Asset aus
  Projekt-Medien → Overlay anlegen; Overlay-Clips trimm-/verschiebbar.

## Baureihenfolge (bite-sized) & Phasen

**Phase 1 — Ton ersetzen (Voiceover/Musik je Bereich):** geringes Risiko, reift den Musik-Render.
1. Migration `role`+`gain_percent` + Repos (add/list/remove overlay).
2. Render: Audio-Overlay je Bereich (Erweiterung meines `amix`).
3. API: place/remove audio-overlay.
4. UI: A2-Lane + „Ton ersetzen" + Tests.

**Phase 2 — Bild ersetzen (B-Roll):** der neue/härtere Teil (Video-Compositing).
5. Render: Video-Overlay (Basis-Bild für den Bereich durch B-Roll ersetzen).
6. API + UI: V2-Lane + „Bild ersetzen".
7. OTIO-Tracks für beide + Tests.

## Besitz

Fast alles **meins**: Migration, Repos, Render (`mp4.py`/`handlers.py`), OTIO, neue API,
`api.ts`, `TimelineBar`, `FineCutView`. **Deine `timelines.py` bleibt unberührt** — Overlays sind
additive, separate Endpoints; dein Rough-Cut-/L-J-Pfad ist unbeeinflusst.

## Invarianten (Pflicht)

Frames ganzzahlig; Ton in Samples (gain/offset); Ranges end-exclusive; OTIO = Wahrheit;
Idempotenz (input, pipeline_version). **Riskantester Teil:** Video-Overlay-Compositing (Phase 2).
