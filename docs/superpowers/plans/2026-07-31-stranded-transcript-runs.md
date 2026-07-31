# Gestrandete Transkripte — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transkript-Segmente, die an einem abgestürzten (`status='running'`) oder an einem
älteren Analyselauf hängen, werden wieder gefunden — und neue Leichen entstehen nicht mehr.

**Architecture:** Ein neuer Resolver `repos.get_latest_transcript_run` löst den Lauf nach
*Artefakt* auf (hat-ein-Transkript > `succeeded` > neuester, `LIMIT 1`) statt nach reiner
Aktualität/Status. `search_transcript` bekommt denselben Rumpf als korrelierte Unterabfrage;
17 Transkript-Leser stellen um. Auf der Schreibseite kann best-effort-Semantik keinen
Analyselauf mehr umreißen, und ein abstürzender Handler schließt seinen Lauf immer terminal ab.

**Tech Stack:** Python 3.11+, `uv`, FastAPI, SQLite (portabel Richtung Postgres formuliert),
pytest, mypy (strict), ruff.

**Spec:** [`docs/superpowers/specs/2026-07-31-stranded-transcript-runs-design.md`](../specs/2026-07-31-stranded-transcript-runs-design.md)

## Global Constraints

- Arbeitsverzeichnis für alle Kommandos: `services/local-api` (dort liegt `pyproject.toml`).
- Tests: `uv run pytest`. Lint: `uv run ruff check .`. Typen: **`uv run mypy`** — bare, ohne
  Pfad. Die CI ruft es genau so und prüft damit **auch den `tests/`-Baum**; ein lokales
  `mypy src` verdeckt Fehler in Testdateien.
- Typing strikt: **niemals `any`/`Any` als Rückgabetyp erfinden**, wo ein konkreter Typ geht.
  Bestehende Signaturen in `repos.py` geben `dict[str, Any]` zurück — dem Muster folgen.
- Kein `print` in committed Code (projektlokaler Logger).
- SQL portabel halten: kein SQLite-spezifisches JSON/Window-Funktions-Zeug. `CASE WHEN`,
  `EXISTS`, `COALESCE` sind erlaubt und in beiden Backends identisch.
- Conventional Commits, Prosa im Commit-Body auf Englisch.
- **`git add <exakte Pfade>` — niemals `git add -A`/`.`** Eine parallele Session committet auf
  denselben Branch (`feat/generate-ui`); der Arbeitsbaum kann fremde Änderungen tragen.
- Branch: `claude/vibrant-kirch-9beaea`, basiert auf `feat/generate-ui`. Vor dem Ende ggf.
  `git fetch && git rebase feat/generate-ui`.
- Die **Ausschluss-Eigenschaft** ist nicht verhandelbar: pro Asset genau EIN Lauf, nie
  gemischte Segmente aus zwei Läufen. Jede Query, die Segmente auswählt, endet auf `LIMIT 1`
  in der Lauf-Auflösung.
- Die Reihenfolge-Tests sind **nicht flaky**: `util.utcnow_iso` ist explizit monoton
  (Mikrosekunden, erzwungen streng steigend), `started_at` zweier nacheinander gestarteter
  Läufe ist also garantiert verschieden. Wer hier eine Race sieht, sucht am falschen Ort.

---

### Task 1: Der Resolver + `search_transcript`

**Files:**
- Modify: `services/local-api/src/laura/db/repos.py` (neu nach `get_latest_analysis_run`,
  Zeile 391-398; `search_transcript` Zeile 1449-1481)
- Test: `services/local-api/tests/test_discovery.py`

**Interfaces:**
- Consumes: nichts aus früheren Tasks.
- Produces:
  `repos.get_latest_transcript_run(db: Database, asset_id: str) -> dict[str, Any] | None`
  — die volle `analysis_runs`-Zeile des Laufs, der das Transkript des Assets trägt, oder
  `None`, wenn kein einziger Lauf des Assets Segmente hat. Task 2 tauscht darauf um.

- [ ] **Step 1: Write the failing tests**

Ans Ende von `services/local-api/tests/test_discovery.py` anhängen. Die Helfer `_db`,
`_seed_asset_with_scenes` und `FPS` existieren dort bereits — nicht neu schreiben.

