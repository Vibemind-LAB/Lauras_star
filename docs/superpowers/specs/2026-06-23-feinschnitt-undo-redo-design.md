# Feinschnitt Undo/Redo — Design (Backend-Checkpoint-Stack)

- **Datum:** 2026-06-23
- **Status:** Entwurf zur Abnahme (v2 — korrigiert nach adversarialem Code-Review, 6 Reviewer)
- **Branch (Ziel):** Feature-Branch von `main` (`63cb487`)
- **Verfahren:** brainstorming → writing-plans → subagent-driven-development

## 1 · Kontext & Motivation

Der Feinschnitt (`FineCutView` + Hook `useRoughCutTranscript`) editiert die **eine durchgehende
Rough-Cut-Timeline** direkt: Wort-Löschen, Klick→Schnitt→neue Szene, Text-ersetzen→automatischer
Voice-Over, dazu die Tool-Panels Übergänge, Reenact, Lipsync und Szenen-Musik. Diese Edits schreiben
**direkt** in die DB und laden neu — es gibt **kein Undo**. Der `TimelineBar` (Zusammenfügen) hat ein
in-memory **button-only** Clips-Undo/Redo, das nur Clips abdeckt und nicht durchgehend/langlebig ist.

Wunsch des Users: **mehrstufiges Undo + Redo über *alle* Feinschnitt-Aktionen**, langlebig.

## 2 · Ziele / Nicht-Ziele

**Ziele**
- Mehrstufiges Undo **und** Redo für den gesamten editorischen Zustand der Rough-Cut-Timeline:
  Clips (inkl. Übergänge & Overlays), Szenen (inkl. Szenen-Musik), Audio-Clips (VO), Transition-Reviews.
- Langlebig: übersteht Reload, Szenen-Sprünge (eine durchgehende Timeline) und App-Neustart.
- Voice-Over/Lipsync/Reenact undo-bar **ohne Neu-Rendern** (nur Referenzen werden zurückgesetzt).
- Zwei Buttons im `EditorialToolsBar` + neue Tastatur-Bindung (Strg+Z / Strg+Umschalt+Z).

