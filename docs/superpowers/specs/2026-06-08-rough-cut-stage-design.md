# Rough Cut (Stufe 3) — Detail-Spec

- **Datum:** 2026-06-08
- **Status:** Entwurf (vom User freigegeben — „okay mach")
- **Betrifft:** `services/local-api` (Szenen-Modell + Gruppierung + API) · `apps/desktop` (Rough-Cut-Ansicht)
- **Baut auf:** [`2026-06-03-editorial-pipeline-architecture-design.md`](2026-06-03-editorial-pipeline-architecture-design.md) (Gesamtarchitektur, freigegeben) · vorhandenes `shots`/`timelines`/`transcript`-Backend · `build_from_shots`.
- **Erste von drei Bespoke-Stufen** (Rough Cut → Feinschnitt → Zusammenfügen); jede bekommt eigenen Spec+Plan. Diese hier erzeugt die **Szenen**, auf denen Stufe 4 und 5 aufbauen.

## 1 · Vision & mentales Modell

Rough Cut macht aus einem analysierten Asset (Shots + Transkript) die neue Editorial-Einheit **Szene**. Eine Szene ist ein **zusammenhängender Bereich des Rough-Cut** (der Folge gekeepter Shots eines Assets). Der User sieht unter dem Haupt-Player einen **Szenen-Strip** und justiert Grenzen per **Teilen (✂)** / **Zusammenführen (⇄)**.

Leitentscheidungen (vom User bestätigt):
- **(a) Grenzen liegen ausschließlich auf Clip-/Shot-Grenzen** — nie mitten in einem Shot. Damit ist alles frame-exakt und es gibt kein src↔seq-Remap innerhalb eines Shots.
- **(b) „Szenen erzeugen" subsumiert den Rough-Cut-Build** — die Aktion stellt erst den gekeepten Rough-Cut sicher (vorhandenes `build_from_shots`) und gruppiert ihn dann.
- **(c) Per-Asset, nicht projektweit** — Szenen entstehen je Asset; das assetübergreifende Anordnen ist Stufe 5.

Eine Szene ist hier ein **leichter Grenz-Marker** über dem Rough-Cut (Identität: ID/Name/Reihenfolge/Frame-Range). Die schwere `kind="scene"`-Sub-Timeline wird **erst in Stufe 4** materialisiert, wenn eine Szene wirklich editiert wird (lazy, YAGNI).

## 2 · Invarianten (Kern des Produkts)

- Szenen-Grenzen in **Ganzzahl-Frames**, **end-exclusive** (`seq_out_frame_exclusive`), relativ zur Source-Timeline.
- Grenzen sind **immer == Clip-Grenzen** des Rough-Cut (Validierung erzwingt das).
- Transkript-/Audio-Zeiten bleiben in **Samples** (Quelle); Frames sind eine Projektion für Gruppierung/UI.
- Szenen **kacheln** den Rough-Cut **lückenlos und überschneidungsfrei** in Timeline-Reihenfolge; jeder Clip gehört zu genau einer Szene.
- **OTIO bleibt Wahrheitsquelle** der Timeline; Szenen sind eine Annotation darüber, kein zweiter Timeline-Zustand.

## 3 · Datenmodell (neu)

`services/local-api/src/laura/db/migrations/0008_scenes.sql`:

```sql
CREATE TABLE scenes (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  source_timeline_id TEXT NOT NULL,          -- der rough_cut, über dem die Szene liegt
  name TEXT NOT NULL,                         -- Default "Szene N", umbenennbar
  order_index INTEGER NOT NULL,               -- 0..n-1 in Timeline-Reihenfolge
  seq_in_frame INTEGER NOT NULL,              -- Ganzzahl-Frames, end-exclusive
  seq_out_frame_exclusive INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_scenes_timeline ON scenes (source_timeline_id, order_index);
```

Keine Änderung an `timelines`/`timeline_clips`. `(source_timeline_id, order_index)` ist die natürliche Ordnung.

## 4 · Backend

### 4.1 Gruppierungs-Service
`services/local-api/src/laura/scenes/grouping.py`:

```
group_into_scenes(clips, words_by_clip, *, gap_frames) -> list[tuple[int, int]]
```
- **Input:** geordnete Rough-Cut-Clips (je = ein gekeepter Shot, mit `seq_in/seq_out`, `src`-Range, `asset_id`) + die Transkript-Wörter (gleiches Asset), je Clip via `src`-Range-Schnitt zugeordnet (Wort-Frames im Asset-Raum).
- **Pro Clip** ableiten: dominanter `speaker_id`, erstes/letztes Wort (Asset-Frame) — oder „kein Transkript-Cover".
- **Grenze zwischen Clip i und i+1**, wenn **(1)** Sprecherwechsel **oder** **(2)** Stille-Lücke zwischen letztem Wort von i und erstem Wort von i+1 ≥ `gap_frames`. Clip 0 beginnt immer Szene 1.
- **Fallback** wenn das ganze Asset kein Transkript-Cover hat: **„1 Shot = 1 Szene"** (jeder Clip eigene Szene) — vom User manuell mergebar.
- **Output:** Liste von `(seq_in_frame, seq_out_frame_exclusive)` — Grenzen liegen per Konstruktion auf Clip-Grenzen.
- **`gap_frames` Default** = `round(1.5 * rate_num / rate_den)`; per Request übersteuerbar.
- Reine Funktion, deterministisch, voll unit-testbar (kein ffmpeg/Modell nötig).

### 4.2 Repos
`services/local-api/src/laura/db/repos.py` (neu):
- `create_scene(db, *, project_id, source_timeline_id, name, order_index, seq_in_frame, seq_out_frame_exclusive) -> dict`
- `list_scenes(db, source_timeline_id) -> list[dict]` (nach `order_index`)
- `replace_scenes(db, source_timeline_id, rows) -> None` (atomar: löschen + neu schreiben; für generate/split/merge)
- `get_scene(db, scene_id) -> dict | None`
- `update_scene_name(db, scene_id, name) -> None`

### 4.3 API
`services/local-api/src/laura/api/scenes.py` (neuer Router, in `create_app` registriert); Modelle in `api/models.py`:

| Methode | Pfad | Body | Antwort | Wirkung |
|---|---|---|---|---|
| POST | `/timelines/{id}/scenes:generate` | `GenerateScenesRequest{asset_id, gap_frames?}` | `list[SceneOut]` | stellt Rough-Cut sicher (`build_from_shots`), gruppiert, `replace_scenes` |
| GET | `/timelines/{id}/scenes` | — | `list[SceneOut]` | Szenen der Timeline |
| POST | `/timelines/{id}/scenes/{sid}/split` | `SplitSceneRequest{at_seq_frame}` | `list[SceneOut]` | teilt Szene an innerer Clip-Grenze |
| POST | `/timelines/{id}/scenes/merge` | `MergeScenesRequest{scene_id}` | `list[SceneOut]` | merge Szene mit Folge-Szene |
| PATCH | `/scenes/{sid}` | `RenameSceneRequest{name}` | `SceneOut` | umbenennen |

`SceneOut{ id, project_id, source_timeline_id, name, order_index, seq_in_frame, seq_out_frame_exclusive }`.

**Validierung / Fehler:**
- `split.at_seq_frame` muss eine Clip-Grenze **echt innerhalb** der Szene sein → sonst `422`.
- `merge.scene_id` braucht eine Folge-Szene (nicht die letzte) → sonst `422`.
- `:generate` ist **idempotent**: ersetzt vorhandene Szenen des Timelines (kein Duplikat).
- Nach jeder Mutation `order_index` lückenlos neu vergeben; Kacheln bleibt invariant.
- Timeline/Asset fehlt → `404`.

## 5 · Frontend (alle Dateien NEU → kollisionssicher)

- `apps/desktop/src/api.ts` — **additiv:** Typ `Scene` + `generateScenes(timelineId, assetId, gapFrames?)`, `listScenes(timelineId)`, `splitScene(timelineId, sceneId, atSeqFrame)`, `mergeScenes(timelineId, sceneId)`, `renameScene(sceneId, name)`. Keine vorhandene Methode wird umgebaut.
- `apps/desktop/src/hooks/useScenes.ts` — lädt/generiert Szenen, split/merge/rename, `loading`/`error`.
- `apps/desktop/src/components/SceneStrip.tsx` — horizontaler Strip; pro Szene: **Frame-Thumbnails** (Shots im Frame-Bereich; `ShotStrip`-Muster wiederverwenden), **Transkript-Auszug** (Segmente im Bereich), **Name**, **✂/⇄**. Klick → `onSeek(scene.seq_in_frame)`.
- `apps/desktop/src/components/RoughCutView.tsx` — Stufe-3-Layout: großer **Player** (reuse) + Transport/Ruler + Aktion **„Szenen erzeugen"** + unten `SceneStrip`. Props: `{ client, projectId, asset, roughCut, seek, currentFrame, onSeek, onFrame }`. Leerzustände inklusive.

**Reuse:** `Player`, `ShotStrip`, Transkript-Render, `useAnalysis`.

## 6 · Datenfluss (Ende-zu-Ende)

1. User wählt Asset (vorhandene Medien-Liste).
2. Falls nötig: Analyse (vorhandener Flow) → Shots + Transkript.
3. **„Szenen erzeugen"** → Backend baut/findet Rough-Cut, gruppiert, schreibt Szenen.
4. `SceneStrip` zeigt Szenen; **✂/⇄/umbenennen** justiert Grenzen (jeweils `replace_scenes`).
5. Klick auf Szene → Player springt zu `seq_in_frame`.
6. Szenen liegen für Stufe 4 (Feinschnitt) / Stufe 5 (Zusammenfügen) bereit.

## 7 · Kollisionssichere Bau-Strategie

Der User arbeitet parallel an `App.tsx` im selben Working-Tree. Daher:
- **Kein Branch-Wechsel** (würde den Working-Tree stören). Arbeit bleibt auf dem aktuellen Branch.
- Alle neuen Komponenten sind **eigene Dateien**.
- **Einziger `App.tsx`-Eingriff:** im Zweig `stage === "roughcut"` statt des generischen Layouts `<RoughCutView … />` rendern (finecut/assemble bleiben vorerst auf dem geteilten Layout). Dieser ~3-Zeilen-Swap ist der **allerletzte Schritt**, lokalisiert, nur wenn `App.tsx` gerade committet/clean ist — oder wird dem User überlassen.
- Git-Hygiene: nur die jeweils genannten Dateien stagen; nie `.claude/`, `build/`, `uv.lock`.

## 8 · Fehler-/Leerzustände (UI)

- Kein Asset gewählt → CTA „Asset in Import wählen".
- Keine Shots/Analyse → CTA „Erst analysieren".
- Kein Transkript → Fallback-Gruppierung läuft + Hinweis „ohne Transkript gruppiert".
- Backend-Fehler → nicht-blockierende Fehlerzeile (Muster wie `ExportView`).

## 9 · Tests

**Backend (`pytest`, kein ffmpeg/Modell nötig):**
- `grouping`: Sprecherwechsel → Grenze; Stille-Lücke ≥/< Schwelle; Fallback ohne Transkript; alle Grenzen == Clip-Grenzen.
- Repos: `create/list/replace/get/update_scene_name` (Kacheln + `order_index` invariant).
- API: `:generate` idempotent; `split` happy + `422` (Nicht-Grenze); `merge` happy + `422` (letzte Szene); `404`.

**Frontend (`vitest`, plain asserts — keine jsdom-Matcher):**
- `SceneStrip` rendert N Szenen mit Name/Transkript; Klick ruft `onSeek`.
- `useScenes`: generate/split/merge aktualisieren State.

## 10 · Nicht in v1 (YAGNI)

- Keine `kind="scene"`-Sub-Timelines hier (erst Stufe 4, lazy materialisiert).
- Kein Szenen-Reorder (Reihenfolge = Timeline-Position; freies Anordnen ist Stufe 5).
- Keine projektweite/assetübergreifende Szenenbildung (Stufe 5).
- Keine Übergänge, kein Audio/Musik (Stufe 4+).

## 11 · Offene Punkte

- `gap_frames`-Default (1.5 s) ist eine erste Schätzung; nach erstem realen Material evtl. justieren — kein Blocker (per Request übersteuerbar).
- „Dominanter Sprecher pro Clip" bei gemischten Clips: v1 nimmt den Sprecher des **ersten** Worts im Clip; reicht, da Grenzen ohnehin manuell justierbar.
