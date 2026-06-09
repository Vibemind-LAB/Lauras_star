# Mehrspur-Timeline für Feinschnitt + Zusammenfügen (Gesamtdesign)

> Löst das frühere `2026-06-09-broll-voiceover-design.md` ab und fasst beide Tabs zusammen.

**Vision:** Beide Tabs werden frame-genaue **Mehrspur-Timeline-Editoren** mit **einem**
gemeinsamen Datenmodell und **einer** geteilten UI-Komponente. Feinschnitt editiert eine
**Szene**, Zusammenfügen die **Sequenz** (fertige Szenen exakt positioniert + Übergänge +
Overlays). Was im Feinschnitt gebaut wird, fällt im Zusammenfügen fast geschenkt ab.

## Gemeinsames Fundament

- Beide arbeiten auf einer **Timeline** (Szene bzw. Sequenz) mit **Clips auf Lanes**.
- Lane-Modell: Spalte `lane` (existiert) + **`role`** ('base'|'broll'|'vo', neu) +
  **`gain_percent`** (neu, für Audio-Overlays).
- Eine geteilte **MultiLane-TimelineBar** (Weiterentwicklung der jetzigen TimelineBar):
  V1/A1 (+ TX im Feinschnitt) + V2/A2-Overlays + Playhead + frame-genaue Positionierung/Trim.
- **Schon erledigt:** TX-Transkriptspur, Playhead über alle Spuren, Proxy mit Ton.

```
Feinschnitt (Szene)            Zusammenfügen (Sequenz)
V2 │  [ B-Roll ]         │     V2 │   [ B-Roll über Szenen ]      │
V1 │ Szenen-Clips        │     V1 │ Szene1 │×│ Szene2 │×│ Szene3   │  ×=Übergang
A1 │ Ton                 │     A1 │ Ton ──────────────────────────│
A2 │  [ Voiceover/Musik ]│     A2 │   [ Musik-Bett / Voiceover ]  │
TX │ w o r t e           │
```

## Tab Feinschnitt (Szene-Timeline)

- Spuren: **V1** (Bild), **A1** (Ton), **TX** (Transkript ✅), + **V2** (B-Roll), **A2** (Voiceover/Musik).
- Editieren: Trim/Split/Reorder (vorhanden) · Overlays platzieren (neu) · In/Out-Inspector (✅).

## Tab Zusammenfügen (Sequenz-Timeline)

- Heute: flache „Reihenfolge"-Liste. → **echte Timeline**: jede fertige Szene = Clip auf **V1**,
  frame-genau auf der Zeitachse; Ton auf **A1**. Damit **exaktes Alignen** der Szenen.
- Spuren: V1 (Szenen in Folge), A1 (Ton), + V2 (B-Roll über mehrere Szenen), A2 (Musik-Bett/
  Voiceover über den Film).
- **Übergänge** zwischen Szenen (`cut` | `crossfade` | `dip_black` | `dip_white` | `wipe` + Dauer)
  — „Effekte beim Wechseln".

## Datenmodell (additiv; meins + Migrationen)

- `timeline_clips`: **+ `role`**, **+ `gain_percent`** (Overlay-Clips auf eigenen Lanes).
- `sequence_items`: **+ `transition_kind`**, **+ `transition_frames`** (Übergang **in** dieses Item).
- Sequenz bleibt szenen-referenziert; `flatten_sequence` erzeugt die Clips; Overlays/Übergänge
  sind additive Zusätze. OTIO bleibt Source-of-Truth (Overlays = zusätzliche OTIO-Tracks).

## Render (`render/mp4.py` + `handlers.py`, meins)

- **Audio-Overlay** (A2): Erweiterung meines bestehenden `adelay`+`volume`+`amix` (Szenen-Musik).
- **Video-Overlay** (V2/B-Roll): Basis-Bild für den Bereich durch B-Roll ersetzen (concat/`overlay`).
- **Übergänge:** `xfade` (Bild) + `acrossfade` (Ton); die zwei Segmente überlappen um
  `transition_frames` (Filtergraph statt plain concat).

## UI (`TimelineBar` → MultiLane; `FineCutView`/`AssembleView`, meins)

- Eine geteilte MultiLane-TimelineBar für **beide** Tabs (Lanes datengetrieben).
- Zusammenfügen: „Reihenfolge" → Timeline-Spur; **Übergangs-Chips** zwischen Szenen (Klick → Typ +
  Dauer); Overlay-Lanes V2/A2.

## Phasen (bite-sized, Risiko-gestaffelt)

- **Phase 0 (✅):** TX-Spur, Playhead, Proxy-Audio.
- **Phase A — Sequenz-als-Timeline + geteilte MultiLane-Komponente.** „Reihenfolge" wird eine
  echte, frame-genaue Spur; TimelineBar wird von beiden Tabs geteilt. *(Liefert „exaktes Alignen".)*
- **Phase B — Übergänge.** `transition_kind/frames` + `xfade`/`acrossfade`-Render + Übergangs-Chips.
- **Phase C — Overlay-Lanes Ton.** Voiceover/Musik (A2) je Bereich — beide Tabs. *(Reift meinen `amix`.)*
- **Phase D — Overlay-Lanes Bild.** B-Roll (V2) — beide Tabs. *(Video-Compositing, härtester Teil.)*

Jede Phase ist eigenständig testbar (Zeitkern → `pytest`, Ingest → echter ffprobe, UI → manuell/CDP).

## Besitz

Fast alles **meins**: Migrationen, Repos, Render (`mp4.py`/`handlers.py`), `otio_sync.py`, neue API-
Endpoints, `api.ts`, `TimelineBar`, `FineCutView`, `AssembleView`. **Deine `api/timelines.py` bleibt
unberührt** — alles additive, separate Endpoints; dein Rough-Cut-/L-J-Pfad ist unbeeinflusst.

## Invarianten / Risiken

Frames ganzzahlig (nie Float-Sekunden als Zustand); Audio/Alignment in Samples; Ranges end-exclusive;
DF/NDF nur Anzeige; OTIO = Wahrheit; Idempotenz `(input, pipeline_version)`. **Riskanteste Teile:**
Video-Overlay-Compositing (Phase D) und der `xfade`-Filtergraph mit korrektem Frame-Offset (Phase B).
