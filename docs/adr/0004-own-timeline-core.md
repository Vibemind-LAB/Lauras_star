# ADR-0004 — Eigener Timeline-Kern statt MLT/GES (zunächst)

- **Status:** akzeptiert
- **Kontext:** Für Timeline/Render gibt es MLT (Kdenlive/Shotcut) und GStreamer Editing Services.
  Beide sind legitime NLE-Frameworks.

## Entscheidung

Zunächst **eigener Timeline-Kern + OTIO + FFmpeg/libmpv**. MLT/GES **nicht** als Kern.

## Begründung

- MLT/GES erzeugen früh viel Komplexität im Render-/Timeline-Modell — **bevor** der AI-Mehrwert
  (Analyse, transcript-first Editing, Interchange) ausgeliefert ist.
- Für einen AI-first Editorial Assistant mit starkem Export ist der eigene, schlanke Kern der schnellere Weg.

## Konsequenzen

- Kein vollständiges NLE-Authoring/Compositing/Effektgraph im MVP (bewusst).
- Sobald volles NLE-Authoring/komplexe Effekte nötig werden, MLT/GES erneut evaluieren.
- Der Kern bleibt bewusst klein: Ranges, Clips, Lanes, Operationen — alles frame-/sample-exakt.
