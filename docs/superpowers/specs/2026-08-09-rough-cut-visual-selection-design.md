# Rough-Cut-basierte Visual-Auswahl

**Status:** Vom User am 2026-08-09 im Chat freigegeben
**Erweitert:** `2026-08-08-visual-recut-full-frame-design.md`

## Problem

Der erste Visual-Recut-Pfad gruppiert die Auswahl nach Sprecher-Beats. Im Live-Test führte ein bestehendes Script mit zwei Beats deshalb nur zu zwei ausgewählten Szenen. Das erfüllt die redaktionelle Aufgabe nicht: Der User möchte alle im aktuellen Rough Cut definierten Szenenwechsel sehen, mehr als zwei Szenen verwenden können, die Shot-Länge bestimmen und anhand belastbarer Beschreibungen entscheiden.

Ein weiterer Live-Fund betrifft bereits abgeschlossene Boards: Eine bestätigte neue Bildauswahl darf nicht als bereits kohärent übersprungen werden. Sie muss einen neuen Cut, Kontaktbogen und Render-Zweig erzeugen, ohne Storyline, Script oder Voice zu verändern.

## Ziele

- Jeder Szenenwechsel des aktuellen Rough Cuts erscheint genau einmal als auswählbare Zeile.
- Die Reihenfolge bleibt fest wie im Rough Cut.
- Pro Szene stehen bis zu vier zeitlich verteilte Quellfenster zur Wahl.
- Jede Option zeigt Beschreibung, erkannten UI-/Transkripttext, Relevanzbegründung und Source-In/Out.
- Die Länge ist in Ein-Sekunden-Schritten von 1 bis 10 Sekunden wählbar.
- Die Empfehlung verwendet alle Rough-Cut-Szenen einmal, sofern sie mit mindestens einer Sekunde in die unveränderte Voice passen. Andernfalls empfiehlt Laura eine relevante Teilmenge von mindestens drei Szenen.
- Die finale Cutlist entspricht framegenau der vorhandenen Voice-Gesamtlänge.
- Visual-Auswahl und Kontaktbogen bleiben persistierte, hashgebundene Hard-Stops.

## Nicht-Ziele

- Kein freies Storyboard und kein Drag-and-drop-Reordering.
- Keine Änderung oder Neusynthese von Storyline, Script oder Voice.
- Keine automatische Veränderung der Rough-Cut-Reihenfolge.
- Keine neue harte VLM-, AutoGen- oder Video-Modell-Dependency.
- Kein Render vor der aktuellen Kontaktbogenfreigabe.

## Datenmodell

Der persistierte Visual Plan wird von einer ausschließlich beat-zentrierten Struktur zu einer Rough-Cut-Coverage-Struktur erweitert.

### VisualSceneChoice

Für jeden Rough-Cut-Szenenwechsel existiert genau ein Eintrag mit:

- stabiler Szenenidentität und `rough_cut_order`;
- Source-Range in Ganzzahl-Frames mit end-exklusivem Ende;
- vollständiger Szenenbeschreibung;
- erkanntem UI-/Transkripttext;
- kurzer Relevanzbegründung;
- bis zu vier zeitlich verteilten Kandidatenfenstern;
- empfohlenem und ausgewähltem Kandidaten;
- `included` für verwenden oder überspringen;
- `requested_duration_s` als ganze Zahl von 1 bis 10;
- daraus abgeleiteter finaler Frame-Länge.

Kandidaten-IDs bleiben stabile SHA-256-Werte über Szenenidentität und end-exklusive Source-Range. Der Proposal-Hash bindet die geordnete vollständige Coverage. Die Bestätigung bindet Proposal-Hash, Kandidaten-ID, `included` und Wunschlänge je Szene.

### Verhältnis zur Voice

Die Visual-Auswahl ist nicht mehr durch die Anzahl der Voice-Segmente begrenzt. Aus den eingeschlossenen Szenenzeilen entsteht eine geordnete Shot-Liste über die vollständige Voice-Timeline. Narrationsbezug wird als Metadatum und Scoring-Signal geführt, nicht als eins-zu-eins Strukturzwang.

## Kandidatengenerierung

Jede Rough-Cut-Szene wird unabhängig bewertet und erscheint auch dann im Vorschlag, wenn sie nicht empfohlen ist.

- Gesunde Review-Fenster werden bevorzugt.
- Lange oder degradierte Szenen liefern zeitlich verteilte Fenster: Anfang, erstes Drittel, zweites Drittel und spätestes legales Fenster.
- Timestamped Transcript-Spans verbessern die Reihenfolge innerhalb dieser Abdeckung, dürfen aber keine Rough-Cut-Szene aus dem Vorschlag entfernen.
- Ein Fenster ist nur für Längen freigegeben, die innerhalb seiner end-exklusiven Source-Range liegen.
- Beschreibung, Transcript-Ausschnitt und Rationale werden als Entscheidungsdaten persistiert und an Electron übertragen.

Die Standardempfehlung schließt jede Rough-Cut-Szene mit zunächst einer Sekunde ein, wenn deren Anzahl in die Voice-Länge passt. Verbleibende Dauer wird deterministisch bis maximal zehn Sekunden je Szene verteilt. Passen nicht alle Szenen mit einer Sekunde, wählt das Scoring eine relevante Teilmenge von mindestens drei Szenen; alle übrigen Zeilen bleiben sichtbar und sind als übersprungen markiert.

