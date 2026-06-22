# Feinschnitt-Editierbereich — Design (Transkript als Steuerfläche)

_Stand: 2026-06-22 · Branch: feat/laura-build-ready · Status: zur Umsetzung freigegeben_

## 1. Kontext & Ziel

Der Feinschnitt (`FineCutView`) ist heute szenen-isoliert: man wählt links **eine**
Szene und editiert deren materialisierte Kopie. Übergänge, Voice-Over, Lippensync,
Reenact und der EU-Act-Hinweis liegen verstreut im **Zusammenfügen**-Tab und fühlen
sich unzusammenhängend an. Ziel dieses Designs:

> **Das Transkript ist die Steuerfläche.** Der Nutzer arbeitet im durchgehenden Text;
> Schnitte, Stimmen-Ersatz und Lippensync sind *Konsequenzen* von Transkript-Aktionen,
> keine separat anzustoßenden Werkzeuge. Alles passiert frame-genau und auf **einem
> Bildschirm**.

Das Präzisions-Fundament existiert bereits (Wort→Frame-Mapping in Samples,
end-exclusive, `delete_words`, `split_clip`/`split_scene`, VO-TTS, Lippensync,
Übergangs-Review). Dieses Design ist überwiegend **Konsolidierung + Verdrahtung +
eine echte neue Schicht (Vorschau-Re-Render)** — kein neuer Präzisions-Motor.

## 2. Nicht-verhandelbare Rahmenbedingungen

**Produkt:**

1. **Ein Bildschirm.** Der gesamte editorische Kreislauf passt auf den Feinschnitt-Screen.
   Keine neuen Top-Level-Views, keine Modal-Ketten wo vermeidbar. Werkzeuge liegen als
   kompakter Streifen **unter dem Video**. Novizen- und Agenten-bedienbar.
2. **Keine Tool-Auswahl.** Der Nutzer wählt nicht „VO machen?" / „Lipsync machen?".
   Diese passieren automatisch als Folge einer Transkript-Änderung. Das **Einzige**,
   was gewählt wird, ist die **Stimme**.
3. **Compliance ist Default, kein Werkzeug.** Der EU-Act/Synthetik-Hinweis ist immer an.

**Technisch (Laura-Invarianten — Verstöße = Bugs):**

- Timeline-Edits in **Ganzzahl-Frames** relativ zur Sequence. Nie Float-Sekunden als Zustand.
- Ranges **end-exclusive** (`out_frame_exclusive`), überall.
- Audio/Alignment in **Samples**; Frames sind UI-Projektion.
- **OTIO ist Source of Truth**; EDL/FCP7/FCPXML/SRT/VTT sind Exporte.
- **Idempotenz** über `(input, pipeline_version)` bzw. semantische Identität (Boundary,
  Recipe). Schwere Modelle (Whisper/TTS-neural/Lipsync/VLM) bleiben **optionale Extras** —
  der Default-Pfad läuft ohne sie (SAPI-TTS, Heuristik-Übergänge, Stub-Lipsync).

## 3. Kernidee: das Transkript als Steuerfläche

Der Feinschnitt editiert **direkt die durchgehende Rough-Cut-Timeline** (nicht mehr
isolierte Szenen-Kopien). Folge: ein **einziger Clip-Satz** → Wiederholungen sind
strukturell unmöglich, Szenengrenzen wandern als Marker ripple-bewusst mit.

Szenen werden zu **Sprungmarken/Abschnitten**: die linke Szenenliste *navigiert*
(scrollt/sucht im durchgehenden Transkript + Timeline), sie isoliert nicht mehr.

Drei Gesten — alle frame-genau, ohne Werkzeug-Auswahl:

| Geste im Transkript | Folge |
|---|---|
| **Bereich markieren** (Drag/Shift-Klick) | Wörter + Videomaterial löschen (Ripple); Schnittkante wird auto-geglättet |
| **Klick zwischen zwei Wörter** | Schnitt → ab dort beginnt eine **neue Szene** (Rest wird Folgeszene) |
| **Text ersetzen/tippen** | Auto-VO (gewählte Stimme, Originalton raus) → Auto-Lippensync *(nur wenn Gesicht erkannt)* → Vorschau aktualisiert sich |

## 4. Architektur

### 4.1 Editieren auf dem Rough-Cut

