# Freeing the Last v2 Constants Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the v2 production pipeline tell the truth about three things it currently gets wrong — how fast a language is spoken, whether the script fills its target, and whether a run actually produced a video — and stop the agent loop destroying its own valid work.

**Architecture:** Four independent fixes, each in one file. Three follow the pattern already shipped four times this session (`c24df68` format, `c9f3618` dedup, `58d4b4f` roi rule, `d96c290` language): a per-production decision that v1 froze into the source because it only ever made one kind of video. The fourth (`Board.save`) is a plain correctness fix: an unchanged artifact must not invalidate downstream work.

**Tech Stack:** Python 3.11, pydantic v2, pytest, uv, mypy, ruff.

## Global Constraints

- Python 3.11+, managed with `uv`. Run everything from `services/local-api`.
- Typing strict (`uv run mypy src` must stay at "Success"). Never `Any` where a type is knowable.
- `uv run ruff check src tests` must pass. Do **not** run `ruff format` — this repo is not ruff-formatted (265 files would churn).
- No `print` in committed code — the project logger only.
- TDD: the failing test comes first and must be **seen** to fail for the stated reason.
- Conventional Commits. `git add` **explicit paths only** — never `-A` (a parallel Codex session works `services/ai-runtimes/`, `ai/runtime_*`, `api/ai_runtimes.py` in this same tree; never touch those).
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- Every default must preserve today's behaviour. German and the reel stay the defaults; an unmeasured language falls back to German's rate. No task may change what an existing board renders.
- Measured values are facts, not opinions — carry them into the code comments with their sample size.

---

## Why these four, and not the "demo genre"

An earlier version of this plan included "window durations punish held frames" and a demo-vs-reel genre. **Both were dropped: measurement refuted the premise.**

`_segment_duration_s` returns a *weight*, not a length. With a voice sidecar present, `_scale_chapter_durations(base, stretch_caps, chapter_audio_len)` rescales it, and the cap is `min(scene_duration_s, max(2.0, scene_duration_s - window.offset_s))` — the **scene**, not the window. Verified against the real functions:

| input | base | stretch cap | with a 20.2s chapter audio window |
|---|---|---|---|
| reviewer's 1.0s window, 45s scene | 2.0s | 45.0s | **20.2s** |
| hand-written 36.0s window, 45s scene | 36.0s | 41.0s | **20.2s** |

Identical. A chapter's video length **is** its share of the voice, so the script — not the window, not `target_seconds`, not the arc's "hook (2-3s)" — is what shapes the film. That reframes the whole problem and is why Task 2 exists.

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `src/laura/short_creator/production_tools.py` | speech-rate table + budget reporting | 1, 2 |
| `src/laura/short_creator/board.py` | artifact store; unchanged save must not invalidate | 3 |
| `src/laura/short_creator/production_orchestrator.py` | run result must distinguish "loop survived" from "video exists" | 4 |
| `tests/test_voice_rate.py` | **new** — the rate table and both budget directions | 1 |
| `tests/test_production_format.py` | existing home of the per-production-property tests | 2 |
| `tests/test_production_board.py` | existing board tests — helpers `_meta()`, `_storyline(thread="one app")`, `_script()`, `_cutlist()`; boards are built with `Board.create(tmp_path / "board", _meta())` | 3 |
| `tests/test_production_orchestrator.py` | existing orchestrator tests — helpers `_seed_scene(tmp_path) -> (db, asset_id)`, `_review(n)`, `_make_execute(script)` where `script` maps stage → `(status, weak)`; the fake execute does **not** touch the board | 4 |

Tasks 1+2 share a file and must run in order. Tasks 3 and 4 are independent of everything.

---

