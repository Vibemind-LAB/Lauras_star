# Replacement-Lane mit Render-Vorrang (Source-Replace-Primitive) — Design

> Erstes Bau-Teil des **R3-Programms** (Identitäts-Ebene). Brainstorming-Ergebnis vom 2026-06-09.
> Baut das nicht-destruktive Multi-Lane-Fundament, auf dem **R3-C Reenact** (LivePortrait) als
> Adapter aufsetzt. Ergänzt `2026-06-09-multilane-both-tabs-design.md` (Phase A/D, „lite") und
> `2026-06-09-ai-effects-integration-plan.md`.

## Kontext & Abgrenzung

R3 („Identitäts-Ebene": konsentierte, gekennzeichnete reenactment/face-swap-Reels) ist ein **Programm
aus mehreren Subsystemen** (Consent/Kennzeichnung, Aufnahme-Ingest, Reenact, Swap, Face-Probe,
Qualität). Dieses Spec deckt **nur das erste Teilstück** ab: die generische **Source-Replace-Primitive**,
weil das range-getriebene Reenact-Szenario eine Platzierungs-Mechanik braucht, die noch nicht existiert.
Reenact selbst, der Sidecar, Consent-Gate und Kennzeichnung sind **eigene, spätere Specs**; dieses Spec
beschreibt sie nur so weit, dass die Primitive die richtigen Andockpunkte hat.

## Goal

Ein Clip auf einer **Overlay-Lane** (`lane ≥ 1`) ersetzt beim **Render** die Base-Lane (`lane 0`) in
seiner Frame-Range — **nicht-destruktiv** (Base bleibt unberührt, Ersetzen ist reversibel). Funktioniert
in **beiden** Tabs: Feinschnitt (Szene-Timeline) und Zusammenfügen (Sequenz-Timeline). Erste Variante:
**opakes Voll-Frame-Replace** (deckt das ganze Bild über die Range) — exakt der reenact/swap-Fall.

## Scope

**In scope:**
- Datenmodell: `timeline_clips.role` ('base'|'replace'), Overlay-Clips auf `lane ≥ 1`.
- Reine Präzedenz-Auflösung (Base + Replace-Overlays → flache Render-Segmentliste).
- Render über den **bestehenden** Trim+Concat-Pfad (`render_clips_mp4`) — kein neuer Filtergraph.
- Additiver Endpoint (eigener Router) zum Setzen/Entfernen eines Replace-Overlays über eine Range.
- Geteilte MultiLane-TimelineBar: zweite Lane (V over) in **FineCutView und AssembleView**.
- Beide Timeline-Arten: Szene (`timeline_clips`) und Sequenz (flattened).

**Explizit NICHT (spätere Teile):**
- Transparente/Teil-Overlays (B-Roll Picture-in-Picture über echten `overlay`-Filter) — Phase D proper.
- Übergänge (`xfade`) — Multilane Phase B.
- Audio-Overlays (A2) — schon teils via `amix` da, hier nicht erweitert.
- Reenact-Sidecar / Consent-Gate / Kennzeichnung — eigene Specs (R3-C, R3-A).

## Architektur

```
Base-Lane (lane 0):     [ Clip A ........... | Clip B ........... ]   (unberührt)
Replace-Lane (lane 1):           [ Replace-Overlay (role='replace') ]
                                 └ Range r_in..r_out (end-exclusive)
Render-Präzedenz  →     [ A vor r | Overlay in r | A/B nach r ]  → bestehender Trim+Concat
```

Eine **reine Funktion** `resolve_precedence(base_clips, overlay_clips) -> list[RenderSeg]` schneidet die
Base-Segmente an den Overlay-Range-Grenzen und ersetzt das mittlere Stück durch das Overlay-Asset. Höhere
Lane gewinnt; bei opakem Voll-Frame-Replace ist „gewinnen" = vollständig ersetzen. Das Ergebnis ist die
gleiche flache Liste `(asset_path, src_in, src_out, seq_in, seq_out)`, die `render_clips_mp4` heute schon
verarbeitet — **keine Render-Änderung außer der vorgelagerten Auflösung**.

## Datenmodell (additiv, meins)

- Migration `0014_clip_role.sql` (nächste freie Nummer; beim Planen verifizieren): `ALTER TABLE timeline_clips ADD COLUMN role TEXT NOT NULL DEFAULT 'base';`
  (`'base'|'replace'`; bestehende Clips = 'base', unverändert). `lane` existiert bereits.
- Ein **Replace-Overlay** = ein `timeline_clips`-Row mit `role='replace'`, `lane ≥ 1`, `asset_id` =
  Ersatz-Asset, `seq_in/seq_out` = abzudeckende Range, `src_in=0` (Ersatz-Asset deckt die Range exakt;
  Validierung: `src_out-src_in == seq_out-seq_in`).
- **Base bleibt unberührt** → Overlay entfernen = Original sofort zurück (reversibel).
- **Sequenz-Tab:** die Sequenz wird über `flatten_sequence` zu Base-Clips; Replace-Overlays werden
  analog als `timeline_clips`-Rows auf der **die-Sequenz-tragenden Timeline** in Sequenz-Frame-Raum
  gespeichert (gleiche Spalten, gleiche Präzedenz-Auflösung). *(Genaues Backing der Sequenz-Timeline beim
  Planen gegen das Schema prüfen; Default-Annahme: die Sequenz hat eine Timeline-Zeile, an die Overlays
  hängen — sonst dünne Parallel-Tabelle `sequence_overlays` mit denselben Spalten.)*
- OTIO bleibt Source-of-Truth: ein Replace-Overlay = zusätzlicher OTIO-Track; `otio_sync` erweitert.

## Render-Vorrang

- `resolve_clip_rows` (SA3) ruft vorab `resolve_precedence(...)` auf, wenn Replace-Overlays existieren;
  sonst unverändert (byte-identischer Base-Pfad).
- Opakes Voll-Frame-Replace ⇒ Segment-Auswahl, **kein** `overlay`-Compositing. Der harte
  Compositing-Teil (transparente Overlays) bleibt der späteren Erweiterung vorbehalten.
- Frame-genau, end-exclusive; bei mehreren überlappenden Overlays gewinnt der höchste `lane`.

## API (additiv, eigener Router — **nicht** `timelines.py`)

- `POST /timelines/{id}/overlays` — Replace-Overlay setzen: Body `{ lane, asset_id, seq_in_frame,
  seq_out_frame_exclusive }`, legt `timeline_clips`-Row mit `role='replace'` an (Validierung der Range +
  Asset-Länge). Antwort = Overlay-Clip.
- `DELETE /timelines/{id}/overlays/{clip_id}` — entfernen (Base unberührt → Original zurück).
- `GET` der Clips liefert bereits `role`/`lane` mit (SceneOut/Clip-Modelle additiv erweitern).
- Sequenz-Tab analog (gleicher Router oder Sequenz-Pendant), je nach Backing.

## UI (geteilte MultiLane-TimelineBar; FineCutView + AssembleView)

- TimelineBar zeichnet `lane ≥ 1` als **zweite Spur über V1** (datengetrieben, gleiche Komponente in
  beiden Tabs). Replace-Overlay sichtbar als Block über der Base-Range; Klick → entfernen.
- Setzen-Flow minimal: Range wählen + Asset → „als Replace einsetzen". (Reenact ruft denselben Endpoint.)

## Andockpunkt R3-C / Consent (nächste Specs, nicht hier)

Reenact-Job erzeugt ein Voll-Frame-Asset (Zielgesicht ⨉ Driving-Range) und ruft `POST …/overlays`, um es
als `role='replace'` über die Driving-Range zu setzen. Davor **Consent-Record** (Job verweigert ohne),
danach `synthetic=true` + sichtbares Burn-in (R0). Die Primitive ist KI-agnostisch — sie platziert nur ein
Asset; jede Quelle (reenact/swap/b-roll-replace/manuelles Asset) nutzt denselben Endpoint.

## Invarianten

Integer-Frames (nie Float-Sekunden als Zustand); Ranges end-exclusive; Audio/Alignment in Samples;
DF/NDF nur Anzeige; **OTIO = Wahrheit** (Overlay = zusätzlicher Track); Idempotenz `(input,
pipeline_version)`. `timelines.py` (User) bleibt unberührt — alles additive, separate Endpoints.

## Testing

- **`resolve_precedence`** = reine Funktion → `pytest`: Overlay mitten im Base-Clip → 3 Segmente korrekt
  (Frames exakt, end-exclusive); Overlay == ganzer Clip; mehrere Overlays (höchste Lane gewinnt);
  keine Overlays → Base unverändert.
- **Migration** `role`-Spalte additiv (bestehende Clips 'base'); Repos Round-Trip.
- **Render** echter ffprobe: Base+Replace-Overlay → Output zeigt in der Range das Ersatz-Asset
  (z. B. unterscheidbare Testmuster), Länge/Frames stimmen; ohne Overlay byte-identisch.
- **API**: Overlay setzen/entfernen → Clips-Liste spiegelt `role`/`lane`; Range-Validierung.
- **UI**: zweite Lane sichtbar in beiden Tabs (manuell/CDP).

## Risiken / De-Risk

- **Sequenz-Backing** (wo hängen Sequenz-Overlays) ist die einzige offene Modell-Frage → beim Planen
  gegen das echte Schema klären; Default: an der Sequenz-Timeline, sonst dünne `sequence_overlays`.
- **Präzedenz-Kanten** (Overlay exakt an Clip-Grenze, Overlay über mehrere Base-Clips) → von der reinen
  Funktion + Tests abgedeckt; Voll-Frame-opak hält die Render-Seite simpel.
- **Transparente Overlays** bewusst ausgeklammert (der teure `overlay`-Filtergraph) — kommt als eigene
  Erweiterung, wenn B-Roll-PiP dran ist.

## Festgehaltene Entscheidungen (Brainstorming 2026-06-09)

1. Nächster Track nach R0/R1: **R3 Identitäts-Ebene**.
2. Erstes R3-Teil: **Reenact (LivePortrait)** — aber Platzierung zuerst.
3. Ausführung Reenact: **separater Sidecar-Service** (persistenter lokaler HTTP, eigenes venv).
4. Ein-/Ausgabe Reenact: **timeline-range-getrieben** (Driving = Range, Ergebnis ersetzt deren Quelle).
5. Platzierung: **erst die Multi-Lane-Scheibe bauen** (dieses Spec), Reenact danach als Adapter.
6. Ansatz Source-Replace: **Replacement-Lane + Render-Vorrang** (nicht-destruktiv), **opak**, **beide Tabs**.
