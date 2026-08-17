# Freie Mehrspur-Clip-Platzierung — Design-Spec (`place_clip`, lane-aware Packing, Flatten)

> Status: **Design only** (kein Code). Branch-Kontext: `feat/auto-shorts-cutter`.
> Datum: 2026-06-27. Autor: Backend-Architekt.
> Verwandt: [`2026-06-09-multilane-both-tabs-design.md`](2026-06-09-multilane-both-tabs-design.md)
> (strategische Vision: Lanes/Roles/Übergänge/Overlays). Diese Spec liefert das **operative Fundament**
> darunter: das fehlende absolute Platzierungs-Primitive und die lane-bewusste Packing-Semantik, von
> denen das dortige Ziel „exaktes Alignen" (Phase A) und „Video-Overlay" (Phase D) abhängen.

## 0. Problem & Zielbild

Der User will **Szenen/Clips frei auf mehrere Videospuren ziehen** und sie **frame-genau** ausrichten —
mit **Lücken und Überlappungen über Spuren hinweg** ausdrücklich erlaubt. Das aktuelle Modell kann das
strukturell nicht:

1. **Keine absolute Platzierung.** Es gibt keine Op, die einem Clip ein absolutes `(seq_in_frame, lane)`
   gibt. `append_clip`/`insert_clip`/`move_clip` berechnen die Position selbst.
2. **Jede Op packt kontiguierlich.** `move_clip` (operations.py:280–308) wirft *alle* Clips in eine
   einzige `ordered()`-Liste und re-packt sie **back-to-back ab Frame 0**, lane-blind:
   ```python
   # operations.py:302-307
   offset = 0
   for c in cs:                                   # cs = ordered(clips) — ALLE Lanes vermischt
       length = c.seq_out_frame_exclusive - c.seq_in_frame
       result.append(replace(c, seq_in_frame=offset, seq_out_frame_exclusive=offset + length))
       offset += length
   ```
   Ein Move auf Lane 0 würde damit Lane-1-Clips an Lane-0-Positionen ziehen und jede Lücke schließen.
   `insert_clip`/`delete_range` rippeln ebenfalls lane-blind über **alle** Clips mit `seq_in >= at`.
3. **Flatten re-offsettet nach Szenenlänge.** `flatten_sequence` (flatten.py:12–28) summiert einen
   `offset += scene_len` und verschiebt **jeden** Szenen-Clip um diesen laufenden Offset — die absolute
   `(seq_in, lane)`-Position eines Clips innerhalb der Szene geht in der Sequenz verloren.