### Task 1: Speech rate follows the language

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_tools.py:701-717` (the constants and both budget functions), and `:1075-1126` (the `script_budget` tool)
- Test: `services/local-api/tests/test_voice_rate.py` (create)

**Interfaces:**
- Consumes: `board.meta().language` (a `str`, shipped in `d96c290`; "German" | "English" today, free text by contract)
- Produces: `seconds_per_word(language: str) -> float`, `estimate_voice_seconds(words: int, language: str = "German") -> float`, `word_budget_for(target_seconds: float, language: str = "German") -> int`. Task 2 calls `word_budget_for` with the board's language.

**The measurement this encodes:** German 0.58 s/word (fitted over four real syntheses of this project's scripts, ±20% spread). English 0.340 s/word (one synthesis: 308 words → 104.77s, hash-verified as that exact script). German is slower because its compounds are long words. One shared constant made `script_budget` ask an English author for 300 words where 174 seconds needed 512.

- [ ] **Step 1: Write the failing test**

Create `services/local-api/tests/test_voice_rate.py`:

```python
"""Speech rate is a property of the language, not a constant.

Measured on real ElevenLabs syntheses of this project's own scripts: German 0.58 s/word
(four scripts, +-20% spread), English 0.340 s/word (one script: 308 words -> 104.77s,
verified by script_hash). German runs slower because its compounds are long words. A single
shared constant made script_budget ask an English author for 300 words where 174 seconds
needed 512 — and the resulting film was half its target length.
"""

from __future__ import annotations

import pytest

from laura.short_creator.production_tools import (
    estimate_voice_seconds,
    seconds_per_word,
    word_budget_for,
)


def test_german_keeps_the_rate_that_shipped() -> None:
    assert seconds_per_word("German") == pytest.approx(0.58)


def test_english_is_measurably_faster() -> None:
    """1.7x faster — far outside the +-20% tolerance the single constant claimed."""
    assert seconds_per_word("English") == pytest.approx(0.340)
    assert seconds_per_word("English") < seconds_per_word("German") * 0.8


def test_an_unmeasured_language_falls_back_to_german() -> None:
    """Guessing a rate for a language nobody measured is worse than the shipped default."""
    assert seconds_per_word("Klingon") == seconds_per_word("German")


def test_the_english_budget_is_the_measured_one() -> None:
    """174s of English needs ~512 words; the old single constant said 300."""
    assert word_budget_for(174.0, "English") == 511
    assert word_budget_for(174.0, "German") == 300


def test_estimate_round_trips_against_the_live_measurement() -> None:
    """308 English words really did synthesize to 104.77s."""
    assert estimate_voice_seconds(308, "English") == pytest.approx(104.7, abs=0.5)


def test_both_budget_directions_agree() -> None:
    for language in ("German", "English"):
        words = word_budget_for(120.0, language)
        assert estimate_voice_seconds(words, language) == pytest.approx(120.0, abs=1.0)


def test_the_language_argument_defaults_to_german_so_old_callers_are_unchanged() -> None:
    assert word_budget_for(174.0) == word_budget_for(174.0, "German")
    assert estimate_voice_seconds(100) == estimate_voice_seconds(100, "German")
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd services/local-api && uv run pytest tests/test_voice_rate.py -q
```
Expected: FAIL — `ImportError: cannot import name 'seconds_per_word'`.

- [ ] **Step 3: Replace the constant with the table**

In `production_tools.py`, replace lines 701-717 (`_VOICE_SECONDS_PER_WORD` through the end of `word_budget_for`) with:

```python
# Seconds per spoken word, per language — measured on real ElevenLabs syntheses of this
# project's own scripts, never guessed. German: 0.58 (fitted over four scripts, +-20%
# spread). English: 0.340 (one script — 308 words -> 104.77s, verified by script_hash).
# German is slower because its compounds are long words: the same word count fills 1.7x the
# time. One shared constant made script_budget lie to an English author by that factor.
_SECONDS_PER_WORD: dict[str, float] = {"German": 0.58, "English": 0.340}
_DEFAULT_LANGUAGE = "German"
_VOICE_RATE_TOLERANCE = 0.20


def seconds_per_word(language: str) -> float:
    """The measured TTS rate for *language*.

    An unmeasured language falls back to German's rate: the pipeline shipped on it, and a
    made-up number would be worse than a known-wrong one nobody can audit.
    """
    return _SECONDS_PER_WORD.get(language, _SECONDS_PER_WORD[_DEFAULT_LANGUAGE])


def estimate_voice_seconds(words: int, language: str = _DEFAULT_LANGUAGE) -> float:
    """Roughly how long TTS speaks *words* in *language*. Good to about +/-20% —
    synthesize to know."""
    return words * seconds_per_word(language)


