# Der Analyselauf endet auch dann, wenn kein Python mehr läuft

**Datum:** 2026-07-31 · **Status:** vom User freigegeben · **Scope:** Reaper-Erweiterung,
einmaliger Repair-Sweep, `latest succeeded`-Resolver, sauberer Neustart eines Laufs.

Folgeschritt zu [`2026-07-31-stranded-transcript-runs-design.md`](2026-07-31-stranded-transcript-runs-design.md).
Dessen § 4 benennt diese Lücke ausdrücklich als offen: der dort eingebaute
`try/except` in `handle_analysis_run` schließt **nur den Exception-Pfad**.

## Befund

Nach dem Vorgänger-Fix gilt: eine werfende Stufe erreicht garantiert
`finish_analysis_run(..., status="failed", ...)`. Zwei Pfade lassen
`analysis_runs.status='running'` weiterhin dauerhaft stehen:

1. **SIGKILL / Stromausfall / OOM-Kill** des Worker-Prozesses — es läuft kein `except` mehr,
   das den Lauf noch abschließen könnte.
2. **Der Runtime-Cap-Pfad** in `jobs/runner.py:283-290`: der Heartbeat hört nach
   `max_runtime_seconds` (Default 3600) absichtlich auf, damit der Reaper eines *anderen*
   Workers den Job requeuen kann — während der Handler-Thread noch läuft und nichts geworfen
   hat. Die `jobs`-Zeile wird eingesammelt, die `analysis_runs`-Zeile erfährt davon nichts.

Die `jobs`-Tabelle hat für genau diese Fehlerklasse `JobRunner.reap_expired()`;
`analysis_runs` hat kein Gegenstück.

**Warum das mehr ist als Kosmetik.** Fünf Stellen gattern auf „der **neueste** Lauf hat
`status == 'succeeded'`" und profitieren nicht vom `get_latest_transcript_run`-Resolver des
Vorgängers:

