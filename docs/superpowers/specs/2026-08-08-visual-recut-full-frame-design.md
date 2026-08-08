# Visual Recut mit Full-frame + Blur-Fill

**Status:** vom User in drei Abschnitten bestätigt

**Datum:** 2026-08-08

**Scope:** bestehende Auto-Short-Produktion nach visueller Kritik neu schneiden, ohne Sprechertext oder Voice neu zu erzeugen

## 1. Problem und beobachtetes Fehlverhalten

Der Rowboat-Short ist technisch renderbar, aber der vertikale Bildschnitt schneidet breite UI-Aufnahmen ab. Zusätzlich blieb die neue Bildauswahl faktisch an einer bereits bestätigten Scene-Selection hängen. Ein langer, degradiert analysierter Clip lieferte nur das erste Standardfenster von etwa vier Sekunden. Der Orchestrator wiederholte daraufhin Planung und Statusausgaben, statt an einem User-Gate hart zu stoppen.

Die bestehenden Mechanismen reichen jeweils nur teilweise:

- `build_cutlist(zoom="off")` und der Renderer mit `fit="blur"` können das vollständige Querformat bereits sicher in 9:16 zeigen.
- Gate S schützt die Auswahl vor der Storyline, ist nach Bestätigung aber final und liegt in der Provenienz vor Storyline, Script und Voice.
- Der Kontaktbogen ist bisher eine Prompt-Konvention, kein persistierter, vom Renderer erzwungener Freigabestatus.

Ein nachträglicher visueller Recutt darf daher nicht einfach Gate S zurücksetzen: Das würde die bestehende Provenienzkette bis zu Script und Voice ungültig machen, obwohl beide ausdrücklich erhalten bleiben sollen.

## 2. Ziele

1. Der freigegebene Sprechertext und die vorhandene Voice bleiben byte- und versionsidentisch.
2. Laura analysiert Szenen und Zeitfenster für den Bildschnitt neu.
3. Breite UI-Aufnahmen verwenden standardmäßig Full-frame mit Blur-Fill; automatische ROI-Crops sind in diesem Modus ausgeschlossen.
4. Auswahlvorschlag und Kontaktbogen sind zwei echte, persistierte Hard Stops.
5. Der Orchestrator delegiert nach einem wartenden Gate keinen weiteren Agent-Turn.
6. Lange, degradiert analysierte Szenen liefern zeitlich verteilte Kandidaten statt ausschließlich des Anfangsfensters.
7. Abbruch und widersprüchliche Agent-Erfolgsmeldungen werden fail-closed behandelt.

## 3. Nicht-Ziele

- Kein neuer Sprechertext, keine neue Voice und keine Sprachänderung.
- Kein allgemeines Smart-Zoom-System und keine neue ROI-Erkennung.
- Kein Wechsel auf ein horizontales Ausgabeformat.
- Keine Änderung des bestehenden Gate-S-Verhaltens für neue Produktionen.
- Kein automatisches Rendern unmittelbar nach der neuen Auswahl.

## 4. Architekturentscheidung

Der visuelle Recutt erhält einen eigenen, **post-voice** liegenden Artefaktzweig. Das bestehende `scene_selection`-Artefakt bleibt unverändert, weil es zur narrativen Storyline-Provenienz gehört.

```text
existing script hash ─┐
                     ├─ visual_recut_request
existing voice hash ─┘           │
                                  ▼
                         visual_plan proposal
                                  │
                      HARD STOP: visual selection
                                  │ confirm proposal hash
                                  ▼
                         cutlist (zoom off)
                                  │
                             contact sheet
                                  │
                      HARD STOP: sheet approval
                                  │ confirm sheet hash
                                  ▼
                         render + QA report
```

Dieser Zweig invalidiert bei einer neuen visuellen Bitte nur `visual_plan`, `cutlist`, `contact_sheet`, `render_report` und `qa_report`. Storyline, Script und Voice werden weder gelöscht noch neu gespeichert.

## 5. Komponenten und Verträge

### 5.1 `visual_recut_request`

Ein neues Board-Artefakt registriert den ausdrücklichen visuellen Änderungswunsch. Es enthält:

- die aktuellen Content-Hashes und Versionen von Script und Voice,
- `framing_mode="full_frame_blur"`,
- `preserve_script=true` und `preserve_voice=true`,
- den User-Auftrag sowie den Erstellungszeitpunkt.

Ein Agent-Tool `start_visual_recut` darf dieses Artefakt nur anlegen, wenn aktuelle Script- und Voice-Artefakte existieren. Das Tool führt die beschränkte Downstream-Invalidierung aus. Die Agent-Prompts ordnen einen ausdrücklichen Wunsch nach neuer Bildauswahl oder vollständigem UI diesem Tool zu; ein reiner Framing-Wunsch darf weiterhin direkt über `build_cutlist(zoom="off")` laufen.

