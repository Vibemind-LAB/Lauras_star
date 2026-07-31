# Gestrandete Transkripte: der Lauf wird nach Artefakt aufgelöst, nicht nach Aktualität

**Datum:** 2026-07-31 · **Status:** vom User freigegeben · **Scope:** Lese-Seite (Auflösungs-
regel) + Schreib-Seite (Prävention). Ein Semantik-Backfill ist **nicht** Teil dieser Spec
(siehe § 7).

Instanziiert die Beobachtung „Run-Status-Asymmetrie" aus dem Auto-Short-Schlussreview.

## Befund

In `workspace-livetest/laura.db` stehen **drei** Läufe dauerhaft in `status='running'`; an
zweien davon hängen die **einzigen** Transkript-Segmente ihres Assets. Betroffen sind damit
2 von 5 Assets (der Schlussreview sprach von 3 — der dritte tote Lauf gehört zu AgentFarm
und trägt selbst keine Segmente):

| Asset | Lauf | Status | Segmente | Shots |
|---|---|---|---|---|
| AgentFarm Autogen | `f6b7a789` | running | **165** | 9 |
| AgentFarm Autogen | `3de1f311` | running | 0 | 0 |
| AgentFarm Autogen | `aec8da55` | succeeded | 0 | 9 |
| n8n Farm | `29db1dc2` | running | **8** | 5 |
| n8n Farm | `3024aaf3` | succeeded | 0 | 5 |

Die übrigen drei Assets sind unauffällig: zwei haben ihr Transkript an einem `succeeded`-Lauf
(192 bzw. 413 Segmente), eines hat gar keines. Für sie ändert die neue Regel nichts.

Die Läufe wurden **nicht** am Runtime-Cap abgeschnitten — sie sind abgestürzt, und niemand
hat es aufgeschrieben. Die zugehörigen `jobs`-Zeilen stehen auf `failed` mit einem echten
Fehler: bei den beiden transkripttragenden Läufen
`ResponseHandlingException: [WinError 10061] Verbindung verweigert` — Qdrant; beim dritten
ein `PermissionError` auf eine gesperrte Datei. Zwei Ursachen, dieselbe Lücke dahinter.

Der Pfad dorthin, in drei Schichten:

1. **Auslöser:** In `analysis/handlers.py::_run_transcript` steht `index = get_index()`
   *außerhalb* des `try`, das direkt darunter folgt. Ein nicht erreichbarer `laura-qdrant`
   lässt den Konstruktor werfen — best-effort-Indizierung reißt den ganzen Analyselauf um.
   Dieselbe Fehlerklasse, die `fd0914b` auf der Leseseite geschlossen hat, lebt auf der
   Schreibseite weiter.
2. **Schreib-Seite:** `handle_analysis_run` hat kein `try/finally`;
   `finish_analysis_run` (Zeile 404) ist der einzige Aufruf und wird bei einer Exception nie
   erreicht. Die `jobs`-Tabelle hat einen Reaper, `analysis_runs` hat keinen — der Lauf
   bleibt für immer `running`, `diagnostics_json` bleibt `{}`. Die Segmente sind zu dem
   Zeitpunkt längst committed.
3. **Lese-Seite:** Jeder Leser löst den Lauf über **Aktualität/Status** auf, nie über
   „hat das Artefakt, nach dem ich frage". `search_transcript` filtert seit `5845bf9` hart
   auf `status='succeeded'`; `get_latest_analysis_run` (ohne Status-Filter, ~17 Aufrufer)
   nimmt schlicht den neuesten Lauf. Für AgentFarm ist das `aec8da55` — eine Szenen-only-
   Reanalyse mit **0 Segmenten**. Die 165 Segmente sind damit auch für den Transkript-Leser
   unsichtbar, nicht nur für die Suche.

Punkt 3 ist der strukturelle Kern: Lauras Reanalyse ist stufenkonfigurierbar
(`stages.asr: false` hat genau diese 0-Segment-Läufe erzeugt). **„Neuester Lauf" und
„neuestes Transkript" sind legitim verschiedene Läufe.**

