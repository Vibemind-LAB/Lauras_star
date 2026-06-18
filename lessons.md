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

## 2026-06-14 — Szenen folgen dem aktuellen Rough-Cut
- **Kontext:** Beim erneuten „Szenen erzeugen" und beim Wechsel in Feinschnitt wirkte die UI stale: Szenen wurden nicht aus dem aktuell angepassten Rough-Cut abgeleitet bzw. Szenenklicks sprangen nicht sichtbar zur passenden Stelle.
- **Korrektur:** Die API/DB-Timeline ist die Wahrheit, nicht ein möglicherweise alter `roughCut.clips`-Prop im Renderer. Feinschnitt-Szenenwechsel muss die materialisierte Szene öffnen und zum ersten Source-Frame springen.
- **Konsequenz:** Rough-Cut-Szenengenerierung versucht zuerst den aktuellen Backend-Timeline-State; nur echte „rough cut empty"-Antworten lösen einen Build aus. Feinschnitt setzt ungültige Scene-Selektion zurück und seeked nach Scene-Timeline-Load.

## 2026-06-08 — Subagent-Commits müssen per Pathspec committen
- **Kontext:** Während paralleler User-git-Arbeit committete ein Implementer-Subagent mit `git add <dateien> && git commit` (= committet den **ganzen Index**) und zog dadurch die parallel vom User gestageten Dateien (`analysis/refine.py`, `analysis/shots.py`, `test_refine.py`, `test_hybrid.py`) in seinen Commit. Kurzer History-Tangle; `be02777` (SA5) fiel beim parallelen Rebase raus.
- **Korrektur:** Commits, die nur bestimmte Dateien umfassen sollen, **immer per Pathspec**: `git commit -m "msg" -- <pfade>` (nie `git commit` ohne Pfade, wenn der Index fremde gestagete Dateien enthalten könnte). `-m` **vor** `--` (alles nach `--` ist Pathspec). Bei aktiver paralleler User-git-Arbeit: Subagenten committen **gar nicht** — der Orchestrator committet zentral per Pathspec, vorher `.git/rebase-merge`/`rebase-apply` prüfen (Rebase-Guard).
- **Konsequenz:** Implementer-Subagent-Prompts schreiben „keine git-Befehle" vor; der Orchestrator staged/committed pro Task nur die eigenen Dateien per `git commit -- <paths>` mit Rebase-Guard. Verifikationsketten (`typecheck && commit`) so bauen, dass ein **fehlgeschlagener Typecheck den Commit verhindert** (sonst wird kaputter Code committet — passierte bei `658ec43`).
