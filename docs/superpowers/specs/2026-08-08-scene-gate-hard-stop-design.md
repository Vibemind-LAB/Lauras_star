# Gate-S Hard-Stop gegen Orchestrator-Wiederholungen — Design

Datum: 2026-08-08 · Branch: `feat/generate-ui` · Status: **Vom User freigegeben 2026-08-08**

## 1. Kontext und belegter Fehler

Der Live-Lauf
`workspace-livetest/project-6cd8c18818624c7dafdf617d2e47f4ba/agent-runs/
f69cd5ba75194a5b92353e5f282496c7/runs/20260807T130145Z.ndjson`
belegt einen Orchestrator-Loop am Szenen-Auswahl-Gate:

- Nach dem ersten gespeicherten Vorschlag delegierte `MagenticOneOrchestrator` viermal
  sinngleich „warte auf die Bestätigung"; `story_architect` antwortete viermal mit
  „ich warte". Drei der vier Paare waren überflüssige Wiederholungen.
- Nach einem erneut gespeicherten Vorschlag wiederholte sich derselbe Viererblock.
- Der vollständige Task-/Fact-Sheet-Kontext wurde insgesamt zehnmal rekonstruiert.
- Der Orchestrator behauptete dreimal eine User-Bestätigung, obwohl kein neuer
  `user`-Event im Run existierte und das Board weiterhin
  `pending=true`, `confirmed=false`, `selected=[]` meldete.
- Der Lauf endete widersprüchlich mit `ok=true`, obwohl
  `resume_point="scene_selection"` und kein Film entstanden war.

Die Agenten sind nicht die State Authority. Insbesondere verweigerte
`story_architect` die Storyline korrekt, solange das Board unbestätigt blieb.

## 2. Root Cause

`propose_scene_selection` persistiert den Vorschlag korrekt, liefert aber nur einen
normalen Tool-Result mit dem Hinweis „STOP now". Das ist eine Prompt-Anweisung, kein
AutoGen-Terminationssignal.

`build_production_team` baut `MagenticOneGroupChat` aktuell ausschließlich mit
`max_turns=30`. Nachdem das Tool den Vorschlag gespeichert hat, bleibt die globale
Produktionsaufgabe aus Sicht von MagenticOne unfertig. Der Orchestrator plant deshalb
weiter, delegiert das Warten erneut und rekonstruiert den Task-Kontext.

Der bestehende Gate-S-Guard in `run_production` verhindert nur einen **neuen**
unbegründeten Resume-Lauf, wenn das unbestätigte Proposal bereits vor Run-Start auf dem
Board liegt. Er kann den **aktuell laufenden** `team.run_stream()` nicht nach dem
Proposal stoppen.

Die externe Bestätigung ist bereits deterministisch implementiert:
`select_scenes` ruft den serverseitigen `confirm_scene_selection`-Service auf, der als
einziger Writer `confirmed_utc` und `selected_scene_numbers` persistiert und danach einen
neuen Resume-Lauf queued. `story_architect` soll bewusst kein Confirm-Tool besitzen.

## 3. Ziele und Nicht-Ziele

### Ziele

- Der aktive MagenticOne-Run endet unmittelbar nach einem neu persistierten,
  unbestätigten `scene_selection`-Proposal.
- Zwischen Proposal und externem User-Turn wird kein Agent erneut aufgerufen und keine
  weitere Orchestrator-Planungsrunde erzeugt.
- Das Run-Ergebnis bezeichnet den Zustand ausdrücklich als `awaiting_user_input`, nicht
  als normalen Erfolg oder Fehler.
- Eine externe Bestätigung über `select_scenes` persistiert zuerst den Board-State und
  startet erst danach einen neuen Run.
- Ein Änderungswunsch zu einem offenen Proposal darf genau einen Teamlauf auslösen; nach
  Speicherung der neuen Proposal-Version stoppt auch dieser Run sofort wieder.
- Turn-Budget-Erschöpfung bleibt ein harter Fehler und darf nicht als `ok=true` erscheinen.

### Nicht-Ziele

- Kein Umbau von MagenticOne oder der fünf Produktionsagenten.
- Kein neues Confirm-Tool für einen Agenten.
- Keine promptbasierte State Authority und keine Auswertung natürlichsprachlicher
  „confirmed"-Behauptungen.
