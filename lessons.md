# lessons.md

Reale Korrekturen & Lehren aus der Arbeit an Laura. Wird bei **echten** Korrekturen des Users
ergänzt (nicht nur Acknowledgements). Neueste oben.

Format pro Eintrag:

```
## YYYY-MM-DD — Kurztitel
- **Kontext:** was passierte
- **Korrektur:** was richtig ist
- **Konsequenz:** was sich dadurch im Code/Prozess ändert
```

---

## 2026-06-24 — Migration, die schema_version erhöht, muss die Frontend-Konstante mitziehen
- **Kontext:** Das Undo/Redo-Feature fügte Migration `0029_timeline_history` hinzu (Backend-Schema → 29), ließ aber `EXPECTED_SCHEMA_VERSION` in `apps/desktop/src/App.tsx:61` auf 28. Beim Start gegen das neue Backend zeigte die App **„Frontend veraltet · schema 29/28"**. Die Frontend-Tests (`App.test.tsx`) fingen es nicht, weil sie die Konstante interpolieren statt einen festen Wert zu prüfen.
- **Korrektur:** Jede Migration, die `schema_version` erhöht, muss im selben Zug `EXPECTED_SCHEMA_VERSION` (App.tsx) auf denselben Wert ziehen. Der Versions-Guard vergleicht `health.schema_version` mit der Konstante: `<` → „Backend veraltet", `>` → „Frontend veraltet".
- **Konsequenz:** In Backend-Migrations-Tasks gehört das Bumpen der Frontend-Konstante in die Definition-of-Done. Nur ein Live-Backend mit der neuen Schema-Version deckt den Mismatch auf → bei schema-erhöhenden Features einen Boot-Smoke-Test gegen das echte Backend einplanen.

## 2026-06-14 — Szenen folgen dem aktuellen Rough-Cut
- **Kontext:** Beim erneuten „Szenen erzeugen" und beim Wechsel in Feinschnitt wirkte die UI stale: Szenen wurden nicht aus dem aktuell angepassten Rough-Cut abgeleitet bzw. Szenenklicks sprangen nicht sichtbar zur passenden Stelle.
- **Korrektur:** Die API/DB-Timeline ist die Wahrheit, nicht ein möglicherweise alter `roughCut.clips`-Prop im Renderer. Feinschnitt-Szenenwechsel muss die materialisierte Szene öffnen und zum ersten Source-Frame springen.
- **Konsequenz:** Rough-Cut-Szenengenerierung versucht zuerst den aktuellen Backend-Timeline-State; nur echte „rough cut empty"-Antworten lösen einen Build aus. Feinschnitt setzt ungültige Scene-Selektion zurück und seeked nach Scene-Timeline-Load.

## 2026-06-08 — Subagent-Commits müssen per Pathspec committen
- **Kontext:** Während paralleler User-git-Arbeit committete ein Implementer-Subagent mit `git add <dateien> && git commit` (= committet den **ganzen Index**) und zog dadurch die parallel vom User gestageten Dateien (`analysis/refine.py`, `analysis/shots.py`, `test_refine.py`, `test_hybrid.py`) in seinen Commit. Kurzer History-Tangle; `be02777` (SA5) fiel beim parallelen Rebase raus.
- **Korrektur:** Commits, die nur bestimmte Dateien umfassen sollen, **immer per Pathspec**: `git commit -m "msg" -- <pfade>` (nie `git commit` ohne Pfade, wenn der Index fremde gestagete Dateien enthalten könnte). `-m` **vor** `--` (alles nach `--` ist Pathspec). Bei aktiver paralleler User-git-Arbeit: Subagenten committen **gar nicht** — der Orchestrator committet zentral per Pathspec, vorher `.git/rebase-merge`/`rebase-apply` prüfen (Rebase-Guard).
- **Konsequenz:** Implementer-Subagent-Prompts schreiben „keine git-Befehle" vor; der Orchestrator staged/committed pro Task nur die eigenen Dateien per `git commit -- <paths>` mit Rebase-Guard. Verifikationsketten (`typecheck && commit`) so bauen, dass ein **fehlgeschlagener Typecheck den Commit verhindert** (sonst wird kaputter Code committet — passierte bei `658ec43`).

## 2026-07-20 — Lokale Gates müssen den CI-Befehl fahren, nicht eine Teilmenge
- **Kontext:** Alle SDD-Pläne/Gates liefen `uv run mypy src` (197 Dateien, grün); die CI fährt bares `uv run mypy` (config-gesteuert, 481 Dateien inkl. tests/). PR #13 fiel mit 11 Test-Typfehlern, die lokal nie sichtbar waren. 5 der 6 Fix-Dateien lagen sogar schon fertig-gefixt UNCOMMITTET im Working Tree (fruehere Session hatte offenbar bare mypy gefahren, aber explizite Adds liessen sie zurueck).
- **Korrektur:** Gate-Befehl ist exakt der CI-Befehl: `uv run mypy` (ohne Pfadargument) + `uv run pytest` + `uv run ruff check src tests`. Vor jedem Push eines PR-Branches einmal bare mypy laufen lassen.
- **Konsequenz:** Kuenftige Plan-Templates schreiben `uv run mypy` (bare) als Gate; uncommittete Working-Tree-Fixes bei Sessionstart ernst nehmen (git status Snapshot lesen) statt sie monatelang mitzuschleppen.
