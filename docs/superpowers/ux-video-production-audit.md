# Laura — UX/UI-Audit: „bestes Werkzeug zum Videos-Produzieren"

> Quelle: 6-Agenten-Workflow-Audit (2026-06-16), je eine Produktions-Stufe, aus Sicht eines Creators,
> der ein fertiges (Reel-)Video produziert. Jede Behauptung ist am echten Code verankert. Dieses Doc ist
> die priorisierte Arbeitsgrundlage für die UX-Iteration (Loop „bis es das beste ist").

## Was schon gut ist (nicht kaputtmachen)
- **Download-Progress** (Phase/%/Bytes/Speed/ETA, Cancel+Retry pro Item) — über NLE-Standard.
- **Frame-genauer Player** (J/K/L Shuttle, Frame-Step, Home/End, Keyboard-Legende, „Proxy wird erstellt…").
- **Scene-Strip** content-first (echte Frame-Thumbs + Transkript-Auszug); **BiasSlider** als opinionated „Bild-genau↔Schnitt-sauber"; **QualityPanel** macht die Heuristik sichtbar.
- **SceneInspector** IN/OUT-Filmstreifen + Nudges (-1/-fps/+fps/+1) = echte frame-genaue Trim-UX.
- **Consent-Gate** korrekt hart (Reenact-Button erst nach Consent; Reset bei Projektwechsel); **synthetic-Provenienz-Badge**.
- **Caption-Controls** in Export (Preset/Karaoke/Position/Size/Safe-Zone) + Default-on KI-Disclosure.
- **JobCenter** + Offline/Schema-Guard.

## P0 — bricht „ein gutes Video produzieren" (zuerst)

1. **Export-Sackgasse: das fertige Video ist nicht zugänglich.** Export-Card `onClick/onRetry = () => undefined`, kein Thumbnail, kein Pfad. Nach dem Render kann der Creator das MP4 **nicht abspielen/öffnen/im Ordner zeigen/Pfad kopieren** — muss `workspace/` im OS-Explorer durchsuchen. *Der Moment, auf den alles hinausläuft, hat null Payoff.* → preload-Bridge `reveal/openPath` (shell.openPath/showItemInFolder in main.ts) + In-App-Preview via `laura-media://export/<id>` + Card-Aktionen (MediaCard hat schon `menu`-Prop). **(Export+Global, je „high")** [Hinweis: berührt electron `main.ts` — Commit-Hygiene gegen vorhandene Dev-Scaffolding beachten.]

2. **Export rendert evtl. die FALSCHE Timeline.** ExportView bekommt `roughCut?.id` (per-Video-Rough-Cut), nicht die in *Zusammenfügen* assemblierte **Sequenz**. Wer Szenen/Transitions/Voiceover/Overlays assembliert, exportiert ggf. den Einzel-Clip — ohne Hinweis. → Sequenz-`timeline_id` übergeben *oder* expliziter Quell-Selector; **anzeigen, was gerendert wird** („Exportiere: Sequenz · N Clips · mm:ss"). **(Export+Global, „high")**

3. **Analyse ist ein verstecktes, manuelles Gate.** Nichts sagt dem Erstnutzer, dass er pro Asset „Analysieren" klicken muss, bevor Rough Cut/Transkript/Captions gehen. Import→Rough Cut scheitert still mit „Wähle ein Asset". → Analyse nach Import (settled) auto-anstoßen *oder* als klares Stufen-Prerequisite mit Status surfacen; RoughCut-Empty-State „Analyse läuft…/starten" statt generisch. **(Global, „high")**

4. **Reenact/Overlay verlangen rohe Integer-Frame-Eingabe.** Kein „Playhead übernehmen", kein Mark-In/Out, keine mm:ss-Anzeige — der Creator muss Frame-Nummern aus dem „1234/5000 f"-Readout im Kopf ableiten. *Größte Barriere, die KI-Features überhaupt zu nutzen.* → „In = Playhead"/„Out = Playhead"-Buttons (seqFrame ist in AssembleView schon vorhanden) + mm:ss-Konvertierung neben den Inputs. **(Assemble, „high")**

5. **AI-Jobs (Reenact/Voiceover/Overlay) zeigen im Panel keinen Fortschritt.** Nur „Job gestartet: <uuid>"; Erfolg/Fehler nur im separaten JobCenter-Popover. → in-panel bis-Terminal pollen (JobCenter-Muster wiederverwenden), Status-Chip + Retry + onChange-Reload bei Erfolg. **(Assemble, „high")**

6. **MP4-„Exportieren"-Button ohne Busy-State** → ungeduldiges Mehrfachklicken queued Doppel-Renders. → `exportBusy` wie `reelBusy`. **(Export, „high", S)**

7. **BiasSlider zerstört manuelle Szenen-Edits + feuert pro 0.05-Schritt einen Full-Rebuild.** Datenverlust ohne Warnung/Undo. → Commit-on-Release/Debounce + Dirty-Guard-Confirm. **(RoughCut, „high", S)** [Hinweis: RoughCutView ist User-Datei — nur mit Ansage.]

## P1 — großer Qualitäts-/Flow-Gewinn

- **Blind-Caption-Styling:** kein echtes 9:16-WYSIWYG vor dem (langsamen) Render. → Pre-Render-Preview: ein 9:16-Posterframe + CSS/SVG-Caption-Overlay aus Preset/Position/Size/Safe-Margin (kein Backend-Render). **(Export+Global)**
- **Export-Formate kryptisch** (MP4/OTIO/EDL/FCPXML/SRT als bare Tokens). → `<optgroup>` „Fertiges Video / Für anderes Schnittprogramm / Untertitel" + Hilfetext. **(Export, S)**
- **Plattform-Presets** „Export für Reels/TikTok/Shorts" (Aspect+Captions+Disclosure+Preset in einem Klick). **(Export, S)**
- **Feinschnitt zeigt Quelle statt Schnitt:** Player spielt das rohe Asset, Transkript ist das *Source*-Transkript (nicht der Cut). → assemblierte Szene abspielen + Transkript aus dem Scene-Timeline ableiten (Wort-Löschen = primärer Trim). **(FineCut, „high"→P1, L/M)** [FineCutView ist User-Datei — nur mit Ansage.]
- **Kein Undo** über Stufen (Scene-Split/Merge/Rename, Assemble-Mutationen, Trim, delete_words). → Undo-Stack je timeline.id (`setClips` existiert für genau das). **(alle, L)**
- **Reicher Card-Metadata** (Dauer/fps/Auflösung/Codec/Größe + Duplikat-Badge per sha256) im Bin; **Sortieren/Filtern/Suchen** im Bin. **(Import, S/M)**
- **Bin-Grid inert:** Card-`onClick = () => undefined`; Auswahl nur via separater MediaSidebar (zwei konkurrierende Listen). → Klick = Preview + Keyboard-Nav. **(Import, M)**
- **Reenact/Overlay-Ergebnis unsichtbar:** kein Vorher/Nachher, kein „synthetic"-Badge/Scrub auf der Timeline-V2-Lane. → labeln + klick-to-seek. **(Assemble, M)**
- **Tools-Rail = undifferenzierter Stapel** (Reenact 4. unter der Falte). → collapsible Sektionen, AI-Identity oben. **(Assemble, S)**
- **Portrait/Asset-Picker** ohne Thumbnail/Typ-Filter. → Thumbnail-Grid (Thumb-Komponente existiert), nach Typ filtern. **(Assemble, M)**
- **Render-Progress/ETA/Cancel** in Export (Job-System kann es). **(Export, M)**
- **Failed Export Retry** echt machen (`onRetry` ist No-op). **(Export, M)**
- **Auto-Trim-to-Speech** aus der schon gerenderten Per-Clip-Waveform (Energie-Threshold, kein Modell). **(FineCut, M)**

## P2 — Politur
- Paste-to-import (Ctrl/Cmd-V URL) + letzte Quality/Cookie-Wahl merken; `brave` in Cookie-Dropdown.
- Multi-Select + Bulk (analyze/delete/retry-all).
- Hover-Scrub-Thumbnails + besseres Poster-Frame (nicht src_in, oft schwarz).
- Szenen reorder/reject/delete im Bin; Keyboard-Shortcuts (G/S/M/F2…).
- mm:ss statt rohe Frames überall; Reel-Preflight (Dauer/Aspect-Warnung).
- Dead-Legacy-Layout in App.tsx (`false as boolean`, ~Z.545-783) entfernen + verlorene Fähigkeiten (Transkript-Suche, InspectorPanel) re-exposen.
- SequencePlayer Keyboard-Transport (Space/J/K/L/Pfeile) + aria-labels; Source-Switch-Stutter mildern (Doppel-`<video>`-Preload).

## Reihenfolge der Iteration (dep-frei, kollisions-sicher zuerst)
1. **Export-Sackgasse schließen** (P0-1) — höchster Hebel; produce-loop-Payoff.
2. **Export = richtige Sequenz + anzeigen, was rendert** (P0-2).
3. **9:16-Caption-Preview vor dem Render** (P1, WYSIWYG).
4. **„Playhead übernehmen" für Reenact/Overlay + mm:ss** (P0-4).
5. **In-Panel-Job-Progress** (P0-5) · **MP4-Busy-State + Format-Labels + Plattform-Presets** (P0-6/P1).
6. Dann Bin/FineCut/Undo-Themen (z. T. mit User-Ansage wegen User-Dateien).

> **User-Dateien (nur mit Ansage anfassen):** `RoughCutView.tsx`, `FineCutView.tsx`, `Player.tsx`. P0-7 (Bias) & FineCut-Punkte daher zurückgestellt/abgesprochen.