| Datei | Zeile | blockiert |
|---|---|---|
| `api/shorts_candidates.py` | 57 | Shorts-Kandidaten (409 „analyze the asset first") |
| `analysis/shorts_handlers.py` | 114 | Shorts-Extraktion (`ValueError`) |
| `mcp/tools.py` | 195 | `build_roughcut` |
| `mcp/tools.py` | 283 | `extract_shorts` |
| `analysis/visual_embed.py` | 353 | Frame-Embeddings |

Ein Asset, dessen **neuester** Lauf eine Leiche ist, ist damit dauerhaft von Shorts-Extraktion
und Visual-Embedding ausgeschlossen — bis jemand von Hand neu analysiert. `workspace-livetest`
entgeht dem nur durch Zufall: die dortigen Leichen sind älter als die erfolgreichen
Szenen-only-Reanalysen.

Dazu kommt eine sichtbare Nebenwirkung: das Desktop bindet seinen Transkript-Spinner an
`analysis.status === "running"` (`App.tsx:598/618`, `InspectorPanel.tsx:93`). Eine Leiche
dreht dort für immer.

## Verworfene Ansätze

- **Spalte `analysis_runs.job_id`** statt Payload-Lesen: bräuchte Migration 0034 *und* einen
  Backfill — und der Backfill ließe sich nur schreiben, indem man `payload_json` parst
  (`json_extract` ist SQLite-only, Migrationen laufen auch gegen Postgres). Die Spalte kauft
  nichts, was die Payload nicht schon hergibt. Der Job trägt `analysis_run_id` seit jeher an
  allen drei Enqueue-Stellen (`api/analysis.py:75`, `ingest/handlers.py:209`,
  `mcp/tools.py:161`).
- **Den Sweep periodisch im Runner mitlaufen lassen** (statt einmal beim Start): `run_once`
  feuert alle 0.5 s pro Worker-Thread, und `analysis_runs` hat nur einen Index auf
  `asset_id` — ein Dauerscan für ein Ereignis, das der Reaper (§ 1) ohnehin schon
  ereignisgesteuert abdeckt.
- **Beim Requeue den Lauf auf `'queued'` spiegeln:** der wiedereingereihte Job stempelt die
  Zeile beim nächsten Versuch ohnehin über `start_analysis_run` neu — und im Runtime-Cap-Fall
  läuft der Zombie-Handler-Thread noch, gegen den man sich damit ins Rennen setzt.
- **Den Status-Filter aus den fünf Gates ersatzlos streichen:** dann liefe Shorts-Extraktion
  gegen die halbfertigen Shots eines gerade laufenden Laufs. Die Frage ist nicht „egal welcher
  Status", sondern „der neueste Lauf, **der** erfolgreich war".

## Design

### 1. `reap_expired` schließt den Lease-Ablauf-Pfad

`reap_expired` feuert heute zwei blinde Bulk-`UPDATE`s und weiß hinterher nicht, *welche*
Jobs es angefasst hat. Es bekommt ein vorgeschaltetes `SELECT` mit demselben Prädikat, in
derselben Transaktion:

```python
with self.db.transaction(immediate=True) as conn:
    expired = conn.execute(
        "SELECT id, kind, payload_json, attempt, max_attempts FROM jobs "
        "WHERE status IN ('leased','running') AND lease_expires_at IS NOT NULL "
        "AND lease_expires_at < ?", (now,)
    ).fetchall()
    failed   = conn.execute(...)   # unverändert
    requeued = conn.execute(...)   # unverändert
    stranded = [...]               # kind == 'analysis.run' AND attempt >= max_attempts
for run_id in stranded:            # NACH der Transaktion
    repos.fail_stranded_analysis_run(self.db, run_id, error=..., recovered_by="job reaper")
```

Drei Entscheidungen darin:

- **Nur der Fail-Zweig finalisiert.** Beim Requeue läuft der Job erneut und
  `start_analysis_run` stempelt die Zeile neu (§ 4). Bei `max_attempts=2` — dem Wert, mit dem
  `analysis.run` eingereiht wird — ist eine Leiche beim zweiten Reap terminal.
- **Finalisieren *nach* der Transaktion.** Eine zweite Schreibverbindung innerhalb einer
  gehaltenen SQLite-Schreibsperre verklemmt. Ein Absturz in der Lücke dazwischen lässt den
  Lauf gestrandet — was der Sweep (§ 2) heilt. Die beiden Mechanismen decken sich gegenseitig.
- **`reap_expired` behält seinen Rückgabewert** („angefasste Jobs"). Finalisierte Läufe
  werden geloggt, nicht mitgezählt.

Neu in `db/repos.py`:

```python
def fail_stranded_analysis_run(
    db: Database, run_id: str, *, error: str, recovered_by: str
) -> bool:
    "UPDATE analysis_runs SET status='failed', finished_at=?, diagnostics_json=? "
    "WHERE id=? AND status IN ('queued','running')"
```

`diagnostics_json` wird zu `{"error": <error>, "recovered_by": <recovered_by>}` — dieselbe
`error`-Konvention, die `handle_analysis_run` schon schreibt, plus die Herkunft. Der
`WHERE`-Wächter auf `('queued','running')` macht den Aufruf zum No-op, wenn der Lauf
inzwischen selbst terminal geworden ist; der Rückgabewert sagt, ob geschrieben wurde.

Damit sind **beide** Pfade aus dem Befund abgedeckt: der Runtime-Cap und SIGKILL/OOM (dort
läuft der Lease des toten Workers ab, der Reaper feuert, derselbe Zweig greift).

`jobs/runner.py` kennt damit den String `"analysis.run"` — dieselbe Kopplung, die
`jobs/queues.py:30` für das Queue-Routing bereits hat. Ein generisches Finalizer-Registry
wäre mehr Architektur, als eine einzige Job-Art rechtfertigt.

### 2. Startup-Sweep für die Leichen, die schon in der DB liegen

Der Reaper heilt nur, was **ab jetzt** abläuft. Die Leichen in
`workspace-livetest/laura.db` hängen an Jobs, die längst `failed` sind — dort kommt der
Reaper nie wieder vorbei.

Neu: `analysis/recovery.py` mit `recover_stranded_analysis_runs(db) -> list[str]`, aufgerufen
im Lifespan von `create_app`, vor `runner.start()`:

1. `SELECT id, status FROM analysis_runs WHERE status IN ('queued','running')` — im
   Normalfall leer, dann ist der Sweep nach einer billigen Query fertig.
2. Sonst: `analysis.run`-Jobs laden und in Python `payload["analysis_run_id"] → Job` mappen.
   Python statt `json_extract`, damit der Sweep dialektneutral bleibt (SQLite **und**
   Postgres).
3. Finalisiert wird ein Lauf **nur**, wenn sein Job terminal (`succeeded`/`failed`/`canceled`)
   oder gar nicht vorhanden ist. Ein Job in `queued`/`leased`/`running` heißt: Finger weg.
4. Pro geheiltem Lauf eine `logger.warning`-Zeile; zurück kommen die IDs.

Regel 3 ist es, die den Sweep bei nebenläufigen Workern sicher macht — und bei einem
Neustart nach SIGKILL: dort steht der Job noch auf `running` mit abgelaufenem Lease, der
Sweep lässt ihn also in Ruhe, der Reaper requeued ihn Sekunden später, der nächste Versuch
stempelt den Lauf neu.

Sie fängt außerdem zwei Fälle, die der Reaper strukturell nicht sehen kann:

- Ein Lauf **ohne Job-Zeile** — `enqueue` *löscht* bei Wiederverwendung eines
  `idempotency_key` den vorherigen gescheiterten Job (`jobs/runner.py:87`).
- Ein Lauf, der auf `'queued'` stehen blieb, weil zwischen `create_analysis_run` und
  `enqueue` etwas geworfen hat.

**Nicht-Regression, die belegt werden muss:** eine Leiche von `running` auf `failed` zu
drehen darf ihr Transkript nicht verstecken. `get_latest_transcript_run` sortiert
`succeeded` nach vorn, aber ein `failed`-Lauf *mit* Segmenten schlägt weiterhin einen
`succeeded`-Lauf *ohne* — die `EXISTS`-Bedingung filtert letztere vorher weg. AgentFarms 165
Segmente und n8ns 8 bleiben erreichbar. Eigener Test (§ 5).

### 3. `get_latest_succeeded_analysis_run` — die fünf Gates sind nicht mehr blockierbar

Geschwister-Resolver zu `get_latest_transcript_run`, dieselbe Ordnung ohne das `EXISTS`:

```sql
SELECT * FROM analysis_runs WHERE asset_id = ? AND status = 'succeeded'
ORDER BY COALESCE(started_at, '') DESC, id DESC LIMIT 1
```

Die fünf Stellen aus dem Befund wechseln von „der neueste Lauf, und der sollte gefälligst
`succeeded` sein" auf „der neueste Lauf, **der** `succeeded` war". Jeweils kollabiert
`if run is None or run["status"] != "succeeded"` zu `if run is None`.

`analysis/shorts_handlers.py:114` behält seine diagnostische Fehlermeldung („latest status:
…"), indem es den neuesten Lauf **nur im Fehlerfall** zusätzlich nachschlägt.

Bewusst **nicht** umgezogen — dieselbe Grenze, die § 3 der Vorgänger-Spec zieht: `api/analysis.py:87`
(Lauf-Metadaten) und `:96` (Shot-Liste — „was hat die letzte Analyse produziert" ist eine
legitime Aktualitätsfrage), `api/scenes.py:56`, `api/shorts.py:66`, `demo/drafts.py:41`,
`ingest/handlers.py:190` (Existenzprüfung).

### 4. `start_analysis_run` schleppt den Vorversuch nicht mehr mit

```sql
UPDATE analysis_runs
SET status='running', started_at=?, finished_at=NULL, diagnostics_json='{}'
WHERE id=?
```

Heute setzt die Funktion nur `status` und `started_at`. Mit `max_attempts=2` und § 1 säße ein
wiederholter Lauf sonst auf `running` **mit** gesetztem `finished_at` und einem alten
`{"error": ...}` in den Diagnostics — beides reicht `api/analysis.py:47` unverändert an die
UI durch.

`started_at` wird neu gesetzt, nicht genullt: die Ordnung in `get_latest_analysis_run` und
in beiden neuen Resolvern hängt an `COALESCE(started_at, '') DESC`.

## 5. Tests

Neu, `tests/test_stranded_run_recovery.py`. Durchgehend echte `SqliteDatabase` unter
`tmp_path`, Seeds über `repos`, Lease-Ablauf simuliert durch ein `lease_expires_at` in der
Vergangenheit (das Idiom aus `tests/test_job_runner.py::test_reaper_requeues_then_fails`).

Reaper:

- **`test_reaper_fails_the_analysis_run_when_attempts_are_exhausted`** — Job abgelaufen,
  `attempt >= max_attempts`: der Lauf steht danach auf `failed`, mit `recovered_by` in den
  Diagnostics.
- **`test_reaper_leaves_the_run_running_when_the_job_is_requeued`** — `attempt < max_attempts`:
  der Job ist wieder `queued`, der Lauf unverändert `running`.
- **`test_reaper_ignores_jobs_of_other_kinds`** — ein Job anderer Art mit derselben
  Payload-Form lässt `analysis_runs` unberührt.
- **`test_reaper_does_not_overwrite_an_already_finished_run`** — Lauf `succeeded`, Job
  abgelaufen: der Lauf bleibt `succeeded`.

Sweep:

- **`test_sweep_finalizes_a_run_whose_job_already_failed`** — die Live-Form.
- **`test_sweep_finalizes_a_run_without_any_job`**.
- **`test_sweep_leaves_a_run_with_a_live_job_alone`** — Job `queued` bzw. `running`.
- **`test_sweep_is_idempotent`** — der zweite Aufruf liefert `[]`.
- **`test_startup_runs_the_sweep`** — Leiche seeden, `TestClient` betreten, Lauf ist `failed`.
- **`test_finalized_run_keeps_its_transcript_reachable`** — die Nicht-Regression aus § 2:
  `get_latest_transcript_run` liefert nach dem Sweep denselben Lauf wie davor.

Resolver und Gates:

- **`test_latest_succeeded_run_ignores_a_newer_failed_run`**.
- **`test_shorts_candidates_accepts_an_asset_whose_newest_run_failed`** — 202 statt 409.
- **`test_extract_shorts_uses_the_latest_succeeded_run`** — die Shots kommen aus dem
  erfolgreichen, nicht aus dem toten Lauf.

Neustart:

- **`test_restart_clears_the_previous_attempts_diagnostics`** — `start_analysis_run` nach
  einem `failed`-Abschluss: `finished_at is None`, `diagnostics_json == '{}'`.

## 6. Verifikation

`uv run pytest`, bare `uv run mypy` (die CI typt auch `tests/`) und `uv run ruff check` aus
`services/local-api`. Dazu ein Sweep-Lauf gegen eine **Kopie** von
`workspace-livetest/laura.db` (nie das Original): die drei Leichen stehen danach auf `failed`,
und `get_latest_transcript_run` liefert unverändert AgentFarms 165-Segment-Lauf und n8ns 8.

## 7. Was diese Spec ausdrücklich NICHT behebt

- **Der Zombie-Thread.** Im Runtime-Cap-Fall läuft der alte Handler weiter, während der Job
  requeued wird; er kann den Lauf später noch selbst abschließen. Das ist die
  Doppellauf-Eigenschaft der `jobs`-Ebene, die es vorher schon gab — diese Spec macht sie
  nicht schlimmer und löst sie nicht.
- **Die fehlende Semantik-Indizierung** der geretteten Segmente (§ 7 der Vorgänger-Spec):
  unverändert nur durch eine Neuanalyse bei erreichbarem Qdrant zu beheben.
- **Ein eigener „latest run mit Frame-Embeddings"-Resolver**: `visual_embed.py:353` wird hier
  auf den `succeeded`-Resolver gehoben (§ 3) — dessen Leser `visual_query.py:75`
  (`_asset_frame_embeddings`) zieht mit auf denselben Resolver, sonst sucht der Leser unter
  einer neueren Leiche nach Embeddings, die der Schreiber unter dem `succeeded`-Lauf abgelegt
  hat, und findet nichts. Ein Schreiber/Leser-Paar, das nur zur Hälfte umzieht, ist schlechter
  als eines, das gar nicht umzieht — deshalb ist das kein separater Scope-Punkt mehr, sondern
  Teil von § 3.
- **Ein Reaper für andere Job-Arten mit eigener Statuszeile** (`production_sessions`,
  `short_runs`): dieselbe Frage, anderer Zyklus.