- Der Feinschnitt lädt die durchgehende Rough-Cut-Timeline + die zugehörigen Szenen
  (als Marker). Keine `openScene`-Materialisierung mehr im Feinschnitt-Editierpfad.
- Transkript: `projectCutWords(segments, clips)`
  ([transcriptProjection.ts:33](apps/desktop/src/shared/transcriptProjection.ts:33))
  projiziert *alle* Wörter auf durchgehende Seq-Frames; danach werden sie pro Szene über
  `[seq_in_frame, seq_out_frame_exclusive)` gruppiert und mit Szenen-Label + Schnitt-Marker
  gerendert.
- Wort-Klick → `onSeek(seqFrame)` (durchgehend; entfällt das heutige src↔seq-Hin-und-Her).

### 4.2 Geste „markieren → löschen"

- Auswahl liefert `word_start_id`/`word_end_id` → bestehendes `delete_words`
  ([timelines.py:907](services/local-api/src/laura/api/timelines.py:907)) auf der
  Rough-Cut-Timeline. Ripple, frame-genau via `map_asset_range_to_seq` + `delete_range`.
- **Neu (Frontend):** Mehrwort-Auswahl-State (Drag/Shift-Klick) im durchgehenden Transkript.
- **Default-Entscheidung:** Eine Auswahl, die eine **Szenengrenze überspannt**, **erhält**
  die Grenze (nur der Text dazwischen wird gelöscht); separat mergebar.

### 4.3 Geste „Klick → Schnitt → neue Szene"

- Caret zwischen zwei Wörtern → Seq-Frame des Folgeworts. Liegt er mitten im Clip:
  erst `split_clip` an dem Frame (Clip-Grenze), dann `split_scene`
  ([scenes.py:85](services/local-api/src/laura/api/scenes.py:85)) dort.
- **Neu (Backend):** ein kompositorischer Endpoint „Schnitt am Frame" (`split_clip` +
  `split_scene` atomar), der eine gültige Clip-Grenze garantiert.

### 4.4 Ripple-bewusster Szenen-Abgleich

- Weil direkt auf dem Rough-Cut geschnitten wird, müssen Szenen-Marker nach jedem Edit
  deterministisch mitwandern (Grenze nach einem Löschbereich rückt um die gelöschte
  Länge; Grenzen *im* gelöschten Bereich fallen zusammen; manuelle Schnitte + Szenen-Musik
  bleiben erhalten).
- **Neu (Backend):** reine Funktion `reconcile_scene_bounds(old_bounds, edit) -> new_bounds`,
  voll testbar. Behebt zugleich die heute dokumentierte Veraltung (delete_words ließ
  Szenengrenzen stehen).

## 5. Die Auto-Pipeline (Transkript ersetzen → neu erzeugen)

Beim **Bestätigen** einer Transkript-Text-Änderung über eine Spanne (kurzes Debounce,
**nicht** pro Tastendruck) wird automatisch angestoßen:

1. **VO:** `POST /timelines/{id}/voiceover` mit `text` (neuer Text), gewählter Stimme
   (`voice_id`), `mix_mode="replace_original"`, `ducking_percent=0` →
   **Originalton unter der Spanne raus**, neue lokale Stimme rein. Backend existiert
   vollständig ([voiceover.py:41](services/local-api/src/laura/api/voiceover.py:41)).
2. **Lippensync (probe-gated):** Erkennt der Lipsync-Probe ein Gesicht/Mund in der Spanne,
   wird `POST /timelines/{id}/lipsync` mit dem **neuen VO-Audio** + der Spanne automatisch
   enqueued. **Kein Gesicht / Sidecar fehlt → still übersprungen** (nur VO bleibt).
   Backend existiert ([lipsync.py:40](services/local-api/src/laura/api/lipsync.py:40)).
3. **Kanten glätten:** Entstehen durch Edits same-source-Schnittkanten (Jump-Cuts),
   werden sie automatisch **markiert** und mit einer Ein-Tipp-Glättung (Crossfade-Default)
   angeboten — nicht still angewendet (siehe §8).
4. **Vorschau aktualisieren:** Der Player spielt das neue Audio + zeigt das Lipsync-Overlay
   (siehe §6).

**Charakter & Ehrlichkeit:**

- „Automatisch neu gerendert" = **Async-Jobs** werden ohne Nutzer-Klick angestoßen; ein
  **Inline-Fortschritt** wird gezeigt; die Vorschau aktualisiert sich bei Fertigstellung.
  Nicht instant (TTS/Lipsync sind schwer), aber ohne manuelles Anstoßen.