## Verworfene Ansätze

- **Nur ein Dead-Run-Finalizer** (tote `running`-Läufe auf `failed` setzen): macht die DB
  ehrlich, aber die 165 Segmente bleiben für immer unsichtbar — jetzt an einem `failed`-Lauf.
  Die Alternative, sie stattdessen auf `succeeded` zu heben, ist nicht belegbar:
  `diagnostics_json` ist `{}`, es gibt keinen Hinweis, ob ASR die Datei zu Ende gelesen hat
  oder mittendrin gestorben ist.
- **Status-Filter aus `search_transcript` entfernen:** bricht die Ausschluss-Eigenschaft, die
  `5845bf9` gerade erst hergestellt hat — eine laufende Reanalyse würde ihre halbfertigen
  Segmente gegen den Vorgängerlauf doppeln.

## Design

### 1. Die Auflösungsregel

Neu in `db/repos.py`:

```python
def get_latest_transcript_run(db: Database, asset_id: str) -> dict[str, Any] | None:
```

```sql
SELECT ar.* FROM analysis_runs ar
WHERE ar.asset_id = ?
  AND EXISTS (SELECT 1 FROM transcript_segments ts
              WHERE ts.asset_id = ar.asset_id AND ts.analysis_run_id = ar.id)
ORDER BY CASE WHEN ar.status = 'succeeded' THEN 0 ELSE 1 END,
         COALESCE(ar.started_at, '') DESC, ar.id DESC
LIMIT 1
```

Drei Eigenschaften, in dieser Reihenfolge:

1. **hat ein Transkript** (`EXISTS`) — Läufe ohne Segmente kommen nicht in Frage;
2. **`succeeded` gewinnt** (`CASE`) — ein abgeschlossener Lauf schlägt jeden unfertigen,
   unabhängig vom Alter;
3. **neuester gewinnt** — innerhalb einer Statusgruppe die bestehende Ordnung aus
   `get_latest_analysis_run` (`COALESCE(started_at,'') DESC, id DESC`).

`LIMIT 1` **ist** die Ausschluss-Eigenschaft: genau ein Lauf pro Asset, alle Leser filtern
anschließend auf `analysis_run_id = <dieser Lauf>`. Segmente zweier Läufe können sich per
Konstruktion nicht mischen.

Solange irgendein `succeeded`-Lauf ein Transkript hat, ist das Verhalten **byte-identisch**
zu heute. Die Fallback-Stufe greift ausschließlich für Assets, die gar keinen erfolgreichen
Transkriptlauf haben — den gestrandeten Fall. Eine laufende Reanalyse überschattet damit nie
ein vollständiges Transkript.

`EXISTS` führt `asset_id` mit, damit die Unterabfrage auf
`idx_segments_asset_run(asset_id, analysis_run_id, start_sample)` läuft statt zu scannen.

### 2. `search_transcript` bekommt denselben Rumpf

Die korrelierte Unterabfrage in `search_transcript` (heute `status='succeeded'` +
Aktualität) wird auf exakt die Ordnung aus § 1 umgestellt — `s.asset_id` der äußeren Zeile
als Korrelation. Damit können lexikalische Suche (`/search` im Lexical-Modus, der
Lexical-Fallback in `short_creator/discovery.py`) und die Transkript-Leser sich nicht mehr
widersprechen: beide Seiten benennen dieselbe Regel.

### 3. Übernahme bei den Lesern

`get_latest_analysis_run` → `get_latest_transcript_run` überall dort, wo der aufgelöste Lauf
in einen **Transkript-Leser** fließt (`get_transcript`, `list_words_for_run`,
`candidate_caption_words`). Je eine Zeile; die `run is None`-Wächter bleiben unverändert
gültig. Die vollständige Liste — 17 Stellen:

| Datei | Stelle(n) | liest |
|---|---|---|
| `analysis/handlers.py` | 465 | `get_transcript` (Realign) |
| `api/analysis.py` | 116, 185 | `get_transcript` |
| `api/analysis.py` | 159 | `config_json.language` des Laufs |
| `api/assets.py` | 346 | `get_transcript` (SRT/VTT) |
| `demo/drafts.py` | 75 | `get_transcript` |
| `render/captions_source.py` | 77 | `list_words_for_run` |
| `render/handlers.py` | 269 | `get_transcript` |
| `render/shorts_render.py` | 248 | `candidate_caption_words` |
| `sequences/transcript.py` | 31 | `get_transcript` |
| `short_creator/context.py` | 50, 101, 187 | `get_transcript` |
| `short_creator/context.py` | 291, 322 | `list_words_for_run` |
| `short_creator/production_tools.py` | 600 | `get_transcript` |
| `short_creator/scout.py` | 211 | `get_transcript` |

Zwei Stellen brauchen mehr als den Namenstausch:

- `api/analysis.py:159` (`_language_for_asset`) liest die ASR-Sprache aus `config_json`. Sie
  muss vom Lauf kommen, der das Transkript erzeugt hat — sonst realignt Laura Segmente aus
  Lauf A mit der Sprache aus Lauf B.
- `render/shorts_render.py:248` gattert zusätzlich auf `run["status"] == "succeeded"`. Der
  Resolver kodiert diese Präferenz bereits; `run is not None` heißt dort ab jetzt „hat ein
  Transkript". Das Gate wird entsprechend auf `run is not None` reduziert, sonst blieben
  gestrandete Assets ohne Captions.

**Nicht** angefasst:

- `scenes/build.py` — bekommt die `run_id` des gerade laufenden Analyselaufs übergeben; das
  ist per Design innerhalb-des-Laufs und korrekt.
- `analysis/visual_embed.py:353`, `analysis/visual_query.py:75` — Frame-Artefakte, eigener
  Resolver. Dieselbe Asymmetrie existiert dort; sie wird hier benannt, nicht behoben.
- Shot-Leser und reine Zustands-Gates bleiben auf `get_latest_analysis_run` — dort ist
  „neuester Lauf" die richtige Frage: `api/analysis.py:87` (Lauf-Metadaten), `:96` (Shots),
  `api/scenes.py:56`, `api/shorts.py:66` (Status-Anzeige), `api/shorts_candidates.py:57`,
  `api/timelines.py:465`, `analysis/shorts_handlers.py:114`, `demo/drafts.py:41`,
  `ingest/handlers.py:190` (Existenzprüfung), `mcp/tools.py:195`, `:283`.

Warum alle siebzehn und nicht nur die Suche: sonst rankt Discovery AgentFarm als Material,
während `scout.get_scene_context` / `context.transcript_window` den *neuesten* Lauf lesen,
0 Segmente finden und „kein Transkript" melden — die Auto-Short-Kette würde sich selbst
widersprechen.

### 4. Schreib-Seite: keine neuen Leichen

Zwei kleine Eingriffe am Auslöser:

- **`_run_transcript`:** `get_index()` wandert *in* den best-effort-`try`. Ein nicht
  erreichbares Qdrant wird zu `"embed failed: <Typ>"` in den Diagnostics — nie zum
  Lauf-Killer. (Spiegelbild von `fd0914b`.)
- **`handle_analysis_run`:** Der Rumpf wird so gekapselt, dass eine Exception den Lauf über
  `finish_analysis_run(..., status="failed", ...)` abschließt — mit dem Fehler *und* den
  Stufen, die es noch geschafft haben, in den Diagnostics — und danach **weiterwirft**. Die
  `jobs`-Tabelle behält ihren Trace und ihre Retry-Semantik unverändert; `analysis_runs`
  bekommt zum ersten Mal einen ehrlichen Endzustand.