```python
def _seed_run_with_segments(
    db: Database,
    asset_id: str,
    *,
    pipeline_version: str,
    status: str,
    texts: list[str],
) -> str:
    """A run with one segment per text at [10,60). ``status`` 'running' leaves it unfinished
    (the live shape: the handler crashed before finish_analysis_run)."""
    run = repos.create_analysis_run(
        db, asset_id=asset_id, pipeline_version=pipeline_version, config={}
    )
    repos.start_analysis_run(db, run["id"])
    for text in texts:
        repos.insert_segment_with_words(
            db, asset_id=asset_id, run_id=run["id"], speaker_id=None,
            segment={"start_sample": 16000, "end_sample": 32000, "start_frame": 10,
                     "end_frame": 60, "text": text, "confidence": 1.0},
            words=[],
        )
    if status != "running":
        repos.finish_analysis_run(db, run["id"], status=status, diagnostics={})
    return str(run["id"])


def test_transcript_stranded_on_unfinished_run_is_still_found(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The live shape (workspace-livetest): the ONLY transcript hangs on a run frozen in
    'running' (the handler crashed on an unreachable Qdrant before finish_analysis_run),
    while a LATER succeeded run re-analysed scenes only and carries zero segments."""
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset_id = _seed_asset_with_scenes(db, project["id"], "a.mp4", segments=[])
    stranded = _seed_run_with_segments(
        db, asset_id, pipeline_version="1", status="running", texts=["mission stranded"]
    )
    _seed_run_with_segments(db, asset_id, pipeline_version="2", status="succeeded", texts=[])

    run = repos.get_latest_transcript_run(db, asset_id)
    assert run is not None
    assert run["id"] == stranded

    found = repos.search_transcript(db, project_id=project["id"], query="mission")
    assert [r["text"] for r in found] == ["mission stranded"]

    out = discovery.search_material(db, project["id"], "mission")
    assert [r["asset_id"] for r in out["ranking"]] == [asset_id]


def test_succeeded_run_wins_over_newer_unfinished_run(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A re-analysis in flight has already written part of its segments. It must NOT shadow
    the previous complete transcript — succeeded outranks unfinished regardless of age."""
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset_id = _seed_asset_with_scenes(db, project["id"], "a.mp4", segments=[])
    complete = _seed_run_with_segments(
        db, asset_id, pipeline_version="1", status="succeeded", texts=["mission complete"]
    )
    _seed_run_with_segments(
        db, asset_id, pipeline_version="2", status="running", texts=["mission partial"]
    )

    run = repos.get_latest_transcript_run(db, asset_id)
    assert run is not None
    assert run["id"] == complete

    found = repos.search_transcript(db, project_id=project["id"], query="mission")
    assert [r["text"] for r in found] == ["mission complete"]


def test_search_rows_never_mix_two_runs(tmp_path: Path, monkeypatch: Any) -> None:
    """The exclusion property: three runs carry matching segments, exactly one is chosen."""
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset_id = _seed_asset_with_scenes(db, project["id"], "a.mp4", segments=[])
    _seed_run_with_segments(
        db, asset_id, pipeline_version="1", status="succeeded", texts=["mission one"]
    )
    newest = _seed_run_with_segments(
        db, asset_id, pipeline_version="2", status="succeeded",
        texts=["mission two", "mission three"],
    )
    _seed_run_with_segments(
        db, asset_id, pipeline_version="3", status="running", texts=["mission four"]
    )

    found = repos.search_transcript(db, project_id=project["id"], query="mission")

    assert len(found) == 2
    with db.connection() as conn:
        run_ids = {
            str(
                conn.execute(
                    "SELECT analysis_run_id FROM transcript_segments WHERE id=?",
                    (row["segment_id"],),
                ).fetchone()["analysis_run_id"]
            )
            for row in found
        }
    assert run_ids == {newest}


def test_asset_without_any_segments_resolves_to_none(tmp_path: Path) -> None:
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mp4",
        source_path="/tmp/a.mp4",
    )
    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="1", config={}
    )
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})

    assert repos.get_latest_transcript_run(db, str(asset["id"])) is None
```

`_seed_asset_with_scenes` wird mit `segments=[]` aufgerufen — es legt Asset, Rough-Cut-Clip
und zwei Szenen `[0,300)`/`[300,600)` an, ohne eigene Segmente. Die Seed-Segmente bei
`start_frame=10` fallen damit in Szene 1.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/local-api && uv run pytest tests/test_discovery.py -v
```

Erwartet: die vier neuen Tests scheitern mit
`AttributeError: module 'laura.db.repos' has no attribute 'get_latest_transcript_run'`.
`test_stale_run_segments_are_excluded_and_ranked_once` und die übrigen bestehenden Tests
müssen **grün** sein.

- [ ] **Step 3: Add the resolver**

In `services/local-api/src/laura/db/repos.py` direkt hinter `get_latest_analysis_run`
(nach Zeile 398) einfügen:

```python
# The run-resolution ordering shared by get_latest_transcript_run and search_transcript's
# correlated subquery: has-a-transcript, then succeeded, then newest. Kept in one place so
# the readers and the lexical search can never drift apart.
_TRANSCRIPT_RUN_ORDER = (
    "ORDER BY CASE WHEN ar.status = 'succeeded' THEN 0 ELSE 1 END, "
    "COALESCE(ar.started_at, '') DESC, ar.id DESC LIMIT 1"
)
_TRANSCRIPT_RUN_HAS_SEGMENTS = (
    "EXISTS (SELECT 1 FROM transcript_segments ts "
    "WHERE ts.asset_id = ar.asset_id AND ts.analysis_run_id = ar.id)"
)