def word_budget_for(target_seconds: float, language: str = _DEFAULT_LANGUAGE) -> int:
    """A STARTING word count for *target_seconds* in *language*.

    Write to it ONCE, synthesize, then correct against the MEASURED ``voice_s`` — the rate
    varies +/-20% per script. Iterating the script by feel instead is what burned a whole
    job on 34 saves that never reached a render.
    """
    return max(0, int(target_seconds / seconds_per_word(language)))
```

- [ ] **Step 4: Run it and watch it pass**

```bash
cd services/local-api && uv run pytest tests/test_voice_rate.py -q
```
Expected: PASS (7 tests).

- [ ] **Step 5: Point script_budget at the board's language**

In `production_tools.py`'s `script_budget` tool (~line 1075), the budget currently uses the module constant. Change the word-count call to pass the board's language, and report which rate was used. Replace the `seconds_per_word`/`tolerance` entries of the returned dict:

```python
            language = board.meta().language
            ...
                "seconds_per_word": seconds_per_word(language),
                "language": language,
                "tolerance": _VOICE_RATE_TOLERANCE,
```

and the `words` value it computes must become `word_budget_for(material_seconds, language)` (find the existing `word_budget_for(...)` call in this tool and add the language argument). Update the tool's `"how"` string to name the language:

```python
                "how": (
                    "material_seconds is the sum of the reviewed windows this storyline "
                    f"references — the longest video worth cutting. Write about 'words' "
                    f"words of {language} total, then synthesize ONCE and correct from the "
                    f"measured voice_s; the rate is only good to "
                    f"+/-{int(_VOICE_RATE_TOLERANCE * 100)}%."
                ),
```

- [ ] **Step 6: Run the whole suite + types + lint**

```bash
cd services/local-api && uv run pytest -q && uv run mypy src && uv run ruff check src tests
```
Expected: pytest exit 0, "Success: no issues found in 197 source files", "All checks passed!".

Any other test that imported `_VOICE_SECONDS_PER_WORD` encodes the old spec — update it deliberately and say so in the commit.

- [ ] **Step 7: Commit**

```bash
git add services/local-api/src/laura/short_creator/production_tools.py \
        services/local-api/tests/test_voice_rate.py
git commit -F - << 'EOF'
fix(short-creator): measure the speech rate per language

script_budget told an English author to write 300 words for 174 seconds. The synthesis
came back at 104.8 — barely half the film. The number was not wrong, it was German:
0.58 s/word, fitted over four German scripts, applied to a language that runs 0.340.
German compounds are long words; the same count fills 1.7x the time.

So the rate becomes a table keyed by the board's language, and script_budget reads it
from there. German keeps its value and stays the fallback for any language nobody has
measured — a made-up rate would be worse than a known one.

The English figure is ONE synthesis (308 words -> 104.77s, verified by script_hash). It
is a measurement, not a law; the +-20% tolerance still applies and the charter still
says correct from the measured voice_s.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 2: The script's shortfall becomes visible

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_tools.py` (the `get_script` tool)
- Test: `services/local-api/tests/test_production_format.py` (append)

**Interfaces:**
- Consumes: `word_budget_for(target, language)` and `seconds_per_word(language)` from Task 1; `board.meta().language`; `board.meta().target_seconds`
- Produces: `get_script()` returns the existing keys plus `words: int`, `budget_words: int`, `estimated_voice_s: float`, `shortfall_pct: float`

**Why:** the agent wrote 140 words against a 300-word budget and nothing anywhere compared the two. Since a chapter's video length **is** its share of the voice (see the note above the File Structure), that shortfall alone halved the film. `get_script` is the tool the roster already calls to verify its own work — it is the honest place for the number, and it costs one comparison.

- [ ] **Step 1: Write the failing test**

Append to `services/local-api/tests/test_production_format.py`:

```python
# --- the script's own shortfall must be visible where the author checks its work ---------
# Live finding: the scene_author wrote 140 words against a 300-word budget and called
# get_script to verify. Nothing compared the two, so nothing said the film would come out
# at half its target. A chapter's video length IS its share of the voice, so a short script
# is a short film — this is the number that decides the whole shape.


