# Zusammenfügen (Stufe 5) — Detail-Spec

- **Datum:** 2026-06-08
- **Status:** Entwurf (vom User freigegeben — „weiter")
- **Betrifft:** `services/local-api` (Sequenz-Modell, Flatten, sequenz-bewusster Render/OTIO, Assemble-API) · `apps/desktop` (Zusammenfügen-Ansicht + Sequenz-Player)
- **Baut auf:** [Feinschnitt-Spec](2026-06-08-feinschnitt-stage-design.md) (materialisierte `kind="scene"`-Timelines, `materialize_scene`) · [Rough-Cut-Spec](2026-06-08-rough-cut-stage-design.md) (`scenes`) · `editing/otio_sync.py` (`build_model`/`rebuild_otio`) · Render (`render/mp4.py`, nimmt beliebige `(asset, src_in, src_out)`-Tupel) · Export-Stufe.
- **Letzte der drei Bespoke-Stufen** → vervollständigt die 6-Stufen-Pipeline.

## 1 · Vision
Die fertigen Szenen zu einer **finalen Sequenz** anordnen. Eine Sequenz = eine `kind="sequence"`-Timeline pro Projekt; ihre Anordnung lebt in **`sequence_items`** (geordnete **Szenen-Referenzen**, nicht kopierte Clips). **Nested + Flatten-on-Demand:** nichts wird flach gespeichert; eine reine Funktion löst zur Laufzeit Items → Szenen → Szene-Clips auf, sodass **Feinschnitt-Änderungen automatisch in der Sequenz durchschlagen**.

Leitentscheidungen (vom User bestätigt):
- **(a) `sequence_items` referenzieren Szenen** (nested) + **Flatten-on-Demand** (Edits propagieren).
- **(b) Render/OTIO werden sequenz-bewusst** über `resolve_clip_rows` → Export rendert die Sequenz.
- **(c) Bau in zwei Inkrementen:** 5a Anordnen+Export, 5b echter konkatenierender Sequenz-Player.

## 2 · Invarianten
- Flatten in **Ganzzahl-Frames**, **end-exclusive**, mit laufendem Seq-Offset; Grenzen liegen auf Szenen-/Clip-Grenzen.
- **OTIO bleibt Wahrheitsquelle** — für Sequenzen ist OTIO die **geflattete** Projektion (regeneriert bei jedem Reorder).
- `timeline_clips`/`EditClip` (getesteter Editing-Kern) werden **nicht** angefasst; Sequenz-Inhalt lebt in `sequence_items`.
- Audio/Transkript in Samples (Quelle); Sequenz-Render v1 = Video.

## 3 · Datenmodell (Migration `0010_sequences.sql`)
```sql
CREATE TABLE sequence_items (
  id TEXT PRIMARY KEY,
  sequence_timeline_id TEXT NOT NULL,
  scene_id TEXT NOT NULL,
  order_index INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_sequence_items ON sequence_items (sequence_timeline_id, order_index);
```
Pro Projekt eine `kind="sequence"`-Timeline (on-demand angelegt, analog `rough_cut`).

## 4 · Backend — Inkrement 5a

### 4.1 Flatten (rein)
`services/local-api/src/laura/sequences/flatten.py`:
```
flatten_sequence(db, sequence_timeline_id) -> list[dict]
```
- `list_sequence_items` (geordnet) → je Item `scene = get_scene`; dessen `scene_timeline_id` muss gesetzt sein (Materialisierung passiert beim Hinzufügen, s. 4.3).
- Laufender `offset=0`; für jeden Clip der Szene-Timeline (`list_timeline_clips`, nach `seq_in`): Clip-Dict kopieren mit `seq_in_frame = offset + clip.seq_in`, `seq_out_frame_exclusive = offset + clip.seq_out`; nach der Szene `offset += scene_length` (max `seq_out` der Szene-Clips).
- Ergebnis: flache Clip-Zeilen `(asset_id, src_in, src_out, seq_in, seq_out, speed…)` über mehrere Assets.

### 4.2 `resolve_clip_rows` (Weiche)
In `editing/otio_sync.py` (oder Helfer): `resolve_clip_rows(db, timeline_row)` → für `kind=="sequence"` `flatten_sequence(...)`, sonst `repos.list_timeline_clips(...)`. **`build_model` nutzt `resolve_clip_rows`** statt direkt `list_timeline_clips` (für Nicht-Sequenzen identisch → keine Regression). Der **Render-Handler** nutzt ebenfalls `resolve_clip_rows`. Damit rendert `render_clips_mp4` die gesamte Sequenz; **Export der Sequenz funktioniert sofort**.

### 4.3 Repos + Assemble-API (`api/sequences.py`)
Repos: `get_or_create_project_sequence(db, project_id)`, `list_sequence_items(db, seq_id)`, `replace_sequence_items(db, seq_id, scene_ids)` (atomar, `order_index` = Position).

| Methode | Pfad | Body | Antwort | Wirkung |
|---|---|---|---|---|
| GET | `/projects/{id}/sequence` | — | `SequenceOut` | Sequenz-Timeline holen/anlegen + Items (mit Szenen-Name) |
| PUT | `/sequences/{id}/scenes` | `SetSequenceScenesRequest{scene_ids:[…]}` | `SequenceOut` | Szenen **materialisieren** (`materialize_scene`), `replace_sequence_items`, OTIO regenerieren |
| GET | `/sequences/{id}/flattened` | — | `list[ClipOut]` | flache Clip-Liste (`flatten_sequence`) — für den Player |

`SequenceOut{ timeline_id, project_id, items: list[SequenceItemOut{id, scene_id, scene_name, order_index}] }`. Validierung: Szenen existieren + gehören zum Projekt (sonst 422); unbekannte Sequenz/Projekt → 404. PUT ist idempotent (vollständige Reihenfolge ersetzen).

## 5 · Frontend — Inkrement 5a (Anordnen + Export)
- `api.ts` additiv: `getProjectSequence(projectId)`, `setSequenceScenes(seqId, sceneIds)`, `getSequenceFlattened(seqId)` + Typen `Sequence`/`SequenceItem`.
- `hooks/useSequence.ts`: lädt Sequenz+Items, `setScenes` (reorder/add/remove → ein PUT).
- `components/AssembleView.tsx`: **Szenen-Bin** (alle Szenen des Projekt-Rough-Cuts, aus `useScenes`) + **Sequenz-Spur** (geordnete Szenen-Blöcke; HTML5-Drag-Reorder + Drop aus dem Bin — Muster wie `TimelineBar`-DnD) + **Player**. **Strukturelle Vorschau (5a):** Klick auf Sequenz-Block → Player springt zu dieser Szene (Einzel-Asset); das echte Gesamtvideo per **Export** (Render→MP4, läuft schon).
- `App.tsx`: `stage === "assemble"` → `<AssembleView/>` als **allerletzter** ~3-Zeilen-Schritt (nur wenn clean, sonst dem User überlassen).

## 6 · Frontend — Inkrement 5b (echter Sequenz-Player)
`components/SequencePlayer.tsx`: spielt die **flache Clip-Liste** (`getSequenceFlattened`) assetübergreifend — lädt den Proxy des aktuellen Clips, seekt auf `src_in`, spielt bis `src_out`, wechselt an der Clip-Grenze die Quelle (Preload der nächsten zur Lückenminimierung). Ersetzt in `AssembleView` die strukturelle Vorschau → **bei Move live das Gesamtvideo**. Best-effort nahtlos (kleine Switch-Latenz als Limitation notiert).

## 7 · Datenfluss
Stufe 5 → Sequenz holen/anlegen → Szenen aus dem Bin in die Spur ziehen + frei umordnen → jeder Drag = ein `PUT` (Szenen materialisiert, Items neu, OTIO regeneriert) → Vorschau (5a strukturell, 5b live) → **Export** rendert die geflattete Sequenz zu MP4.

## 8 · Bau-Sequenz
**5a:** Migration `sequence_items` · `flatten_sequence` (rein) · `resolve_clip_rows` + `build_model`/Render-Handler sequenz-bewusst · Repos + Assemble-API · `api.ts`+`useSequence`+`AssembleView` (Bin/Spur/strukturelle Vorschau) · App.tsx. → Anordnen+Export lauffähig.
**5b:** `SequencePlayer` (konkatenierende Multi-Asset-Wiedergabe) → live Gesamtvorschau.

## 9 · Fehler-/Leerzustände
Keine Szenen → CTA „Erst Rough Cut / Feinschnitt". Leere Sequenz → Hinweis „Szenen in die Spur ziehen". Szene ohne Material → `materialize_scene` beim Hinzufügen. Render/Flatten-Fehler → nicht-blockierende Fehlerzeile.

## 10 · Tests
**Backend:** `flatten_sequence` (rein: Reihenfolge, Offsets, Multi-Asset, Szenenlänge); `resolve_clip_rows`-Weiche; Assemble-API (get-or-create, PUT set/reorder + Materialisierung + 422/404); **Sequenz-Render** (echtes ffmpeg: zwei Szenen aus zwei Assets → ein MP4). **Frontend (vitest, plain asserts):** `useSequence`, `AssembleView` (Drag-Reorder → genau ein `setSequenceScenes`-PUT mit neuer Ordnung), 5b `SequencePlayer` (Quellenwechsel an Clip-Grenze, Preload).

## 11 · Nicht in v1 (YAGNI)
Kein **Sequenz-Audio** (per-Szene-Musik in den Gesamt-Render — eigener Folge-Spec; v1 Gesamt-Render = Video); keine Übergänge; keine Mehrspur; kein Spur-Zoom-Feintuning (einfaches scrollbares Reorder); kein Multi-Select-Massen-Move (Einzel-Drag reicht v1).

## 12 · Offene Punkte
- Sequenz-Audio (Musik je Szene über die Gesamtsequenz) — Folge-Spec; braucht per-Clip-Audio-Provenienz im Flatten + Render-Mix mehrerer Quellen.
- Nahtlosigkeit des `SequencePlayer` an Clip-Grenzen (Source-Switch-Latenz) — v1 best-effort; ggf. später Doppel-Element-Crossfade.
- Aufräumen verwaister `kind="scene"`/`kind="sequence"`-Timelines beim Neu-Anordnen — wie in Rough/Feinschnitt v1 akzeptiert.