def get_latest_transcript_run(db: Database, asset_id: str) -> dict[str, Any] | None:
    """The run that carries this asset's transcript, or None if no run has segments.

    NOT the same question as :func:`get_latest_analysis_run`. Analysis is stage-configurable
    (``stages.asr: false``), so a scene-only re-analysis is the *latest* run while carrying no
    transcript at all — and a run that crashed after writing its segments (an unreachable
    Qdrant used to kill the whole handler) stays ``'running'`` forever with a complete
    transcript attached. Both shapes exist in workspace-livetest.

    Resolution order, see docs/superpowers/specs/2026-07-31-stranded-transcript-runs-design.md:

    1. only runs that HAVE segments,
    2. ``succeeded`` outranks anything unfinished (an in-flight re-analysis never shadows a
       complete transcript),
    3. newest first, mirroring :func:`get_latest_analysis_run`'s ordering.

    ``LIMIT 1`` is the exclusion property: exactly one run per asset, so callers filtering on
    ``analysis_run_id`` can never mix two runs' segments.
    """
    with db.connection() as conn:
        row = conn.execute(
            "SELECT ar.* FROM analysis_runs ar "
            f"WHERE ar.asset_id = ? AND {_TRANSCRIPT_RUN_HAS_SEGMENTS} "
            f"{_TRANSCRIPT_RUN_ORDER}",
            (asset_id,),
        ).fetchone()
        return dict(row) if row is not None else None
```

Die `EXISTS`-Unterabfrage führt `asset_id` mit, damit sie auf
`idx_segments_asset_run(asset_id, analysis_run_id, start_sample)` läuft.

- [ ] **Step 4: Rewrite `search_transcript`'s subquery**

In derselben Datei, `search_transcript` (Zeile 1449-1481). Docstring-Absatz und die
korrelierte Unterabfrage ersetzen:

```python
def search_transcript(
    db: Database, *, project_id: str, query: str, limit: int = 50
) -> list[dict[str, Any]]:
    """Lexical, case-insensitive transcript search scoped to a project.

    Restricted to each asset's transcript run — the same resolution
    :func:`get_latest_transcript_run` applies, inlined as a correlated subquery so search and
    the readers can't disagree: only runs that HAVE segments, ``succeeded`` before unfinished,
    newest first, ``LIMIT 1``. Without that single-run restriction a re-analysis' old segments
    would double-count matches instead of replacing them (the semantic index is immune: it
    deletes-before-reindexing, see :mod:`laura.short_creator.discovery`).

    Portable across SQLite/Postgres (LOWER + LIKE). FTS5/semantic search is a
    later optimisation (docs/15)."""
    pattern = f"%{query.lower()}%"
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT s.id AS segment_id, s.asset_id AS asset_id, a.display_name AS asset_name, "
            "s.start_frame AS start_frame, s.end_frame AS end_frame, s.text AS text, "
            "sp.label AS speaker_label "
            "FROM transcript_segments s "
            "JOIN media_assets a ON a.id = s.asset_id "
            "LEFT JOIN speakers sp ON sp.id = s.speaker_id "
            "WHERE a.project_id = ? AND LOWER(s.text) LIKE ? "
            "AND s.analysis_run_id = ("
            "  SELECT ar.id FROM analysis_runs ar "
            f"  WHERE ar.asset_id = s.asset_id AND {_TRANSCRIPT_RUN_HAS_SEGMENTS} "
            f"  {_TRANSCRIPT_RUN_ORDER}"
            ") "
            "ORDER BY s.start_sample LIMIT ?",
            (project_id, pattern, limit),
        ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 5: Run the tests**

```bash
cd services/local-api && uv run pytest tests/test_discovery.py -v
```

Erwartet: alle grün, inklusive des unveränderten
`test_stale_run_segments_are_excluded_and_ranked_once`.

- [ ] **Step 6: Full suite + lint + types**

```bash
cd services/local-api && uv run pytest -q && uv run ruff check . && uv run mypy
```

Erwartet: alles grün. `test_gap_closure.py` berührt `search_transcript` — läuft es rot,
prüfen, ob der Seed dort einen Lauf ohne `finish_analysis_run` benutzt; das ist jetzt
**zulässig** und der Test sollte weiter grün sein (der Resolver findet den Lauf auch
unfertig). Rot heißt hier: echter Regressionsbefund, nicht Testkosmetik.

- [ ] **Step 7: Commit**

```bash
git add services/local-api/src/laura/db/repos.py services/local-api/tests/test_discovery.py
git commit -m "fix(db): resolve the transcript run by artifact, not by recency"
```

Commit-Body (mehrzeilig anhängen):

```
get_latest_analysis_run answers "which run ran last", which is a different question from
"which run holds the transcript". Analysis is stage-configurable, so a scene-only
re-analysis is the latest run with zero segments; and a run that crashed after writing its
segments stays 'running' forever. Both shapes are live in workspace-livetest, where two
assets' only transcripts were invisible to lexical search.

get_latest_transcript_run resolves has-a-transcript > succeeded > newest, LIMIT 1.
search_transcript inlines the same ordering as its correlated subquery, so search and the
readers cannot drift. The exclusion property is unchanged: one run per asset, never mixed.
```

---

### Task 2: Die Transkript-Leser stellen um

