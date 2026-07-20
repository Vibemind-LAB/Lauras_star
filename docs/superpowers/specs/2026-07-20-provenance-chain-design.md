# Provenienz-Kette & Voll-Suffix-Restore für das Production Board

**Datum:** 2026-07-20 · **Status:** vom User freigegeben · **Vorgänger:** der Entry-Guard-Restore
(`Board.restore_render_matching_script`, eingeführt `93dfe02`, review-widerlegt und entfernt in
`41ecc51`).

## Problem

Ein Revise oder `revert_artifact` invalidiert den Ketten-Suffix unterhalb der Änderung. Fertige,
teure Artefakte — die Stimme (echte TTS-Synthese), der Render (Minuten ffmpeg, zählt gegen
`_MAX_RENDER_CYCLES`) — liegen dann verwaist unter `versions/`, während der Resume-Vertrag
„neu bauen" liest. Der entfernte Erstversuch restaurierte den Render anhand des Skript-Texts
allein; die Review widerlegte das mit Live-Repro: **ein Render ist gleichermaßen eine Projektion
der konkreten Cutlist und der konkreten Stimme** (deren mp3 gemuxt wird), und `script_hash`
deckt beides nicht. Außerdem war ein Einzel-Glied-Restore im Motivationsfall selbstzerstörend:
Der Revise hatte auch Voice/Cutlist/Sheet gewischt, der Pflicht-Rebuild invalidierte den
restaurierten Render, bevor ihn irgendetwas nutzte.

## Entscheidungen (User)

1. **Auslöser:** automatisch bei jedem Resume (Board-Öffnung in `run_production`).
2. **Reichweite:** voller Suffix **inklusive `qa_report`** — ein komplett kohärentes Board
   erreicht `complete: True` ohne Agent-Turn.
