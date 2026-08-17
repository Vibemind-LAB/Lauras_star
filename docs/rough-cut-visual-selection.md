# Rough-Cut-basierte Visual-Auswahl

Stand: 17. August 2026
Branch: `codex/resumable-visual-selection`

## Ergebnis

Die Visual-Recut-Auswahl bildet jetzt den aktuellen Rough Cut ab, statt die
Bildauswahl auf wenige Sprecher-Beats zu begrenzen. Jeder Rough-Cut-Szenenwechsel
erscheint genau einmal und in unveränderter Reihenfolge. Die bestehende
Storyline, das Script und die Voice werden durch einen reinen Visual-Recut nicht
neu erzeugt oder verändert.

## Auswahl in Electron

Für jede Rough-Cut-Szene zeigt die Auswahlkarte:

- eine Beschreibung, erkannten UI- oder Transkripttext und eine
  Relevanzbegründung;
- Source-In und end-exklusives Source-Out in Ganzzahl-Frames;
- bis zu vier zeitlich verteilte Kandidatenfenster, damit auch spätere Inhalte
  langer Aufnahmen auswählbar sind;
- `Verwenden` oder `Überspringen`;
- eine wählbare Wunschlänge von 1 bis 10 Sekunden, begrenzt durch die
  verfügbare Quellkapazität.

Die Empfehlung verwendet alle Szenen, wenn mindestens eine Sekunde pro Szene in
die vorhandene Voice passt. Andernfalls wird eine relevante Teilmenge mit
mindestens drei Szenen empfohlen; nicht empfohlene Szenen bleiben sichtbar und
können manuell zugeschaltet werden.

## Frame-genaue Schnittlogik

Die Auswahl bleibt gesperrt, solange die gewählten Shots die Voice unterdecken,
weniger als drei Szenen verwendet werden oder ein Kandidat seine gewünschte
Länge nicht tragen kann. Bei Überdeckung bleiben alle Shots außer dem letzten
eingeschlossenen Shot unverändert. Nur dieser letzte Shot wird framegenau auf
die verbleibende Voice-Länge gekürzt. Ein ungültiger finaler Rest wird vor der
Bestätigung abgewiesen.

Die daraus erzeugte Cutlist hat exakt die Voice-Länge in Sequence-Frames. Die
Recut-Shots verwenden Full-frame mit Blur-Fill, ohne Center-Crop, Zoom oder
ROI-Reframing. Die Kontaktbogen-Kacheln übernehmen Rough-Cut-Position,
Beschreibung sowie gewünschte und finale Dauer als Entscheidungsgrundlage.

## API, Chat und persistierte Gates

Visual-Plan und Kontaktbogen sind persistierte, hashgebundene Hard-Stops:

1. Ein Visual-Vorschlag wird gespeichert und der Orchestrator beendet den
   aktuellen Lauf mit `awaiting_user_input`.
2. Karten- und Chatbestätigung verwenden dieselbe strukturierte Auswahl aus
   Proposal-Hash, Rough-Cut-Position, Kandidaten-ID, Verwenden/Überspringen und
   Wunschlänge.
3. Die Bestätigung speichert atomar und startet genau einen Resume-Job. Veraltete
   oder doppelte Bestätigungen erzeugen keinen zweiten Job.
4. Der Resume baut Cutlist und Kontaktbogen und stoppt wieder terminal bei der
   Kontaktbogenfreigabe.
5. Erst eine aktuelle, hashgebundene Kontaktbogenbestätigung darf Render und QA
   fortsetzen.

Agententext kann keine Gate-Bestätigung vortäuschen. Fehlende oder nicht mehr
bestätigbare Vorschläge brechen handlungsfähig und ohne erfundene
State-Transition ab. Auch ein bereits abgeschlossenes Board invalidiert bei
einer neuen Visual-Revision ausschließlich die nachgelagerte Kette
`visual_plan -> cutlist -> contact_sheet -> render -> qa/export`.

## Speichern und später fortsetzen

Jede Änderung an Kandidat, `Verwenden`/`Überspringen` oder Wunschlänge wird
direkt als Draft in der lokalen SQLite-Datenbank gespeichert. Die Karte zeigt
`Speichert …`, `Gespeichert · Revision N` oder einen konkreten Konflikt. Dieses
Autosave startet weder einen Produktionsjob noch einen Agenten- oder LLM-Aufruf
und verbraucht deshalb keine LLM-Tokens.