def test_get_script_reports_the_gap_between_what_was_written_and_the_budget(
    tmp_path: Path,
) -> None:
    from laura.short_creator.board import Board
    from laura.short_creator.board_models import Script, ScriptLine
    from laura.short_creator.production_tools import build_production_tool_specs

    board = Board.create(
        tmp_path / "b",
        BoardMeta(
            session_id="s1",
            asset_id="a1",
            created_utc="2026-07-17T00:00:00+00:00",
            task="demo",
            format="x",
            language="English",
            target_seconds=174.0,
        ),
    )
    board.save("script", Script(language="English", lines=[ScriptLine(
        chapter=1, scene_number=1, text=" ".join(["word"] * 140)
    )]))

    specs = {s.name: s for s in build_production_tool_specs(_db(tmp_path), board, asset_id="a1")}
    out = specs["get_script"].func()

    assert out["words"] == 140
    assert out["budget_words"] == 511  # 174s of English at the measured 0.340 s/word
    assert out["estimated_voice_s"] == pytest.approx(47.6, abs=0.5)
    assert out["shortfall_pct"] == pytest.approx(72.6, abs=1.0)
```

This test needs a `_db` helper and a `Path` import. Reuse the pattern already in `tests/test_production_tools_render.py` (`_seed_two_scenes` builds a real db in a tmp_path); if a bare db is enough here, use:

```python
def _db(tmp_path: Path) -> Database:
    from laura.config import Settings
    from laura.db.database import create_database

    return create_database(Settings(workspace_root=tmp_path))
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd services/local-api && uv run pytest tests/test_production_format.py -k get_script -q
```
Expected: FAIL — `KeyError: 'words'`.

- [ ] **Step 3: Add the numbers to get_script**

In `production_tools.py`, find the `get_script` tool and add to the dict it returns (keep every existing key):

```python
            language = board.meta().language
            words = len(script_text(script.lines).split())
            budget = word_budget_for(board.meta().target_seconds, language)
            # A chapter's video length is its share of the voice, so a short script is a
            # short film. The author calls this tool to verify itself — the gap belongs here.
            shortfall = 0.0 if budget <= 0 else max(0.0, (budget - words) / budget * 100.0)
            ...
                "words": words,
                "budget_words": budget,
                "estimated_voice_s": round(estimate_voice_seconds(words, language), 1),
                "shortfall_pct": round(shortfall, 1),
```

- [ ] **Step 4: Run it and watch it pass**

```bash
cd services/local-api && uv run pytest tests/test_production_format.py -q
```
Expected: PASS.

- [ ] **Step 5: Tell the author to read it**

In `production_agents.py`, the `scene_author`'s system message ends with "Verify with get_script once every chapter in the storyline has its lines written." Extend that sentence:

```python
                "Verify with get_script once every chapter in the storyline has its "
                "lines written — it reports words against budget_words and a "
                "shortfall_pct; a shortfall above 15% means the film comes out that much "
                "shorter than its target, so write the missing words BEFORE the voice is "
                "synthesized. "
```

- [ ] **Step 6: Full suite + types + lint**

```bash
cd services/local-api && uv run pytest -q && uv run mypy src && uv run ruff check src tests
```
Expected: pytest exit 0, mypy Success, ruff passed.

Note: `tests/test_production_format.py` asserts the scene_author's system message elsewhere (Task from `d96c290`). Re-read those assertions — extending the message must not break them.

- [ ] **Step 7: Commit**

```bash
git add services/local-api/src/laura/short_creator/production_tools.py \
        services/local-api/src/laura/short_creator/production_agents.py \
        services/local-api/tests/test_production_format.py
git commit -F - << 'EOF'
feat(short-creator): show the script its own shortfall

The author wrote 140 words against a 300-word budget, called get_script to verify, and
got back a script that looked fine. Nothing compared the two numbers, anywhere.

That single gap decided the whole film. A chapter's video length is its share of the
voice — _scale_chapter_durations rescales the segments to the chapter's audio window,
and the review windows are only weights inside it. So the script is the shape of the
film, and half a script is half a film. The reviewer's window lengths, which look like
the culprit, are provably not: a 1s window and a 36s window in the same 45s scene both
come out at 20.2s once the voice sidecar exists.