- **Consent einmal:** Lippensync/Reenact brauchen Consent. Erstes VO-mit-Gesicht fragt
  **einmal** „Das bin ich"-Consent pro Subjekt ab; danach nutzen Auto-Jobs ihn wieder
  (kein Dialog pro Edit). Ohne Consent für ein erkanntes Gesicht wird Lippensync
  zurückgehalten (nur VO) und einmalig ein Hinweis gezeigt.
- **Job-Kopplung neu (Backend):** ein Completion-Hook, der nach erfolgreichem
  `ai.voiceover` (mit Face-Probe-Treffer + gültigem Consent) `ai.lipsync` mit gebundenem
  Audio-Asset + abgeleiteter Spanne enqueued. Idempotenz über die übliche
  `idempotency_key`-Bildung.

## 6. Vorschau-Re-Render-Schicht (das einzige große neue Stück)

**Problem (verifiziert):** Der Vorschau-Player spielt nur die **Videospur** der
Quell-Clips ([SequencePlayer.tsx:318](apps/desktop/src/components/SequencePlayer.tsx:318));
VO/Musik/Lipsync/Crossfade entstehen erst beim **Export** (ffmpeg `amix`,
[mp4.py:225](services/local-api/src/laura/render/mp4.py:225)). Darum ist eine platzierte
neue Stimme im Editor **nicht hörbar**.

**Lösung:**

- **Web-Audio-Mix in der Vorschau:** `SequencePlayer` bekommt `audioClips` (VO + Musik)
  und mischt sie synchron zum Video-Playhead (zweite Audio-Quelle(n) via `AudioContext`/
  `<audio>`), inkl. **Ducking** der Videospur unter VO-Spannen. Frame-genaue Synchronität
  zum `currentFrame`. → **neue Stimme sofort hörbar**.
- **Lipsync-Overlay sichtbar:** Liegt für die Spanne ein Lipsync-Replace-Overlay (Lane ≥ 1)
  vor, zeigt die Vorschau es statt der Basis (analog zur Export-Auflösung).
- **Übergänge sichtbar:** Crossfades zumindest **markieren** (Kante kennzeichnen); echtes
  Crossfade-Preview ist optional/zweitrangig. Damit verschwindet das „klick Blende → nichts
  passiert"-Gefühl.

## 7. Compliance immer-an

- **Disclosure Pflicht:** Off-Schalter im Export entfernen
  ([ExportView.tsx:110](apps/desktop/src/components/ExportView.tsx:110)) — der Text bleibt
  editierbar, die **Präsenz** ist Pflicht (read-only Bestätigung statt Checkbox).
- **Synthetik-Hinweis im Feinschnitt:** persistente, dezente Zeile/Badge „Enthält
  synthetische Inhalte: {Effekte}" + Consent-Subjekte, sichtbar **während** des Editierens.
- **`audit_events` schreiben:** `audit.record(...)` bei Erfolg jedes AI-Jobs
  (voiceover/lipsync/reenact) und bei jedem Export — heute existiert die Tabelle
  ([audit.py:15](services/local-api/src/laura/audit.py:15)), wird aber nicht befüllt.
- **Consent-UI „Das bin ich":** minimaler Inspektor zum Anlegen/Ansehen/Widerrufen von
  Consent-Records pro Projekt (Datenmodell existiert: `consent_records`,
  `revoked_at`). Reicht für die Auto-Pipeline (§5).

## 8. Übergänge: koppeln, vereinfachen, sichtbar machen

Die Engine ist solide (semantische Boundary-Identität, end-exclusive, gecacht/idempotent,
[transition_review.py:70](services/local-api/src/laura/analysis/transition_review.py:70)).
Schwächen: manuell + nachträglich, in der Vorschau unsichtbar, Heuristik ohne VLM grob,
„Blende vs. Resnap"-Jargon. Maßnahmen:

- **An den Edit koppeln:** Ein Löschen erzeugt einen contiguous-same-source-Schnitt = genau
  den Jump-Cut der Heuristik → Kante automatisch markieren + eine **„glätten"-Aktion**
  (Crossfade-Default, Ein-Tipp) anbieten — nicht still anwenden. Kein separater
  „Prüfen"-Knopf im Hauptfluss.