### 5.2 Kandidatengenerator

Der Kandidatengenerator arbeitet pro Sprechertext-Abschnitt beziehungsweise Voice-Timing-Abschnitt. Er kombiniert:

1. gesunde Scene-Review-Fenster,
2. passende Transkriptsegmente mit echten Zeitstempeln,
3. einen deterministischen Coverage-Fallback.

Der Coverage-Fallback erzeugt bei langen Szenen mehrere end-exclusive Fenster über die gesamte Szenendauer. Er verwendet relevante Transkriptanker, sofern vorhanden; andernfalls gleichmäßig verteilte Anker. Ein Clip, der deutlich länger als ein Kandidatenfenster ist, darf im degradierten Zustand nicht auf ausschließlich `0–4 s` kollabieren.

Pro Abschnitt werden höchstens vier Kandidaten gehalten. Bewertungskriterien sind:

- semantische Nähe zum zugehörigen Sprechertext,
- vollständige und stabile UI,
- nutzbare Dauer,
- zeitliche Abdeckung der Quelle,
- visuelle Abwechslung gegenüber benachbarten Shots.

Fast identische oder direkt wiederholte Fenster werden abgewertet. Reichen die Kandidaten nicht für jeden Abschnitt, wird kein Plan gespeichert; der Lauf endet mit einer konkreten Qualitätsmeldung.

### 5.3 `visual_plan` und Auswahl-Gate

`visual_plan` ist ein geordneter Vorschlag aus stabilen Shot-IDs. Jede Shot-ID bindet Sprechertext-Abschnitt, Asset, Szene und end-exclusive Quellframes. Der Plan speichert außerdem Auswahlgrund und Framing-Modus.

Die Bestätigung erfolgt ausschließlich serverseitig und enthält den aktuellen Proposal-Hash. Eine alte oder fremde Bestätigung wird mit Konflikt abgewiesen. Nach dem Speichern eines unbestätigten Plans liefert der Orchestrator:

```json
{
  "status": "awaiting_user_input",
  "gate": "visual_selection",
  "proposal_hash": "sha256:current-visual-plan-content",
  "required_action": "confirm_visual_selection"
}
```

Dieser Rückgabestatus ist für den aktuellen Run terminal. Die Chat- und HTTP-Wege rufen denselben Confirmation-Service auf.

### 5.4 Cutlist und Framing

Nach bestätigtem `visual_plan` baut `build_cutlist` die Segmente aus dessen Shot-Referenzen, während Script und Voice unverändert bleiben. Für `framing_mode="full_frame_blur"` gelten harte Invarianten:

- `zoom="off"`,
- `roi=None` für jedes Segment,
- keine Zoom-Timings,
- vollständige Quellframes,
- vertikaler Renderer weiterhin mit `fit="blur"`.

Die Cutlist-Provenienz enthält Script-, Voice- und Visual-Plan-Hash. Dadurch kann kein alter Plan mit einer neuen Voice oder umgekehrt gerendert werden.

### 5.5 Kontaktbogen-Gate

Der Kontaktbogen bleibt direkt hinter der Cutlist, erhält aber eine echte Freigabe. Die Tiles liefern in API und Electron mindestens Reihenfolge, Szene, In/Out, Sprechertext-Auszug und Auswahlgrund. Das PNG darf seine kompakte Reihenfolge-/Szenen-Beschriftung behalten; die zusätzlichen Angaben erscheinen neben der Vorschau in Electron. Bestehende Felder bleiben rückwärtskompatibel; neue Metadaten sind optional für alte Boards.

Die serverseitige Bestätigung bindet sich an den aktuellen Contact-Sheet-Hash. `render_production` verweigert den Aufruf, solange:

- kein Kontaktbogen existiert,
- der aktuelle Bogen nicht bestätigt ist,
- oder Cutlist beziehungsweise Visual-Plan seit der Bestätigung verändert wurden.

Nach `save_contact_sheet` endet der aktuelle Run mit `status="awaiting_user_input"`, `gate="contact_sheet"` und dem aktuellen Sheet-Hash.

## 6. Orchestrator- und Abbruchverhalten

Der Orchestrator prüft persistierte Gates sowohl nach Tool-Aufrufen als auch nach vollständigen Agent-Antworten. Sobald ein unbestätigter `visual_plan` oder Kontaktbogen existiert, wird das Team beendet und der wartende Status zurückgegeben. Text wie „weiter warten“ wird nie erneut delegiert.

