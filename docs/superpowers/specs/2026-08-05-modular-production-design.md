# Modulare Produktion: deterministische Post-Gate-Kette

**Datum:** 2026-08-05 · **Status:** entworfen, vom User freigegeben (Chat)

## Motivation

Der Live-Check vom 2026-08-05 (Transkript-Gates-Arc) zeigte: Nach der Script-Freigabe
(`approve_script`) wird heute das volle Agenten-Team per Follow-up-Text
(„Script freigegeben — bitte fortsetzen …") wieder aufgeweckt. Ein aufgewecktes Team
schreibt das freigegebene Script gern noch einmal um; die content_hash-Bindung der
Freigabe entwertet die Freigabe dann korrekt (Gate bewaffnet sich neu), aber der User
zahlt eine **Doppel-Freigabe** für einen Lauf, in dem es nichts Kreatives mehr zu tun gab.

Kernbeobachtung: **Hinter dem Gate ist die Arbeit deterministisch.** Voice, Cutlist,
Contact Sheet und Render sind reine Tool-Aufrufe über `ProductionDeps` — dieselbe Kette,
die die deterministischen Rebuild-Skripte längst ohne Team bewiesen haben. Ein
deterministischer Post-Gate-Pfad macht das Umschreiben strukturell unmöglich statt es
einem LLM per Charter-Bitte auszureden.

## Entscheidungen (User, 2026-08-05)

1. **Ansatz: modular** — deterministische Kette nach der Freigabe, kein Charter-Patch.
2. **QA bleibt, aber begrenzt**: Nach dem Render läuft EIN Bewertungs-Agent mit einer
   Whitelist aus reinen Lese- + QA-Tools. `save_script_chapter`/`save_storyline` sind
   nicht im Angebot — Umschreiben ist per Konstruktion ausgeschlossen.
3. **Fehlerbild: Auto-Retry + ehrlich scheitern** — jeder Schritt bekommt genau einen
   automatischen Retry (transiente API-/ffmpeg-Fehler); danach scheitert der Lauf ehrlich
   (Karte ✗ mit Schrittnamen). **Kein Team-Fallback** — der würde das Umschreib-Risiko
   wieder einschleppen.
4. **Scope v1: nur der Post-Freigabe-Pfad** gated Chat-Sessions. Fresh Runs,
   Text-Follow-ups und HTTP-Auto-Short bleiben unverändert agentisch.

## Architektur

### Neues Modul: `short_creator/production_pipeline.py`

Eine Funktion trägt die Kette:

```python
def run_deterministic_tail(
    db: Database,
    board: Board,
    config: AgentConfig,
    deps: ProductionDeps,
    event_sink: Callable[[dict[str, Any]], None] | None,
) -> TailOutcome
```

- Schritte in Kettenreihenfolge: `synthesize_script_voice → build_cutlist →
  save_contact_sheet → render` — jeweils der EXISTIERENDE Tool-Closure aus
  `build_production_tool_specs` (bzw. dieselben zugrunde liegenden Funktionen mit
  denselben `ProductionDeps`). Kein neuer Render-/Voice-Code.
- **Skip-Semantik = Resume-Semantik:** Ein Schritt, dessen Artefakt bereits vorhanden
  und nicht stale ist, wird übersprungen (`board.load(...)` + bestehende
  Staleness-Logik). Ein zweiter Anlauf nach einem Fehler setzt exakt am
  fehlgeschlagenen Schritt fort.
- **Retry:** Genau ein automatischer Wiederholungsversuch pro Schritt. Schlägt auch der
  fehl, endet die Kette mit `TailOutcome(status="hard_fail", failed_step=<name>,
  reason=<Tool-Reason>)`.
- **Events:** Jeder Schritt emittiert über `event_sink` dieselben Event-Formen wie der
  Team-Pfad (`tool_call`/`tool_result`-Äquivalente plus eine Abschlusszeile), sodass die
  bestehende ProductionActionCard den Lauf unverändert live erzählt. Keine neuen
  Event-Typen.
- Das Modul fasst `script`/`storyline` **nie** an; es besitzt keinerlei Schreibpfad auf
  Kreativ-Artefakte.

### QA-Stufe (begrenzter Agent)

Nach erfolgreichem Render ruft die Kette die bestehende Execute-Maschinerie
(`_safe_execute`, Stage A, magentic) mit einem **eingeschränkten Roster** auf: nur der
QA-Agent, dessen `tool_names` ausschließlich Lese- + QA-Tools enthalten (get_*,
script_budget, qa-Report-Save; exakte Liste im Plan als Exakt-Tupel-Test gepinnt).
Ergebnis ist das gewohnte `qa_report`-Artefakt mit Ship/Weak-Urteil. Scheitert die
QA-Stufe, gilt Entscheidung 3 (1 Retry, dann ehrliches Scheitern) — ein gerenderter Film
ohne QA-Urteil bleibt als Artefakt erhalten und der nächste Anlauf beginnt bei
`qa_report`.

### Einstieg: ein Zweig in `run_production`

Vor dem Team-Aufbau (nach `restore_coherent_suffix` und dem bestehenden
„board already coherent"-Short-Circuit) prüft `run_production` ein reines Prädikat:

```python
def deterministic_eligible(board: Board, message: str | None,
                           expected_scenes: list[int]) -> bool
```

Wahr genau dann, wenn **alle** gelten:

1. `meta.script_gate` ist aktiv,
2. die Freigabe ist content-aktuell (`script_approved_utc` gesetzt UND
   `script_approved_script_hash == content_hash(aktuelles Script)` — dieselben
   Vergleichspartner wie im Voice-Gate),
3. `board.resume_point(expected_scenes)` ∈ {`voice`, `cutlist`, `contact_sheet`,
   `render_report`, `qa_report`},
4. `message is None` (Text-Follow-ups sind Änderungswünsche → Team wie heute).

Trifft das Prädikat, läuft `run_deterministic_tail` statt des Teams; das Ergebnis wird
über die bestehende `_completed_result`-Form gemeldet (Job-Row, Session-Endpoint,
Karten unverändert). Andernfalls: Team-Pfad exakt wie heute.

### `approve_script` wird ein purer Resume

`_handle_approve_script` ruft nach dem Stempeln **keinen Follow-up-Text** mehr auf,
sondern den Resume-Pfad ohne `message` (die Konstante
`_SCRIPT_APPROVED_FOLLOW_UP_TEXT` entfällt ersatzlos). Dadurch greift das Prädikat und
die Kette läuft deterministisch. Die kompensierende Rollback-Logik
(`clear_script_approval()` wenn der Folgelauf nicht startet) bleibt unverändert.

## Fehlerbehandlung

- Schritt schlägt zweimal fehl → `board.set_status("failed")`, Ergebnis nennt
  `failed_step` + Tool-Reason; Karte zeigt ✗ mit dieser Zeile; Chat-Antwort nennt den
  Schritt. Bereits erzeugte Artefakte bleiben auf dem Board.
- Erneutes „Script freigeben" ist dank content-aware Idempotenz ein No-Op-Stempel
  (Hash unverändert → „Script war schon freigegeben." **plus** Resume, wenn das Board
  nicht komplett ist — die bestehende Doppel-Freigabe-Antwort wird dafür um den
  Resume-Fall erweitert: schon freigegeben + Board unfertig → Lauf wird erneut
  angestoßen statt nur zu antworten).
- Das Voice-Gate in `synthesize_script_voice` bleibt als Defense-in-Depth unverändert
  bestehen — die Kette läuft ohnehin nur bei aktueller Freigabe.

## Bewusst NICHT in v1

- Deterministische Kette für ungated Sessions / HTTP-Auto-Short / plain Resumes
  (Scope-Entscheidung 4; das Eligibility-Prädikat ist bewusst so geschnitten, dass eine
  spätere Erweiterung nur das Prädikat lockert).
- Team-Fallback bei Kettenfehlern (Entscheidung 3).
- Änderungen an der Kreativ-Phase (Scout/Storyline/Script bleiben agentisch).
- Aufräumen des deferred Minors „HTTP-Auto-Short-Sessions sind gated ohne
  Freigabemöglichkeit" — separater Punkt, wird durch diesen Arc weder besser noch
  schlechter.

## Tests

1. **Eligibility-Matrix** (reine Unit-Tests): gated/ungated × Freigabe aktuell/stale/fehlend
   × resume_point vor/hinter script × message gesetzt/None — nur die eine Kombination ist wahr.
2. **Tail mit Fake-Deps:** Happy Path (alle vier Schritte + QA, Artefakte entstehen in
   Kettenreihenfolge), Skip-Semantik (vorhandene voice wird nicht neu synthetisiert),
   Retry-Pfad (erster Aufruf wirft, zweiter gelingt), Doppel-Fehler → `hard_fail` mit
   `failed_step`, Events in erwarteter Reihenfolge am Sink.
3. **Ledger-/Verhaltens-Assertion:** approve→done erzeugt KEINEN
   `save_script_chapter`-Aufruf (der Tail besitzt den Closure gar nicht — Test pinnt die
   Tool-Menge des Tails als Exakt-Tupel).
4. **QA-Roster-Pin:** Exakt-Tupel-Test der QA-Stufe-Tools (keine Schreib-Tools).
5. **Integration `test_script_gate`:** bestehende approve-Flows umgestellt (approve →
   deterministischer Resume statt Follow-up-Text); Doppel-Freigabe auf unfertigem Board
   stößt den Resume an; post-approval Script-Änderung (durch Text-Follow-up) bewaffnet
   das Gate weiter neu und der nächste approve läuft wieder deterministisch.