get_script now reports words, budget_words, estimated_voice_s and shortfall_pct, and the
roster is told to close a gap above 15% before synthesis.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 3: An unchanged save must not destroy downstream work

**Files:**
- Modify: `services/local-api/src/laura/short_creator/board.py:122-143` (`Board.save`)
- Test: `services/local-api/tests/test_production_board.py` (append)

**Interfaces:**
- Consumes: nothing new
- Produces: `Board.save(name, artifact)` unchanged signature; returns the **current** version without archiving or invalidating when the artifact's content equals what is stored.

**Why:** `save()` calls `self.invalidate(name)` unconditionally, which archives and deletes every downstream artifact. In one live run the agent re-saved upstream artifacts three times; each time the chain below was wiped and rebuilt, and the run ended out of turns with only `voice` on the board — no render. Re-saving *changed* content SHOULD invalidate; that is the board's whole contract. Re-saving *identical* content invalidates nothing in truth, so it must not in code.

- [ ] **Step 1: Write the failing test**

Append to `services/local-api/tests/test_production_board.py` (its existing helpers `_meta`, `_storyline`, `_script`, `_cutlist` are already imported there; `_storyline` takes the red thread as its argument):

```python
def test_saving_identical_content_does_not_invalidate_downstream(tmp_path: Path) -> None:
    """Live finding: an agent re-saved upstream artifacts three times in one run. Each save
    wiped the chain below, it rebuilt, and the turn budget ran out with only voice on the
    board and no render. A save that changes nothing has made nothing stale."""
    board = Board.create(tmp_path / "board", _meta())
    board.save("storyline", _storyline("one app"))
    board.save("script", _script())
    board.save("cutlist", _cutlist())

    version = board.save("storyline", _storyline("one app"))  # identical content

    assert board.load("script") is not None, "an unchanged storyline must not drop the script"
    assert board.load("cutlist") is not None
    assert version == 1, "an unchanged save keeps the current version — it is not a new one"
    assert board.versions("storyline") == [], "and it archives nothing"


def test_saving_changed_content_still_invalidates_downstream(tmp_path: Path) -> None:
    """The contract that matters survives untouched: a real change makes downstream stale."""
    board = Board.create(tmp_path / "board", _meta())
    board.save("storyline", _storyline("one app"))
    board.save("script", _script())

    version = board.save("storyline", _storyline("a different thread"))

    assert board.load("script") is None, "a changed storyline must drop the script"
    assert version == 2
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd services/local-api && uv run pytest tests/test_production_board.py -k identical -q
```
Expected: FAIL — `assert board.load("script") is not None` fails; the identical re-save wiped it.

- [ ] **Step 3: Make an unchanged save a no-op**

In `board.py`, add above `class Board`:

```python
def _same_content(a: BaseModel, b: BaseModel) -> bool:
    """Do two artifacts carry the same content? ``version`` is bookkeeping, not content."""
    return a.model_dump(exclude={"version"}) == b.model_dump(exclude={"version"})
```

and in `Board.save`, replace the archive block:

```python
        path = self.root / f"{name}.json"
        current_version = 0
        if path.is_file():
            old = model_type.model_validate_json(path.read_text(encoding="utf-8"))
            old_versioned = cast(_Versioned, old)
            current_version = int(old_versioned.version)
            # A save that changes nothing has made nothing downstream stale. Re-saving an
            # identical artifact used to wipe the whole chain below it and force a rebuild —
            # a live run burned its turn budget doing exactly that three times over.
            if _same_content(old, artifact):
                return current_version
            self._archive(name, current_version, path)
```

- [ ] **Step 4: Run it and watch it pass**

```bash
cd services/local-api && uv run pytest tests/test_production_board.py -q
```
Expected: PASS. `test_save_invalidates_downstream` and `test_singleton_save_load_and_version_stamp` must stay green untouched — both already save *changed* content (`_storyline("changed")`, `_storyline("v2 thread")`), which is exactly the path this task leaves alone.