**Files:**
- Modify (je eine Zeile, sofern nicht anders vermerkt):
  - `services/local-api/src/laura/analysis/handlers.py:465`
  - `services/local-api/src/laura/api/analysis.py:116`, `:159`, `:185`
  - `services/local-api/src/laura/api/assets.py:346`
  - `services/local-api/src/laura/demo/drafts.py:75`
  - `services/local-api/src/laura/render/captions_source.py:77`
  - `services/local-api/src/laura/render/handlers.py:269`
  - `services/local-api/src/laura/render/shorts_render.py:248` (+ Gate, s.u.)
  - `services/local-api/src/laura/sequences/transcript.py:31`
  - `services/local-api/src/laura/short_creator/context.py:50`, `:101`, `:187`, `:291`, `:322`
  - `services/local-api/src/laura/short_creator/production_tools.py:600`
  - `services/local-api/src/laura/short_creator/scout.py:211`
- Test: `services/local-api/tests/test_discovery.py`

**Interfaces:**
- Consumes: `repos.get_latest_transcript_run(db, asset_id) -> dict[str, Any] | None` aus Task 1.
- Produces: nichts Neues; ab hier lesen Suche und Leser garantiert denselben Lauf.

- [ ] **Step 1: Write the failing test**

Ans Ende von `services/local-api/tests/test_discovery.py`:

```python
def test_reader_and_search_resolve_the_same_run(tmp_path: Path, monkeypatch: Any) -> None:
    """Coherence: discovery ranks the asset off the stranded run, so the scout's context
    reader must find that same transcript. Otherwise the chain contradicts itself — search
    says 'here is your material', get_scene_context says 'no transcript'."""
    from laura.short_creator import context

    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset_id = _seed_asset_with_scenes(db, project["id"], "a.mp4", segments=[])
    _seed_run_with_segments(
        db, asset_id, pipeline_version="1", status="running", texts=["mission stranded"]
    )
    _seed_run_with_segments(db, asset_id, pipeline_version="2", status="succeeded", texts=[])

    window = context.transcript_window(db, asset_id, center_frame=30)

    assert window["ok"] is True
    assert "mission stranded" in window["text"]
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd services/local-api && uv run pytest tests/test_discovery.py::test_reader_and_search_resolve_the_same_run -v
```

Erwartet: FAIL — `window["text"]` ist leer, weil `context.transcript_window` noch den
neuesten (Szenen-only, 0 Segmente) Lauf auflöst.

- [ ] **Step 3: Swap the plain readers**

An diesen 15 Stellen `repos.get_latest_analysis_run(` → `repos.get_latest_transcript_run(`
ersetzen. Der Rest der Zeile und alle `if run is None`-Wächter bleiben unverändert:

`analysis/handlers.py:465`, `api/analysis.py:116`, `api/analysis.py:185`,
`api/assets.py:346`, `demo/drafts.py:75`, `render/captions_source.py:77`,
`render/handlers.py:269`, `sequences/transcript.py:31`, `short_creator/context.py:50`,
`:101`, `:187`, `:291`, `:322`, `short_creator/production_tools.py:600`,
`short_creator/scout.py:211`.

**Nicht anfassen** — dort ist „neuester Lauf" die richtige Frage (Shots, Lauf-Metadaten,
Zustands-Gates): `api/analysis.py:87`, `:96`, `api/scenes.py:56`, `api/shorts.py:66`,
`api/shorts_candidates.py:57`, `api/timelines.py:465`, `analysis/shorts_handlers.py:114`,
`analysis/visual_embed.py:353`, `analysis/visual_query.py:75`, `demo/drafts.py:41`,
`ingest/handlers.py:190`, `mcp/tools.py:195`, `:283`, sowie `scenes/build.py` (bekommt die
`run_id` des laufenden Analyselaufs übergeben).

- [ ] **Step 4: Swap the two sites that need more than a rename**

`services/local-api/src/laura/api/analysis.py:159` — die ASR-Sprache muss vom Lauf kommen,
der das Transkript erzeugt hat, sonst realignt Laura Segmente aus Lauf A mit der Sprache aus
Lauf B:

```python
    # The language belongs to the run that produced the transcript we are about to realign —
    # not to whatever ran last (a scene-only re-analysis carries no ASR config that matters).
    run = repos.get_latest_transcript_run(db, asset_id)
    if run is None:
        return "en"
```

`services/local-api/src/laura/render/shorts_render.py:248` — das zusätzliche
`status == "succeeded"`-Gate ist jetzt redundant *und* schädlich (es sperrt genau die
gestrandeten Assets von Captions aus). Der Resolver kodiert die Präferenz bereits; ein
Nicht-`None`-Ergebnis heißt „hat ein Transkript":

```python
        run = repos.get_latest_transcript_run(ctx.db, asset["id"])
        if run is not None:
```

- [ ] **Step 5: Run the test**

```bash
cd services/local-api && uv run pytest tests/test_discovery.py -v
```

Erwartet: alle grün.

- [ ] **Step 6: Full suite + lint + types**