**Nicht-Ziele**
- Kein selektives „diesen einen Edit von vor drei Schritten" rückgängig (nur linearer Stack).
- Kein Undo über Timeline-Grenzen hinweg (Historie ist **pro Rough-Cut-Timeline**).
- **`scene_timeline_id`-materialisierte Sub-Timelines** (alter Per-Szene-Editierpfad, `FS4`) sind
  **nicht** Teil des Feinschnitt-Flows und werden nicht versioniert. *Plan-Task 1 verifiziert*, dass
  `useRoughCutTranscript` ausschließlich gegen die Rough-Cut-`timeline_id` schreibt (Docstring:
  „CONTINUOUS rough-cut timeline, not isolated scene copies").
- Kein Zwischenspeichern eines *noch generierenden* VO für Redo (bewusste Grenze, §6).

## 3 · Invarianten (siehe `CLAUDE.md`)

1. Edits in **Ganzzahl-Frames** relativ zur Sequence. Snapshots speichern die Frame-Felder roh.
2. Ranges **end-exclusive** (`seq_out_frame_exclusive`). Restore ändert keine Semantik.
3. Audio/Alignment in **Samples** (`audio_offset_samples`). Snapshot speichert die Spalte roh.
4. **OTIO = Source of Truth** — konkret ist `timeline_clips` die Wahrheit, `timelines.otio_json` ein
   daraus regenerierter Cache (verifiziert: `otio_sync.py:146-153`). Restore setzt die DB-Zeilen und
   ruft **danach** `rebuild_otio` als **separaten, idempotenten** Schritt (§5).
5. **Idempotenz**: Undo/Redo verändern keine Analyse-Artefakte, nur den editorischen Timeline-Zustand.

## 4 · Architektur (Ansatz A — Backend-Checkpoint-Stack)

Pro Rough-Cut-Timeline zwei Stapel (Undo/Redo) von **Pre-Edit-Snapshots** des gesamten editorischen
Zustands. Verifizierte Bausteine (Review, minor #2): `serialize_timeline_otio`/`rebuild_otio`
(`otio_sync.py:140-203`), `cancel_job` (`repos.py:119`), das `transition_reviews`-Persistenzmuster
(`0024`), und das „skip-otio-then-rebuild"-Muster von `cut_at_frame` (`scenes.py:144`).

### 4.1 Datenmodell — Migration `0029_timeline_history.sql`

Gespiegelt an `0024_transition_reviews.sql` (gleiches CASCADE-Muster, `schema_version` → 29):

```sql
CREATE TABLE timeline_history (
  id            TEXT PRIMARY KEY,
  timeline_id   TEXT NOT NULL REFERENCES timelines(id) ON DELETE CASCADE,
  seq_no        INTEGER NOT NULL,   -- monoton steigend pro timeline_id
  stack         TEXT NOT NULL,      -- 'undo' | 'redo'
  label         TEXT NOT NULL,      -- z.B. "Wörter gelöscht", "Stimme ersetzt"
  payload_json  TEXT NOT NULL,      -- Snapshot (§4.2)
  created_at    TEXT NOT NULL,
  CHECK (stack IN ('undo','redo'))
);
CREATE INDEX idx_timeline_history_lookup ON timeline_history(timeline_id, stack, seq_no);
```

> **Kein `job_id`-Feld** (anders als v1): Eine VO-Aktion löst **zwei** Jobs aus (VO kettet synchron
> `ai.lipsync`, `handlers.py:404-413,578`), und `enqueue` kann per Idempotenz eine *fremde* Job-ID
> zurückgeben (`runner.py:78-84`). Die „läuft noch ein Job?"-Sperre (§6) fragt deshalb **timeline-weit**,
> nicht über eine gespeicherte Einzel-ID.

### 4.2 Snapshot-Umfang (`payload_json`) — vollständige editorische Zeilen *einer* Timeline

```json
{
  "clips":       [ <timeline_clips WHERE timeline_id = T — ALLE Spalten> ],
  "scenes":      [ <scenes WHERE source_timeline_id = T — ALLE Spalten> ],
  "audio_clips": [ <timeline_audio_clips WHERE timeline_id = T — nur VO> ],
  "transitions": [ <transition_reviews WHERE timeline_id = T> ]
}
```

Korrekturen aus dem Review (load-bearing):

- **`clips` = volle Spaltenliste.** `timeline_clips` trägt mehr als Geometrie: zusätzlich `role`
  (`0014`), `transition_after_kind`/`transition_after_frames` (`0023`), `linked_audio_group` (`0001`).
  Der vorhandene Helfer `replace_timeline_clips` (`repos.py:1046-1064`) **lässt genau diese weg** —
  er darf **nicht** wiederverwendet werden. Capture & Restore machen einen **literal full-column
  round-trip** (`SELECT *` → INSERT mit *allen* aktuellen Spalten). Ein `PRAGMA table_info`-Regressionstest
  (§10) erzwingt, dass die Snapshot-Spaltenmenge == Live-Tabellenspalten ist (fängt künftige `ADD COLUMN`).
- **`scenes` werden über `source_timeline_id` gesammelt/wiederhergestellt** — die `scenes`-Tabelle hat
  **kein** `timeline_id` (`0008`). Voller Zeilen-Roundtrip inkl. `music_asset_id`, `music_gain_percent`
  (`0009` — **hier** lebt die Szenen-Musik, *nicht* in `timeline_audio_clips`), `scene_timeline_id`,
  `order_index`, `name`.
- **`audio_clips` = nur VO.** `timeline_audio_clips` erhält ausschließlich Voice-Over-Clips
  (`handlers.py:561`). Szenen-Musik ist über die `scenes`-Gruppe abgedeckt.
- **Generierte Medien** (`media_assets`/`asset_files` für VO/Lipsync/Reenact) sind **nicht** im Snapshot
  und werden bei Undo **nicht** gelöscht. Nur die *referenzierenden* Zeilen sind erfasst → Undo eines VO
  entfernt nur die Referenz (Original-Audio spielt wieder), **kein Neu-Rendern**.
- `timelines.otio_json` wird **nicht** gespeichert (Cache, nach Restore via `rebuild_otio` regeneriert).

### 4.3 Checkpoint-Wrapper & **konkrete** Coverage-Liste

Kontextmanager `editing/history.py::timeline_checkpoint(db, timeline_id, label)` schreibt **vor** der
Mutation einen Pre-Edit-Snapshot auf den Undo-Stapel, leert den Redo-Stapel, kappt die Tiefe (50).

**Synchron zu umschließende Routen** (jede mutiert die Timeline *innerhalb* des Requests):

| Route | Datei | mutiert |
|---|---|---|
| `POST /timelines/{id}/operations` (`apply_operation` — delete/lift/split/trim/move/set_speed/set_audio_offset/append/insert_clip/…) | `api/timelines.py:944` | clips |
| `POST /timelines/{id}/cut-at-frame` | `api/scenes.py:115` | clips, scenes |
| `POST /timelines/{id}/scenes/{sid}/split`, `…/scenes/merge`, `PATCH /scenes/{sid}` (rename), `POST /timelines/{id}/scenes:generate` | `api/scenes.py:111,210,219,61` | scenes (generate auch clips) |
| `PATCH /timelines/{id}/clips/{cid}/transition` (`set_clip_transition`) | `api/timelines.py:1029` | clips |
| `POST/PATCH/DELETE /timelines/{id}/audio-clips[/{cid}]` | `api/audio.py:93,126,142` | audio_clips |
| `POST/DELETE /timelines/{id}/overlays[/{cid}]` | `api/overlays.py:64,98` | clips |
| `PUT/DELETE /scenes/{sid}/music` | `api/scenes.py:246,260` | scenes |

> **Routen, die nur per `scene_id` adressiert sind** (Szenen-Musik): der Wrapper löst `timeline_id`
> zuerst über `scene["source_timeline_id"]` auf, bevor er snapshottet.

**Async-Dispatch-Routen** `POST /voiceover` (`api/voiceover.py:46`), Reenact, Lipsync: diese
**enqueuen nur** (HTTP 202) und schreiben synchron **nichts** in die Timeline — der echte Clip-Write
passiert später im Job-Handler (`handlers.py:561/811/280`). Der Wrapper an der Dispatch-Route nimmt also
nur den korrekten **Pre-VO-Snapshot**; die spätere Handler-Mutation wird durch das **Live-Diff** (§6)
beim nächsten Undo erfasst, nicht durch den Wrapper.

**Coverage-Test mit Allowlist (§10).** Ein Test stellt sicher, dass **jede** Schreibstelle auf
`timeline_clips`/`scenes`/`timeline_audio_clips` unter `timeline_checkpoint` läuft — **außer** der
explizit erlaubten:
1. die Restore-Primitive (`set_timeline_clips` + die neue `restore_timeline_snapshot`, §5),
2. die drei Async-Handler-Writes in `ai/handlers.py` (`add_timeline_audio_clip`/`delete_timeline_audio_clips_overlapping` im VO-Handler, `add_timeline_clip` in Lipsync/Reenact) — bewusst außerhalb des Wrappers, durch §6 abgedeckt.

So ist „Alles" *strukturell* garantiert: ein künftiges Tool, das durch den Wrapper schreibt, ist
automatisch undo-bar; alles andere fällt im Test auf.

## 5 · Undo/Redo-Semantik & Atomarität (korrigiert)

- **Edit:** `timeline_checkpoint` pusht Pre-Edit-Snapshot auf Undo, leert Redo.
- **Undo:** Live-Zustand als Snapshot auf **Redo**; obersten **Undo**-Snapshot wiederherstellen. **Redo**
  spiegelbildlich.
- **Atomarität — realistisch.** Der DB-Layer kann **keine** tabellenübergreifende Transaktion über die
  bestehenden Repo-Helfer (jeder öffnet eine eigene Connection, `base.py:71-73`/`sqlite.py:42-52`).
  Lösung: **neue Repo-Funktion `restore_timeline_snapshot(db, timeline_id, payload)`**, die *eine*
  `with db.transaction() as conn:` über **alle** DELETE+INSERT der vier Gruppen hält (Roh-SQL, da kein
  vorhandener Helfer Cross-Table-Arbeit auf einer Connection macht). Damit ist der **Zeilen-Restore
  atomar**.
- **`rebuild_otio` ist ein separater, idempotenter Folgeschritt** (eigene Transaktion). Schlägt er fehl
  (z.B. fehlendes Asset in `build_model`), sind die **Zeilen korrekt wiederhergestellt** und nur
  `otio_json` ist veraltet-aber-regenerierbar; der Undo-Endpunkt rebuildet und behandelt einen
  Rebuild-Fehler als weiche Warnung (Zeilen-Restore gilt als Erfolg). Kein „Teilzustand" der Clips.

## 6 · Async-Jobs (VO / Lipsync / Reenact) — korrigiert

Snapshots sind „pre-edit". Weil Undo/Redo gegen den **Live**-DB-Zustand diffen, sind bereits **fertige**
Jobs (Audio-/Clip-Zeile schon geschrieben) korrekt erfasst — Undo schiebt den Live-Zustand (mit Zeile)
auf Redo und stellt den Pre-Edit-Snapshot her. Die Async-Handler-Writes laufen bewusst **außerhalb** des
Wrappers (Allowlist §4.3) — das ist genau, warum das Live-Diff sie erfasst.

**Laufende Jobs werden nicht „weggecancelt".** Review-Befund: `cancel_job` stoppt nur `queued`-Jobs;
bei `running` setzt es lediglich `cancel_requested=1`, und die AI-Handler **pollen das nie**
(`repos.py:119-136`, keine Cancel-Prüfung in `handlers.py`). Ein laufender VO-Job würde also seinen Clip
**nach** dem Undo anhängen. Deshalb:

- **Primärmechanismus: Undo/Redo sind gesperrt, solange für die Timeline ein nicht-terminaler Job
  existiert.** Der `GET /history`-Endpunkt (§8) ermittelt das, indem er nicht-terminale Jobs scannt und
  `payload_json.timeline_id` matcht (die `jobs`-Tabelle hat **keine** `timeline_id`-Spalte; die ID liegt
  nur im Payload — `runner.py:62-113`). N ist klein (wenige in-flight Jobs) → Scan ist günstig.
- Das deckt auch den **VO→Lipsync-Auto-Chain** (zwei Jobs) ab: gesperrt wird timeline-weit, nicht über
  eine Einzel-Job-ID.
- **Bewusste Grenze:** Hat man Text ersetzt und klickt Undo, *bevor* der VO fertig ist, ist Undo kurz
  gesperrt (Button disabled) statt einen halben Zwischenzustand zu erzeugen. Nach Job-Ende greift Undo
  ganz normal und entfernt den fertigen VO-Clip. (Kein `cancel_job` auf laufende AI-Jobs.)

## 7 · Tiefe & Langlebigkeit

- **Tiefe:** max. 50 Undo-Schritte/Timeline (Wrapper kappt den ältesten).
- **Langlebig:** Historie in der DB ⇒ übersteht Reload, Szenen-Sprünge (eine durchgehende
  Rough-Cut-Timeline; Szenenwechsel = Navigation, kein Timeline-Wechsel) und App-Neustart.
- **Redo-Invalidierung:** jeder neue Edit leert den Redo-Stapel.

## 8 · API

- `POST /timelines/{id}/undo` → `{ clips, scenes }` + `409`, wenn Undo-Stapel leer **oder** ein
  nicht-terminaler Job für die Timeline läuft.
- `POST /timelines/{id}/redo` → `{ clips, scenes }` + `409` analog.
- `GET  /timelines/{id}/history` → `{ canUndo, canRedo, undoLabel, redoLabel, busy }` (`busy` = §6-Sperre).
- Modelle in `api/models.py`; Routen in `api/timelines.py`.

## 9 · Frontend

- `api.ts`: `undo(timelineId)`, `redo(timelineId)`, `getHistory(timelineId)`.
- `useRoughCutTranscript`: ergänzt um `undo()`, `redo()`, `canUndo`, `canRedo`, `undoLabel`, `redoLabel`,
  `historyBusy`; nach Undo/Redo das vorhandene `reload()`; `getHistory` nach jeder Mutation +
  nach Undo/Redo. `lastVoJobId`/laufende Jobs speisen `historyBusy` (Server-`busy` ist die Wahrheit).
- `EditorialToolsBar`: zwei Buttons (↶ Rückgängig / ↷ Wiederholen), Tooltip = Label, `disabled` bei
  `!canUndo`/`!canRedo` oder `historyBusy`. Grün/weiß-Token (`accent`), kein `sky-*`. Button-Muster wie
  `TimelineBar` (button-only) spiegeln.
- **Tastatur — Neuland:** Es gibt **keinen** vorhandenen Keyboard-Undo (TimelineBar ist button-only;
  Review fand keinen `ctrlKey`/`keydown`-Undo-Handler). Strg+Z / Strg+Umschalt+Z wird **neu** an der
  `FineCutView` gebaut, mit **Fokus-Guard**: kein Auslösen, wenn der Fokus in einem Texteingabefeld liegt
  — speziell das „Neuer Text"-`<input>` der `ContinuousTranscript` (`:117-128`) hat eigenes Enter/Escape.

## 10 · Tests

**Backend (`uv run pytest`):**
- **Full-column-Roundtrip:** Snapshot→Restore byte-identisch für `timeline_clips` *inkl.* `role`,
  `transition_after_kind/frames`, `linked_audio_group`; `scenes` *inkl.* `music_asset_id`,
  `music_gain_percent`, `scene_timeline_id`, `order_index`; `audio_clips`; `transition_reviews`.
- **`PRAGMA table_info`-Regression:** Snapshot-Spaltenmenge == Live-Tabellenspalten (fängt künftige `ADD COLUMN`).
- Undo→Redo idempotent (A→Edit→B; Undo→A; Redo→B).
- Neuer Edit nach Undo leert Redo-Stapel.
- **VO-Undo** entfernt den Audio-Clip, **Asset bleibt auf Platte**.
- **Szenen-Musik-Undo:** set/clear `scenes.music_*` Roundtrip (deckt die korrigierte Tabellen-Lage ab).
- **Übergang/Overlay-Undo:** `set_clip_transition` bzw. Overlay-Add rückgängig — Übergänge/`role` bleiben erhalten.
- **Sperre bei laufendem Job:** mit nicht-terminalem Job für die Timeline → `undo`/`redo` `409`, `busy=true`.
- **Atomarität:** simulierter Fehler im Restore ⇒ kein Teilzustand der vier Gruppen.
- **Coverage-Allowlist-Test:** keine Timeline-Mutation am Wrapper vorbei außer der erlaubten (§4.3).
- Tiefen-Cap (51. Edit ⇒ ältester weg) + grobe Snapshot-Größe bei vielen Clips.

**Frontend (`npx tsc` + vitest):**
- Hook: `canUndo/canRedo/undoLabel/historyBusy` korrekt aus `getHistory`.
- Button-`disabled`-Logik inkl. `historyBusy`.
- Neuer Keyboard-Handler feuert undo/redo nur außerhalb von Textfeldern.

Schwere Modelle/ffmpeg sind nicht nötig (Snapshots sind reine DB-Zeilen).

## 11 · Risiken & Entscheidungen

- **Unvollständige Coverage** = stiller Undo-Verlust → konkrete Routen-Tabelle (§4.3) + Allowlist-Test (§10).
- **Unvollständiger Spalten-Roundtrip** (Review-Critical) → Full-column-Restore + `PRAGMA`-Regressionstest;
  `replace_timeline_clips` **nicht** wiederverwenden.
- **Atomarität** nur für den Zeilen-Restore garantiert (neue Ein-Connection-Funktion); `rebuild_otio`
  separat + idempotent (§5).
- **Laufende AI-Jobs nicht abbrechbar** → Undo/Redo timeline-weit gesperrt, solange ein Job offen ist (§6).
- **Snapshot-Größe** → Tiefen-Cap (50), kompaktes JSON.
- **Annahme** „Feinschnitt editiert nur die Rough-Cut-`timeline_id`" → Plan-Task 1 verifiziert sie, bevor
  gebaut wird (sonst wäre der Single-Timeline-Snapshot das falsche Aggregat).
- **Verifizierte Bausteine** (Review): `otio_json`-Cache, `rebuild_otio`, `serialize_timeline_otio`,
  `cancel_job` existieren mit den angenommenen Signaturen.
