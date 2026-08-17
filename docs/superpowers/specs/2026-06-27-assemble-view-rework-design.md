# Assemble-View („Zusammenfügen") — Überarbeitung (Design-Spec)

**Datum:** 2026-06-27 · **Status:** Anforderungs-Capture aus User-Feedback (annotierter Screenshot).
Noch **nicht** implementiert. Offene Design-Entscheidungen je Punkt + gesammelt am Ende.

## Ist-Zustand

Die Zusammenfügen-Ansicht (`apps/desktop/src/components/AssembleView.tsx`) zeigt heute:

- **Szenen-Bin** (links): Szenen je Quell-Video, „+" / „+ alle" hängt sie an die Sequenz.
- **Player + Rough-Cut-Sequenz**: **eine** Video-Spur (V1), **eine** Audio-Spur (A1) mit Segmenten A2–A10.
- **Storyboard** (unten): angehängte Szenen in Reihenfolge, mit **Übergang**-Dropdown (Hard/…) je
  Szenen-Paar (`updateTransition`) + „entfernen".
- **Transkript-Panels** (rechts): aktuell **mehrere** TRANSCRIPT-Blöcke gleichzeitig (eins je Szene/Clip),
  je mit „Speichern + neu ausrichten".
- **Caption-Preview ein**: einfacher Vorschau-Toggle.

## Überarbeitungs-Punkte

### 1. Szenen-Übergänge in die Szenen-Bin integrieren
- **Heute:** Übergang (Hard/xfade) wird im **Storyboard** je Paar gesetzt.
- **Ziel:** Übergang direkt **in der Szenen-Bin** an der Szene setzbar, nicht erst im Storyboard.
- **Ansatz:** Übergangs-Auswahl je Bin-Eintrag (Dropdown/Chip am Szenen-Kärtchen). Transition-Datenmodell
  bleibt; nur die UI wandert in die Bin (Storyboard-Dropdown gespiegelt oder entfernt).
- **Offen:** Übergang gilt „nach dieser Szene"? Bin-Reihenfolge ≠ Sequenz-Reihenfolge — wo greift er genau?

### 2. KI-Agent für frame-basierte Übergänge
- **Ziel:** Ein AI-Agent wählt den Übergang **basierend auf den Frames um den Schnitt** und bestimmt
  einen **Bereich (Range) um den Cut**, in dem der Übergang liegt.