```bash
cd services/local-api && uv run pytest -q && uv run ruff check . && uv run mypy
```

Erwartet: alles grün. Scheitert ein Test, der einen Lauf seedet ohne
`finish_analysis_run` aufzurufen, ist das **kein** Grund, die Quelle zu ändern — prüfen, ob
der Test eine echte Erwartung verletzt.

- [ ] **Step 7: Commit**

```bash
git add services/local-api/src/laura/analysis/handlers.py services/local-api/src/laura/api/analysis.py services/local-api/src/laura/api/assets.py services/local-api/src/laura/demo/drafts.py services/local-api/src/laura/render/captions_source.py services/local-api/src/laura/render/handlers.py services/local-api/src/laura/render/shorts_render.py services/local-api/src/laura/sequences/transcript.py services/local-api/src/laura/short_creator/context.py services/local-api/src/laura/short_creator/production_tools.py services/local-api/src/laura/short_creator/scout.py services/local-api/tests/test_discovery.py
git commit -m "fix(transcript): every transcript reader resolves the run the same way"
```

Commit-Body:

```
Seventeen call sites resolved the run with get_latest_analysis_run and then read a
transcript off it. With the search side already resolving by artifact, leaving them would
split the chain: discovery ranks an asset as material while the scout's get_scene_context
reads the latest run, finds zero segments and reports "no transcript".

Shot readers, run-metadata endpoints and state gates keep get_latest_analysis_run -- there,
"which run ran last" is the right question. Two sites needed more than a rename: the realign
language now comes from the transcript's own run, and shorts_render's redundant
status='succeeded' gate would have locked stranded assets out of captions.
```

---

### Task 3: Best-effort-Semantik kann keinen Lauf mehr umreißen

**Files:**
- Modify: `services/local-api/src/laura/analysis/handlers.py:337-344` (in `_run_transcript`)
- Create: `services/local-api/tests/test_analysis_run_finalization.py`

**Interfaces:**
- Consumes: nichts aus Tasks 1-2.
- Produces: `tests/test_analysis_run_finalization.py` mit den Helfern `_seed` und `_ctx`,
  die Task 4 weiterbenutzt.

- [ ] **Step 1: Write the failing test**

Neue Datei `services/local-api/tests/test_analysis_run_finalization.py`:

```python
"""analysis.run always leaves its run in a terminal state, and best-effort stages never kill it.

Both properties were missing and produced the corpses in workspace-livetest: get_index()
sat outside the best-effort try in _run_transcript, so an unreachable Qdrant raised straight
through handle_analysis_run -- which had no try/except, so finish_analysis_run never ran and
the row stayed 'running' forever with its 165 segments attached and diagnostics_json '{}'.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from laura.analysis import handlers
from laura.analysis.types import SegmentResult
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.config import Settings
from laura.jobs.runner import JobContext

FPS = 30


def _db(tmp_path: Path) -> Database:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db: Database = SqliteDatabase(settings.db_path)
    db.migrate()
    return db


def _seed(db: Database, tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    """(project, asset, run_id) — an asset probed with audio, plus a queued analysis run."""
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False,
        workspace_root=str(tmp_path / "ws"),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mp4",
        source_path=str(tmp_path / "a.mp4"),
    )
    repos.update_asset_probe(
        db, str(asset["id"]), type="video", duration_frames=600, rate_num=FPS, rate_den=1,
        audio_sample_rate=16000, start_timecode=None, width=1920, height=1080,
        codec_video="h264", codec_audio="aac", is_vfr=False, sha256=None,
    )
    asset = repos.get_asset(db, str(asset["id"]))
    assert asset is not None
    run = repos.create_analysis_run(
        db, asset_id=str(asset["id"]), pipeline_version="t", config={}
    )
    return project, asset, str(run["id"])


def _ctx(db: Database, payload: dict[str, Any]) -> JobContext:
    return JobContext(
        job_id="job-1", kind="analysis.run", queue="analysis.scene", payload=payload, db=db
    )


def test_unreachable_index_does_not_fail_the_transcript_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A down Qdrant raises inside get_index() itself (client/collection construction). That
    is best-effort work -- it belongs in the diagnostics, not in the caller's face."""
    db = _db(tmp_path)
    project, asset, run_id = _seed(db, tmp_path)

    def _raise_index() -> None:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(handlers, "asr_available", lambda: True)
    monkeypatch.setattr(
        handlers,
        "transcribe",
        lambda path, model_size=None, language=None: [
            SegmentResult(text="mission talk", start_sec=0.5, end_sec=2.0, confidence=1.0)
        ],
    )
    monkeypatch.setattr(handlers, "get_index", _raise_index)

    result = handlers._run_transcript(
        db, asset, project, run_id,
        {"audio_mono16k": {"path": str(tmp_path / "a.wav")}},
        {"stages": {}, "model": "base", "language": None},
    )

    assert result["status"] == "ok"
    assert result["segments"] == 1
    assert result["embedded"] == 0
    assert "embed failed: RuntimeError" in result["diarization"]
    # The segments are what matter: they must be committed even though the embed blew up.
    assert [s["text"] for s in repos.get_transcript(db, str(asset["id"]), run_id)] == [
        "mission talk"
    ]
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd services/local-api && uv run pytest tests/test_analysis_run_finalization.py -v
```