## Längenallokation

Der User wählt pro eingeschlossener Szene eine ganzzahlige Wunschlänge von 1 bis 10 Sekunden.

- Liegt die Summe unter der Voice-Gesamtlänge, bleibt die Bestätigung gesperrt.
- Liegt die Summe genau auf der Voice-Länge, werden alle Wunschlängen übernommen.
- Liegt die Summe darüber, bleiben alle Shots außer dem letzten unverändert; ausschließlich der letzte eingeschlossene Shot wird framegenau auf die verbleibende Voice-Länge gekürzt.
- Ergäbe die Kürzung für den letzten Shot weniger als eine Sekunde oder reicht dessen Source-Fenster nicht aus, bleibt die Bestätigung gesperrt und die UI zeigt den konkreten Konflikt.
- Die Cutlist-Summe muss exakt der Voice-Dauer in Sequence-Frames entsprechen.

## Electron-Auswahl

Die Karte zeigt oben eine laufende Bilanz aus Voice-Länge, gewählter Gesamtlänge und der erwarteten finalen Kürzung.

Darunter erscheint jede Rough-Cut-Szene genau einmal und in fester Reihenfolge. Jede Zeile enthält:

- Checkbox `Verwenden`;
- Szenennummer und Rough-Cut-Position;
- Beschreibung, erkannten Text und Relevanzbegründung;
- bis zu vier authentifiziert geladene Vorschaubilder;
- sichtbare Source-In/Out-Frames;
- Längenknöpfe von 1 bis 10 Sekunden, begrenzt durch die verfügbare Source-Range.

Übersprungene Zeilen bleiben sichtbar, werden aber kompakt dargestellt. Die Freigabe ist deaktiviert, wenn weniger als drei Szenen eingeschlossen sind, die Voice unterdeckt ist, eine Länge außerhalb des erlaubten Bereichs liegt oder die finale Kürzung ungültig wäre.

## Persistenz und Invalidierung

Eine neue Bildanfrage oder eine geänderte bestätigte Auswahl invalidiert ausschließlich:

`visual_plan -> cutlist -> contact_sheet -> render -> qa/export`

Storyline, Script und Voice behalten Version, Content-Hash und Dateien unverändert. Die Invalidierung gilt auch für zuvor abgeschlossene Boards; ein altes `done` darf eine neue Visual-Revision nicht als bereits kohärent überspringen.

Die Visual-Bestätigung speichert die aktuelle Auswahl atomar und enqueued genau einen Resume-Job. Eine stale oder doppelte Bestätigung liefert Konflikt und erzeugt keinen zweiten Job. Der Resume baut Cutlist und Kontaktbogen und stoppt terminal bei `contact_sheet_approval`. Erst die hashgebundene Kontaktbogenbestätigung erlaubt genau einen Render- und QA-Lauf.

## Fehlerbehandlung

- Fehlende Rough-Cut-Szenen oder unzureichende Source-Kapazität liefern einen nicht mutierenden, handlungsfähigen Fehler.
- Unterdeckung oder ungültige finale Kürzung werden vor der Bestätigung angezeigt.
- Stale Proposal-/Kontaktbogen-Hashes liefern `409` ohne Board- oder Jobmutation.
- Cancel bleibt vor jedem mutierenden Tool und zwischen Agent-Turns terminal.
- Agententext kann weder eine Auswahl bestätigen noch fehlende Artefakte synthetisieren.

## Verifikation

### Backend

- Jede Rough-Cut-Szene erscheint genau einmal und in stabiler Reihenfolge.
- Lange Szenen liefern frühe, mittlere und späte Kandidaten.
- Empfehlungen verwenden alle passenden Szenen oder mindestens drei.
- Nur Längen 1 bis 10 Sekunden werden akzeptiert.
- Unterdeckung blockiert; Überdeckung kürzt ausschließlich den letzten Shot.
- Die Cutlist entspricht framegenau der Voice und verwendet Full-frame plus Blur-Fill.
- Neue Visual-Revisionen auf einem fertigen Board invalidieren und erzeugen neuen Cut, Kontaktbogen und Render.
- Gate-, Parallelitäts-, Cancel- und Receipt-Tests bleiben grün.

### Electron

- Alle Rough-Cut-Zeilen, Beschreibungen, Texte, Begründungen, In/Out und Längen sind sichtbar.
- Empfehlungen sind vorausgewählt; verwenden/überspringen und Länge sind änderbar.
- Bilanz und Sperrgründe aktualisieren sich deterministisch.
- Stale Fehler bleiben sichtbar und lösen keinen Refresh aus.
- Natürliche Chatbestätigung und Kartenbestätigung verwenden denselben Job-Pollingpfad.

### Live-Abnahme

- Cloud-Modell ist explizit `gpt-5.6-luna`.
- Script und Voice bleiben an jedem Checkpoint hash- und versionsidentisch.
- Der Vorschlag enthält jeden aktuellen Rough-Cut-Szenenwechsel genau einmal.
- Visual- und Kontaktbogen-Gate werden jeweils einmal bestätigt.
- Es gibt genau einen neuen Visual Plan, Kontaktbogen und Render pro bestätigter Revision.
- Export ist 1080x1920, enthält Audio, dekodiert erfolgreich und zeigt in verteilten Frames vollständige UI mit Blur-Fill.
- Ein separater Wegwerf-Recut endet bei Cancel ehrlich und terminal als `cancelled`.