- [ ] **Step 5: Full suite + types + lint**

```bash
cd services/local-api && uv run pytest -q && uv run mypy src && uv run ruff check src tests
```
Expected: pytest exit 0, mypy Success, ruff passed.

- [ ] **Step 6: Commit**

```bash
git add services/local-api/src/laura/short_creator/board.py \
        services/local-api/tests/test_board.py
git commit -F - << 'EOF'
fix(short-creator): an unchanged save must not wipe the chain below it

Board.save invalidated downstream unconditionally. In one live run the agent re-saved
upstream artifacts three times; each save archived and deleted everything below, the
chain rebuilt, and the run ended out of turns with only voice on the board and no
render. The prompt had told it not to — twice. Prompts do not enforce invariants.

Invalidation on change is the board's contract and stays exactly as it was. Invalidation
on NO change was never anything but damage: an artifact whose content is identical has
made nothing stale. So an identical save now returns the current version and touches
nothing.

This does not make the loop disciplined — a re-save with genuinely new content (a fresh
voice mp3, say) still invalidates, correctly. It removes the case where the damage was
pure waste.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 4: "ok" must not mean "the loop survived"

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_orchestrator.py:309-321` (the result dict)
- Test: `services/local-api/tests/test_production_orchestrator.py` (append)

**Interfaces:**
- Consumes: `board.resume_point(expected_scenes)` — already computed on line 320; returns `"done"` when every artifact in the chain exists
- Produces: the run result dict gains `complete: bool`. `ok` keeps its current meaning (the agent loop did not hard-fail) and its current value — nothing that reads `ok` today changes.

**Why:** a live run returned `{"ok": true, "weak": true, "export_id": null, "resume_point": "script"}`. Every field was accurate and the whole was misleading: `ok` reports the loop's status, not the production's. A caller — the API, a job log, a person — reads `ok: true` as "there is a video". There was no video. The board already knows the difference; the result just never said it.

- [ ] **Step 1: Write the failing test**

Append to `services/local-api/tests/test_production_orchestrator.py`. Its `_make_execute` fake returns a `StageOutcome` **without touching the board**, which is exactly what this test needs: the board state is set up directly, and the fake only decides the loop's own status.

```python
def test_a_run_that_stopped_early_reports_ok_but_not_complete(tmp_path: Path) -> None:
    """Live finding: ok=true, weak=true, export_id=null, resume_point="script". Every field
    was accurate and the whole was misleading — ok is the LOOP's status, not the
    production's. A caller reads ok=true as "there is a video". There was none."""
    db, asset_id = _seed_scene(tmp_path)
    execute, _calls = _make_execute({"A": ("ok", True)})

    result = production_orchestrator.run_production(
        db,
        _config(),
        asset_id=asset_id,
        session_id="sess_early",
        task="demo",
        execute=execute,
    )

    assert result["ok"] is True, "the loop really did survive — that meaning is unchanged"
    assert result["complete"] is False, "but nothing was produced, and the result must say so"
    assert result["export_id"] is None


def test_a_finished_board_reports_complete(tmp_path: Path) -> None:
    """resume_point already knew; the result just never asked."""
    db, asset_id = _seed_scene(tmp_path)
    root = production_orchestrator.board_root_for(db, asset_id, "sess_done")
    board = Board.create(root, _meta_for(asset_id, "sess_done"))
    board.save_scene_review(_review(1))
    for name, artifact in _full_chain():
        board.save(name, artifact)
    execute, _calls = _make_execute({"A": ("ok", False)})

    result = production_orchestrator.run_production(
        db,
        _config(),
        asset_id=asset_id,
        session_id="sess_done",
        task="demo",
        execute=execute,
    )

    assert result["complete"] is True
    assert result["resume_point"] == "done"
```

`_config()`, `_meta_for()` and `_full_chain()` are **not** guaranteed to exist under those names. Before writing this test, read `tests/test_production_orchestrator.py` and reuse whatever it already has for (a) building an `AgentConfig`, (b) creating a board for a session, and (c) filling the chain — several of its existing tests (`test_task_text_contains_contract_and_resume`, the resume tests) must already do all three. Use the real names; do not add helpers that duplicate them.

