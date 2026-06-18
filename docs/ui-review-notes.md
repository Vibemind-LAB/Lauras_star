# UI-Review — Notizen (laufend, via /loop)

**Stand:** 2026-06-08 · Iteration 3 (Review konvergiert)
**Methode:** Renderer headless via Vite (:5180) geladen und das gerenderte DOM inspiziert.
Im **Offline-Modus** (kein `window.laura`-Preload) rendert nur die *Chrome* (Nav-Rail,
Header, DropZone, Offline-Hinweis); die Stufeninhalte 3–6 sind auf `client` gegated und
brauchen das **laufende Backend** (`:8765`). Stufen-Inhalte daher hier **code-fundiert**
notiert (alle Stufen-Komponenten in dieser Session gebaut), eine **Live-Sichtprüfung +
Funktionstest mit dem Test-Video** folgt in Iteration 2.

> Test-Video (für den Funktionspass): Google-Drive-Link vom User —
> `https://drive.google.com/file/d/1OjevyLI-541xVbTl1evdLH3fThLFDnNk/view`.

---

## 🎯 Konsolidierter Backlog (priorisiert) — der eine Abschnitt zum Handeln

**P0 — UX-Fallen / Bug**
1. **Sequenz-Audio:** Szenen-Musik fehlt im **Gesamt-Export** (nur Einzel-Szenen-Export hat sie).
   Entweder klar labeln („Musik nur im Szenen-Export") **oder** bauen (Flatten trägt Audio-
   Provenienz, Render mischt mehrere Quellen). *(Backend + UI)*
