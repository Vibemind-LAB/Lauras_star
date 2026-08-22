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

## 2026-08-09 — Visual-Recut-Auswahl muss den Rough-Cut abbilden
- **Kontext:** Der erste Visual-Recut-Vorschlag bot nur zwei Sprecher-Beats mit je einer impliziten festen Länge an. Für die Bildentscheidung fehlten damit ein Großteil der Rough-Cut-Szenen, wählbare Shot-Längen und ausreichende Szenenbeschreibungen.
- **Korrektur:** Jeder im aktuellen Rough-Cut definierte Szenenwechsel muss mindestens einmal als auswählbare Bildoption erscheinen. Die Auswahl muss mehr als zwei Szenen zulassen, die verwendete Länge über Voreinstellungen bis 10 Sekunden sichtbar und wählbar machen und pro Option eine Beschreibung als Entscheidungsgrundlage zeigen.
- **Konsequenz:** Visual-Plan, Kandidatengenerierung und Electron-Karte werden nicht nur beat-, sondern zusätzlich rough-cut-coverage-basiert entworfen. Bestätigung bleibt gesperrt, bis alle Rough-Cut-Szenenwechsel vertreten sind und jede Auswahl eine explizite Dauer bis maximal 10 Sekunden sowie Beschreibung besitzt.

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

## 2026-08-21 — Screen-Recording-Beats per Frame-Index verifizieren, nie per Zeit-Seek
- **Kontext:** Rowboat-Produktvideo v1/v2 — Beat-Frames wurden vorab per `ffmpeg -ss <sekunden>` gesichtet, geschnitten wird in Laura aber per Frame-Index. Bei den VFR-Screen-Recordings (bes. dem 3,4-min-Video) divergieren beide deutlich: der verifizierte Accounts-Popup lag im Render plötzlich im Sync-Beat ("11s zeigt was anderes als gesprochen wird"), der Tools-Beat zeigte stattdessen nackten Graphen.
- **Korrektur (User):** Transkript/Voice passte nicht zu den Szenen; Fix kam erst, als die Kandidaten-Frames mit `select='eq(n\,IDX)'` (frame-index-genau, wie der Schnitt) extrahiert und die Spans danach gewählt wurden — und der fertige Render selbst nochmal per Beat-Mitten-Frames gegengeprüft wurde.
- **Konsequenz:** Für O-Ton-lose Collagen aus Screen-Recordings gilt: Kandidaten-Sichtung UND End-Abnahme immer index-basiert (`select=eq(n,..)` bzw. Frames aus dem Render), Zeit-Seek nur für grobe Orientierung. Invariante 5 (VFR→CFR) gilt auch für meine eigenen Verifikations-Werkzeuge.

## 2026-08-21 — Renderer-Verifikation braucht Pixel, nicht Filter-Strings
- **Kontext:** Final-Review des Narrated-Reel-Arcs: der Trailing-Fade-Fix (1a31a32) wurde per Filtergraph-String-Assertions verifiziert ("`fade=t=in:st=<Stream-Ende>` ist inert") — der erste echte Render kam KOMPLETT schwarz zurück (YAVG 16 ab t=1s). ffmpegs `fade=t=in:st=X` schwärzt ALLE Frames vor `st`, nicht nur das eigene Fenster; per Weiß-Clip-Probe bestätigt. Der String-Pinning-Test hatte den Bug sogar festgeschrieben (assert auf ANWESENHEIT der t=in-Hälfte).
- **Korrektur:** Fix 745b9d1 — `_video_transition_chain` bekommt `total_frames` und lässt die t=in-Hälfte bei `boundary_frame >= total_frames` weg; dazu zwei echte-ffmpeg-Pixel-Tests (Weiß-Clips durch `render_clips_mp4`, concat- UND xfade-Pfad, signalstats-YAVG früh-hell/vor-Fade-hell/Ende-schwarz), die gegen 1a31a32 RED waren.
- **Konsequenz:** Filtergraph-String-Assertions prüfen nur die Verdrahtung, nie die Semantik. Jede neue Filter-Kombination im Renderer braucht mindestens einen Output-Level-Test (synthetische Quelle + Luma/Pixel-Messung, RED/GREEN hier: 16.0 → 235/235/23) — und angenommene ffmpeg-Filter-Semantik ("inert", "no-op") wird vor dem Festschreiben mit einer 5-Sekunden-lavfi-Probe belegt, nicht aus dem Kopf behauptet. Live-Verifikation am Arc-Ende ist nicht optional: Sie hat hier den einzigen Escape des Review-Netzes gefangen. Merker: mid-timeline Dip-to-Black hat denselben t=in-Giftpfad (alles vor der Boundary schwarz) — als separater Task geflaggt, nicht in 745b9d1 gefixt.

## 2026-08-22 — ffmpeg-`fade` braucht `enable=`, sonst faerbt es den ganzen Stream
- **Kontext:** Beim Abgabe-Fertigmachen lief das neue Demo-Skript in ein komplett schwarzes Video. Zwei getrennte Ursachen, beide derselben Familie wie die Schwarzbild-Lektion vom 2026-08-21: (a) Ein Crossfade ohne Reserve wird korrekt zum harten Schnitt degradiert — die entwertete Transition landete aber trotzdem in der Dip-to-Black-Kette des Concat-Pfads (der xfade-Pfad filterte sie, der Concat-Pfad nicht). (b) Ein regulaerer mid-timeline `kind="fade"` (benutzerseitig waehlbar!) schwaerzte ebenfalls den ganzen Stream: `fade=t=out` bleibt nach seinem Fenster dauerhaft schwarz, `fade=t=in:st=X` schwaerzt alles davor.
- **Korrektur:** (a) Die Liste der Dip-Transitions wird EINMAL vor der Pfad-Verzweigung gefiltert (`kind not in ("hard","crossfade")`) und von beiden Pfaden benutzt — zwei Pfade mit derselben Absicht duerfen die Filterregel nicht doppelt und getrennt tragen. (b) Beide Fade-Haelften bekommen `enable='between(t,A,B)'`, damit sie nur ihr eigenes Fenster beeinflussen. Pixel-Beweis: 16/16/16 -> 235/16/235 (hell, schwarz genau am Uebergang, wieder hell).
- **Konsequenz:** ffmpeg-Filter, die eine Zeitspanne meinen, brauchen `enable=` — `st`/`d` allein begrenzen die Wirkung NICHT. Und: ein Demo-/Abnahmepfad, den ein Fremder laeuft, findet Bugs, die kein Test im Repo je angefasst hat (hier: Clips, die exakt am Quellenende enden).
