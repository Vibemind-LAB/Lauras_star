# Rough-Cut-basierte Visual-Auswahl

Stand: 13. August 2026  
Branch: `codex/scene-gate-hard-stop`

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

Automatisierte Abschluss-Gates werden vor dem Push frisch ausgeführt und im
Abschlussbericht mit exakten Ergebnissen dokumentiert.

Nicht als verifiziert behauptet wird ein neuer Live-End-to-End-Lauf mit
`gpt-5.6-luna`, einschließlich realem Render, Medienprüfung und separatem
Cancel-Lauf. Dafür werden vorhandene API-Zugangsdaten ausschließlich in einer
temporären Prozessumgebung benötigt; Schlüssel und Runtime-Artefakte gehören
nicht in Git.