3. **Mechanismus:** Ansatz A — einheitliche Content-Hash-Eltern (statt Bespoke-Feldern pro
   Glied oder Epochen-Zählern; Epochen scheitern strukturell am Motivationsfall
   „Revert auf identischen Inhalt").

## Design

### 1. Identität: `content_hash`

In `board_models.py`:

```python
def content_hash(artifact: BaseModel) -> str:
    """sha256 über das kanonische JSON von model_dump(exclude={"version"})."""
```

- Kanonisch = `json.dumps(model_dump(mode="json", exclude={"version"}), sort_keys=True,
  ensure_ascii=False)`.
- **Nur `version` ist ausgeschlossen** — sie ist Buchhaltung. Alles andere ist Inhalt:
  Skript A→B→zurück-zu-A hasht gleich (der Motivationsfall matcht); ein neu synthetisiertes
  mp3 desselben Texts hat einen anderen `mp3_path` und hasht anders (die Cutlist schnitt gegen
  *diese eine* Stimme — genau die Unterscheidung, die dem Erstversuch fehlte).

### 2. Datenmodell: `parents`

Jedes abgeleitete Artefakt erhält `parents: dict[str, str] = {}` (Name → `content_hash` des
Elternteils **zum Bauzeitpunkt**). Gestempelt wird, was das Tool beim Bauen tatsächlich liest:

| Artefakt        | parents                                    | Schreibstelle              |
|-----------------|--------------------------------------------|----------------------------|
| `script`        | `{storyline}`                              | `save_script_chapter`      |
| `voice`         | `{storyline, script}`                      | `synthesize_script_voice`  |
| `cutlist`       | `{storyline, script, voice}`               | `build_cutlist`            |
| `contact_sheet` | `{cutlist}`                                | `save_contact_sheet`       |
| `render_report` | `{storyline, script, voice, cutlist}`      | `render_production`        |
| `qa_report`     | `{render_report}`                          | `save_qa_report`           |

- QA keyt **bewusst nur auf den Render**: Das Urteil gilt dem Film, nicht dem Weg dorthin.
- `storyline` ist Wurzel: keine `parents`, wird nie auto-restauriert (billig neu zu bauen).
- Die bestehenden `script_hash`-Felder **bleiben** (Voice-Synthese-Cache-Key, Chip-Anzeige,
  Rückwärtskompatibilität der Alt-Boards).

### 3. Restore-Walk: `Board.restore_coherent_suffix() -> list[str]`

```
für name in _CHAIN (in Ketten-Ordnung):
    wenn load(name) präsent: weiter
    für version in versions(name), neueste zuerst:
        kandidat = Archivdatei lesen (validieren; unlesbar → nächste)
        wenn kandidat.parents leer: nächste             # Alt-Board/Wurzel: unbekannt ≠ kohärent
        wenn jedes Elternteil PRÄSENT ist und
             content_hash(load(eltern)) == kandidat.parents[eltern] für ALLE:
            revert(name, version); restored.append(name); break
    sonst (kein Kandidat): Walk endet                   # erster nicht-restaurierbarer Fehlender
return restored
```

- **Upstream-zuerst ist automatisch korrekt:** `revert` invalidiert nur bereits fehlende
  Glieder (No-Op), und das eben Restaurierte ist exakt die Instanz, auf die die Kinder-Hashes
  zeigen — die Kette schließt sich Instanz für Instanz.
- **Der Review-Killer-Fall wird zum Feature:** Cutlist auf vAlt zurückgedreht → der Walk holt
  **deren eigenen** Sheet und Render zurück (deren `parents[cutlist]` auf vAlts Content-Hash
  zeigen), nicht die der verworfenen Cutlist.
- Prüfung **vor** dem Revert (Archivdatei peeken) — ein Nicht-Treffer wird nie auch nur
  momentan aktuell.

### 4. `status()` / `stale` verallgemeinert

Für Artefakte mit nicht-leeren `parents`:

- `stale = true` — mindestens ein Elternteil ist präsent und sein Content-Hash weicht ab.
- `stale = false` — alle Eltern präsent und passend.
- `stale = null` — mindestens ein Elternteil fehlt (nichts zu vergleichen) — unbekannt,
  niemals als „aktuell" präsentiert.

Artefakte ohne `parents` behalten die bestehende `script_hash`-Logik (Alt-Boards). Die
Frontend-Chips brauchen keine Änderung — sie lesen `stale` bereits dreiwertig.

### 5. Einstiegspunkt

`run_production` ruft `board.restore_coherent_suffix()` direkt nach `Board.open`/`Board.create`
und **vor** `build_production_task` — der Task-Text listet Restauriertes korrekt als DONE (die
Task-Text-Lüge des Erstversuchs ist strukturell unmöglich). Das Ergebnis-Dict erhält
`restored: list[str]`; der Event-Sink loggt eine Zeile
(`{"type": "restored", "artifacts": [...]}`), wenn etwas restauriert wurde.

### 6. Grenzfälle & bewusste Abstriche

- **Intern kohärent, aber stale gegen das Skript:** Der Walk restauriert passend zum präsenten
  Präfix; `status()` meldet die Staleness weiterhin. Nichts wird versteckt.
- **Übervorsicht akzeptiert:** Eine kosmetische Storyline-Änderung ändert deren Content-Hash
  und blockiert damit z.B. einen Voice-Restore, obwohl der Text gleich blieb. Kosten: ein
  Rebuild. Ein falscher Restore kostete die Wahrheit — und der Carry-Over aus `160e784` fängt
  kosmetische Storyline-Saves ohnehin **vor** der Invalidierung ab (Script+Voice überleben dann,
  der Restore-Fall entsteht gar nicht).
- **Alt-Boards:** `parents` default `{}` — laden unverändert, restaurieren nie, `stale` fällt
  auf die `script_hash`-Logik zurück.

### 7. Tests

- `content_hash`: Determinismus; Version-Ausschluss (v1-Inhalt == v3-Inhalt); Pfad-Sensitivität
  (neues mp3 ≠ altes mp3).
- Walk: Voll-Suffix-Revival nach A→B→A bis inkl. QA (→ `resume_point == "done"` ohne
  Agent-Turn); Revert-Cutlist holt ihren eigenen Render; Storyline-Reorder blockiert
  Voice-Restore; Alt-Board (leere `parents`) restauriert nie; Teil-Suffix (Voice passt,
  Cutlist nicht → nur Voice zurück, Walk endet); unlesbare Archivdatei wird übersprungen.
- Stempel: jede Schreibstelle stempelt die dokumentierten Eltern (Tool-Level-Tests).
- Entry: `run_production` mit injiziertem Execute — `restored`-Feld im Ergebnis, Task-Text
  liest DONE, Event-Zeile im Sink.
- `status()`: `stale` in allen drei Zuständen über `parents`; Alt-Board-Fallback unverändert
  (bestehende Tests bleiben grün).

## Nicht in diesem Scope

- Restore der `storyline` (Wurzel, billig).
- Änderung der Carry-Over-Logik (`160e784`) oder der Render-Cap-Semantik.
- Frontend-Änderungen (Chips lesen `stale` bereits dreiwertig).
