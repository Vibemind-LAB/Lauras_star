# 03 — Frame-/Sample-genaue Zeitbasis (Kern-Risiko)

> Dies ist der wichtigste Teil des Produkts. Größtes Risiko laut Report. Hier wird
> **kompromisslos** gearbeitet. Implementierung: `services/local-api/src/laura/timebase/`.
> Verifikation: `services/local-api/tests/` (Golden-Fixtures, exhaustiv).

## Drei Zeitdomänen — gleichzeitig

1. **Sequence Time** — ganzzahlige Frames relativ zur Sequenz.
2. **Source Media Time** — Quelldomänen-Timestamps relativ zum Originalclip.
3. **Audio Sample Time** — sample-genaue Audiopositionen fürs Alignment.

OTIO nutzt `RationalTime(value, rate)`; dieses Prinzip übernehmen wir intern, aber **härter**
auf Sample-Genauigkeit zugespitzt.

## RationalTime (Grundbaustein)

`RationalTime{ value: int, rate_num: int, rate_den: int }` — exakt, rational, **nie Float als Zustand**.

- `rescale_to(rate)` über Ganzzahl-Arithmetik mit definierter Rundung (round-half-to-even),
  Rundungs­modus explizit, dokumentiert und getestet.
- Vergleich/Addition über gemeinsamen Nenner (lcm), kein Float-Zwischenschritt.

Beispiel-Raten (`rate_num/rate_den`):

| FPS (Anzeige) | num/den | DF üblich |
|---|---|---|
| 23.976 | 24000/1001 | nein |
| 24 | 24/1 | nein |
| 25 | 25/1 | nein |
| 29.97 | 30000/1001 | **ja (DF)** und NDF |
| 30 | 30/1 | nein |
| 50 | 50/1 | nein |
| 59.94 | 60000/1001 | **ja (DF)** und NDF |
| 60 | 60/1 | nein |

## Drop-Frame: nur Nummerierung, nie Dauer

DF betrifft **die Anzeige/Nummerierung**, **nicht** die physische Frame-Dauer. Regeln:

- Interne Rechnung: **immer NDF-Frame-Indizes** (linear, lückenlos).
- DF nur beim **Formatieren** eines Timecodes (Anzeige) anwenden bzw. beim **Parsen** zurückrechnen.
- 29.97 DF: 2 Frames pro Minute droppen, außer bei Minuten, die durch 10 teilbar sind.
- 59.94 DF: 4 Frames pro Minute droppen (gleiche Ausnahme).
- DF-Timecodes mit `;`/`.` als Trenner formatieren, NDF mit `:` (konvention, konfigurierbar).

## Datentypen (kanonisch)

```text
FramePoint  { frame_index: i64, rate_num: i32, rate_den: i32, drop_frame: bool }
AudioPoint  { sample_index: i64, sample_rate: i32 }                 # z. B. 48000
MediaRange  {
  src_in_frame: i64, src_out_frame_exclusive: i64,                 # Quelldomäne
  seq_in_frame: i64, seq_out_frame_exclusive: i64,                 # Sequence-Domäne
  src_timecode_start: str|None,
  src_rate_num: i32, src_rate_den: i32,
  speed_num: i32, speed_den: i32                                   # 1/1 = normal
}
```

## Implementierungsregeln (verbindlich)

| Regel | Entscheidung |
|---|---|
| Timeline-Edits | immer in **Frames** speichern |
| Transcript-Alignment | Wortgrenzen in **Samples** speichern, für UI auf Frames projizieren |
| UI-Anzeige | DF/NDF streng als **Anzeigeformat** behandeln |
| VFR-Material | für Editorial **CFR-Proxies** erzeugen; Source-Mapping separat halten |
| Range-Enden | konsequent **end-exclusive** |
| Export | Adapter bekommt **kanonische Ranges**, nicht UI-Zustände |

## Sample ↔ Frame Projektion

- `sample_to_frame(sample, sample_rate, rate_num, rate_den, rounding)` und Umkehrung —
  rein rational, Rundungsmodus explizit.
- **Word-Snapping:** Wortgrenze (Sample) → nächster Frame nach definierter Regel
  (`floor` für In-Punkte, `ceil` für Out-Punkte exklusiv), damit kein Audio „abgeschnitten" wirkt.
- Sample bleibt kanonisch; die projizierte Frame-Zahl ist abgeleitet und darf **nicht** zurückgeschrieben werden.

## Speed Changes

`speed_num/speed_den` skaliert Source↔Sequence. Dauer in Sequence-Frames =
`round(src_len * speed_den / speed_num)` (Modus definiert). Round-trip-Tests pflicht.

## SMPTE-Timecode

- `frames_to_timecode(frame_index, rate, drop_frame) -> "HH:MM:SS:FF"` (DF: `;`).
- `timecode_to_frames("HH:MM:SS:FF", rate, drop_frame) -> i64`.
- `start_timecode` eines Assets (aus ffprobe) ist Offset der Source-Domäne; Sequence beginnt i. d. R. bei `00:00:00:00` oder projektdefiniertem Start.

## Was getestet werden MUSS (Golden-Corpus)

- DF↔NDF-Konvertierung an Minutengrenzen (xx:00, xx:10, Drop-Punkte) für 29.97 **und** 59.94.
- Round-trip `frames → timecode → frames` für alle Raten der Tabelle.
- End-exclusive Range-Math: Länge, Schnitt, Überlappung, Ripple/Lift-Deltas.
- Sample↔Frame Projektion inkl. Word-Snapping (floor/ceil), Grenzfälle bei 48000 vs. 30000/1001.
- Speed-Change Round-trips.
- Rundungsmodus-Determinismus (gleiche Eingabe → gleiche Ausgabe, plattformunabhängig).

→ Akzeptanzkriterium Phase „Härtung": **keine Timecode-Drifts** im Testkorpus.
