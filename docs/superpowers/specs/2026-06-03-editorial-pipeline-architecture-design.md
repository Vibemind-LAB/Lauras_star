# Editorial-Pipeline — Architektur- & UI-Gesamtspec

- **Datum:** 2026-06-03
- **Status:** Entwurf (vom User freigegeben, „ohne Halt" bauen)
- **Betrifft:** `apps/desktop` (UI-Neuordnung) + `services/local-api` (Szenen/Sequenz-Modell, Render)
- **Baut auf:** Import-UI + Download-Backend (implementiert), Timeline/Interchange-Backend (vorhanden)

## Vision

Laura wird zu einer **6-Stufen-Editorial-Pipeline** mit linker Navigations-Rail. Mentales
Modell: **Szenen als Einheiten** — Material → Szenen (je eine Sub-Timeline) → finale
Sequenz (montierte Szenen). Jede Stufe ist eine fokussierte Ansicht; Navigation ist frei
(kein Zwangs-Wizard), Voraussetzungen werden als Hinweis gezeigt, nicht blockiert.

```
[Download] → [Import] → [Rough Cut] → [Feinschnitt] → [Zusammenfügen] → [Export]
  URL/Queue   Medien     Szenen        pro Szene        Szenen ordnen     Render/Interchange
```

## Nicht-Ziele / vertagt (YAGNI)

- **Übergänge** zwischen Szenen (Stufe 5) → erst Folge-Spec; v1 = harte Schnitte.
- Mehrspur-Compositing, Effekte, Color, Keyframes → kein Ziel (kein Resolve-Klon).
- Cloud/Collab → nein (local-first).

## Navigations-Shell

- **Linke Rail** mit 6 Einträgen (Download · Import · Rough Cut · Feinschnitt ·
  Zusammenfügen · Export), Icon + Label, aktiver Zustand hervorgehoben (wie Clipchamp-Rail
  in den Referenzbildern).
- **Globaler Projekt-Kontext** über der Rail/Hauptbereich: aktuelles Projekt wählbar; alle
  Stufen arbeiten am selben Projekt.
- **Routing:** ein `stage`-State (`"download"|"import"|"roughcut"|"finecut"|"assemble"|"export"`)
  schaltet den Hauptbereich. Kein React-Router nötig — schlichter State-Switch im `App`.
- **Voraussetzungs-Hinweise:** Stufen ohne Daten zeigen einen leeren Zustand mit CTA
  („Noch keine Szenen — erst Rough Cut ausführen"), Navigation bleibt frei.

## Datenmodell-Rückgrat

Wiederverwendet die bestehende **Timeline-Infrastruktur** (`timelines` + Clips + OTIO +
Edit-Operationen):

- **Szene** = `timelines`-Datensatz mit `kind="scene"` (eigene Sub-Timeline aus Clips).
- **Sequenz** = `timelines`-Datensatz mit `kind="sequence"`, dessen geordnete Clips die
  Szenen **referenzieren** (verschachtelte Timeline → **neu**: ein Clip darf statt eines
  Assets eine Szenen-Timeline referenzieren).
- **Shot→Szene-Gruppierung** (Rough Cut) → **neu**: Backend gruppiert erkannte `shots` zu
  Szenen-Timelines.
- Bestehend nutzbar: `assets`, `shots`, `timelines`, `timeline_from_shots`, Edit-Operationen
  (`_apply`/OperationRequest), Interchange-Export (EDL/OTIO/FCPXML/SRT), `analysis`.

**Neue Backend-Bausteine (über die Stufen verteilt):**
- `timeline_clips.scene_ref` (Clip referenziert eine Szenen-Timeline) — nested sequence.
- Szenen-Erzeugung aus Shots (`POST /timelines/{id}/scenes` o. ä.).
- Musik/Audio-Spur in einer Szenen-Timeline (Lane für ein Audio-Asset).
- **MP4-Render** der Sequenz (Job analog `ingest.fetch`, mit Fortschritt) + Endpoint.
- **Download-Abbrechen** (`POST /assets/{id}/import-cancel`).

## Querschnitts-UI

- **`MediaCard`** — Karte mit Poster-Thumbnail, Titel, Meta, `⋯`-Menü, optionalem
  **Karten-Fortschrittsbalken** (Download-Bytes / Import-Analyse / Render). Vereinheitlicht
  Download-, Import- und Export-Galerie.
- **Karten-Fortschritt** = Weiterentwicklung des bestehenden `ImportProgress` (Balken im
  Karten-Footer statt Listenzeile).
- **Globales DropZone-Overlay** bleibt (Dateien/Ordner/Links überall ablegbar; nur auf
  Download/Import-Stufe aktiv).
- **`workspace/`-Speicheranzeige** unten in der Rail (statt Clipchamps „Cloudspeicher").

## Die 6 Stufen

### Stufe 1 — Download (Karten-Galerie)
Layout wie Clipchamp-Home-Grid. Header: Suche · Sortieren · **URL-Feld „+ Download"**
(http/Magnet). Grid aus `MediaCard`s laufender/fertiger Downloads mit **Byte-Balken**
(`45 % · 5 MiB/s · ETA 1:30`), Fehler→Retry auf der Karte. Fertige Downloads erscheinen
automatisch in Import. **Wiederverwenden:** `useImportStatus`, Download-Backend (fertig),
`DropZone`. **Neu:** `MediaCard`, Galerie-Layout, Abbrechen-Button.

### Stufe 2 — Import (Karten-Galerie)
Gleiches Grid; Header-Action **„+ Importieren"** (Datei/Ordner-Picker) + Drop. Karten =
Projekt-Assets mit **Analyse-Phasen-Balken** (Probe→Proxy→Analyse→fertig). **Wiederverwenden:**
`useImportStatus`, Import-Komponenten (fertig), `MediaCard`.

### Stufe 3 — Rough Cut (Editor-Layout + Szenen-Strip)
```
[rail] │ Projekt-Medien │ Hauptvideo-Player + Transport + Ruler
       ├────────────────┴──────────────────────────────────────
       │ Szenen-Strip: [Szene1 frames+„transkript" ✂/⇄] [Szene2 …] …
```
- Großer **Player** (Hauptvideo) + Transport + Time-Ruler.
- Unten **Szenen-Strip**: pro Szene Frame-Thumbnails + Transkript-Auszug.
- Aktionen: **Szenen erzeugen** (Analyse: Shots+Transkript → Gruppierung), Szene **Teilen
  (✂)** / **Zusammenführen (⇄)** (Szenengrenzen justieren). Klick → Player springt zur Szene.
- **Wiederverwenden:** `Player`, `ShotStrip`→Szenen-Strip, `TranscriptBar`, `useAnalysis`.
  **Neu:** Shot→Szene-Gruppierung (Backend), Teilen/Mergen.

### Stufe 4 — Feinschnitt (Pro-Szene-Editor)
```
[rail] │ Szenen-Liste │ Player (DIESE Szene) + Transport
       │ ▸Szene2◄     │ Transkript (Wort-Klick = schneiden)
       │              │ [V] ▭ Clip-Trim   [A] ▭ Musik [+ Musik]
```
- **Szenen-Liste links** (eine Szene wählen, prev/next) — die Seite ist auf *eine* Szene
  fokussiert.
- **Player** zeigt nur die Szene; darunter ihre **eigene Timeline** (`kind="scene"`).
- Werkzeuge: Clip-**Trim** (In/Out), **transkript-basiert schneiden** (Wort/Segment löschen),
  **Musik/Audio-Spur** hinzufügen.
- **Wiederverwenden:** `Player`, `TimelineBar`, `TranscriptBar`, `InspectorPanel`,
  Edit-Operationen-Backend. **Neu:** manueller Trim-UI, Transkript-Schnitt→Clip-Op,
  Audio-Lane.

### Stufe 5 — Zusammenfügen (viele Szenen anordnen)
```
[rail] │ Player (GESAMTE Sequenz) + Transport (Gesamtdauer)
       ├──────────────────────────────────────────────────────
       │ Szenen-Bin: [S1][S2][S3]…  ◄ scrollbar ►
       │ Sequenz:    ┌S3┐┌S1┐┌S5┐…  ◄ scrollbar/Zoom ►  drag
```
- **Szenen-Bin**: *alle* fertigen Szenen (horizontal scrollbar — beliebig viele).
- **Sequenz-Spur**: Szenen hineinziehen + frei umordnen (scrollbar + Zoom); Mehrfachauswahl
  möglich. **Drag/Move → Gesamtvideo-Player aktualisiert live.**
- **Wiederverwenden:** `Player` (ganze Sequenz), `TimelineBar` (Szenen-Blöcke). **Neu:**
  `kind="sequence"` mit Szenen-Referenzen, Drag-Reorder, Live-Konkatenation der Vorschau.

### Stufe 6 — Export (Galerie der Ausgaben)
```
[rail] │ Ziel [MP4 ▾] Auflösung [1080p ▾]  [Exportieren]  (auch EDL/OTIO/FCPXML/SRT)
       ├──────────────────────────────────────────────────────
       │ Deine Exporte: [thumb][▓60% rendert][v2 OTIO ⬇][v1 MP4 ⬇] …
```
- Oben Export-Steuerung (MP4-Render + Interchange-Formate, Auflösung).
- Unten **Galerie** der fertigen Exporte (`MediaCard`: Thumbnail, Format/Auflösung, ⬇/Ordner/⋯,
  Render-Balken bei laufendem Job).
- **Wiederverwenden:** `MediaCard`+Karten-Fortschritt, Interchange-Export (vorhanden). **Neu:**
  MP4-Render (Job+Endpoint+Fortschritt), Export-Galerie.

## Existiert vs. neu (Zusammenfassung)

| Bereich | Vorhanden | Neu |
|---|---|---|
| Download/Import | Backend + Komponenten | `MediaCard`-Galerie, Abbrechen |
| Szenen | `shots`, `timelines`, `timeline_from_shots` | Shot→Szene-Gruppierung, `kind="scene"` |
| Sequenz | Timeline + Clips + OTIO | nested clips (`scene_ref`), Reorder |
| Feinschnitt | Player/Timeline/Transcript, Edit-Ops | Trim-UI, Transkript-Schnitt, Audio-Lane |
| Export | Interchange (EDL/OTIO/FCPXML/SRT) | MP4-Render, Export-Galerie |
| Navigation | — | linke Rail + Stage-Switch |

## Bau-Reihenfolge (jede = eigener Spec+Plan)

1. **Fundament:** Nav-Rail + Stage-Switch + `MediaCard`-Galerie für **Download & Import**
   (nutzt vorhandene Backend/Komponenten). → erstes lauffähiges Inkrement.
2. **Szenen-Modell + Rough Cut:** Shot→Szene-Gruppierung (Backend) + Rough-Cut-Ansicht
   (Player + Szenen-Strip + Teilen/Mergen).
3. **Feinschnitt:** Pro-Szene-Editor (Trim, Transkript-Schnitt, Audio-Lane).
4. **Zusammenfügen:** Sequenz (`kind="sequence"`, `scene_ref`) + Bin/Drag-Reorder + Live-Preview.
5. **Export:** MP4-Render-Job + Export-Galerie.

Jedes Inkrement ist eigenständig lauffähig/testbar. Reihenfolge wird so abgearbeitet;
spätere Stufen bekommen vor dem Bau je einen Detail-Spec, wenn ihre Produktfragen anstehen.

## Offene Punkte (in den jeweiligen Stufen-Specs zu klären)

- Szenen-Gruppierungs-Heuristik (Shots→Szenen): Schwelle/Regeln — im Rough-Cut-Spec.
- Transkript-Schnitt-Semantik (Wort löschen → Frame-Mapping) — im Feinschnitt-Spec.
- Musik-Lane: ein Audio-Asset pro Szene oder mehrere? Lautstärke/Ducking — Feinschnitt-Spec.
- MP4-Render-Engine: ffmpeg-Concat der Szenen-Proxies vs. Voll-Render der Originale — Export-Spec.
- Übergänge — eigener Folge-Spec nach v1.
