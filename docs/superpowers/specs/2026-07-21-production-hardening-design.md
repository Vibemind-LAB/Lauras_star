# Produktions-Härtung: drei Wunden vom 20.07. schließen

**Datum:** 2026-07-21 · **Status:** vom User freigegeben · **Scope:** A+B+C aus der
Restpunkte-Liste (D „Revert-API/UI" und E „Auto-Short" folgen als eigene Zyklen).

## Befunde (alle live-evidenziert am 2026-07-20)

- **A:** Drei tote Produktionsläufe, weil die Text-Agenten still auf `qwen2.5:7b` liefen —
  `resolve_from_env` defaultet den Provider auf `ollama`, und `config_problems` prüft nur
  fehlende Keys, nicht die Tauglichkeit. Das 7B-Modell emittierte Tool-Calls als JSON-Prosa,
  erfand Schemata (`save_storyline` mit `chapters`/`scenes`) und der Orchestrator
  halluzinierte „saved".
- **B:** `result.summary` echot in JEDEM Lauf den Task-Text: `_parse_outcome`
  (production_orchestrator.py:230) konkateniert **alle** Messages und kappt auf 2000
  Zeichen — `messages[0]` ist der Task, also gewinnt immer dessen Anfang.
- **C:** Ein 31,6s-Film gegen 174s Ziel ging mit QA `ship` durch. `voice_fits` prüft
  A/V-Deckung, `export_ready`/`has_voice_timings` die Mechanik — **niemand berichtet die
  Zielerreichung.** (Der `checks_ok: None`-Verdacht war ein Mess-Artefakt: das Feld lebt in
  `status()`, nicht im Report.)

## Design

### A. `config_warnings` — warnen, nie blockieren

Neue Funktion in `short_creator/providers.py`:

```python
def config_warnings(config: AgentConfig) -> list[str]:
    """Advisory (non-fatal) config findings, parallel zu config_problems."""
```

- Eine Warnung, wenn der Stage-A-Text-Provider `ollama` ist: lokale 7B-Modelle scheitern
  nachweislich am Magentic-Tool-Calling; Empfehlung `LAURA_AGENT_PROVIDER=openai-compat` +
  gehostetes Modell. Wortlaut nennt das aufgelöste Modell.
- **Kein Hard-Fail** (local-first: das Backend muss ohne gehostete Modelle laufen; wer lokal
  experimentieren will, darf).
- Sichtbarkeit an zwei Stellen:
  1. `POST /assets/{id}/production` → 202-Response erhält `warnings: list[str]` (leer, wenn
     keine). `POST /production/{sid}/message` analog.
  2. `run_production` schreibt bei nicht-leeren Warnungen eine Zeile
     `{"type": "config_warning", "warnings": [...]}` in den Event-Sink (Run-Log).

### B. Summary = letzte Team-Antwort

`_parse_outcome` nimmt statt der Konkatenation die **letzte nicht-leere** Message des
Results (weiterhin auf 2000 Zeichen gekappt; leeres Result → leere Summary). Der Task-Text
steht in `messages[0]` und erscheint damit nie wieder als „Zusammenfassung".

### C. `target_ratio` im RenderReport — Reporting, kein Gate

- `RenderReport.target_ratio: float | None = None` (board_models): `video_s /
  meta.target_seconds`, gerundet auf 3 Stellen; `None` wenn `target_seconds <= 0`.
- `render_production` befüllt es und ergänzt seine Reply um eine Note
  (`"video 31.6s vs target 174.0s (18%)"`).
- **Bewusst KEIN vierter Check:** `ok=all(checks)` steuert den Coding-Agenten — ein
  failender Längen-Check würde Render-Thrashing provozieren (dieselbe Falle, vor der der
  voice_fits-Kommentar warnt). Die QA liest den Report und würdigt die Abweichung im
  Verdict; die Charter erlaubt kürzere Filme weiterhin ausdrücklich.
- Alt-Reports laden unverändert (Default `None`; `parents`/`content_hash`-Semantik: das neue
  Feld zählt zum Inhalt — ein Alt-Archiv ohne Feld hasht anders als ein Neu-Render, was
  korrekt ist, denn es IST ein anderer Buildstand).
- **Bewusste Konsequenz:** Ein VOR dem Deploy archivierter `qa_report` stempelte
  `parents[render_report]` gegen den Hash ohne das neue Feld; derselbe Render heute geladen
  hasht mit `target_ratio: None` anders → solche QA-Archive restaurieren nicht mehr
  automatisch (Walk endet vor QA). Über-Vorsicht per Provenienz-Spec §6 akzeptiert — Kosten:
  ein QA-Rebuild auf Alt-Boards, nie eine falsche Wiederherstellung.

## Tests

- A: `config_warnings` leer bei openai-compat/9router; Warnung bei ollama (Wortlaut enthält
  Modellname + Empfehlung); 202-Response trägt `warnings` (beide Endpoints); Event-Zeile im
  Sink bei Warnung, keine Zeile ohne.
- B: Fake-Result mit [Task-Echo, Zwischenschritt, finale Antwort] → Summary == finale
  Antwort (gekappt); leeres Result → leere Summary; das bestehende Verhalten von
  `weak`/`status` unverändert.
- C: Report trägt `target_ratio` (Wert nachgerechnet); `target_seconds=0` → None;
  Tool-Reply-Note enthält Ziel und Prozent; Alt-Report-JSON ohne Feld lädt.

## Nicht in diesem Scope

- Kein Blockieren lokaler Modelle; keine Modell-Fähigkeits-Datenbank.
- Kein QA-Gate auf Ziel-Länge; keine Charter-Änderung.
- Frontend-Anzeige der Warnungen/Ratio (Chips) — späterer Zyklus, D deckt die UI-Seite.