Drafts verwenden fortlaufende Revisionen. Zwei offene Fenster dürfen eine
neuere Auswahl nicht still überschreiben: Eine veraltete Revision wird mit dem
aktuellen Serverstand zurückgewiesen und muss bewusst neu geladen werden. Erst
`Auswahl bestätigen` validiert die vollständige Dauerbilanz, löscht den Draft
und startet genau einen Resume-Job.

Der Einstieg `Offene Sessions` listet wartende und laufende Produktionen nach
der letzten Änderung. Ein Klick auf `Fortsetzen` öffnet entweder den
verknüpften Chat oder — bei einer älteren/verwaisten Session — eine
schreibgeschützte Produktionskarte mit dem ursprünglichen Auftrag. Beim bloßen
Öffnen wird keine Session automatisch gewählt und kein Chat-Turn erzeugt.

Board-Artefakte und Drafts liegen außerhalb des Electron-Prozesses. Deshalb
bleiben Proposal-Hash, alle Szenenentscheidungen und die Revision nach einem
vollständigen App- und Backend-Neustart sowie nach mehrtägigen Pausen erhalten.
Ein automatisierter Akzeptanztest deckt einen Neustart nach sieben Tagen ab.

Vor Draft-Saves wird ein schneller Fingerprint der Rough-Cut-/Drive-Quelle
geprüft; vor der finalen Bestätigung zusätzlich der Dateiinhalt. Geänderte
Dateimetadaten, geänderter Inhalt bei identischen Metadaten oder ein ersetzter
Proposal werden als `stale`/Konflikt angezeigt. In diesem Zustand werden weder
Board noch Draft überschrieben und kein Produktionsjob angelegt.

## Kompatibilität

Der neue Visual Plan verwendet Schema v2. Bestehende v1-Pläne und Boards ohne
Visual-Artefakte bleiben ladbar und folgen weiterhin ihrem bisherigen
Resume-Pfad. Die Änderung fügt keine neue harte Modell-, GPU- oder
Videoanalyse-Abhängigkeit hinzu.

## Verifikation und Grenzen

Die Implementierung enthält Backend-Regressionen für Persistenz,
Rough-Cut-Abdeckung, Kandidatengenerierung, Frame-Allokation, Cutlist,
Kontaktbogen, API-Konflikte, Orchestrator-Hard-Stops, atomaren Resume und Cancel.
Electron-Tests decken Datenvertrag, feste Szenenreihenfolge, Metadaten,
Dauerbilanz, Sperrgründe, Bestätigung und den gemeinsamen Refresh-Pfad ab.

Frische Abschluss-Gates auf `codex/resumable-visual-selection`:

- Backend: 2.893 Tests bestanden, 12 übersprungen, zwei bekannte
  Dependency-Warnungen; Mypy prüfte 543 Dateien, Ruff war sauber.
- Desktop: 72 Testdateien mit 498 Tests bestanden; Typecheck und Token-Lint
  waren sauber.
- Der repositoryweite `pnpm run lint` bleibt ausschließlich am bereits
  bekannten, nicht installierten `eslint`-Befehl hängen. Es wurde dafür keine
  Dependency oder Lockdatei verändert.

Live wurde Electron zweimal vollständig mit dem bestehenden
`workspace-livetest` gestartet. Die Drive-VibeMind-Produktion erschien nach
jedem Neustart unter `Offene Produktionen`; `Fortsetzen` öffnete die
schreibgeschützte Orphan-Ansicht ohne Chat- oder LLM-Turn. Dabei wurde ein
veralteter Desktop-Schema-Pin (35 statt Migration 36) gefunden, testgetrieben
korrigiert und anschließend live als grünes `API … schema 36` bestätigt.

Nicht als live verifiziert behauptet werden Kandidat-/Dauer-Autosave und der
anschließende Render mit `gpt-5.6-luna`: Im vorhandenen Live-Workspace war keine
noch offene Visual-v2-Auswahl vorhanden. Der vollständige Vertrag — inklusive
vier exakten Entscheidungen, sieben Tagen Pause, Backend-Neustart, genau einem
Resume-Job sowie unveränderten Script-/Voice-Hashes — ist stattdessen durch den
Datei-SQLite-Akzeptanztest automatisiert abgedeckt. API-Schlüssel und
Runtime-Artefakte wurden weder geloggt noch committed.
