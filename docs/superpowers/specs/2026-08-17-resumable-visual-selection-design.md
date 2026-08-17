# Design: Fortsetzbare Rough-Cut-Szenenauswahl

**Status:** vom Nutzer am 17. August 2026 freigegeben

**Geltungsbereich:** lokale Produktions-Sessions, Rough-Cut-Bildauswahl v2 und Electron-Chat

**Erweitert:** `2026-08-09-rough-cut-visual-selection-design.md`, `2026-08-03-chat-first-design.md` und `2026-08-05-follow-up-experience-design.md`

## 1. Problem und Ziel

Die Rough-Cut-Szenenauswahl ist bereits ein persistiertes Freigabe-Gate, aber die noch nicht bestätigten Entscheidungen leben ausschließlich im React-Zustand. Wer Kandidat, Verwenden/Überspringen oder Dauer ändert und anschließend die App schließt, verliert diese Zwischenauswahl. Nach Stunden oder Wochen lässt sich zwar der alte Chat finden, nicht aber zuverlässig der genaue Bearbeitungsstand.

Laura soll deshalb jede Änderung der offenen Bildauswahl automatisch lokal speichern und die zugehörige Produktions-Session später explizit fortsetzen können. Das gilt nach einem Electron-Neustart, einem Backend-Neustart und einer längeren Pause ohne feste Ablaufzeit.

Das Ergebnis muss folgende Eigenschaften haben:

- Kandidat, Verwenden/Überspringen und Dauer von 1 bis 10 Sekunden werden pro Rough-Cut-Zeile gespeichert.
- Die Auswahl ist mit Produktions-Session, Proposal, Quellmaterial, Chat und ursprünglichem Brief verknüpft.
- Ein sichtbarer Einstieg „Offene Sessions“ führt zurück zum gespeicherten Stand.
- Erst „Bildauswahl übernehmen“ bestätigt den Entwurf und setzt die Produktion fort.
- Veraltete Proposals, geänderte Rough-Cut-Daten und fehlende oder geänderte Drive-Dateien stoppen fail-closed.
- Autosave erzeugt keinen Agentenlauf, keinen Renderjob und keinen zusätzlichen LLM-Aufruf.

## 2. Produktentscheidung

Die Auswahl wird als eigener serverseitiger Entwurf in der lokalen SQLite-Datenbank gespeichert. Der Browser- oder Electron-Zustand ist nur eine Darstellung dieses Entwurfs, nicht dessen Quelle der Wahrheit.

Eine Session wird nicht ungefragt geöffnet. Die App zeigt offene Sessions deutlich an; bei genau einer offenen Session erscheint ein hervorgehobener „Fortsetzen“-Eintrag. Erst ein Klick öffnet den verknüpften Chat und die passende Freigabekarte.

Die bestehende finale Bestätigung bleibt die einzige Transition vom offenen Visual-Selection-Gate zur nächsten Produktionsstufe. Ein Entwurf darf zwischenzeitlich noch unvollständig sein, solange jede einzelne gespeicherte Zeile strukturell gültig ist.

## 3. Zustandsmodell

### 3.1 Produktions-Session

`production_sessions` erhält folgende persistierte Metadaten:

- `conversation_id`: optionaler Verweis auf den Chat, aus dem die Produktion gestartet wurde;
- `brief_text`: der vollständige ursprüngliche Produktionsauftrag;
- `updated_utc`: Zeitpunkt der letzten fachlichen Änderung an Session, Gate oder Auswahlentwurf.

Bestehende Sessions bleiben lesbar. Für sie ist `conversation_id` zunächst leer, `brief_text` leer und `updated_utc` wird bei der Migration mit `created_utc` befüllt.

### 3.2 Visual-Selection-Entwurf

Eine neue Tabelle `visual_selection_drafts` enthält genau einen aktuellen Entwurf pro Produktions-Session:

| Feld | Bedeutung |
|---|---|
| `session_id` | Primärschlüssel und Fremdschlüssel zur Produktions-Session |
| `proposal_hash` | Bindung an genau das angezeigte Visual-Plan-Proposal |
| `source_fingerprint` | Bindung an den beim Speichern geprüften Materialstand |
| `selections_json` | Vollständige Entscheidungen in Rough-Cut-Reihenfolge |
| `revision` | monoton steigende Versionsnummer für Compare-and-Swap |
| `updated_utc` | Zeitpunkt der letzten erfolgreichen Speicherung |

`selections_json` enthält für jede aktuelle Rough-Cut-Zeile genau einen Eintrag:

```json
{
  "rough_cut_order": 0,
  "included": true,
  "candidate_id": "candidate-...",
  "requested_duration_s": 5
}
```

Die Liste muss vollständig, lückenlos und in Rough-Cut-Reihenfolge vorliegen. Der Kandidat muss zur jeweiligen Zeile gehören. Die Dauer ist strikt numerisch, liegt zwischen 1 und 10 Sekunden und überschreitet nicht die für den Kandidaten erlaubte Dauer.

Ein Entwurf darf weniger als drei verwendete Szenen oder eine noch unzureichende Gesamtdauer enthalten. Das ist ein zulässiger Zwischenstand. Die bestehenden finalen Coverage-, Mindestanzahl- und Kapazitätsregeln werden erst bei der Bestätigung verbindlich geprüft und in der UI vorab verständlich angezeigt.

### 3.3 Lebenszyklus

```text
Visual-Plan pending
        |
        v
Default-Entwurf oder gespeicherter Entwurf
        |
        +-- jede Änderung --> Autosave, revision + 1
        |
        +-- App/Backend aus --> Zustand bleibt in SQLite
        |
        +-- Fortsetzen --> Entwurf laden und Freshness prüfen
        |
        +-- stale --> sichtbarer Stopp, neues Proposal erforderlich
        |
        +-- Bildauswahl übernehmen --> finale Validierung
                                      |
                                      v
                              Visual-Plan confirmed
                                      |
                                      v
                         Kontaktbogen-Gate / nächster Job
```

Nach erfolgreicher Bestätigung wird der Entwurf in derselben fachlichen Operation entfernt. Falls ein Prozessabbruch zwischen Board-Bestätigung und Bereinigung liegt, ignoriert und entfernt der nächste Lesezugriff den Entwurf, weil das Visual-Plan-Gate nicht mehr pending ist.

Entwürfe haben keine automatische Zeitablauffrist. Acht Stunden, eine Woche oder länger verändern ihre Gültigkeit nicht; entscheidend ist ausschließlich, ob Proposal und Quellen noch frisch sind.

## 4. Bindung an Proposal und Quellmaterial

### 4.1 Kanonischer Quell-Fingerprint

Das Proposal wird zusätzlich an einen kanonischen Quell-Fingerprint gebunden. Dieser enthält mindestens:

- Asset-ID und beim Ingest gespeicherten Asset-SHA-256;
- kanonische Identität des aufgelösten Quellpfads;
- aktuelle Dateigröße und `mtime_ns`;
- Hash der geordneten Rough-Cut-Quellranges;
- Projekt-FPS;
- Voice-Content-Hash und projizierte Gesamtframes;
- Script-, Request- und weitere bereits bestehende Parent-Hashes.

Der Fingerprint wird deterministisch aus kanonischem JSON gebildet. Das Visual-Plan-Proposal speichert ihn als Parent `source_media`. Der Entwurf speichert den beim letzten Autosave geprüften Wert.

### 4.2 Schnelle und starke Prüfung

Zwei Prüfstufen vermeiden unnötige Voll-Hashes bei jeder UI-Änderung:

1. **Autosave und Resume:** prüfen Existenz, Pfadauflösung, Größe, `mtime_ns`, Rough-Cut-Hash, FPS, Voice und Proposal-Parents. Ein Unterschied ergibt sofort `409 stale` und keinerlei Mutation.
2. **Finale Bestätigung:** berechnet zusätzlich den SHA-256 der aktuellen Quelldatei und vergleicht ihn mit der beim Ingest gespeicherten Identität. Fehlt der gespeicherte SHA, wird das Proposal vor einer Bestätigung neu aufgebaut und mit einem starken Hash gebunden. Ein Unterschied ergibt `409 stale`, ohne Board-Transition oder Job-Enqueue.

Damit bleibt Autosave schnell, während geänderte Drive-Dateien vor jeder produktiven Fortsetzung sicher erkannt werden. Auch eine Inhaltsänderung mit absichtlich identischer Dateigröße und Zeitangabe kann die finale Prüfung nicht passieren.

Bestehende Visual-v2-Proposals ohne `source_media`-Parent werden nicht stillschweigend bestätigt. Laura markiert sie als veraltet und baut nach Nutzeraktion ein frisches Proposal. Legacy- und Gate-off-Abläufe ohne Visual-v2-Bestätigung bleiben kompatibel.

## 5. Backend-Verträge

### 5.1 Draft lesen

`GET /production/{session_id}/visual-selection/draft`

Die Antwort enthält:

- `session_id`, `proposal_hash`, `revision`, `updated_utc`;
- die vollständigen `selections`;
- `stale: false` bei frischem Stand;
- bei veraltetem Stand einen stabilen `stale_reason`, ohne den Entwurf zu überschreiben.

Ohne gespeicherten Entwurf liefert der Endpoint einen deterministischen Default aus dem aktuellen pending Visual-Plan mit `revision: null`. Ohne pending v2-Gate antwortet er fail-closed.

### 5.2 Draft speichern

`PUT /production/{session_id}/visual-selection/draft`

```json
{
  "proposal_hash": "64 lowercase hex characters",
  "expected_revision": 3,
  "selections": []
}
```

Beim ersten Schreiben ist `expected_revision` `null`. Danach muss es exakt der aktuellen Revision entsprechen. Die Operation läuft in einer SQLite-Transaktion und führt in dieser Reihenfolge aus:

1. Session und Board unter dem Schreib-Lock neu laden;
2. pending Visual-v2-Gate und Proposal-Hash prüfen;
3. aktuelle schnelle Source-Freshness prüfen;
4. vollständige Struktur der Auswahl prüfen;
5. Revision per Compare-and-Swap erhöhen;
6. `production_sessions.updated_utc` aktualisieren.

Eine abweichende Revision liefert `409 conflict` und den aktuellen serverseitigen Entwurf. Laura überschreibt niemals still die neuere Auswahl eines zweiten Fensters.

Stale Proposal oder Quelle liefert `409 stale`. Strukturell ungültige Daten liefern `422`. In allen Fehlerfällen bleiben Entwurf, Board und Job-Queue unverändert.

### 5.3 Produktionsstatus

`GET /production/{session_id}` liefert am pending Visual-v2-Gate zusätzlich den aktuellen Entwurf beziehungsweise den Default-Entwurf, seine Revision, den Speicherzeitpunkt und einen möglichen Stale-Grund. Dadurch hydratisiert die Aktionskarte aus einem konsistenten Status-Snapshot und benötigt beim Öffnen keinen konkurrierenden zweiten Initial-Request.

### 5.4 Finale Bestätigung

Der bestehende Confirm-Service verwendet dieselbe gemeinsame Proposal-, Struktur- und Freshness-Validierung wie der Draft-Service. Vor der Board-Transition läuft die starke Quelldatei-Prüfung. Nach Erfolg wird der Entwurf bereinigt und genau ein Resume-Job erzeugt; bestehende Busy- und Idempotenz-Garantien bleiben erhalten.

Ein Autosave ruft niemals den Confirm-Service auf und erzeugt niemals einen Job.

## 6. Offene Sessions, Chat und Kontext

### 6.1 Session-Verknüpfung

Beim Start einer Produktion speichert Laura den vollständigen Task als `brief_text`. Wird die Produktion aus dem Chat gestartet, wird anschließend die `conversation_id` transaktional mit der Session verknüpft.

Der Router erhält bei einer aktiven Produktions-Session den persistierten Brief und den strukturierten Gate-Status unabhängig vom rollierenden Chatfenster. Die bestehende Begrenzung auf die letzten 20 Nachrichten bleibt bestehen und spart Tokens; der ursprüngliche Auftrag muss deshalb nicht immer wieder in die LLM-Historie kopiert werden.

### 6.2 Open-Sessions-Endpoint

`GET /production-sessions/open` liefert lokale, noch nicht abgeschlossene Sessions, nach `updated_utc` absteigend:

- `session_id`, `conversation_id` und `project_id`;
- Projekt-/Asset-Anzeige und gekürzte Brief-Vorschau;
- `resume_point` und verständlicher Zustandsname;
- `updated_utc` und optional `draft_updated_utc`;
- `stale` und `stale_reason`;
- Kennzeichen, ob der verknüpfte Chat noch vorhanden ist.

„Offen“ bedeutet insbesondere pending Visual-Selection, pending Kontaktbogen oder ein anderer bestätigungsfähiger Resume-Punkt. Abgeschlossene, endgültig fehlgeschlagene oder abgebrochene Sessions erscheinen nicht in der Standardliste, bleiben aber in der Datenbank nachvollziehbar.

### 6.3 Desktop-Einstieg

Oberhalb der Chatliste erscheint der Bereich „Offene Sessions“. Bei genau einer offenen Session zeigt er einen großen „Fortsetzen“-Eintrag; bei mehreren eine sortierte Liste.

Ein Klick:

1. öffnet den verknüpften Chat, falls vorhanden;
2. lädt den Produktionsstatus;
3. scrollt zur aktuellen Aktionskarte;
4. hydratisiert die Visual-Auswahl aus dem Serverentwurf.

Fehlt ein alter Chat, öffnet Laura eine Session-Ansicht mit Brief und Gate-Zustand, statt die Produktion unauffindbar zu machen. Die App öffnet keine Session automatisch und startet beim Fortsetzen keinen LLM-Lauf.

## 7. Electron-Autosave und Bedienung

Die `VisualSelectionCard` initialisiert ihre Entscheidungen ausschließlich aus dem vom Backend gelieferten Entwurf oder Default. Lokaler Zustand darf diesen Stand optimistisch darstellen, bleibt aber nicht die dauerhafte Quelle.

Jede der folgenden Aktionen sendet sofort den vollständigen strukturellen Entwurf:

- Kandidat wechseln;
- Szene verwenden oder überspringen;
- Dauer über Preset oder Eingabe ändern.

Eine serialisierte Save-Queue verhindert Umordnung schneller Änderungen. Jeder Request verwendet die letzte bestätigte Revision. Die UI zeigt eindeutig:

- `Speichert …` während eines Requests;
- `Gespeichert <Uhrzeit>` nach Erfolg;
- einen sichtbaren Fehler mit Wiederholen bei Netzwerk-/Serverfehlern;
- einen Konflikt-Hinweis mit „Serverstand laden“, wenn ein anderes Fenster neuer ist;
- einen Stale-Hinweis mit „Auswahl neu aufbauen“, wenn Material oder Proposal abweicht.

„Bildauswahl übernehmen“ ist deaktiviert, solange ein Save läuft, ein Save fehlgeschlagen ist, ein Konflikt ungelöst ist oder der Stand stale ist. Vor dem Confirm wartet der Client auf die leere Save-Queue und verwendet exakt den zuletzt serverbestätigten Entwurf.

Unvollständige finale Coverage wird als Auswahlbilanz erklärt, blockiert aber nicht das Zwischenspeichern.

## 8. Nebenläufigkeit und Fehlerfälle

- Zwei Electron-Fenster beginnen mit derselben Revision. Das erste speichert erfolgreich; das zweite erhält `409 conflict` und darf den Serverstand nicht überschreiben.
- Eine neue Visual-Plan-Version macht einen alten Entwurf stale. Der alte Entwurf bleibt zur Diagnose lesbar, kann aber weder gespeichert noch bestätigt werden.
- Geänderte Rough-Cut-Ranges, FPS, Voice-Projektion oder Script-/Request-Parents machen den Entwurf stale.
- Eine fehlende, nicht lesbare oder inhaltlich veränderte Drive-Datei blockiert die finale Bestätigung vor jeder Board- oder Queue-Mutation.
- Ein Backend-Neustart rekonstruiert den Zustand ausschließlich aus SQLite und Board-Artefakten; Prozessspeicher ist nicht erforderlich.
- Ein abgebrochener Autosave verändert weder Gate noch Produktionsjob.
- Mehrfaches finales Bestätigen bleibt durch bestehende Lock-, Busy- und Forward-Heal-Regeln idempotent.

## 9. Migration und Kompatibilität

Die nächste Datenbankmigration ergänzt die Session-Metadaten und die Draft-Tabelle. Sie ist additiv und benötigt keine Löschung vorhandener Workspace-Daten.

Kompatibilitätsregeln:

- bestehende Chats und Produktions-Sessions bleiben aufrufbar;
- Visual-v1-, Scene-Selection- und Gate-off-Pfade verändern ihr Payload-Format nicht;
- alte pending Visual-v2-Proposals ohne starken Source-Parent werden kontrolliert als stale behandelt und neu aufgebaut;
- bestätigte oder abgeschlossene Boards erzeugen keinen neuen Entwurf;
- API-Modelle bleiben strikt und verbieten Scalar-Coercion sowie unbekannte Felder.

## 10. Datenschutz und Tokenverbrauch

Entwürfe, Briefs und Session-Metadaten liegen ausschließlich in Lauras lokaler SQLite-/Workspace-Struktur. Die Funktion führt keine neue Cloud-Synchronisierung ein.

Autosave, Sessionliste und Fortsetzen sind deterministische lokale Operationen und verbrauchen keine LLM-Tokens. Der persistierte Brief ersetzt wiederholtes Einfügen großer Kontextblöcke; der Router behält sein begrenztes Nachrichtenfenster.

## 11. Teststrategie

### 11.1 Backend und Datenbank