Nach diesem Fix gilt: ein `running`-Lauf, dessen Prozess weg ist, kann nicht mehr entstehen.
Ein Finalizer/Reaper für `analysis_runs` wird dadurch überflüssig — deshalb ist keiner Teil
dieser Spec.

### 5. Tests

Durchgehend im Stil von `tests/test_discovery.py`: echte `SqliteDatabase` unter `tmp_path`,
Seeds über `repos`, keine Mocks außer `monkeypatch` auf `get_index`.

In `tests/test_discovery.py`:

- **`test_transcript_stranded_on_unfinished_run_is_still_found`** — die Live-Form: Lauf A
  (`running`, mit Segmenten) plus neuerer Lauf B (`succeeded`, Szenen-only, 0 Segmente).
  `search_transcript` findet A's Text; `discovery.search_material` rankt das Asset.
- **`test_succeeded_run_wins_over_newer_unfinished_run`** — `succeeded` mit Segmenten plus
  neuerer `running` mit Teil-Segmenten: nur der `succeeded`-Text kommt zurück.
- **`test_search_rows_never_mix_two_runs`** — drei Läufe mit Segmenten; jede zurückgegebene
  Zeile trägt dieselbe `analysis_run_id`.
- **`test_asset_without_any_segments_resolves_to_none`** — ein Asset, dessen Läufe alle
  segmentlos sind, löst auf `None` auf statt auf einen leeren Lauf.
- **`test_reader_and_search_resolve_the_same_run`** — `context.transcript_window` und
  `search_transcript` landen auf demselben Lauf.
- **`test_stale_run_segments_are_excluded_and_ranked_once`** (bestehend) muss
  **unverändert** grün bleiben — die Regressionswache auf der Ausschluss-Eigenschaft.

Neu, `tests/test_analysis_run_finalization.py`:

- **`test_crashing_stage_finalizes_the_run_as_failed`** — eine werfende Stufe hinterlässt
  `status='failed'` mit dem Fehler in den Diagnostics, und die Exception propagiert weiter
  (der Job scheitert wie bisher).
- **`test_failed_run_keeps_the_stages_that_did_complete`** — was vor dem Absturz fertig
  wurde, überlebt in den Diagnostics; genau das macht den Fehler diagnostizierbar statt `{}`.
- **`test_clean_run_still_succeeds`** — Regressionswache: die Kapselung darf einen gesunden
  Lauf nicht verändern.
- **`test_unreachable_index_does_not_fail_the_transcript_stage`** — ein werfendes
  `get_index()` lässt `_run_transcript` normal zurückkehren, die Segmente sind persistiert,
  der Embed-Fehler steht in den Diagnostics.

### 6. Live-Verifikation

Gegen eine **Kopie** von `workspace-livetest/laura.db` (nie das Original): nach dem Fix
liefert `search_transcript` AgentFarms 165-Segment-Lauf und n8ns 8 Segmente, und
`discovery.search_material` rankt beide Assets. Damit ist der Befund ohne einen einzigen
Whisper-Neulauf belegt.

## 7. Was diese Spec ausdrücklich NICHT behebt

Die 165 Segmente wurden **nie eingebettet** — `get_index()` warf, bevor `index()` lief.
Semantische Suche findet sie also weiterhin nicht. Verschärfend: `discovery._segment_hits`
nimmt das semantische Ergebnis, sobald der Index *irgendeinen* Treffer liefert, und fällt
nur bei null Treffern auf lexikalisch zurück. Mit laufendem `laura-qdrant` bringt die
Auto-Short-Discovery diese drei Assets deshalb weiterhin nicht nach oben.

Die Reparatur braucht keinen Code: **Analyse für diese Assets einmal neu laufen lassen,
solange Qdrant erreichbar ist** — was der Schreib-Seiten-Fix (§ 4) erstmals verlässlich
macht. Ein Backfill-Endpoint (SQLite-Segmente ohne ASR-Neulauf nach Qdrant spiegeln) wäre
ein eigener Zyklus und wird hier bewusst nicht mitgeplant.