Erwartet: FAIL mit `RuntimeError: connection refused` — der Aufruf propagiert, weil
`get_index()` außerhalb des `try` steht.

- [ ] **Step 3: Move `get_index()` inside the best-effort try**

In `services/local-api/src/laura/analysis/handlers.py`, den Block ab Zeile 337 ersetzen:

```python
    embedded = 0
    if index_items:
        try:
            # get_index() itself raises when Qdrant is unreachable (the client/collection
            # construction talks to the server). Semantic indexing is best-effort -- it must
            # never take the analysis run with it. This is fd0914b's fix on the write side.
            index = get_index()
            if index is not None:
                index.delete_asset(asset["id"])
                embedded = index.index(index_items)
        except Exception as exc:  # noqa: BLE001 - semantic indexing is best-effort
            diar_status = f"{diar_status}; embed failed: {type(exc).__name__}"
    return {"status": "ok", "segments": len(segments), "diarization": diar_status,
            "alignment": align_status, "embedded": embedded}
```

- [ ] **Step 4: Run the test**

```bash
cd services/local-api && uv run pytest tests/test_analysis_run_finalization.py -v
```

Erwartet: PASS.

- [ ] **Step 5: Full suite + lint + types**

```bash
cd services/local-api && uv run pytest -q && uv run ruff check . && uv run mypy
```

- [ ] **Step 6: Commit**

```bash
git add services/local-api/src/laura/analysis/handlers.py services/local-api/tests/test_analysis_run_finalization.py
git commit -m "fix(analysis): an unreachable Qdrant no longer kills the analysis run"
```

Commit-Body:

```
_run_transcript called get_index() outside the best-effort try that guards delete_asset and
index(). A down Qdrant raises during client/collection construction, so the exception went
straight through handle_analysis_run -- which is how workspace-livetest ended up with two
runs frozen in 'running', each holding the asset's only transcript.

The module docstring already promised the opposite ("runs each stage best-effort and records
per-stage status in the run diagnostics"). Now it holds for the embed step too.
```

---

### Task 4: Ein abstürzender Handler schließt seinen Lauf ab

**Files:**
- Modify: `services/local-api/src/laura/analysis/handlers.py:349-405` (`handle_analysis_run`)
- Test: `services/local-api/tests/test_analysis_run_finalization.py`

**Interfaces:**
- Consumes: `_seed`, `_ctx` aus Task 3.
- Produces:
  `handlers._analysis_run_stages(ctx: JobContext, diagnostics: dict[str, Any]) -> dict[str, Any]`
  — der bisherige Rumpf, der sein Diagnostics-Dict jetzt übergeben bekommt statt es selbst
  anzulegen. `handle_analysis_run` bleibt die öffentliche, in der Registry eingetragene
  Signatur.

- [ ] **Step 1: Write the failing tests**

Ans Ende von `services/local-api/tests/test_analysis_run_finalization.py`:

```python
def test_crashing_stage_finalizes_the_run_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The jobs table has a reaper; analysis_runs had nothing. A handler that dies must still
    leave a terminal row behind -- otherwise the run says 'running' forever and every reader
    that filters on status silently loses its artifacts."""
    db = _db(tmp_path)
    _project, asset, run_id = _seed(db, tmp_path)

    def _boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("scene detector exploded")

    monkeypatch.setattr(handlers, "_run_scene", _boom)

    with pytest.raises(RuntimeError, match="scene detector exploded"):
        handlers.handle_analysis_run(
            _ctx(db, {
                "asset_id": str(asset["id"]),
                "analysis_run_id": run_id,
                "config": {"stages": {"scene": True, "asr": False}},
            })
        )

    run = repos.get_analysis_run(db, run_id)
    assert run is not None
    assert run["status"] == "failed"
    assert run["finished_at"] is not None
    diagnostics = json.loads(run["diagnostics_json"] or "{}")
    assert "RuntimeError: scene detector exploded" in diagnostics["error"]


def test_failed_run_keeps_the_stages_that_did_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever finished before the crash stays in the diagnostics -- that is what makes the
    failure diagnosable instead of an empty '{}'."""
    db = _db(tmp_path)
    _project, asset, run_id = _seed(db, tmp_path)

    monkeypatch.setattr(
        handlers, "_run_scene", lambda *a, **k: {"status": "ok", "count": 3}
    )

    def _boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("asr exploded")

    monkeypatch.setattr(handlers, "_run_transcript", _boom)

    with pytest.raises(RuntimeError, match="asr exploded"):
        handlers.handle_analysis_run(
            _ctx(db, {
                "asset_id": str(asset["id"]),
                "analysis_run_id": run_id,
                "config": {"stages": {"scene": True, "asr": True}},
            })
        )

    run = repos.get_analysis_run(db, run_id)
    assert run is not None
    diagnostics = json.loads(run["diagnostics_json"] or "{}")
    assert diagnostics["scene"] == {"status": "ok", "count": 3}
    assert "RuntimeError: asr exploded" in diagnostics["error"]


def test_clean_run_still_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard on the happy path: the wrap must not change a healthy run."""
    db = _db(tmp_path)
    _project, asset, run_id = _seed(db, tmp_path)

    monkeypatch.setattr(
        handlers, "_run_scene", lambda *a, **k: {"status": "ok", "count": 2}
    )

    diagnostics = handlers.handle_analysis_run(
        _ctx(db, {
            "asset_id": str(asset["id"]),
            "analysis_run_id": run_id,
            "config": {"stages": {"scene": True, "asr": False}},
        })
    )

    run = repos.get_analysis_run(db, run_id)
    assert run is not None
    assert run["status"] == "succeeded"
    assert diagnostics["scene"] == {"status": "ok", "count": 2}
    assert "error" not in diagnostics
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd services/local-api && uv run pytest tests/test_analysis_run_finalization.py -v
```