2. **Verbindungs-/Offline-UX vereinheitlichen + `window.laura`-Guard** (Status hängt auf
   „verbinde…", wenn Preload fehlt/wirft). *(App.tsx — 1 Effekt)*
3. **Import-Abbrechen** für große Downloads (Kern-Use-Case!): `import-cancel`-Endpoint + Button;
   `DELETE /assets` stoppt einen laufenden Fetch nicht zuverlässig. *(Backend + UI)*

**P1 — Klarheit / Feedback**
4. **Lade-/Fortschritts-Feedback** für lange Ops: „Szenen erzeugen", Materialisieren, Render. *(UI)*
5. **Kein-Audio/Transkript-Signal:** Banner „kein Audio → per Shot gruppiert" (Rough Cut) /
   „Transkript-Schnitt nicht verfügbar" (Feinschnitt). *(UI)*
6. **Asset-Metadaten-Ladezustand:** „wird verarbeitet…" bis Probe fertig; `None`-Felder abfangen. *(UI)*
7. **Export:** Auflösungswahl + echter Render-%-Balken + Download/Ordner-Aktion; **Export-Button
   bei leerer Sequenz deaktivieren** (sonst wird ein zum Scheitern verurteilter Render enqueued). *(UI)*
8. **Voraussetzungs-CTAs je Stufe** (leerer Zustand + Sprungbutton) statt leerer Bereich. *(UI)*

**P2 — Polish**
9. Zusammenfügen: **Gesamtdauer** + Insertion-Linie beim Drag + Bin-Thumbnails + Drag-aus-Bin.
10. Rough Cut: Name als Label (Edit on click), ✂-Tooltip „teilt in der Mitte", aktive Szene markieren.
11. Gemeinsame **`SceneCard`** für Rough Cut + Assemble (Konsistenz).
12. **A11y:** Fokus-Ringe, `1`–`6`-Shortcuts, `aria-current`, Kontrast `text-slate-500/600` prüfen.
13. **Frame/Timecode (DF/NDF)** im Player-Transport + Szenenlängen sichtbar (Kernversprechen).

**✅ Validiert (E2E funktioniert):** ganze Pipeline Import→Export · Szenen split/merge (Roundtrip) ·
Sequenz-Reorder (Flatten kontiguierlich) · Render inkl. Einzel-Szenen-Musik · graceful Edges
(leere Sequenz → sauberer Fehler `timeline has no clips`; Single-Clip-Split → 422).

---

## Iteration 3 — Edit-Ops & Edge-Cases (isoliertes Backend)
Alle interaktiven Operationen sauber: **Single-Clip-Split → 422** „split point is not a clip
boundary" (graceful; UI deaktiviert ✂ bei 1 Clip ohnehin) · **Merge↔Split-Roundtrip** stellt die
3 Szenen wieder her (Namen positional) · **Sequenz-Reorder** → Flatten startet bei 0, kontiguierlich ·
**leere Sequenz rendern → Export-Status `error`** „timeline has no clips" (kein Crash) · **Musik
entfernen** ok. → Editing-Schicht ist robust. Einziger UI-Punkt: **Export bei leerer Sequenz
deaktivieren** (Backlog P1.7).

---

## Iteration 2 — Live-E2E gegen isoliertes Backend (:8799)

**Methode:** Separates Backend (eigener Port + Temp-Workspace, **nicht** die laufende :8765-
Instanz des Users) gestartet und die **ganze Pipeline per API** mit einem synthetischen
3-Segment-Clip (6 s) durchlaufen. So echte Datenformen + Funktionsbugs ohne 30-GB-Download.

**Ergebnis: Pipeline läuft komplett grün durch** ✅
`import → probe → analyze (3 Shots in 5 s, „adaptive") → from-shots (3 Clips) →
scenes:generate (3 Szenen) → scene open/materialize (1 Clip, seq 0) → set music →
GET/PUT sequence (3 Items, 3 flattened Clips) → render → **export ready, 79 KB in 1 s**`.
Keine HTTP-Fehler. Das **Datenrückgrat trägt** — die UI kann sich darauf verlassen.

**Neue, datenfundierte Befunde (priorisiert):**

1. 🔴 **Szenen-Musik fehlt im Gesamt-Export.** Musik wird als Szenen-Metadatum gespeichert; der
   Render-Handler sucht Musik per `scene_timeline_id == gerenderte Timeline`. Bei der **Sequenz**
   (`kind=sequence`) trifft das auf **keine** Szene → der Gesamt-Render ist **video-only**. D. h.
   die in Feinschnitt gesetzte Musik landet **nicht** im finalen Export (nur im Einzel-Szenen-
   Export). Das ist die vertagte „Sequenz-Audio"-Lücke — aber ein **echter UX-Trap**: der User
   setzt Musik und wundert sich, dass sie im Endvideo fehlt. **Fix-Optionen:** (a) im UI klar
   labeln („Musik wirkt aktuell nur im Einzel-Szenen-Export"), oder (b) Sequenz-Audio bauen
   (Flatten müsste Audio-Provenienz je Clip mitführen, Render mehrere Quellen mischen).
2. 🟠 **Stille Degradierung ohne Audio/ASR.** Der Clip hatte keine Tonspur → `asr: skipped
   (no audio extracted)` → Transkript leer → Szenen-Gruppierung fiel auf „1 Shot = 1 Szene".
   Alles korrekt, aber **stumm**. UI braucht ein klares Signal: „Kein Audio/Transkript — Szenen
   per Shot gruppiert" in Rough Cut und „Transkript-Schnitt nicht verfügbar (kein Audio / ASR-
   Extra fehlt)" in Feinschnitt. Sonst wirken die Transkript-Features einfach „kaputt".
3. 🟠 **Asset-Metadaten nicht sofort da.** Direkt nach Import sind `duration_frames/rate_num/
   width/height/codec_video` noch `None` (Probe läuft asynchron). Inspector/Import-Karte müssen
   einen **„wird verarbeitet…"**-Zustand zeigen und `None`-Felder sauber abfangen (keine leeren
   „FPS: " / „—" ohne Kontext).