- Keine Änderung von Gate B, deterministischem Tail, Voice/Cutlist oder Rendering.
- Keine allgemeine Workflow-Engine und kein breit angelegter Board-Refactor.
- Proposal-ID-/Versionsbindung im öffentlichen Confirm-Request bleibt ein separates
  Härtungsthema; dieser Fix verwendet die Board-Version nur intern zur Run-Termination.

## 4. Gewünschter Ablauf

### Initialer Vorschlag

```text
run_production
  -> MagenticOne reviewt Szenen
  -> story_architect ruft propose_scene_selection
  -> Board speichert unbestätigte Proposal-Version N
  -> boardbasierte Termination wird wahr
  -> team.run_stream endet
  -> RunResult: status=awaiting_user_input, resume_point=scene_selection
  -> keine weitere Agenten- oder Orchestrator-Nachricht
```

### Externe Bestätigung

```text
neuer User-Turn / SceneSelectionCard
  -> select_scenes
  -> confirm_scene_selection persistiert selected + confirmed_utc
  -> Resume-Job wird queued
  -> neuer run_production sieht resume_point=storyline
  -> Team erstellt Storyline
```

### Änderung eines offenen Vorschlags

```text
User: "nimm Szene 7 noch dazu"
  -> genau ein Follow-up-Teamlauf
  -> story_architect speichert Proposal-Version N+1
  -> Termination erkennt Versionswechsel bei weiterhin offenem Gate
  -> Run endet erneut als awaiting_user_input
```

Ein Agent wird niemals beauftragt, „weiter zu warten". Warten ist ein persistierter
Systemzustand außerhalb eines laufenden Agententeams.

## 5. Architekturentscheidung

### Native, boardbasierte `FunctionalTermination`

`build_production_team` erhält eine optionale Terminationsbedingung für Gate S. Beim
Teamaufbau wird die aktuelle `scene_selection`-Version als Snapshot erfasst:

- kein Proposal vorhanden: Startversion `None`;
- offenes Proposal bei einem Änderungs-Follow-up: Startversion `N`;
- bestätigtes Proposal: Gate-Termination bleibt inaktiv.

Nach jeder AutoGen-Nachricht prüft die `FunctionalTermination` den autoritativen
Board-State. Sie terminiert genau dann, wenn:

1. Gate S für die Session aktiv ist,
2. ein `SceneSelection`-Artefakt existiert,
3. `confirmed_utc is None`, und
4. dessen Version von der Startversion abweicht.

Damit stoppt der initiale Run nach Version 1 und ein Änderungs-Run nach Version N+1.
Ein bereits offenes, unverändertes Proposal beendet einen absichtlich gestarteten
Änderungs-Run nicht vor dessen erster Änderung. Plain Resumes werden weiterhin vor dem
Teamaufbau durch den vorhandenen `run_production`-Guard geparkt.

Die Bedingung liest ausschließlich Board-State. Weder Agententext noch Tool-Notizen oder
Orchestrator-Behauptungen können den Zustand auslösen.

### Expliziter Run-Status

Nach Ende des Teamstreams leitet `run_production` den fachlichen Zustand erneut aus dem
Board ab. Der allgemeine `StageOutcome` der providerübergreifenden Eskalationsleiter
bleibt bewusst auf `ok|hard_fail` begrenzt; Gate-Warten ist kein Provider-Ergebnis.
Liegt ein unbestätigtes Proposal vor, setzt ausschließlich der Produktions-Resultvertrag
den lokalen `ProductionRunStatus` auf `awaiting_user_input`.

`_completed_result` bildet diesen Zustand auf Folgendes ab:

```json
{
  "ok": true,
  "complete": false,
  "status": "awaiting_user_input",
  "resume_point": "scene_selection"
}
```

Das Board bleibt `active`: Der Produktionsauftrag ist gesund geparkt, nicht gescheitert.
Der Session-Status und die bestehende `SceneSelectionCard` bleiben damit kompatibel.

### Stop-Reason-Härtung

Der `TaskResult.stop_reason` wird nicht länger verworfen. Eine Beendigung wegen
maximaler Turns oder Stalls wird als `hard_fail` behandelt, sofern nicht gleichzeitig
der autoritative Board-Zustand eindeutig `awaiting_user_input` belegt. So kann ein
anderer zukünftiger Loop nicht erneut als erfolgreicher Run erscheinen.

## 6. Komponenten und Verantwortlichkeiten