Erwartet: die beiden Crash-Tests scheitern — `run["status"]` ist `'running'`, nicht
`'failed'`. `test_clean_run_still_succeeds` ist bereits grün.

- [ ] **Step 3: Split the body out and wrap it**

In `services/local-api/src/laura/analysis/handlers.py`: die bestehende Zeile 349
`def handle_analysis_run(ctx: JobContext) -> dict[str, Any]:` ersetzen durch den neuen
Wrapper plus die umbenannte Rumpf-Funktion. Der Rumpf selbst bleibt Zeile für Zeile
unverändert — **außer** dass die Zeile `diagnostics: dict[str, Any] = {}` (Zeile 366)
ersatzlos entfällt, weil das Dict jetzt hereingereicht wird:

```python
def handle_analysis_run(ctx: JobContext) -> dict[str, Any]:
    """Run the analysis stages, and leave the run in a TERMINAL state either way.

    The jobs table gets a reaper (jobs/runner.py); analysis_runs never had one, and
    finish_analysis_run was reachable only on the happy path. A handler that raised therefore
    left the row in 'running' forever -- with its segments already committed and
    diagnostics_json still '{}'. workspace-livetest holds three such rows. The exception is
    re-raised untouched so the job's own failure handling and retry budget are unchanged.
    """
    run_id = str(ctx.payload["analysis_run_id"])
    diagnostics: dict[str, Any] = {}
    try:
        return _analysis_run_stages(ctx, diagnostics)
    except Exception as exc:
        diagnostics["error"] = f"{type(exc).__name__}: {exc}"
        repos.finish_analysis_run(
            ctx.db, run_id, status="failed", diagnostics=diagnostics
        )
        raise


def _analysis_run_stages(ctx: JobContext, diagnostics: dict[str, Any]) -> dict[str, Any]:
    asset_id = ctx.payload["asset_id"]
    run_id = ctx.payload["analysis_run_id"]
```

Der Rest ab `config: dict[str, Any] = ctx.payload.get("config", {})` bleibt wie er ist.

- [ ] **Step 4: Run the tests**

```bash
cd services/local-api && uv run pytest tests/test_analysis_run_finalization.py -v
```

Erwartet: alle fünf grün.

- [ ] **Step 5: Full suite + lint + types**

```bash
cd services/local-api && uv run pytest -q && uv run ruff check . && uv run mypy
```

`ruff` kann `BLE001` (blind except) am neuen `except Exception` monieren. Falls ja: hier ist
das Fangen beabsichtigt und der Fehler wird **weitergeworfen**, also
`except Exception as exc:  # noqa: BLE001 - finalize the run, then re-raise untouched`.

- [ ] **Step 6: Commit**

```bash
git add services/local-api/src/laura/analysis/handlers.py services/local-api/tests/test_analysis_run_finalization.py
git commit -m "fix(analysis): a crashing run is written down as failed, not left running"
```

Commit-Body:

```
finish_analysis_run was reachable only on the happy path, so any exception left the row in
'running' with diagnostics_json '{}' -- the state three runs in workspace-livetest have been
stuck in since 2026-07-16, two of them holding their asset's only transcript. The jobs table
recorded those crashes correctly; analysis_runs simply never heard about them.

The body moves to _analysis_run_stages and takes its diagnostics dict from the wrapper, so
the stages that did complete survive into the failed run's diagnostics. The exception is
re-raised untouched: the job's failure trace and retry budget are unchanged.
```

---

### Task 5: Live-Verifikation gegen die echte DB

**Files:**
- Create: `services/local-api/scripts/verify_stranded_transcripts.py`

**Interfaces:**
- Consumes: alles aus Tasks 1-4.
- Produces: nichts, was Code importiert — ein einmaliges Verifikationsskript.

- [ ] **Step 1: Write the verification script**

`services/local-api/scripts/verify_stranded_transcripts.py`. Das Skript arbeitet auf einer
**Kopie**; das Original `workspace-livetest/laura.db` wird nie geöffnet zum Schreiben.
`argparse` + `logging` statt `print`, weil `print` in committed Code verboten ist:

```python
"""Verify the stranded-transcript fix against a COPY of the live database.

Usage (from services/local-api):
    uv run python scripts/verify_stranded_transcripts.py ../../workspace-livetest/laura.db

Reads only: the source file is copied to a temp dir first. Two assets in the live DB have
their only transcript on runs frozen in 'running' (AgentFarm Autogen: 165 segments,
n8n Farm: 8), and their newer succeeded runs carry none -- see
docs/superpowers/specs/2026-07-31-stranded-transcript-runs-design.md.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
from pathlib import Path

from laura.db.database import SqliteDatabase
from laura.db import repos

logger = logging.getLogger("verify_stranded_transcripts")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", type=Path, help="path to the live laura.db (read-only)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "laura.db"
        shutil.copy2(args.db_path, copy)
        db = SqliteDatabase(copy)

        with db.connection() as conn:
            projects = [dict(r) for r in conn.execute("SELECT id, name FROM projects")]
            assets = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, project_id, display_name FROM media_assets ORDER BY display_name"
                )
            ]

        ok = True
        for asset in assets:
            run = repos.get_latest_transcript_run(db, str(asset["id"]))
            if run is None:
                logger.info("%-30s no transcript on any run", asset["display_name"][:30])
                continue
            segments = repos.get_transcript(db, str(asset["id"]), str(run["id"]))
            logger.info(
                "%-30s run %s (%s) -> %d segments",
                asset["display_name"][:30], str(run["id"])[:8], run["status"], len(segments),
            )
            if not segments:
                ok = False

        for project in projects:
            hits = repos.search_transcript(
                db, project_id=str(project["id"]), query="agent", limit=200
            )
            asset_names = sorted({str(h["asset_name"]) for h in hits})
            logger.info(
                "project %-20s lexical 'agent': %d hits across %s",
                str(project["name"])[:20], len(hits), asset_names or "-",
            )

        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it against the live DB copy**

```bash
cd services/local-api && uv run python scripts/verify_stranded_transcripts.py ../../workspace-livetest/laura.db
```

Erwartet, und **genau das ist der Beweis für diesen Plan**:
- `AgentFarm Autogen` löst auf Lauf `f6b7a789` (`running`) mit **165** Segmenten auf,
- `n8n Farm` auf Lauf `29db1dc2` (`running`) mit **8** Segmenten,
- `Bildschirmaufnahme…` (192) und `lnsExa1UbnM` (413) unverändert auf ihren
  `succeeded`-Läufen,
- `CaptainCook Demo Source` weiterhin ohne Transkript,
- die lexikalische Suche liefert Treffer aus den vorher unsichtbaren Assets.

Weicht das ab, ist der Befund echt: **nicht** das Skript anpassen, sondern melden.

- [ ] **Step 3: Confirm the original is untouched**

```bash
git status --short ../../workspace-livetest/ && ls -la ../../workspace-livetest/laura.db
```

Erwartet: keine Änderung an `laura.db` (das Verzeichnis ist ohnehin untracked).

- [ ] **Step 4: Lint the new script; bare mypy does not reach it**

```bash
cd services/local-api && uv run ruff check . && uv run mypy
```

`ruff check .` hat kein Include/Exclude, das `scripts/` ausschließt — es lintet das neue
Skript mit. Das nackte `uv run mypy` tut das **nicht**: `pyproject.toml:90` setzt
`files = ["src", "tests"]`, also typprüft dieser Lauf nur, dass der Rest des Branches sauber
bleibt, nicht `verify_stranded_transcripts.py` selbst. Für ein Wegwerf-Verifikationsskript ist
das eine akzeptable Lücke — aber keine, die man als „mypy deckt es ab" behaupten darf.

- [ ] **Step 5: Commit**

```bash
git add services/local-api/scripts/verify_stranded_transcripts.py
git commit -m "test(analysis): verify the stranded transcripts against a copy of the live DB"
```

Commit-Body:

```
Proves the fix on the data that produced the report -- two assets whose only transcript hung
on a run frozen in 'running' resolve and search again, without re-running Whisper and without
touching the original file (the script works on a temp copy).
```

- [ ] **Step 6: Rebase onto the shared branch and report**

```bash
git fetch origin && git rebase feat/generate-ui
```

Konflikte sind möglich in `handlers.py` / `repos.py`, wenn die parallele Session dort
gearbeitet hat — additiv auflösen, danach `uv run pytest -q` erneut.

Abschlussbericht an den User muss ausdrücklich enthalten, was **nicht** behoben ist: die 165
Segmente sind nie eingebettet worden, semantische Suche findet sie weiterhin nicht, und
`discovery._segment_hits` fällt nur bei *null* semantischen Treffern auf lexikalisch zurück —
mit laufendem `laura-qdrant` bringt Auto-Short diese Assets also weiterhin nicht nach oben.
Reparatur ohne neuen Code: Analyse für die beiden Assets einmal neu laufen lassen, solange
Qdrant erreichbar ist.