4. 🟠 **Kein Import-Abbrechen.** Es gibt nur `DELETE /assets/{id}`, **kein** `import-cancel`
   (in der Architektur als „neu" gelistet, noch nicht gebaut). Für den **Kern-Use-Case große
   Downloads** ist ein **Abbrechen**-Button essenziell (und Löschen tötet einen laufenden Fetch
   nicht zuverlässig). Hohe Priorität für die Download/Import-UI.
5. 🟢 **Drive-Links werden via yt-dlp gehandhabt** (`ytdlp.py` listet `drive.google.com`;
   `download.py` ist nur generisches HTTP). Der View-Link des Users würde dorthin geroutet. Den
   echten 30-GB-Download habe ich bewusst **nicht** gestartet (zu intrusiv neben deiner Arbeit) —
   am besten **in-App** testen und die **Download-Fortschritts-UI** (Bytes/ETA/Speed, Retry,
   Abbrechen) mit der echten Datei beobachten.

---

## A · Chrome (tatsächlich gesehen)

**Nav-Rail** — `1 Download · 2 Import · 3 Rough Cut · 4 Feinschnitt · 5 Zusammenfügen · 6 Export`.
- ✅ Klare 6-Stufen-Nummerierung, aktiver Zustand hervorgehoben.
- 🔧 **Voraussetzungs-Hinweise fehlen:** Die Architektur-Spec wollte „Stufe ohne Daten → leerer
  Zustand mit CTA + Hinweis, Navigation frei". Aktuell führt eine Stufe ohne Voraussetzung
  einfach zu leerem Bereich. Vorschlag: pro Stufe ein dezenter Status-Punkt (z. B. grau = noch
  keine Daten, grün = bereit) und im Hauptbereich ein CTA („Noch keine Szenen — erst Rough Cut
  ausführen", mit Sprungbutton).
- 🔧 **Tastatur/A11y:** Stufen per `1`–`6`-Shortcut umschaltbar machen; `aria-current` auf dem
  aktiven Rail-Button; Fokus-Ring sichtbar.

**Header** — „Laura · frame-genauer KI-Filmschnitt · local-first" + Verbindungsstatus.
- ⚠️ **Offline-/Connecting-UX hängt:** Ohne Preload bleibt der Status auf „verbinde…" und es
  erscheint zusätzlich „Service offline — starte den lokalen Server." Doppelte/teils
  widersprüchliche Signale. Vorschlag: ein **einziger** Verbindungs-Chip mit drei klaren
  Zuständen (Verbinde… / Online ✓ / Offline — Server starten) + Retry-Button. `getServiceInfo`
  sollte einen harten Fehler (Preload fehlt) sauber als „Offline" abfangen statt „verbinde…"
  stehen zu lassen (siehe Code-Hinweis unten).
- 🔧 **Aktuelles Projekt** sollte im Header sichtbar/wählbar sein (Projekt-Kontext gilt über alle
  Stufen) — aktuell nicht prominent.

**DropZone (globales Overlay)** — „Dateien, Ordner oder Link hier ablegen · Video-Dateien · ganze
Ordner · http(s)/Magnet-Links".
- ✅ Gute, einladende Drop-Hilfe; deckt Dateien/Ordner/Links ab.
- 🔧 Sollte laut Spec nur auf Download/Import aktiv sein — prüfen, dass es auf Rough/Fein/Export
  nicht unbeabsichtigt triggert. Beim Drag-Over deutlicher visueller Zustand (Rahmen/Backdrop).

**Code-Hinweis (Bug-Verdacht):** In `App.tsx` ruft der Mount-Effekt
`await window.laura.getServiceInfo()` **ohne Guard** auf. Fehlt das Preload (oder wirft es),
ist das eine *unhandled rejection* und der Status bleibt auf „verbinde…". Fix:
`if (!window.laura) { setOffline(true); return; }` plus try/catch um `getServiceInfo`.
(Niedriges Risiko in Electron, aber sauberer — und Voraussetzung für die geplante
Preload-Entkopplung/VibeMind-Integration.)

---

## B · Pro Stufe (code-fundiert; Live-Sichtprüfung folgt)

### 1 Download / 2 Import (MediaCard-Galerien)
- ✅ Galerie-Grid mit `MediaCard` (Thumbnail, Titel, Meta, Fortschrittsbalken), Header-Action
  („+ Download" URL-Feld / „+ Importieren" Picker), Offline-Fallback.
- 🔧 **Leerer Zustand**: aktuell „Service offline" bzw. leeres Grid — besser ein freundlicher
  Erststart-Zustand mit großem Drop-Ziel + Beispiel („Zieh ein Video rein oder füge einen Link
  ein").
- 🔧 **Karten-Fortschritt**: Byte-Balken/Phasen-Balken sind da — sicherstellen, dass `ETA`,
  `Geschwindigkeit`, `%` und **Fehler→Retry** auf der Karte konsistent sichtbar sind; Abbrechen-
  Button pro laufender Karte.
- 🔧 Sortierung/Suche im Header (Spec sah „Suche · Sortieren" vor) — prüfen, ob vorhanden.

### 3 Rough Cut (`RoughCutView`)
- Layout: großer **Player** + Button **„Szenen erzeugen"** + **SceneStrip** (Szenenkarten mit bis
  zu 4 Frame-Thumbnails, Transkript-Auszug, Name-Input, ✂ Teilen / ⇄ Mergen).
- 🔧 **„Szenen erzeugen" ist zustandslos:** kein Lade-/Fortschritts-Feedback während Analyse+Build
  (kann lange dauern). Vorschlag: Button-Spinner + Phasen-Hinweis („analysiere… / gruppiere…").
- 🔧 **Szenen-Name-Input pro Karte** ist immer editierbar (visuelles Rauschen). Besser: Name als
  Label, Edit per Klick/Doppelklick. Außerdem gehen Namen bei Teilen/Mergen verloren (v1-Limit) —
  im UI andeuten oder beheben.
- 🔧 **✂ teilt am Mittel-Clip** — für den Nutzer unvorhersehbar. Mind. Tooltip „teilt in der Mitte";
  später: Klick auf eine Clip-Grenze zum präzisen Teilen.
- 🔧 **Klick-zu-Szene → Player-Seek**: gut; zusätzlich aktive Szene im Strip hervorheben.
- 🔧 Leerzustand vor Analyse: klarer CTA („Asset wählen → Szenen erzeugen").

### 4 Feinschnitt (`FineCutView`)
- Layout: **Szenen-Liste links** + **Player** + **TimelineBar** (Trim/Split/Undo) + **TranscriptBar**
  (✂ pro Segment = Ripple-Schnitt) + **SceneMusicControls** (Asset-Picker + Gain-Slider).
- 🔧 **Materialisierungs-Feedback**: beim ersten Öffnen einer Szene wird lazy eine Timeline gebaut —
  kurzer Lade-Indikator wäre gut.
- 🔧 **Transkript-Schnitt-Affordance**: aktuell ✂ pro **Segment** (löscht ganzes Segment). Wort-
  genaues Markieren/Löschen (Architektur „Wort-Klick = schneiden") fehlt noch — als nächste
  Ausbaustufe markieren; mind. Hover-Highlight + Undo-Hinweis nach Schnitt.
- 🔧 **SceneInspector (Filmstrip-Feintrim)** ist in 4a bewusst weggelassen — Feintrim aktuell nur
  über TimelineBar-Kanten. Für „frame-genau" wäre der Filmstrip wertvoll (Folge-Task).
- 🔧 **Musik-Preview**: kein hörbares `<audio>`-Preview (best-effort vertagt) — wenigstens
  Dateiname + „wird beim Export gemischt"-Hinweis anzeigen, damit klar ist, dass Musik wirkt.
- 🔧 **Gain-Slider 0–400 %** ohne dB-Bezug — Default 100 % markieren, evtl. dB-Anzeige.

### 5 Zusammenfügen (`AssembleView`)
- Layout: **Szenen-Bin** (Karten + „+ Sequenz") + **Sequenz-Spur** (Drag-Reorder-Blöcke + „entfernen").
- 🔧 **Vorschau ist strukturell** (Klick = Seek zur Szene); der **echte konkatenierende Player** ist
  5b (offen). Bis dahin im UI sagen: „Gesamtvorschau via Export" + prominenter Export-Button hier.
- 🔧 **Drag-Reorder-Feedback**: Drop-Ziel/Insertion-Linie sichtbar machen; Drag aus dem Bin in die
  Spur unterstützen (nicht nur „+"-Button); Bin-Karten Thumbnails geben.
- 🔧 **Gesamtdauer** der Sequenz anzeigen (Summe der Szenenlängen) + Szenenanzahl.
- 🔧 Leerzustand „Szenen aus dem Bin hinzufügen" ist ok — zusätzlich Hinweis, dass Reihenfolge =
  finale Reihenfolge.

### 6 Export (`ExportView`)
- Layout: Format-Select (mp4/otio/edl/fcpxml/srt) + „Exportieren" + **MediaCard-Galerie** der Exporte
  (pollt während Render).
- 🔧 **Auflösung/Ziel** (Spec: „MP4 ▾ · 1080p ▾") fehlt im Select — mind. Auflösungswahl ergänzen.
- 🔧 **Render-Fortschritt**: Karte zeigt „rendert…" — echten %/Balken aus dem Job zeigen (Job hat
  progress); Abbrechen.
- 🔧 **Download/Im-Ordner-zeigen** pro fertigem Export (⬇ / Ordner) — prüfen, ob vorhanden.
- 🔧 Hinweis, dass MP4 die **Gesamtsequenz** rendert (inkl. Szenen-Musik je Szene? aktuell
  Sequenz-Audio vertagt → klar kommunizieren: „Gesamt-Audio folgt").

---

## C · Querschnitt
- **Theme**: dunkles `slate/ink`-Schema, `border-edge`-Akzente, `sky`-Primary — konsistent. 🔧 Einen
  klaren Primary-/Disabled-/Danger-Token-Satz dokumentieren; Fokus-Ringe überall.
- **Frame-Genauigkeit sichtbar machen**: Timecodes/Frames (DF/NDF) konsistent anzeigen
  (Player-Transport, Szenen-Längen) — Kern des Produkts, sollte im UI spürbar sein.
- **Lade-/Fehlerzustände**: durchgängig nicht-blockierende Fehlerzeile (Muster aus `ExportView`) +
  Spinner bei langen Ops (Analyse, Materialisierung, Render).
- **Responsiveness**: Galerie-Grids haben `md/xl`-Breakpoints; die Editor-Stufen (3–5) auf kleine
  Fenster prüfen (horizontales Scrollen der Strips ok, aber Mindesthöhen).
- **A11y**: Buttons mit `title`/`aria-label` (teils vorhanden), Tastatur-Navigation der Strips,
  sichtbarer Fokus, Kontrast der `text-slate-500/600` auf dunklem Grund prüfen.
- **Konsistenz Szenen-Karten**: SceneStrip (Rough Cut) und AssembleView-Bin zeigen Szenen
  unterschiedlich — gemeinsame „SceneCard" wäre konsistenter.

---

## D · Top-Prioritäten (Vorschlag)
1. **Verbindungs-/Offline-UX vereinheitlichen** + `window.laura`-Guard (Bug) — betrifft jede Stufe.
2. **Lade-/Fortschritts-Feedback** für lange Ops (Szenen erzeugen, Materialisieren, Render).
3. **Voraussetzungs-CTAs** je Stufe (leerer Zustand mit Sprungbutton) statt leerer Bereich.
4. **Export**: Auflösungswahl + echter Render-%-Balken + Download/Ordner-Aktion.
5. **Zusammenfügen**: Gesamtdauer + besseres Drag-Feedback + „Gesamtvorschau via Export"-Hinweis
   (bis 5b-Player kommt).

---

## E · Nächste Iteration (live + Test-Video)
- Backend `:8765` hochfahren (headless `uv run laura-api`), `window.laura`-Stub injizieren ODER die
  echte Electron-App starten, dann **echte Screenshots** je Stufe.
- Test-Video per **URL-Import (Drive)** ziehen → Import → Analyse → Rough Cut → Feinschnitt →
  Zusammenfügen → Export einmal real durchspielen und je Schritt UX-Reibung notieren
  (Wartezeiten, unklare Zustände, fehlende Hinweise).
- Diese Datei pro Iteration oben um „Iteration N" ergänzen.