4. **OTIO kennt nur eine Spur.** `timeline_to_otio_string` (otio_io.py:14–64) legt **genau einen**
   `V1`-Video-Track an und ignoriert `lane` vollständig; `tl.ordered()` interleaved Lanes nach
   `seq_in_frame`. **Das ist der zentrale Invarianten-Bruch** (Invariante #6, OTIO = Wahrheit).

Der User hat den **Full-Redesign-Pfad** gewählt: neue **`place_clip`**-Op (setzt `(seq_in_frame, lane)`
absolut, **kein** Re-Pack anderer Clips), **lane-bewusstes** Packing, und eine **Flatten-Änderung**.

### Geltende Invarianten (CLAUDE.md) — dieses Design hält sie ein
1. Timeline-Edits als **Ganzzahl-Frames** relativ zur Sequence. `place_clip` nimmt/speichert nur `int`.
2. Ranges **end-exclusive** (`out_frame_exclusive`). Overlap-/Lücken-Mathematik durchgehend end-exclusive.
3. Audio/Alignment in **Samples**; Frames sind UI-Projektion. `audio_offset_samples` bleibt unangetastet.
4. DF/NDF nur Anzeige; interne Rechnung NDF-Frame-Indizes. (Hier nicht berührt.)
5. **OTIO ist Wahrheit**; EDL/FCP7/FCPXML/SRT sind Exporte. → §4 ist der kritische Check.
6. Idempotenz: `(input, pipeline_version)` bestimmt Analysezustand. `place_clip` ist editorisch,
   nicht analytisch; Idempotenz hier = „selbe Request → selber Zustand" (§1.5).

---

## 1. Die `place_clip`-Op

### 1.1 Semantik (exakt)

`place_clip` setzt **einen** existierenden Clip auf eine **absolute** Sequenzposition und eine Lane,
**ohne** irgendeinen anderen Clip zu bewegen.

```
place_clip(clips, *, clip_ref, seq_in_frame, lane) -> list[EditClip]
```

- Der Clip wird über `clip_ref` identifiziert (siehe §1.3 — bevorzugt `clip_id`, fallback
  `(at_seq_frame, lane)`).
- Sei `dur = c.seq_out_frame_exclusive - c.seq_in_frame` (die **Sequenzlänge** bleibt erhalten —
  Platzieren verschiebt, trimmt nicht; Speed/Source unverändert).
- Ergebnis: `replace(c, seq_in_frame=seq_in_frame, seq_out_frame_exclusive=seq_in_frame + dur,
  lane=lane)`.
- **Alle anderen Clips bleiben byte-identisch.** Kein Re-Pack, kein Ripple. Das ist der ganze Punkt der
  Op und der Unterschied zu `move_clip`.

> **Warum eine neue Op statt `move_clip` erweitern:** `move_clip` ist *per Vertrag* ein Re-Pack
> (Doc-String operations.py:283–290) und hält die Lane-0-Kontiguität, auf der Rough-Cut-Pfad, L/J-Split
> (`_normalize_offsets` „first-clip-0") und Caption-Timing beruhen. `place_clip` ist die genau
> gegenteilige Semantik (frei, absolut, kein Re-Pack). Beides in eine Op zu pressen, würde die
> Lane-0-Garantien verwässern. Zwei klar getrennte Ops.

### 1.2 `audio_offset_samples` unter `place_clip`

Konsistent mit der Per-Op-Regel im Modul-Docstring (operations.py:8–34): der Leading-Edge-Offset ist eine
**Kopf-Eigenschaft** des Clips und **reist mit** (`replace` behält ihn). Danach läuft `_normalize_offsets`:

- Lane-0-Verhalten **unverändert**: der Lane-0-Clip mit kleinstem `seq_in` ist der Sequenzkopf → sein
  Offset wird auf `0` gezwungen (kein Vorgänger).
- **Wichtige Designentscheidung für Lanes ≥ 1:** Der L/J-Split ist ein Konstrukt der **kontiguierlichen
  Lane-0-Bildspur** (otio_split.py:17–24: V1/A1 parallele Tracks, Boundary = `clips[i].src_in_frame`).
  Auf frei platzierten Lanes ≥ 1 gibt es keine wohldefinierten Inter-Clip-Kanten. **Regel:** Ein Clip auf
  Lane ≥ 1 trägt `audio_offset_samples = 0` (harter Schnitt). `place_clip` setzt den Offset eines Clips,
  der nach Lane ≥ 1 platziert wird, auf `0`; ein Clip, der nach Lane 0 zurück platziert wird, behält
  seinen Offset (und `_normalize_offsets` macht ggf. den Kopf-0-Zwang). `_normalize_offsets` muss dafür so
  erweitert werden, dass „Kopf" = kleinster `(seq_in, lane)` **unter den Lane-0-Clips** ist (heute schon
  via `lane` im Key, aber explizit dokumentieren: nur Lane 0 trägt je einen Offset).

### 1.3 Request-Shape

`OperationRequest` (api/models.py:419–443) wird **additiv** erweitert. Neue Op-Kennung `"place_clip"`.
Genutzte Felder:

| Feld                         | Typ           | Pflicht | Bedeutung                                              |
|------------------------------|---------------|---------|--------------------------------------------------------|
| `op`                         | `"place_clip"`| ja      | Op-Diskriminator                                       |
| `clip_id`                    | `str`         | siehe ↓ | **Bevorzugt:** stabile Clip-Identität                  |
| `at_seq_frame` + `lane`(src) | `int`,`int`   | siehe ↓ | Fallback-Identität, wenn `clip_id` fehlt               |
| `seq_in_frame`               | `int ≥ 0`     | ja      | Ziel-Startframe (absolut, Sequenzraum)                 |
| `lane`                       | `int ≥ 0`     | ja      | Ziel-Lane                                              |

**Clip-Identität — Empfehlung:** `clip_id` neu in `OperationRequest` aufnehmen und **bevorzugen**.
Grund: Auf Lanes mit Überlappungen ist `at_seq_frame` **nicht mehr eindeutig** (zwei Clips können auf
verschiedenen Lanes denselben `seq_in` haben — das ist gerade der erlaubte Fall). Heutige Ops finden den
Clip per `c.seq_in_frame == at_seq_frame` (z. B. operations.py:292, 318) — das ist für freie Platzierung
fragil. `place_clip` braucht eine eindeutige Identität.

- **Problem:** `EditClip` (operations.py:44–60) trägt **keine** `id` — die ID lebt nur in der DB-Zeile und
  wird bei jedem `replace_timeline_clips` **neu vergeben** (repos `new_id()` im INSERT). Damit ist
  `clip_id` über eine Op hinweg **nicht stabil** (jede Op schreibt alle Clips neu).
- **Konsequenz / offene Frage (siehe §10-Q1):** Entweder (a) `place_clip` identifiziert den Clip per
  `(at_seq_frame, lane)` der **Quell**position (eindeutig, weil innerhalb **einer** Lane die Clips
  disjunkt-startend sind, solange wir Überlappungen **innerhalb derselben Lane** verbieten — siehe §1.4),
  oder (b) wir führen eine stabile `clip_id` ein, die `replace_timeline_clips` erhält. **Empfehlung:
  Variante (a) für v1** (kein Schema-/Repo-Umbau, eindeutig unter der „keine Überlappung innerhalb einer
  Lane"-Policy), `clip_id` als spätere Härtung. Die Frontend-Drag-Geste kennt die Quellposition ohnehin.

Damit ist die **v1-Signatur**:
`place_clip(clips, at_seq_frame, lane_src, to_seq_frame, lane_dst)` — finde den Clip mit
`(seq_in == at_seq_frame and lane == lane_src)`, setze ihn auf `(to_seq_frame, lane_dst)`.

### 1.4 Validierung

In der Op (`ValueError` → 422 im Router, wie die anderen Ops, timelines.py:885–912):

1. **Clip existiert:** genau ein Clip mit `(at_seq_frame, lane_src)`, sonst `ValueError`.
2. **Keine negativen Frames:** `to_seq_frame >= 0`. (Pydantic-`ge=0` zusätzlich am Request-Feld.)
3. **Lane-Bounds:** `0 <= lane_dst <= MAX_LANE`. `MAX_LANE` als Modul-Konstante (Vorschlag: `8` für v1 —
   genug für Bild-Overlays, deckelt Render-Filtergraph-Komplexität). Lanes sind **dicht ab 0** gedacht,
   aber Lücken in der Lane-Nummerierung sind kein Fehler (Render/Flatten sortieren nach `lane`).
4. **Overlap-Policy (Designentscheidung):**
   - **Über Lanes hinweg: erlaubt** (Kernziel — Bild-Overlay liegt zeitlich über Lane 0).
   - **Innerhalb derselben Lane: verboten** in v1. Wenn der platzierte Clip `[to, to+dur)` einen anderen
     Clip **derselben Ziel-Lane** schneidet (`FrameRange.overlaps`, timebase), → `ValueError`
     („overlap on lane N at [a,b)"). Begründung: (i) hält die `(seq_in, lane)`-Identität aus §1.3
     eindeutig; (ii) eine Lane bleibt eine sauber sequenzielle OTIO-Spur (§4); (iii) Intra-Lane-Overlap
     bräuchte eine echte Compositing-Reihenfolge *innerhalb* der Lane, die wir bewusst auf die
     Lane-Nummer auslagern. Das Frontend snappt ohnehin an Kanten und kann „kein Platz hier" anzeigen.
   - **Alternative für später (§10-Q2):** Intra-Lane-Overlap zulassen und über einen `z`-Tiebreaker oder
     `created_at` auflösen. Für v1 **nicht** — zu viel Compositing-Semantik auf einmal.
5. **Speed/Source unangetastet:** `place_clip` ändert **nie** `src_*`, `speed_*`. Reines Verschieben.

### 1.5 Idempotenz

`place_clip(at, lane_src, to, lane_dst)` ist **idempotent in dem Sinn**, dass ein zweiter identischer
Request (jetzt mit Quellposition `(to, lane_dst)`) denselben Endzustand liefert — der Clip ist schon dort,
`replace` ist ein No-Op-`replace`. Achtung: weil die Identität die **Quell**position ist, muss ein
Retry/Replay die **neue** Quellposition verwenden (das tut das Frontend automatisch, da es nach jeder
Antwort den frischen Zustand hält). Das ist die übliche „last write wins"-Semantik der anderen Ops
(vgl. `set_audio_offset`-Doc, operations.py:396–399), kein neuer Mechanismus.

---

## 2. Lane-bewusstes Packing

**Leitprinzip:** **Lane 0 bleibt exakt wie heute** (kontiguierlich, back-to-back, Rough-Cut/L-J-/
Caption-Garantien unberührt). **Lanes ≥ 1 sind frei** (absolute Positionen, Lücken erlaubt). Jede
geometrieändernde Op wird **lane-scoped**: sie operiert nur innerhalb der **Ziel-Lane** und lässt andere
Lanes byte-identisch.

### 2.1 Helper: Lane-Partitionierung

Eine reine Helper-Schicht in `operations.py` (kein neues Modul nötig):

```python
def clips_on_lane(clips, lane) -> list[EditClip]      # alle Clips dieser Lane, ordered() nach seq_in
def replace_lane(clips, lane, new_lane_clips) -> list  # ersetze nur die Clips von `lane`, Rest unverändert
```

Damit wird jede bestehende Op zu: „nimm `clips_on_lane(clips, L)`, wende die heutige Logik **auf diese
Teilliste** an, setze sie via `replace_lane` zurück." Andere Lanes sind unberührt.

### 2.2 Op-für-Op

| Op             | Heute (lane-blind)                               | Neu (lane-scoped)                                                                 |
|----------------|--------------------------------------------------|-----------------------------------------------------------------------------------|
| `append_clip`  | `start = sequence_length(ALLE)` (ops.py:131)     | `start = lane_length(clips, lane)` (max `seq_out` **dieser** Lane). Hängt am Ende **der Ziel-Lane** an. |
| `insert_clip`  | Ripple ALLE mit `seq_in>=at` (ops.py:141–147)    | Ripple **nur** Clips der Ziel-Lane mit `seq_in>=at`. **Ripple ist lane-lokal.**   |
| `delete_range` | Remove + Ripple ALLE (remove_range, ops.py:155)  | **Default: lane-scoped** — entferne Range nur auf der Ziel-Lane, Ripple nur dort. |
| `lift_range`   | Remove ohne Ripple, ALLE                          | Lane-scoped Lift auf der Ziel-Lane (Lücke bleibt; Lanes ≥ 1 dürfen Lücken).        |
| `move_clip`    | Re-Pack ALLE ab 0 (ops.py:302–307)               | Re-Pack **nur** die Ziel-Lane kontiguierlich. (Bleibt das „aufräumende" Reorder **innerhalb** einer Lane.) |
| `set_speed`    | Ripple ALLE mit `seq_in>=old_end` (ops.py:235)   | Ripple **nur** die Ziel-Lane. Lanes ≥ 1: Längenänderung rippelt nur die nachfolgenden Clips **dieser** Lane. |
| `trim_clip`    | Ripple ALLE mit `seq_in>=old_end` (ops.py:333)   | Ripple **nur** die Ziel-Lane.                                                     |
| `split_clip`   | Teilt 1 Clip, kein Ripple (ops.py:250)           | Unverändert lane-lokal (teilt genau einen Clip; das neue rechte Stück erbt dessen Lane). |
| `roll_boundary`| schon Lane-0-only (ops.py:358–363)               | **Unverändert** — bleibt explizit Lane-0 (Resnap-Ziel).                            |
| `place_clip`   | —                                                | **Neu**, §1: kein Ripple, kein Re-Pack, absolute Position.                         |
| `set_audio_offset` | Lane-0-Kopf-Logik                            | Unverändert; nur Lane-0-Clips tragen einen Offset (§1.2).                          |

### 2.3 Was „Ripple per Lane" bedeutet

„Ripple" heißt heute: alle Clips rechts der Edit-Stelle verschieben, um eine Längenänderung/Lücke
auszugleichen. **Pro Lane** heißt: nur die Clips **derselben Lane** rechts der Stelle verschieben. Damit:

- Ein Insert/Trim/Delete auf **Lane 0** verschiebt **niemals** mehr Lane-1-Overlays — die bleiben absolut
  liegen (genau das will der User: ein Bild-Overlay „klebt" an seiner Sekunde, egal was auf V1 passiert).
- Umgekehrt verschiebt eine Edit auf **Lane 1** nie Lane 0.

> **Designwarnung / bewusste Konsequenz:** Wer „Ripple alle Lanes" (klassisches NLE-„Ripple all tracks")
> will, bekommt es in v1 **nicht**. Das ist Absicht: freie Platzierung und globales Ripple sind
> widersprüchliche Modelle. Globales Ripple kann später als **explizite** Variante (`ripple_scope="all"`)
> nachgereicht werden (§10-Q3). Lane-0-Edits behalten ihr heutiges Verhalten **innerhalb** Lane 0.

### 2.4 `ordered()` / `sequence_length`

- `ordered()` (ops.py:104–105) sortiert bereits nach `(seq_in_frame, lane)` — **bleibt**. Es ist die
  Serialisierungsreihenfolge, kein Packing.
- `sequence_length` (ops.py:100–101) = max `seq_out` über **alle** Lanes — **bleibt** (die Gesamtlänge der
  Sequenz ist das Maximum über alle Spuren, inklusive eines Overlays, das über das Lane-0-Ende hinausragt).
- **Neu:** `lane_length(clips, lane)` = max `seq_out` **dieser** Lane (für `append`/`insert`-Anker).

---

## 3. Flatten-Änderung

### 3.1 Das Problem konkret

`flatten_sequence` (flatten.py:12–28) baut die Sequenz-Clips aus Szenenreferenzen und **re-offsettet jeden
Clip um den laufenden `offset` (Summe der bisherigen Szenenlängen)**:

```python
# flatten.py:20-27
scene_len = max((c["seq_out_frame_exclusive"] for c in clips), default=0)
for c in clips:
    rows.append({**c,
        "seq_in_frame": offset + c["seq_in_frame"],
        "seq_out_frame_exclusive": offset + c["seq_out_frame_exclusive"]})
offset += scene_len
```

Das ist korrekt für die **alte** Welt (Szenen kontiguierlich auf einer Spur hintereinander). Es **zerstört
aber** absolute Lane-Positionen: ein Lane-1-Overlay innerhalb Szene 2 würde um `len(Szene1)` verschoben und
verlöre seine beabsichtigte absolute Sequenzposition. Außerdem stapelt es **alle** Lanes jeder Szene
einfach am selben laufenden Offset — Überlappungen über Szenengrenzen hinweg sind nicht ausdrückbar.

### 3.2 Zwei Flatten-Modi (klare Trennung)

Der Knackpunkt: **eine Sequenz ist heute szenen-referenziert** (flatten erzeugt die Clips erst zur
Laufzeit), während **freie Mehrspur-Platzierung absolute Clip-Positionen braucht**. Das sind zwei
verschiedene Autorenmodelle. Empfehlung:

- **Modus A — „Szenenfolge" (heute, Lane-0-kontiguierlich):** unverändert für Sequenzen, deren Items
  reine Szenen-in-Folge sind. `flatten_sequence` bleibt wie es ist für **Lane 0**.
- **Modus B — „freie Platzierung":** Clips liegen **direkt** auf der Sequenz-Timeline mit **absoluten**
  `(seq_in, lane)` (so wie eine Szene-Timeline im Feinschnitt). Hier **kein Re-Offset** — die Positionen
  sind bereits absolut.

**Konkretes Mapping auf den bestehenden Code:** `resolve_clip_rows` (otio_sync.py:23–49) unterscheidet
schon `kind=="sequence"` (→ flatten) vs. sonst (→ direkte Clips). Frei platzierte Mehrspur-Clips sind der
**Nicht-Sequence-Fall** (eine `scene`/`rough_cut`-Timeline mit Clips auf mehreren Lanes) — der gibt die
Clips **ohne Re-Offset** zurück (otio_sync.py:42–49). **Damit funktioniert freie Platzierung auf einer
Szene-Timeline schon heute geometrisch** — die Lane-Position bleibt absolut, weil dieser Pfad nicht
re-offsettet. Der einzige nötige Flatten-Eingriff ist für **Sequenzen** mit Overlays über Szenen:

### 3.3 Nötige Änderung an `flatten_sequence`

Für Lanes ≥ 1, die als **sequenz-globale Overlays** (über mehrere Szenen) gedacht sind, ist das
laufende-Offset-Modell falsch. Zwei Optionen:

- **Option B1 (empfohlen, additiv):** **Lane-0-Clips** der Szenen weiter via `offset += scene_len`
  flatten (Bildschnitt bleibt kontiguierlich). **Lane-≥1-Overlays auf der Sequenz selbst** werden
  **nicht** aus den Szenen geflattet, sondern liegen — wie heute die `role="replace"`-Overlays
  (otio_sync.py:36–39) — **direkt auf der Sequenz-Timeline** mit absoluten `(seq_in, lane)` und werden
  **ohne Offset** dazugemischt. D. h. `flatten_sequence` betrifft **nur Lane 0** der Szenen; alles ≥ 1
  kommt absolut von der Sequenz. Das passt exakt zur Vision in
  `2026-06-09-multilane-both-tabs-design.md` (Z. 35–38: „B-Roll über mehrere Szenen" = Sequenz-globaler
  Overlay).
- **Option B2 (größer):** Szenen tragen einen expliziten `seq_offset` im `sequence_items` und flatten
  projiziert **alle** Lanes der Szene um diesen Offset (Szene als verschiebbarer Block inkl. ihrer
  Overlays). Mächtiger, aber braucht ein `sequence_items.seq_offset`-Feld und ein Block-Konzept — **nicht
  v1**.

**Empfehlung:** B1. Minimaler Eingriff, hält Lane 0 byte-identisch, gibt Sequenz-Overlays absolute Lanes.

### 3.4 Lücken & Überlappungen für Renderer + Player

Nach dem Flatten ist das Ergebnis eine **flache Clip-Liste mit absoluten `(seq_in, lane)`**, end-exclusive.
Repräsentation:

- **Lücke** = ein Zeitbereich `[a, b)` auf einer Lane, in dem **kein** Clip dieser Lane liegt. Es gibt
  **keine** explizite „Gap-Zeile" im DB-/Flatten-Modell — eine Lücke ist die **Abwesenheit** eines Clips.
  Renderer/Player leiten Lücken pro Lane aus der lückenhaften Abdeckung ab (genau wie OTIO sie beim
  Serialisieren als `Gap` materialisiert, §4).
- **Überlappung über Lanes** = zwei Clips verschiedener Lanes mit sich schneidenden `[seq_in, seq_out)`.
  Das ist **erlaubt und erwartet** und wird vom Renderer als **Stapel** aufgelöst (höhere Lane über
  niedrigerer, §7), vom Player analog (oberste deckende Lane gewinnt das Bild, §6).
- **Player-Kontrakt:** Für ein Frame `f` ist das sichtbare Bild der Clip auf der **höchsten Lane**, deren
  `[seq_in, seq_out)` `f` enthält; gibt es auf keiner Lane einen Clip → schwarz/transparent. (Der Player
  im Frontend braucht dafür die volle Clip-Liste inkl. Lanes — die hat er via `TimelineClip.lane`.)

---

## 4. OTIO-Mapping — der kritische Invarianten-Check (Invariante #6)

**Verdict vorweg: Das Modell round-trippt sauber — *sofern* `place_clip`/Packing die „keine Überlappung
innerhalb einer Lane"-Policy (§1.4) durchsetzt.** Genau diese Policy ist die Voraussetzung, dass jede Lane
eine **gültige sequenzielle OTIO-Spur** ist. OTIO-`Track`s sind sequenziell (Clips + Gaps, keine
Intra-Track-Überlappung) — das ist exakt unsere Per-Lane-Invariante. Multi-Lane → Multi-Track ist damit
die **natürliche** OTIO-Form, nicht ein Workaround.

### 4.1 Was heute fehlt

`timeline_to_otio_string` (otio_io.py:14–64) **und** `apply_split_cuts` (otio_split.py:99–150) legen **genau
einen** Video-Track `V1` an und füllen ihn aus `tl.ordered()` (alle Lanes vermischt nach `seq_in`). Ein
Lane-1-Clip landet damit **fälschlich** als nächster sequenzieller Clip auf V1 — bei Überlappung mit Lane 0
würde die Gap-Mathematik (`if clip.seq_in_frame > playhead`) sogar **negativ** (Clip beginnt vor dem
Playhead) und die Spur wäre kaputt. **Das ist der Bruch, den dieses Design behebt.**

### 4.2 Soll-Mapping

**Eine OTIO-Spur pro Lane**, Lane-Nummer → Track-Reihenfolge (Index = Stacking, niedrig unten):

```
Timeline.tracks = [
  Track("V1", Video),   # lane 0  (unterste Bildspur)
  Track("V2", Video),   # lane 1
  Track("V3", Video),   # lane 2
  ...                    # eine Spur je belegter Lane, aufsteigend nach lane
]
```

Pro Track: die heutige Einzelspur-Logik **unverändert** auf die Clips **dieser** Lane (`clips_on_lane`):
Playhead bei 0, `seq_in > playhead` → `Gap(seq_in - playhead)`, dann der Clip (mit der bestehenden
retimed-/Metadata-Behandlung, otio_io.py:32–60). Weil pro Lane **keine** Intra-Lane-Überlappung erlaubt
ist, bleibt `seq_in >= playhead` immer wahr → die Gap-Mathematik ist immer ≥ 0. **Lücken werden so
automatisch zu echten OTIO-`Gap`s** — das ist die saubere, standardkonforme Darstellung (Invariante #6),
und ein anderes NLE liest die Spuren korrekt.

### 4.3 Round-Trip (Reader)

`otio_string_to_timeline` (otio_io.py:67–104) iteriert heute `timeline.find_clips()` (alle Tracks flach)
und **verliert die Lane** (setzt nie `clip.lane`, Default 0). **Nötige Änderung:** statt `find_clips()`
über `timeline.tracks` **enumerieren** und den Track-Index als `lane` setzen:

```python
for lane, track in enumerate(t for t in timeline.tracks
                             if t.kind == otio.schema.TrackKind.Video):
    for clip in track.find_clips():
        ... Clip(..., lane=lane)
    # Gaps tragen keine Clip-Zeile (Abwesenheit), siehe §3.4
```

`range_in_parent()` liefert weiterhin die absolute Sequenzposition **innerhalb der Spur** (Gaps zählen in
die kumulative Position hinein) → `seq_in_frame` stimmt pro Lane. **Damit ist der Round-Trip exakt:**
`place_clip` → DB → `serialize_timeline_otio` (N Tracks) → `otio_string_to_timeline` (N Lanes) liefert
dieselben `(seq_in, lane, dur)` zurück. Das ist der entscheidende Test (§8, Test 4).

### 4.4 Verträglichkeit mit dem L/J-Split (otio_split.py)

Der Split-Pfad (`apply_split_cuts`) baut V1 + **A1** (Audio) für Lane-0-Splits. Da Lanes ≥ 1 **keinen**
Offset tragen (§1.2), bleibt der A1-Track **rein Lane-0-basiert** — `_fill_audio_track` (otio_split.py:163)
bekommt weiter nur die **Lane-0**-Clips (`clips_on_lane(clips, 0)`), nicht `tl.ordered()`. Damit:

- Die V-Tracks werden zu V1..Vn (eine je Bild-Lane, §4.2).
- Der A1-Audio-Track bleibt **genau wie heute** an Lane 0 gekoppelt — byte-identisch, wenn keine Lanes ≥ 1
  existieren. **Additivität bleibt gewahrt** (kein Split, keine Overlays → byte-für-byte heutiges OTIO).
- `accepted_offsets_from_otio` (otio_split.py:390–418) liest weiter aus der Timeline-Metadata — unberührt.

> **Einzelspur-Backcompat:** Bei genau einer belegten Lane (0) muss der Writer **byte-identisch** zum
> heutigen `V1`-Output bleiben (Track-Name `V1`, keine leeren Vn). Das ist ein expliziter Test (§8, Test
> 4c) und Bedingung dafür, dass bestehende Golden-Fixtures (`tests/test_golden_fixtures.py`) grün bleiben.

---

## 5. Migration / Schema

**Es ist keine DB-Migration nötig.** Die `lane`-Spalte existiert bereits:

- `timeline_clips.lane INTEGER NOT NULL DEFAULT 0` (Migration `0001_init.sql`).
- **Kein** UNIQUE-Constraint auf `(lane, seq_in_frame)` → Überlappungen über Lanes sind speicherbar (die
  „keine Intra-Lane-Überlappung"-Policy wird in der **Op** erzwungen, §1.4, nicht im Schema — bewusst, um
  eine spätere Lockerung nicht durch ein Constraint zu blockieren).
- `repos.replace_timeline_clips` schreibt `lane` bereits im INSERT; `list_timeline_clips` sortiert nach
  `(seq_in_frame, lane)` und liefert `lane` zurück. **Der Lane-Round-Trip durch die DB ist intakt.**
- Index `idx_timeline_clips_seq` ist `(timeline_id, seq_in_frame)` — für Lane-Queries ausreichend; ein
  optionaler `(timeline_id, lane, seq_in_frame)`-Index ist eine **Performance**-Kür (nicht nötig bei den
  realistischen Clip-Zahlen), kein Korrektheits-Thema.

**Kein Backfill nötig** — bestehende Clips haben `lane=0` und bleiben Lane 0.

**Audio-Clips** (`timeline_audio_clips`, Migration `0018`) haben **keine** Lane und brauchen auch keine —
sie sind sequenz-globale Audio-Overlays (Musik/VO) und orthogonal zu den Video-Lanes. Unberührt.

---

## 6. Frontend-Kontrakt

Das Frontend hat die Datenbasis bereits (`TimelineClip.lane`, api.ts:246–267; `lane?` in `Operation`,
api.ts:329–360), aber **weder** die `place_clip`-Op **noch** Cross-Lane-Drag **noch** ein datengetriebenes
Mehr-Lane-Rendering. Das ist klar abgegrenzte Frontend-Arbeit (separater PR, gegen den hier definierten
Backend-Kontrakt):

1. **Op-Client:** `Operation`-Union um `"place_clip"` erweitern; Felder `at_seq_frame`, `lane` (Quelle),
   `to_seq_frame`/`seq_in_frame`, `lane_dst`. (Naming im Request final mit Backend abstimmen — §1.3
   v1-Signatur.)
2. **Drag → place_clip:** Die heutige V1-Reorder-Geste (TimelineBar `onDrop` → `reorderTo` → `move`) wird
   für Lanes ≥ 1 zu einem **2-achsigen** Drag: horizontal → `to_seq_frame`, vertikal (Ziel-Row) → `lane`.
   Ergebnis-Op: `place_clip`. Lane-0-internes Reorder kann weiter `move` nutzen (kontiguierlich) **oder**
   ebenfalls auf `place_clip` umstellen — Empfehlung: V1 behält `move` (Aufräum-Semantik), Lanes ≥ 1 nutzen
   `place_clip`.
3. **Reine `snapToNearestEdge`-Funktion:** Das bestehende `snapEdge` (TimelineBar, pure, snappt Trim-Kanten
   an Nachbar-Cuts, 8 px) ist das Vorbild. Für Drag-Platzierung eine **separate reine** Funktion:
   ```ts
   // pure, testbar, kein DOM
   snapToNearestEdge(
     candidateSeqIn: number,
     edges: readonly number[],   // alle seq_in & seq_out ALLER Lanes + 0 + total + Playhead
     framesPerPx: number,
     thresholdPx = 8,
   ): number   // gesnappter seq_in (oder candidate, wenn nichts in Reichweite)
   ```
   Snap-Kandidaten sind **lane-übergreifend** (an einer Kante auf V1 ausrichten, während man auf V2 zieht —
   das ist der Hauptnutzen für „frame-genaues Alignen"). Die Funktion ist pur → Unit-Test im Frontend
   (Tabelle aus `(candidate, edges, threshold) → erwartet`).
4. **Multi-Lane-Render der Bar:** Aus den hardcodierten Rows (V1/V2) wird ein **datengetriebenes** Rendern:
   Lanes `0..max(lane)` als gestapelte Rows; ein Clip wird in Row `lane` bei `left = seq_in/total`,
   `width = (seq_out-seq_in)/total` absolut positioniert (die Overlay-Positionslogik existiert schon für
   V2, TimelineBar). Lücken = einfach kein Clip-Div in dem Bereich. Überlappungen über Lanes = visuell
   getrennte Rows (kein Problem). Eine „+Spur"-Affordanz, um eine neue leere Lane zu zeigen.
5. **Overlap-Feedback:** Bei Intra-Lane-Überlappung (vom Backend 422) zeigt die UI „kein Platz auf dieser
   Spur" und snappt den Clip zurück; besser noch, die UI verhindert den Drop vorab (sie kennt die Clips der
   Ziel-Lane). Cross-Lane-Drop ist immer erlaubt.

**Explizit NICHT vorhanden / Frontend-To-do:** Cross-Lane-Drag, `place_clip`-Op-Aufruf,
`snapToNearestEdge`, datengetriebenes N-Lane-Rendering, Editier-Affordanzen (Trim/Reorder/Delete) auf
Lanes ≥ 1 (heute nur Remove-Button auf V2).

---

## 7. Render-Auswirkung

**Heute ignoriert der Render `lane` vollständig** (`handlers.py` baut aus `resolve_clip_rows` eine flache
`(path, src_in, src_out)`-Liste und konkateniert sequenziell; `mp4.py` macht `concat`/pairwise `xfade`;
Lücken = implizit Schwarz; nur **Lane-0**-Clips werden für Transitions betrachtet). Mehr-Lane-Compositing
ist der **härteste** Teil (deckt sich mit Phase D in `2026-06-09-multilane-both-tabs-design.md`, Z. 69).

**Soll (ffmpeg-Filtergraph, konzeptionell):**

1. **Pro Lane eine Kette** wie heute: trim/setpts der Clips dieser Lane, dazwischen **explizite**
   Gap-Füllung. Statt impliziten Schwarz wird eine Lücke zu `color=...:d=<gap>` (Lane 0 → schwarz/opak;
   Lanes ≥ 1 → **transparent**, `color=c=black@0.0` + `format=yuva420p`), damit darunterliegende Lanes
   durchscheinen.
2. **Stapeln per `overlay`** in aufsteigender Lane-Reihenfolge:
   ```
   [lane0][lane1] overlay=eof_action=pass [s1]
   [s1][lane2]    overlay=eof_action=pass [s2]
   ...                                     -> [vout]
   ```
   Lane 0 ist die Basis (unten), höhere Lanes liegen darüber. Wo ein Overlay-Clip liegt, deckt er Lane 0
   (Bild-Replace); wo eine Lücke auf der Overlay-Lane ist (transparent), bleibt Lane 0 sichtbar. Das ist
   die **echte** Compositing-Semantik, die `apply_overlay_precedence` (overlays.py) heute nur **flach**
   (durch Wegschneiden der Basis unter `role="replace"`) annähert — das Flatten-Modell bleibt für die
   Single-Track-Exporte nützlich, aber der **Render** stapelt jetzt echt.
3. **Audio:** unverändert via `amix` (mp4.py) — Lane-0-Bildton bleibt die Basis; Audio-Overlays (A2,
   `timeline_audio_clips`) wie heute. Video-Lanes ≥ 1 liefern in v1 **kein** Audio in den Mix (Bild-Overlay
   = stummes B-Roll), konsistent mit §1.2 (kein Lane-≥1-Audio-Modell). Audio von Overlay-Clips ist eine
   spätere Erweiterung.
4. **Transitions:** der bestehende `xfade`/`acrossfade`-Pfad bleibt **lane-lokal** (Transitions zwischen
   aufeinanderfolgenden Clips **derselben** Lane). Cross-Lane ist `overlay`, nicht `xfade`.

**Wichtig fürs Scoping:** Der Render-Umbau ist **die** große Einzelaufgabe und sollte als **eigene Phase**
nach dem Daten-/Op-Fundament laufen (§9). Das Daten-/Op-/OTIO-Fundament ist unabhängig davon testbar (der
OTIO-Round-Trip beweist die Korrektheit der Platzierung, lange bevor der MP4-Render N Lanes kann).

---

## 8. Testplan

Zeitkern-/Op-Tests laufen headless via `uv run pytest` (CLAUDE.md). Bestehende Test-Dateien als Vorbild:
`tests/test_editing_operations.py`, `tests/test_editing_move.py`, `tests/test_flatten_sequence.py`,
`tests/test_otio_sync.py`, `tests/test_otio_split.py`.

**A. `place_clip` (rein, operations.py-Ebene):**
1. Platziert einen Clip auf `(seq_in, lane)`; **alle anderen Clips byte-identisch** (Identitäts-Assert über
   die übrigen `EditClip`).
2. Sequenzlänge des platzierten Clips erhalten (`dur` unverändert); `src_*`/`speed_*` unverändert.
3. `to_seq_frame < 0` → `ValueError`. Out-of-range `lane` → `ValueError`.
4. **Intra-Lane-Overlap** mit existierendem Clip derselben Ziel-Lane → `ValueError`.
5. **Cross-Lane-Overlap** (Ziel-Lane ≠ Quelle, zeitlich überlappend mit Lane 0) → **erlaubt**, kein Fehler.
6. Platzierung **nach Lane ≥ 1** nullt `audio_offset_samples`; Platzierung zurück **auf Lane 0** lässt den
   Offset mitreisen, danach `_normalize_offsets` (Kopf-0).
7. Idempotenz: zweimal dieselbe Zielplatzierung (zweiter Call mit neuer Quellposition) → selber Zustand.

**B. Lane-bewusstes Packing:**
8. `insert_clip` auf Lane 1 rippelt **nur** Lane-1-Clips; Lane-0-Clips unverändert (und umgekehrt).
9. `delete_range` lane-scoped: Lücke nur auf Ziel-Lane; andere Lanes byte-identisch.
10. `move_clip` re-packt **nur** die Ziel-Lane kontiguierlich; andere Lanes unberührt.
11. `trim_clip`/`set_speed` rippeln nur die Ziel-Lane.
12. **Regression Lane 0:** Mit nur Lane-0-Clips sind `move/insert/delete/trim/set_speed/append`
    **byte-identisch** zum heutigen Verhalten (die bestehenden Tests müssen unverändert grün bleiben —
    bestätigt, dass Lane 0 nicht angefasst wird).

**C. Flatten mit Lücken:**
13. Szene-Timeline (Nicht-Sequence) mit Clips auf Lane 0 + Lane 1 (mit Lücke auf Lane 1) → `resolve_clip_rows`
    liefert absolute `(seq_in, lane)` **ohne** Re-Offset; Lücke = Abwesenheit eines Lane-1-Clips.
14. Sequenz (Modus B1): Lane-0-Szenen-Clips kontiguierlich via `offset`; Sequenz-Overlay auf Lane 1 absolut,
    **nicht** um `scene_len` verschoben.
15. Überlappung Lane 0 / Lane 1 über eine Szenengrenze hinweg ist im Flatten-Ergebnis korrekt absolut.

**D. OTIO-Round-Trip (der kritische Test):**
16. `serialize_timeline_otio` einer Mehr-Lane-Timeline erzeugt **N Video-Tracks** (eine je belegte Lane,
    aufsteigend); jede Lane mit ihren `Gap`s an den Lücken.
17. `otio_string_to_timeline` liest **dieselben** `(seq_in, lane, dur)` pro Clip zurück (Track-Index → lane).
    Voll-Round-Trip `clips → OTIO → clips` ist die Identität (modulo Gap-Zeilen, die keine Clips sind).
18. **(c) Single-Lane-Backcompat:** Genau eine belegte Lane → Output **byte-identisch** zum heutigen
    `V1`-only-Writer (Golden-Fixture grün).
19. L/J-Split + Lanes ≥ 1: V-Tracks = V1..Vn, A1 bleibt Lane-0-gekoppelt; `accepted_offsets_from_otio`
    round-trippt unverändert.

**E. (manuell, nicht headless):** Mehr-Lane-MP4-Render (overlay-Stapel, transparente Gaps) — als „manuell zu
prüfen" markiert (CLAUDE.md), echter ffmpeg-Lauf gegen ein Fixture mit 2 Lanes.

---

## 9. Risiken, offene Fragen, Phasen

### 9.1 Risiken

- **R1 — OTIO-Single-Lane-Drift:** Der N-Track-Writer **muss** bei einer Lane byte-identisch bleiben, sonst
  brechen Golden-Fixtures und der L/J-Export. → Test 18 als Gate; Writer so bauen, dass „1 Lane" denselben
  Pfad wie heute nimmt (Track-Name `V1`, kein leerer Vn).
- **R2 — Clip-Identität bei Überlappung:** `(seq_in)` allein ist nicht mehr eindeutig; `(seq_in, lane)` ist
  es nur unter der „keine Intra-Lane-Überlappung"-Policy. Lockert man die Policy je, **muss** zuerst eine
  stabile `clip_id` her (§10-Q1/Q2). v1 hält die Policy → eindeutig.
- **R3 — Render-Komplexität (Phase D):** Der overlay-Stapel mit transparenten Gaps und `yuva420p` ist der
  fehleranfälligste Teil (Frame-Offsets, eof_action, Alpha). Isoliert als eigene Phase, gegen ein 2-Lane-
  Fixture; das Daten-/OTIO-Fundament ist **vorher** vollständig verifizierbar.
- **R4 — Erwartungs-Mismatch „globales Ripple":** User könnten klassisches „ripple all tracks" erwarten.
  v1 liefert lane-lokales Ripple (Absicht). Muss in der UI klar sein; globales Ripple ist eine spätere
  **explizite** Option.
- **R5 — Caption-/Scene-Reconcile:** Caption-Timing (`_timeline_caption_segments`, timelines.py:719–737)
  und `reconcile_after_delete` operieren heute auf der flachen Liste. Mit Overlay-Lanes sollten Captions
  weiter **nur aus Lane 0** (Bild/Ton) gezogen werden — beim Umbau prüfen, dass Lane-≥1-Clips nicht
  fälschlich Cues erzeugen.

### 9.2 Offene Fragen (für den User)

- **Q1 — Clip-Identität:** Reicht für v1 die Quellposition `(at_seq_frame, lane)` als Clip-Identität (kein
  Schema-Umbau, eindeutig unter der No-Intra-Lane-Overlap-Policy), oder soll gleich eine **stabile
  `clip_id`** eingeführt werden (robuster, aber `replace_timeline_clips`/Repos müssen IDs erhalten statt neu
  vergeben)? **Empfehlung: Quellposition für v1, `clip_id` als Folge-Härtung.**
- **Q2 — Intra-Lane-Overlap:** v1 **verbietet** Überlappungen **innerhalb** einer Lane (hält OTIO sauber +
  Identität eindeutig). Ist das akzeptabel, oder braucht es Intra-Lane-Stapeln (dann zwingend `clip_id` +
  eine Z-Order-/Compositing-Regel innerhalb der Lane)?
- **Q3 — Flatten-Modus für Sequenz-Overlays:** Option **B1** (Lane-0-Szenen kontiguierlich + Lane-≥1-
  Overlays absolut direkt auf der Sequenz) — passt das zum gewünschten Autorenmodell, oder sollen Szenen
  als verschiebbare **Blöcke inkl. ihrer Overlays** (Option B2, `sequence_items.seq_offset`) platzierbar
  sein? **Empfehlung: B1 für v1.**

### 9.3 Empfohlene Phasen (bite-sized, risiko-gestaffelt)

- **P1 — `place_clip` + lane-aware Packing (rein, operations.py).** Helper `clips_on_lane`/`replace_lane`,
  alle Ops lane-scoped, neue `place_clip`-Op + Validierung. **Voll headless testbar** (Tests A, B). Lane-0-
  Regression als Gate. *(Kein Schema, kein Render, kein OTIO.)*
- **P2 — OTIO Multi-Track Writer + Reader.** N Video-Tracks (eine je Lane) in `otio_io.py` (+ `otio_split.py`
  V-Tracks), Reader liest Lane aus Track-Index. **Round-Trip-Tests (D)** sind das Herzstück; Single-Lane-
  Backcompat-Gate (Test 18). *(Der eigentliche Invarianten-Beweis.)*
- **P3 — Flatten (Modus B1).** `flatten_sequence` betrifft nur Lane 0; Sequenz-Overlays absolut. Tests C.
- **P4 — API/Op-Wiring + Frontend-Kontrakt.** `OperationRequest`-Feld, Router-Branch (`place_clip` →
  `_apply`), `api.ts`-Op, `snapToNearestEdge` (pure + Unit-Test), datengetriebenes N-Lane-Rendering,
  Cross-Lane-Drag. *(UI-Pfade „manuell zu prüfen".)*
- **P5 — Render-Compositing (härtester Teil, eigene Phase).** Per-Lane-Ketten, transparente Gaps,
  `overlay`-Stapel in `mp4.py`/`handlers.py`. Manueller ffmpeg-Lauf gegen 2-Lane-Fixture (Test E).

Jede Phase ist eigenständig verifizierbar; P1–P3 liefern die korrekte, getestete Daten-/Interchange-Basis,
bevor irgendein Pixel komponiert wird.

---

## Anhang — Referenzierte Stellen (Datei:Zeile)

- Ops/Repack: `services/local-api/src/laura/editing/operations.py` — `ordered()` 104, `sequence_length` 100,
  `append_clip` 129, `insert_clip` 138, `remove_range`/`delete_range` 155/201, `move_clip` 280 (Repack
  302–307), `trim_clip` 311, `set_speed` 209, `split_clip` 250, `roll_boundary` 348, `set_audio_offset` 385,
  `_normalize_offsets` 108, `EditClip` 44.
- Flatten: `services/local-api/src/laura/sequences/flatten.py:12` (Re-Offset 20–27).
- Resolve/OTIO-Sync: `services/local-api/src/laura/editing/otio_sync.py:23` (`resolve_clip_rows`),
  `build_model` 52, `serialize_timeline_otio` 140.
- OTIO Writer/Reader: `services/local-api/src/laura/interchange/otio_io.py:14`/`:67` (single `V1` 17–18).
- OTIO Split (V1/A1): `services/local-api/src/laura/api/otio_split.py:99` (`apply_split_cuts`), `_fill_video_track`
  153, `_fill_audio_track` 163, `accepted_offsets_from_otio` 390.
- Overlay-Precedence (flach): `services/local-api/src/laura/editing/overlays.py:41`.
- Op-Dispatch/Router: `services/local-api/src/laura/api/timelines.py` — `_apply` 839, `apply_operation` 947,
  `OperationRequest` `api/models.py:419`.
- Modell: `services/local-api/src/laura/interchange/timeline.py:13` (`Clip.lane` 20, `ordered()` 41).
- DB: `lane`-Spalte `0001_init.sql` (`timeline_clips`), `replace_timeline_clips`/`list_timeline_clips` in
  `services/local-api/src/laura/db/repos.py` (ORDER BY `seq_in_frame, lane`).
- Render: `services/local-api/src/laura/render/handlers.py` (Lane heute ignoriert, nur Lane-0-Transitions),
  `services/local-api/src/laura/render/mp4.py` (concat / pairwise `xfade`/`acrossfade` / `amix`).
- Frontend: `apps/desktop/src/api.ts:246` (`TimelineClip`), `:329` (`Operation`); `TimelineBar.tsx`
  (`snapEdge` pure, V1-Reorder→`move`, V2-Overlay-Row).
- Verwandte Vision: `docs/superpowers/specs/2026-06-09-multilane-both-tabs-design.md`.
