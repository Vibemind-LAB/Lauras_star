# ADR-0005 — Zeitmodell: Ganzzahl-Frames + Samples, end-exclusive

- **Status:** akzeptiert
- **Kontext:** Frame-/Timecode-Konsistenz ist das **größte** Produktrisiko (DF/NDF, VFR, Speed,
  Sample↔Frame). Float-Sekunden als Zustand führen zu Drift.

## Entscheidung

1. Interner Zustand für Timeline-Edits: **Ganzzahl-Frames** relativ zur Sequence.
2. Audio/Alignment: kanonisch in **Samples**; Frames sind nur **Projektion** für die UI.
3. Alle Ranges **end-exclusive** (`out_frame_exclusive`).
4. Raten als rational `rate_num/rate_den` (RationalTime), **nie** Float.
5. **DF/NDF** ist reine Anzeige; interne Rechnung immer NDF-Frame-Indizes.
6. **VFR** → CFR-Proxy fürs Editorial; Source-Mapping getrennt.

## Begründung

- Ganzzahl-/Rational-Arithmetik ist deterministisch und plattformstabil (reproduzierbar, testbar).
- Sample-Genauigkeit ist nötig, damit Wortschnitte kein Audio „abschneiden".
- end-exclusive vereinfacht Längen-/Schnitt-/Ripple-Math und vermeidet Off-by-one.

## Konsequenzen

- `timebase/`-Modul mit RationalTime, FrameRate (DF/NDF), Timecode, MediaRange, Sample↔Frame-Projektion.
- Exhaustiver Golden-Test-Corpus (DF-Drop-Punkte, Round-trips, Word-Snapping, Speed) ist Pflicht.
- Rundungsmodi explizit und dokumentiert (round-half-to-even; floor für In, ceil für Out-exclusive).
- Projizierte Frame-Werte dürfen **nicht** auf die kanonische Sample-Quelle zurückgeschrieben werden.