- **Sichtbar machen:** Über die Vorschau-Schicht (§6) zumindest die Kante kennzeichnen.
- **Vereinfachen:** eine „glätten"-Aktion; Resnap/Stil hinter „Erweitert". VLM bleibt
  optional (`LAURA_VLM_MODEL`).

## 9. Zusammenfügen verschlanken

- Editorial-Tools (VO, Lippensync, Reenact) wandern in den Feinschnitt-Tools-Streifen.
- In **Zusammenfügen** bleiben nur **Sequenz**-Tools: Szenen-Reihenfolge, Overlay,
  Audio-Lane, Demo-Draft. Klare Trennung: Feinschnitt = bearbeiten, Zusammenfügen =
  arrangieren.
- **Reenact** liegt unter dem Video als **manuelle** Aktion (Gesicht ersetzen ist eine
  kreative Wahl, nicht transkript-getrieben) — nicht in der Auto-Pipeline.
- Nebenbei: kaputtes Job-Polling bei `LipsyncPanel` + `DemoAssistantPanel` fixen
  (`useJobStatus` integrieren) — relevant, weil Lipsync mit in den Feinschnitt kommt.

## 10. Layout (ein Bildschirm)

```
 Szenen        Editier-Spalte (eine Spur des Tuns)
 (springen)    ┌───────────────────────────────────────┐
 ┌────────┐    │  Video-Vorschau (hört VO, zeigt Overlay)│
 │ Szene 1│    ├───────────────────────────────────────┤
 │ Szene 2│    │  Tools-Streifen: Stimme▾ · Übergänge ·  │
 │ Szene 3│    │  Reenact · [Synthetik-Hinweis immer an] │
 │   …    │    ├───────────────────────────────────────┤
 └────────┘    │  Durchgehendes Transkript (Szenen-Label │
               │  + Schnitt-Marker; markieren/klick/tippen)│
               ├───────────────────────────────────────┤
               │  Durchgehende 2-Spuren-Timeline (V/A,   │
               │  Szenengrenzen, Playhead)               │
               └───────────────────────────────────────┘
 (Frame-Inspector: kontextuell eingeblendet, wenn ein Clip/Cut gewählt ist)
```

Linke Szenenliste = Navigation. Rechter Inspector (Trim/Frame-Nudge) erscheint
**kontextuell**, damit der Default-Screen schlank bleibt.

## 11. Datenmodell & Operationen — wiederverwendet vs. neu

**Wiederverwendet:** `delete_words`, `split_clip`, `split_scene`, `map_asset_range_to_seq`,
`projectCutWords`, `SequencePlayer`/`TimelineBar`, scenes-Tabelle + Szenen-Musik,
OTIO-Sync, `timeline_audio_clips` (+ mix_mode/ducking), VO-Backend (SAPI/Stub/Sidecar),
Lipsync-Backend (+ Probe/Quality-Gate/Consent), `transition_review` (+ apply_fix),
`audit_events`, `consent_records`, Provenance-Sidecar.

**Neu — Backend:**

- Komposit-Endpoint „Schnitt am Frame" (`split_clip` + `split_scene`).
- Reine Funktion `reconcile_scene_bounds(...)` (ripple-bewusste Szenengrenzen).
- Completion-Hook `ai.voiceover` → bedingt `ai.lipsync` (probe + consent + Audio-/Span-Bindung).
- Edits/`delete_words` auf den Rough-Cut richten; aktualisierte Szenen zurückgeben.
- `audit.record(...)`-Aufrufe in AI-Job- und Export-Erfolgspfaden.
- Disclosure-Präsenz erzwingen (Export-Option nicht mehr abschaltbar).

**Neu — Frontend:**

- Durchgehendes Transkript mit Szenen-Gliederung + Schnitt-Markern.
- Mehrwort-Auswahl-State (Drag/Shift) + Lösch-Geste; Caret→Schnitt-Geste; Text-Ersatz-Commit.
- Tools-Streifen unter dem Video (Stimmenwahl, Übergänge, Reenact); Synthetik-Hinweis-Zeile.
- `SequencePlayer`-Audio-Mix (Web Audio) + Lipsync-Overlay/Transition-Kennzeichnung.
- Szenen-Sprungliste statt Szenen-Editor; Verdrahtung auf Rough-Cut.
- Consent-„Das bin ich"-Mini-Inspektor.
- Zusammenfügen entschlacken; Lipsync/Demo-Draft-Polling fixen.

## 12. Phasenplan (eine Spec, fünf Phasen)