- Draft-Roundtrip über Schließen und erneutes Öffnen einer SQLite-Verbindung;
- vollständige Auswahl, Revision und Zeitstempel bleiben exakt erhalten;
- Compare-and-Swap mit zwei Schreibern: genau ein Erfolg, ein `409 conflict`;
- kein Autosave-Job und keine Board-Mutation;
- Default-Entwurf ohne gespeicherten Draft;
- Draft-Bereinigung nach erfolgreicher Bestätigung und Heal nach simuliertem Abbruch;
- offene Sessions korrekt sortiert und mit Chat/Brief verknüpft;
- persistierter Brief bleibt nach mehr als 20 Chatnachrichten im Router-Kontext;
- bestehende v1- und Gate-off-Verträge bleiben grün.

### 11.2 Freshness

- Proposal-Hash, Rough-Cut-Ranges, FPS, Voice-Frames und Parent-Hashes jeweils einzeln verändern;
- Drive-Datei löschen, unlesbar machen, Größe/Zeitstempel ändern und Inhalt bei gleicher Größe ändern;
- jeder Stale-Fall stoppt vor Draft-Schreibzugriff, Board-Transition und Enqueue;
- bestehendes Proposal ohne `source_media`-Parent wird fail-closed erneuert;
- unveränderte Quelle bestätigt weiterhin exakt framegenau.

### 11.3 Desktop

- Kandidat, Use/Skip und Dauer lösen jeweils Autosave aus;
- schnelle Änderungen bleiben in Reihenfolge und verwenden steigende Revisionen;
- Unmount/Remount und simulierter App-Neustart stellen alle Werte wieder her;
- Speicher-, Fehler-, Konflikt- und Stale-Zustände sind sichtbar;
- Confirm wartet auf Saves und bleibt bei Fehlern deaktiviert;
- genau eine offene Session zeigt den prominenten Fortsetzen-Eintrag;
- mehrere offene Sessions sind nach Aktualität sortiert;
- Fortsetzen öffnet Chat und korrekte Aktionskarte, ohne LLM- oder Produktionslauf.

### 11.4 Live-Akzeptanz

Mit dem Projekt „Drive VibeMind“ und dem konfigurierten Cloud-Provider:

1. Rough-Cut-Auswahl öffnen;
2. mehrere Kandidaten, Use/Skip-Werte und Dauern verändern;
3. sichtbaren Zustand „Gespeichert“ abwarten;
4. Electron und Backend vollständig beenden;
5. nach Neustart über „Offene Sessions“ fortsetzen;
6. exakte Werte und denselben Proposal-Hash vergleichen;
7. Auswahl bestätigen, Kontaktbogen freigeben und Render bis QA verfolgen;
8. nachweisen, dass Script- und Voice-Hashes unverändert blieben.

Die automatisierten Tests simulieren zusätzlich eine mehrtägige Pause durch alte Zeitstempel und erneutes Öffnen der Datenbank. Zeit allein darf keine Session invalidieren.

## 12. Nicht-Ziele

- keine Cloud- oder geräteübergreifende Draft-Synchronisierung;
- keine automatische Löschung alter Sessions;
- kein automatisches Öffnen einer Session ohne Nutzeraktion;
- keine freie Neuordnung der Rough-Cut-Zeilen;
- kein LLM-Aufruf pro Auswahländerung und keine LLM-Zusammenfassung für Autosave;
- kein Render vor finaler Bildauswahl- und Kontaktbogen-Freigabe;
- keine Änderung an Script oder bestehender Voice während eines reinen Visual-Resume-Flows.

## 13. Abnahmekriterien

Die Funktion ist fertig, wenn alle folgenden Aussagen nachweisbar sind:

1. Jede gültige Zwischenänderung wird lokal persistiert und nach Prozessneustart exakt wiederhergestellt.
2. Die Session ist nach Stunden oder Wochen über „Offene Sessions“ auffindbar und mit Chat sowie ursprünglichem Brief verknüpft.
3. Autosave und Fortsetzen erzeugen weder LLM-Aufruf noch Produktionsjob.
4. Gleichzeitige Bearbeitung überschreibt keine neuere Revision.
5. Proposal-, Timeline-, Voice-, FPS- und Quelldatei-Drift stoppen vor jeder produktiven Mutation.
6. Erst die finale Bestätigung setzt die bestehende framegenaue Produktionspipeline fort.
7. Backend-, Desktop-, Restart-, Stale- und Live-Drive-Verifikation sind grün beziehungsweise bei externer Nichtverfügbarkeit ausdrücklich als Non-Claim dokumentiert.
