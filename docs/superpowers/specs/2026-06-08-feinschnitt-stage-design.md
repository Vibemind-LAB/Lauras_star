# Feinschnitt (Stufe 4) — Detail-Spec

- **Datum:** 2026-06-08
- **Status:** Entwurf (vom User freigegeben — „ok")
- **Betrifft:** `services/local-api` (Szene-Materialisierung, Transkript-Schnitt-Op, Musik + Render-Mix) · `apps/desktop` (Feinschnitt-Ansicht)
- **Baut auf:** [Rough-Cut-Spec](2026-06-08-rough-cut-stage-design.md) (`scenes`-Tabelle, Szenen tilen den `rough_cut` frame-exakt/end-exclusive auf Clip-Grenzen) · vorhandene Editing-Operationen (`editing/operations.py`, `/timelines/{id}/operations`) · Render (`render/mp4.py`, aktuell video-only).
- **Zweite von drei Bespoke-Stufen** (Rough Cut ✓ → **Feinschnitt** → Zusammenfügen). Erzeugt die editierten Szene-Sub-Timelines, die Stufe 5 anordnet.

## 1 · Vision

Der Pro-Szene-Editor. Eine Szene (Marker über dem Rough-Cut) wird beim Öffnen **lazy in eine eigene `kind="scene"`-Timeline materialisiert** und ab da als echte Sub-Timeline editiert: **Clip-Trim**, **transkript-basierter Ripple-Schnitt** (Wort/Segment löschen), **eine Musikspur** pro Szene. Alle Schnitte laufen über die bestehende, getestete `/operations`-Pipeline — kaum neues Edit-Backend.

Leitentscheidungen (vom User bestätigt):
- **(a) Musik als Szenen-Metadaten** (nicht als Timeline-Lane-Clip) — hält den getesteten Editing-Kern (`EditClip`/ops) unangetastet; „ein Musik-Asset pro Szene".
- **(b) `delete_words`** als neue **Ripple**-Operation (Lücke schließen) — Editorial-Standard für Füllwörter.
- **(c) Bau in zwei Inkrementen:** 4a Core (Materialisierung + Trim + Transkript-Schnitt), 4b Audio (Musik + Render-Mix) — isoliert den riskanten Render-Eingriff.

## 2 · Invarianten

- Edits **immer in Ganzzahl-Frames**, **end-exclusive**, über die bestehenden Operationen (die diese Invarianten schon wahren).
- Materialisierung ist **verlustfrei**: Szene-Slice 1:1 aus dem Rough-Cut, nur `seq` re-based.
- **OTIO bleibt Wahrheitsquelle** jeder Timeline (Materialisierung + jede Operation regenerieren OTIO).
- Transkript/Audio in **Samples** (Quelle); Frames sind Projektion. Wort→Seq-Mapping rechnet in Frames (Speed=1 in v1).
- **Idempotenz:** `open` materialisiert eine bereits materialisierte Szene nie neu.

## 3 · Datenmodell (Migration `0009_scene_edit.sql`)
```sql
ALTER TABLE scenes ADD COLUMN scene_timeline_id TEXT;                  -- materialisierte Sub-Timeline (nullable)
ALTER TABLE scenes ADD COLUMN music_asset_id TEXT;                     -- ein Musik-Asset pro Szene (nullable)
ALTER TABLE scenes ADD COLUMN music_gain_percent INTEGER NOT NULL DEFAULT 100;
```
Keine Änderung an `timeline_clips`/`EditClip` (Musik ist Szenen-Metadatum, kein Clip). `SceneOut` wird um `scene_timeline_id`, `music_asset_id`, `music_gain_percent` erweitert.

## 4 · Backend — Inkrement 4a (Core)

### 4.1 Materialisierung
`POST /scenes/{scene_id}/open` → `TimelineOut`, idempotent:
1. `scene = get_scene`; 404 wenn fehlt.
2. Wenn `scene["scene_timeline_id"]` gesetzt → `get_timeline` + Clips zurückgeben (laden).
3. Sonst: `create_timeline(project_id, name=scene["name"], kind="scene")`; aus `list_timeline_clips(scene["source_timeline_id"])` den Slice `[scene.seq_in, scene.seq_out)` nehmen (Grenzen liegen auf Clip-Grenzen → ganze Clips), jeden Clip **re-based** (`seq_in -= scene.seq_in`, `seq_out -= scene.seq_in`, `src`/Rest unverändert); `replace_timeline_clips`; OTIO via `update_timeline_otio(timeline_to_otio_string(_build_model(...)))`; `set_scene_timeline(scene_id, new_id)`; Timeline zurückgeben.

Repos neu: `set_scene_timeline(db, scene_id, timeline_id)`.

### 4.2 Transkript-Schnitt — Operation `delete_words`
Erweitert `OperationRequest` (Felder `word_start_id`/`word_end_id` existieren bereits) und den `_apply`-Dispatch in `api/timelines.py`:
```
if op == "delete_words":
    w0 = repos.get_word(db, word_start_id); w1 = repos.get_word(db, word_end_id)   # 404/422 wie append_from_words
    lo, hi = min(w0.start_frame, w1.start_frame), max(w0.end_frame, w1.end_frame)   # Asset-Frames
    # auf Seq mappen über die aktuellen Clips desselben Assets (Speed=1):
    seq_in, seq_out = map_asset_range_to_seq(current, asset_id=w0.asset_id, src_lo=lo, src_hi=hi)
    return delete_range(current, seq_in, seq_out)   # Ripple
```
`map_asset_range_to_seq` (reine Helferfunktion in `editing/`): erster überlappender Clip → `seq_in = clip.seq_in + (max(lo, clip.src_in) − clip.src_in)`; letzter überlappender Clip → `seq_out = clip.seq_in + (min(hi, clip.src_out) − clip.src_in)`. Bei keinem Treffer → `422`. Wort-Klick in der `TranscriptBar` ruft `op:"delete_words"`. `lift` (Lücke stehen lassen) bleibt über die bestehende `lift`-Op verfügbar.

### 4.3 Trim
Kein neues Backend — `trim`/`split`/`move`/`delete`/`lift` via `/operations` gegen die Szene-Timeline; Undo/Redo via `setClips` (wie Rough-Cut-Timeline-Bar).

## 5 · Backend — Inkrement 4b (Audio)

### 5.1 Musik-API
- `PUT /scenes/{scene_id}/music` `{asset_id, gain_percent}` → `SceneOut` (validiert: Asset existiert + ist Audio/Medien-Asset; `0 ≤ gain_percent ≤ 400`). Repos `set_scene_music`.
- `DELETE /scenes/{scene_id}/music` → `SceneOut`. Repos `clear_scene_music` (setzt `music_asset_id=NULL`, `gain=100`).

### 5.2 Render-Mix
`render_clips_mp4(...)` bekommt optional `music: tuple[Path, int] | None` (Pfad, gain_percent). Mit Musik:
- Zusätzlicher Input `-i {music}`; Audiozweig `[{N}:a]volume={gain/100},atrim=0:{video_dur_s}[aout]`; `-map "[out]" -map "[aout]"` (statt nur `[out]`); `video_dur_s = total_seq_frames * rate_den / rate_num`.
- **Ohne Musik unverändert** (`a=0`, wie heute) → rückwärtskompatibel, bestehende Export-Tests bleiben grün.
Der Render-Handler (`render/handlers.py`) schlägt für die zu rendernde Timeline die zugehörige Szene nach (Szene mit `scene_timeline_id == timeline_id`) und übergibt deren Musik. Kein Ducking, kein Loop.

## 6 · Frontend (neue Dateien → kollisionssicher)
- `api.ts` additiv: `Scene`-Felder ergänzen; `openScene(sceneId) → Timeline`; `deleteWords(timelineId, wordStartId, wordEndId) → Timeline` (über `applyOperation`); `setSceneMusic(sceneId, assetId, gainPercent) → Scene`; `removeSceneMusic(sceneId) → Scene`.
- `hooks/useSceneTimeline.ts`: öffnet/materialisiert die gewählte Szene, hält deren Timeline + Edit-Calls (trim/split/delete/delete_words/move) + Undo/Redo.
- `components/FineCutView.tsx`: **Szenen-Liste** (prev/next, aus `useScenes`) · **Player** (diese Szene) · **TimelineBar** (Szene-TL) · **TranscriptBar** (Wort-Klick = `delete_words`) · **SceneInspector** (Trim) · **Musik-Picker + Gain-Slider** (4b) · best-effort `<audio>`-Preview der Musik (4b).
- Reuse: `Player`, `TimelineBar`, `TranscriptBar`, `SceneInspector`, `useScenes`.
- `App.tsx`: Branch `stage === "finecut"` → `<FineCutView/>` als **allerletzter** ~3-Zeilen-Schritt (kollisionssicher; nur wenn `App.tsx` clean ist, sonst dem User überlassen).

## 7 · Datenfluss
Stufe 4 öffnen → Szenen-Liste (aus Rough Cut) → Szene wählen → `open` materialisiert/lädt die Szene-Timeline → Player+Timeline+Transkript zeigen sie → Trim / Wort-Klick-Schnitt justieren → (4b) Musik wählen + Gain → Szene fertig für Stufe 5.

## 8 · Bau-Sequenz
**4a Core:** Migration (`scene_timeline_id`) · Materialisierung + Repo · `map_asset_range_to_seq` + `delete_words` · api.ts + `useSceneTimeline` + `FineCutView` (Trim + Transkript-Schnitt) · App.tsx-Verdrahtung. → eigenständig lauffähig/testbar.
**4b Audio:** Migration (`music_*`) · music-API + Repos · Render-Mix + Handler-Lookup · Musik-UI + Gain + Preview. → zweiter Plan; isolierter Render-Eingriff.

## 9 · Fehler-/Leerzustände
Keine Szenen → CTA „Erst Rough Cut ausführen". Szene ohne Materialisierung → `open` macht's transparent. `delete_words` ohne Treffer → `422` + nicht-blockierende Fehlerzeile. Kein Musik-Asset → Picker leer; Audio-Import-Hinweis.

## 10 · Tests
**Backend:** Materialisierung (Slice+Rebase korrekt, idempotent, OTIO aktualisiert); `map_asset_range_to_seq` (rein, Frame-Mapping inkl. Teil-Clip-Überlappung) + `delete_words` Ende-zu-Ende (Ripple schließt Lücke, `422` ohne Treffer); music-API (set/clear, Validierung); Render-Mix (echter ffmpeg: mit Musik → Audiospur vorhanden, ohne → wie heute).
**Frontend (`vitest`, plain asserts):** `useSceneTimeline` (open + edit), `FineCutView` (Szenenwahl, Wort-Klick→delete_words, Musik-Picker+Gain).

## 11 · Nicht in v1 (YAGNI)
Kein Ducking, kein Musik-Loop, max. 1 Musik-Asset/Szene; kein speed-aware Transkript-Schnitt (v1 Speed=1); keine Mehrspur über die eine Musikspur hinaus; kein assetübergreifendes Sequenz-Audio (Stufe 5); Live-Musik-Preview nur best-effort (exakte Sync später).

## 12 · Offene Punkte
- Re-Generieren/Splitten von Szenen in Rough Cut **nach** Materialisierung verwaist die `scene_timeline_id`-Verknüpfung (alte Szene-Timelines bleiben unreferenziert). v1: akzeptiert (kein Cleanup); ggf. späterer Aufräum-Job.
- Speed-aware Wort→Seq-Mapping (wenn ein Clip retimed wurde) — v1-Limitation, Mapping nimmt Speed=1 an.