- **Ansatz (Skizze):** Agent bekommt die N Frames vor/nach dem Cut zweier Szenen → schlägt Übergangs-**Typ**
  (Hard/Cross/Dip/…) + **Range** (Start/Ende in Frames relativ zum Cut) vor; schreibt das Transition-Modell.
  Nutzt bestehende Frame-/VLM-Infrastruktur (vgl. „Übergänge prüfen", `LAURA_VLM_MODEL` + Ollama, off by default).
- **Offen:** Nur **Typ-Vorschlag** oder auch echtes **Rendern** über die Range? Pro Paar einzeln oder
  „alle Übergänge automatisch"? Welches Modell, und bleibt es optional/aus per Default?

### 3. Mehr Spuren zum Alignen der Szenen
- **Heute:** eine V-Spur, eine A-Spur.
- **Ziel:** **Mehrere Spuren** (V2/V3 …), damit Szenen **ausgerichtet/überlappt** werden können (für Übergänge,
  Overlays, paralleles Material). (Screenshot-Annotation „Spur" an der Timeline.)
- **Ansatz:** Timeline-Modell auf mehrere Lanes erweitern; Bin-Szenen per Drag auf eine Spur legen +
  **frame-genau** alignen (Invariante: Frames als Zustand, end-exclusive Ranges).
- **Offen:** Wie viele Spuren — fix oder frei hinzufügbar? Alignment per Drag/Snap? Auswirkung auf OTIO-Export.
- **Befund (2026-06-27, blockiert):** `TimelineClip.lane` existiert (persistiert) + Lanes ≥1 rendern bereits
  (Overlay). ABER freies Platzieren (Clip auf beliebigen `seq_in_frame`, **Lücken erlaubt**) widerspricht dem
  **kontinuierlichen Packing** der Ops: `move_clip` & Co. packen nach jeder Op alle Clips lückenlos neu
  (`operations.py`), es gibt **kein** `place_clip`/`set_clip_lane`/`move_to_abs`, und `getSequenceFlattened`
  rechnet absolute Positionen aus den Szenenlängen (kein Lücken-Konzept). **Sauberer Weg:** neue Op
  `place_clip` (setzt `(seq_in_frame, lane)` absolut ohne Repack) + **lane-bewusstes** Packing (Lane 0 bleibt
  kontiguierlich, Lanes ≥1 frei) + Flatten ohne Re-Offset. **Nicht blockiert (Frontend):** reine
  `snapToNearestEdge`-Funktion, Add/Remove-Track-UI, Multi-Lane-Render. → Kern-Sequence-Modell-Änderung
  mit OTIO-/Invarianten-Bezug: **Entscheidung nötig** (voller Backend-Umbau · nur Frontend-Teile jetzt · später).

### 4. Caption als Tool (mit Bool-Toggle)
- **Heute:** „Caption-Preview ein" (reine Vorschau).
- **Ziel:** Caption als richtiges **Tool** in der Tools-Leiste, mit **Boolean-Toggle** (an/aus).
- **Ansatz:** Caption-Tool neben den übrigen Editorial-Tools; Toggle steuert Sichtbarkeit (und ggf. Export-
  Burn-in). Quelle = Transkript der Szene.
- **Offen:** Toggle = nur Preview-Sichtbarkeit, oder auch „Captions in den Export brennen"? Pro Szene oder global?

### 5. Transkript nur für die ausgewählte Szene
- **Heute:** mehrere Transkript-Panels gleichzeitig (eins je Szene/Clip) → unübersichtlich.
- **Ziel:** **Nur das Transkript der aktuell ausgewählten Szene** anzeigen.
- **Ansatz:** Eine Szenen-Auswahl (Klick in Bin / Storyboard / Player) setzt die „aktive Szene"; das rechte
  Panel filtert `qk.sequenceTranscript` auf deren Segmente (bzw. lädt das Szenen-Transkript gezielt).
- **Offen:** Auswahl-Quelle = Bin-Klick, Storyboard-Klick oder Playhead-Position? (Annotation deutet auf
  Player↔Szene-Verknüpfung.)

## Vorgeschlagene Reihenfolge (klein → groß, jede Scheibe einzeln testbar)

1. **#5 Transkript-Filter** — kleinster Eingriff, sofortiger UX-Gewinn.
2. **#1 Übergang in die Bin** — UI-Umzug, Datenmodell bleibt.
3. **#4 Caption-Tool + Toggle**.
4. **#3 Mehr-Spuren-Timeline** — größer, Timeline-Modell + Alignment.
5. **#2 KI-Übergangs-Agent** — baut auf #1 (Transition-Modell in der Bin) + Frame-Infra auf.

## Entscheidungen (2026-06-27, vom User)

- **#1 Übergänge:** **Bin = Anordnung** — Szenen-Bin + Storyboard zu **einer** geordneten Ansicht
  verschmelzen; Übergänge sitzen zwischen den angeordneten Szenen. (Größerer Layout-Umbau.)
- **#2 KI-Übergang:** **Auto-anwenden** — Agent analysiert die Frames um den Schnitt und **setzt**
  Typ + Range direkt ins Transition-Modell (kein separater Render-Schritt).
- **#3 Spuren:** **Frei hinzufügbar** — beliebig viele Spuren add/remove; Szenen per **Drag + Frame-Snap**
  alignen.
- **#4 Caption:** **Preview + Export-Burn-in** — Toggle für Overlay-Vorschau **und** Captions in den
  finalen Export brennen.
- **#5 aktive Szene (erledigt, `be654f8`):** Auswahl per **Storyboard-Klick**; Bin/Playhead später optional.

**Konsequenz:** #1–#4 sind je ein substanzielles Feature (kein Quick-Fix). Reihenfolge: #1 → #4 → #3 → #2,
jede als eigene verifizierte Scheibe. #1 ist der größte Brocken (Layout-Umbau) und Fundament für #2.