- `production_agents.py`: erzeugt die native AutoGen-Terminationsbedingung und reicht
  sie an `MagenticOneGroupChat` weiter.
- `production_orchestrator.py`: klassifiziert den autoritativen Board-Zustand im
  Produktions-Resultvertrag als `awaiting_user_input` und wertet `stop_reason` aus,
  ohne den allgemeinen `StageOutcome` zu verbreitern.
- `production_tools.py`: persistiert das Proposal weiterhin unverändert; der vorhandene
  „STOP now"-Text bleibt nur Nutzer-/Agentenhinweis, nicht Steuerlogik.
- `api/short_creator.py`: bleibt alleiniger Owner der Confirmation-Transition.
- `chat/executor.py`: bleibt der externe Chat-Einstieg in diese Transition.
- `Board`: bleibt alleinige State Authority; keine neue parallele State-Ablage.

## 7. Fehler- und Nebenläufigkeitsverhalten

- Schlägt `propose_scene_selection` fehl, ändert sich die Board-Version nicht; die
  Termination löst nicht aus und der Agent kann den Toolfehler korrigieren.
- Ein identischer No-op-Save ohne Versionsänderung löst nicht aus.
- Eine erfolgreiche neue Proposal-Version löst genau einmal aus; AutoGen beendet den
  Run über seine native Terminationsschnittstelle.
- Eine User-Bestätigung während eines laufenden Produktionsjobs bleibt durch den
  bestehenden Busy-Guard abgelehnt. Nach dem Hard-Stop ist kein Teamjob mehr aktiv und
  die Confirmation kann sicher persistiert werden.
- Ein Agententext wie „the user confirmed" hat keinerlei Auswirkung auf Board oder
  Resume-Point.
- Ein echtes Turn-Limit ohne Gate-Transition ist `hard_fail` und kann die bestehende
  Stage-B-Eskalation auslösen.

## 8. Teststrategie

### Unit-Tests der Termination

- Gate aus: keine Termination.
- Gate an, kein Proposal: keine Termination.
- Gate an, Proposal-Version unverändert: keine Termination.
- Gate an, neue unbestätigte Proposal-Version: Termination.
- Gate an, neue bereits bestätigte Version: keine Awaiting-Termination.
- Fehlgeschlagener/No-op-Proposal-Save: keine Termination.

### Orchestrator-Regression

- Ein Fake-Team speichert während `run_stream` genau ein Proposal und emittiert danach
  potenzielle weitere Agenten-Events. Die Termination muss den Run direkt nach dem Save
  beenden; kein „wait"-Event darf den Event-Sink erreichen.
- Ergebnis: `status=awaiting_user_input`, `ok=true`, `complete=false`,
  `resume_point=scene_selection`, Board bleibt `active`.
- Plain Resume bei bereits offenem Proposal baut weiterhin kein Team.
- Bestätigtes Proposal lässt den neuen Resume zur Storyline weiterlaufen.
- Änderungs-Follow-up startet ein Team, speichert genau eine neue Proposal-Version und
  stoppt danach erneut.
- Ein Agententext mit erfundener Confirmation verändert den Board-State nicht.
- `maximum turns`/`maximum stalls` ohne Board-Transition ergibt `hard_fail`, nicht `ok`.

### Bestehende Gates

- Fokussiert: `test_production_agents.py`,
  `test_production_orchestrator_scene_gate.py`, `test_api_scene_selection.py` und
  `test_chat_executor.py`.
- Danach gesamte Backend-Suite, Mypy strict und Ruff.
- Kein Desktop-Lauf erforderlich, sofern sich API-Payload und Frontend-Vertrag nicht
  ändern; bei einer Typänderung am öffentlichen Status zusätzlich Desktop-Typecheck und
  Vitest.

## 9. Erfolgskriterien

- Im reproduzierten Ablauf existiert nach `propose_scene_selection` kein zweiter
  Orchestrator- oder Agenten-Turn innerhalb desselben Runs.
- Das große Task-/Fact-Sheet wird innerhalb dieses Gate-Stopps nicht erneut injiziert.
- Ohne neuen externen User-Event kann keine Bestätigung entstehen.
- `storyline`, `script` und alle Downstream-Artefakte bleiben bis zur serverseitig
  persistierten Bestätigung unangetastet.
- Ein geparkter Gate-Run wird fachlich als `awaiting_user_input` ausgewiesen.
- Die vollständigen Backend-, Typ- und Lint-Gates bleiben grün.