Zwischen Agent-Turns und vor mutierenden Tools wird `cancel_requested` geprüft. Ein Abbruch beendet den Run ohne weitere Board-Schreibvorgänge. Die Abschlussklassifikation vergleicht Agent-Aussagen mit Board-State und Tool-Receipts; eine Behauptung über Render oder Speicherung ohne passenden Board-Artefaktstatus bleibt ein Hard Fail.

## 7. Datenfluss im bestätigten Rowboat-Recutt

1. Der User verlangt eine neue Bildauswahl mit vollständiger UI.
2. `start_visual_recut` bindet den Auftrag an das bestehende Script und die bestehende Voice.
3. Laura erzeugt zeitlich verteilte Kandidaten und speichert einen `visual_plan`-Vorschlag.
4. Der Run stoppt. Der User bestätigt genau diesen Vorschlag.
5. Laura baut eine Full-frame-Cutlist und speichert den Kontaktbogen.
6. Der Run stoppt erneut. Der User prüft und bestätigt genau diesen Bogen.
7. Erst dann rendert Laura den 9:16-Short mit Blur-Fill und führt QA aus.

## 8. Fehlerfälle

- **Script oder Voice fehlt:** Visual Recutt wird ohne Mutation abgewiesen.
- **Script/Voice ändert sich während des Recuts:** Proposal oder Bestätigung wird als stale abgewiesen.
- **Keine ausreichenden Bildkandidaten:** Qualitätsfehler am Auswahl-Gate; kein Fallback auf den alten schlechten Shot.
- **Stale Proposal- oder Sheet-Bestätigung:** HTTP/Chat-Konflikt; kein Resume.
- **Render vor Sheet-Freigabe:** deterministische Tool-Verweigerung ohne Render-Aufruf.
- **Abbruch während Team-Lauf:** terminaler Cancel-Status ohne weitere Tool-Schreibvorgänge.
- **Agent behauptet Erfolg ohne Artefakte:** Hard Fail mit dem fehlenden Receipt im Ergebnis.

## 9. Rückwärtskompatibilität

Neue Board-Felder und Artefakte erhalten Defaults, sodass alte Boards unverändert laden. Ohne `visual_recut_request` bleibt die bestehende Produktionskette unverändert. Gate S für neue Produktionen und der direkte Framing-only-Pfad bleiben erhalten.

## 10. Teststrategie

Die Umsetzung beginnt mit RED-Tests und deckt mindestens ab:

1. `start_visual_recut` erhält Script- und Voice-Version und invalidiert nur visuelle Downstream-Artefakte.
2. Eine lange degradierte Szene erzeugt mehrere zeitlich verteilte, end-exclusive Fenster.
3. Ein unbestätigter Visual Plan beendet den Orchestrator nach genau einem Proposal.
4. Proposal-Bestätigung ist hashgebunden, idempotent und stale-sicher.
5. Die Full-frame-Cutlist besitzt keine ROI- oder Zoom-Daten; der Render-Aufruf erhält `fit="blur"`.
6. `save_contact_sheet` erzeugt einen terminalen Hard Stop.
7. `render_production` hat vor passender Sheet-Bestätigung einen Raise-on-call-Guard und einen Render-Call-Counter von null.
8. Sheet-Bestätigung ist hashgebunden und wird nach Cutlist-Änderung ungültig.
9. Cancel wird zwischen Team-Turns und vor mutierenden Tools respektiert.
10. Ein echter AutoGen-Vertragstest beweist, dass nach jedem Gate kein zusätzlicher Modellaufruf stattfindet.
11. Fokussierte Tests, vollständiges `pytest`, `mypy` und `ruff` bleiben grün.

## 11. Live-Abnahme in Electron

Die Live-Abnahme verwendet den bestehenden Rowboat-Projektkontext und den konfigurierten 9Router-Pfad:

1. visuellen Recutt im Electron-Chat anfordern,
2. prüfen, dass Script- und Voice-Version unverändert sind,
3. genau einen Visual-Plan-Vorschlag sehen und bestätigen,
4. genau einen Kontaktbogen sehen; vollständige UI und abwechslungsreiche Quellen prüfen,
5. Kontaktbogen einmal bestätigen,
6. Render bis zum terminalen Status verfolgen,
7. Export technisch auf 1080×1920, Audio, Dauer und mehrere nicht-schwarze Frames prüfen,
8. an mehreren Frames visuell bestätigen, dass breite UI vollständig bleibt,
9. Chatlog auf wiederholte Wait-/Fact-Sheet-/Proposal-Schleifen prüfen.

Erfolg bedeutet nicht nur ein vorhandenes MP4, sondern auch unveränderte Narrative-Artefakte, zwei nachweisbare Gate-Stopps, genau einen bestätigten Render und sichtbare vollständige UI.