**A — Durchgehendes Transkript-Editing.** Rough-Cut als eine Fläche; Szenen-Marker +
Sprungliste; markieren=löschen; klick=Schnitt→neue Szene; `reconcile_scene_bounds` +
Komposit-Schnitt-Endpoint. _Akzeptanz:_ Löschen/Schneiden über alle Szenen frame-genau,
keine Quellbereich-Überlappung, Szenen-Marker korrekt.

**B — Vorschau-Audio-Mix.** Web-Audio-Schicht im `SequencePlayer` (VO + Musik + Ducking,
playhead-synchron). _Akzeptanz:_ platzierte/erzeugte VO ist in der Vorschau hörbar.

**C — Transkript-Steuerfläche (Auto-Pipeline).** Text-Ersatz-Commit → Auto-VO (Stimmenwahl)
→ Auto-Lipsync (probe-gated, consent-once) → Kanten glätten → Vorschau-Update; Tools-Streifen.
_Akzeptanz:_ Transkript ersetzen erzeugt ohne weitere Klicks neue Stimme (Original weg) +
Lippensync wenn Gesicht, sonst übersprungen.

**D — Compliance immer-an.** Disclosure Pflicht; Synthetik-Hinweis im Feinschnitt;
`audit_events` bei AI-Jobs/Export; Consent-Inspektor. _Akzeptanz:_ Hinweis nicht abschaltbar,
audit_events vorhanden, Consent anlegbar/widerrufbar.

**E — Zusammenfügen verschlanken + Fixes.** Editorial-Tools raus (Sequenz-Tools bleiben);
Reenact als manuelle Feinschnitt-Aktion; Lipsync/Demo-Draft-Polling gefixt. _Akzeptanz:_
klare Trennung, keine Tot-Knöpfe.

**Reihenfolge:** A → B → C → D → E (B vor C, weil „Stimme hören" C voraussetzt).

## 13. Tests

- **Backend (pytest, reine Funktionen):** Schnitt-am-Frame → korrekter Seq-Frame;
  `reconcile_scene_bounds` nach Löschen (Ripple) und nach Schnitt; Löschen über
  Szenengrenze; **Invarianten:** end-exclusive + Samples, kein Off-by-one an Grenzen;
  **„keine Wiederholung"-Assert:** nach Edits disjunkte Quellabdeckung; VO→Lipsync-Hook
  (probe-Treffer enqueued, kein Gesicht überspringt, kein Consent hält zurück);
  audit_events werden geschrieben; Disclosure-Präsenz erzwungen.
- **Frontend (vitest):** Auswahl-Logik (Drag/Shift), Wort→Op-Mapping, Szenen-Gruppierung
  der projizierten Wörter; Auto-Pipeline-Trigger (Debounce/Commit) als reine Logik;
  Audio-Mix-Sync-Mapping (frame→time).
- **Manuell (headless nicht prüfbar, live auf CDP 9222):** Drag-Select-Gefühl,
  Scroll-zu-Szene, VO-Hörbarkeit, Marker-/Overlay-Darstellung.

## 14. Risiken, Edge-Cases, getroffene Entscheidungen

- **Auto-Render-Kosten:** Debounce + Commit-Trigger statt pro Tastendruck; Async + Fortschritt.
- **Lipsync schwer/abwesend:** probe-gated + Stub-Fallback; VO bleibt hörbar auch ohne Lipsync.
- **Consent-Reibung:** einmal pro Subjekt, danach Wiederverwendung; ohne Consent nur VO.
- **Lösch-Auswahl über Szenengrenze:** Grenze bleibt erhalten (Default), separat mergebar.
- **Web-Audio-Drift:** an `currentFrame` koppeln, bei Seek/Pause neu sync’en.
- **Geteilter Working-Tree (codex parallel):** Implementer committen **nur** ihre
  Task-Dateien via **explizitem `git add <paths>`**, nie `-A`. AI-Runtime-Subtree nicht anfassen.

## 15. Nicht in Scope (YAGNI)

- Neuronales TTS als Default (bleibt optionaler Sidecar; SAPI ist Default).
- Echtes GPU-Crossfade-Preview (Kennzeichnung reicht zunächst).
- C2PA/Video-Seal/Watermark (optionale Extras, separat).
- Mehrspur-Audio-Mischpult / Keyframe-Automation.
- Profi-Tür / alternative Layouts — der eine Bildschirm ist das Ziel.