- [ ] **Step 2: Run it and watch it fail**

```bash
cd services/local-api && uv run pytest tests/test_production_orchestrator.py -k complete -q
```
Expected: FAIL — `KeyError: 'complete'`.

- [ ] **Step 3: Say it in the result**

In `production_orchestrator.py`, replace the return block (lines 309-321):

```python
    resume_point = board.resume_point(expected_scenes)
    return {
        # ok is the agent LOOP's status: it ran without a hard failure. It does not mean a
        # video exists — a live run reported ok=True with export_id=None and half a board.
        # complete is the production's status; the board always knew, the result never said.
        "ok": outcome.status == "ok",
        "complete": resume_point == "done",
        "status": outcome.status,
        "stage": outcome.stage,
        "team": outcome.team,
        "weak": outcome.weak,
        "escalated": escalated,
        "summary": outcome.summary,
        "session_id": session_id,
        "board": board.status(),
        "export_id": export_id,
        "resume_point": resume_point,
    }
```

Note this reuses the `resume_point` local instead of calling `board.resume_point` a second time — the old code called it inline in the dict.

- [ ] **Step 4: Run it and watch it pass**

```bash
cd services/local-api && uv run pytest tests/test_production_orchestrator.py -q
```
Expected: PASS.

- [ ] **Step 5: Full suite + types + lint**

```bash
cd services/local-api && uv run pytest -q && uv run mypy src && uv run ruff check src tests
```
Expected: pytest exit 0, mypy Success, ruff passed.

Any test asserting the exact key set of the result dict encodes the old spec — update it deliberately.

- [ ] **Step 6: Commit**

```bash
git add services/local-api/src/laura/short_creator/production_orchestrator.py \
        services/local-api/tests/test_production_orchestrator.py
git commit -F - << 'EOF'
feat(short-creator): report whether the run actually produced a video

A live run returned ok=true, weak=true, export_id=null, resume_point="script". Every
field was accurate. The whole was misleading: ok is the agent loop's status — it did not
hard-fail — and says nothing about whether a production finished. Read by a caller, a job
log, or a person, ok=true means "there is a video". There was no video.

The board already knew: resume_point returns "done" only when every artifact in the chain
exists. The result just never asked. It does now, as complete, alongside an unchanged ok.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

## Verification (after all four)

Not a task — the gate before this is called done.

- [ ] Full suite green from `services/local-api`: `uv run pytest -q` → exit 0
- [ ] `uv run mypy src` → "Success: no issues found in 197 source files"
- [ ] `uv run ruff check src tests` → "All checks passed!"
- [ ] **Live**: re-run the existing English board (session `3d7fa5c317b341528dc3250401d7d3df`, asset `3a098e6b54b543c19488a4185bde15cf`, format `x`, language English, target 180) from `script`, and confirm from the run result and the board:
  - `script_budget` reports `seconds_per_word: 0.34` and `language: "English"` (Task 1)
  - `get_script` on the shipped 308-word script reports `budget_words: 511` and a `shortfall_pct` around 40 — the honest statement that 308 words cannot fill 180 seconds of English (Task 2)
  - the run result carries `complete: true` only when an `export_id` is present (Task 4)
- [ ] The four commits are on `feat/generate-ui`, each with explicit paths, nothing from `services/ai-runtimes/`, `ai/runtime_*` or `api/ai_runtimes.py` swept in.

## What this plan does NOT fix

State it plainly rather than let it be discovered later:

- **The loop is still not disciplined.** Task 3 removes the pure-waste case (identical re-saves). An agent that re-saves genuinely changed content still invalidates downstream, correctly, and can still thrash its 30 turns away. Fixing that needs either a turn-aware charter or a structural change to who may call `save` — neither is designed yet.
- **`hook_score` still rewards motion.** The reviewer scored a held code screen — the single strongest beat in the user's footage — a 3, and the story_architect reads that. It is real, but it is judgment, not a constant, and no measurement in this session shows it changed the delivered film.
- **The English rate rests on one synthesis.** 0.340 s/word is n=1. A second English script will improve it. The ±20% tolerance and the "correct from the measured voice_s" charter still carry the risk.
