# Provenance Chain + Full-Suffix Restore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every derived board artifact records the content hashes of what it was built from (`parents`), and a resume restores the longest archived suffix whose parents match the board — up to and including the QA verdict.

**Architecture:** One generic identity function (`content_hash` = sha256 over canonical JSON minus `version`) in `board_models.py`; a `parents: dict[str, str]` field on all six derived artifacts, stamped at their write sites in `production_tools.py`; a `Board.restore_coherent_suffix()` walk in `board.py` called by `run_production` right after the board opens; `Board.status()` staleness generalized to parents. Spec: `docs/superpowers/specs/2026-07-20-provenance-chain-design.md`.

**Tech Stack:** Python 3.11 + uv, pydantic v2, pytest, mypy strict, ruff (line length 100).

## Global Constraints

- Python via `uv` in `services/local-api/`; run tests as `uv run pytest …`, gates are `uv run mypy src` (must print "Success") and `uv run ruff check src tests` (must print "All checks passed!").
- **Never run two `uv run` commands in parallel in this repo** (known DB/venv contention); judge pytest by exit code, not by grepping "passed" (the suite reproducibly swallows the final line on this machine).
- mypy is strict: annotate everything, no `Any` leaks in signatures; no `print` — module logger only.
- **Old boards must keep loading**: every new model field needs a default (`parents: dict[str, str] = Field(default_factory=dict)`); never add validation that rejects previously-written JSON.
- Canonical JSON for hashing is exactly: `json.dumps(artifact.model_dump(mode="json", exclude={"version"}), sort_keys=True, ensure_ascii=False)`.
- The parents mapping is exactly (spec §2): `script:{storyline}`, `voice:{storyline, script}`, `cutlist:{storyline, script, voice}`, `contact_sheet:{cutlist}`, `render_report:{storyline, script, voice, cutlist}`, `qa_report:{render_report}`. `storyline` is a root: no parents, never auto-restored.
- Empty `parents` means unknown (pre-provenance board or root) and must NEVER be treated as coherent/restorable.
- Commits: conventional commits, English, explicit `git add <paths>` (never `-A` — the branch can carry unrelated WIP), commit trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- All commands below run from `services/local-api/` unless a path says otherwise.

---

### Task 1: `content_hash` + `parents` fields (board_models)

**Files:**
- Modify: `services/local-api/src/laura/short_creator/board_models.py`
- Test: `services/local-api/tests/test_content_hash.py` (create)

**Interfaces:**
- Consumes: existing models `Script`, `VoiceArtifact`, `Cutlist`, `ContactSheet`, `RenderReport`, `QaReport` (all in `board_models.py`).
- Produces: `content_hash(artifact: BaseModel) -> str` (module function in `board_models.py`); field `parents: dict[str, str]` (default `{}`) on the six models above. Later tasks import `content_hash` from `laura.short_creator.board_models`.

- [ ] **Step 1: Write the failing tests**

Create `services/local-api/tests/test_content_hash.py`:

```python
"""One identity for every artifact: content is what it says, version is bookkeeping.

The review-killed restore keyed a render's identity on script TEXT alone; a render is
equally a projection of the concrete cutlist and the concrete voice. content_hash gives every
artifact a single canonical identity so derived artifacts can record exactly which parent
instances they were built from (spec 2026-07-20-provenance-chain-design.md §1).
"""

from __future__ import annotations

from laura.short_creator.board_models import (
    QaReport,
    Script,
    ScriptLine,
    VoiceArtifact,
    content_hash,
)


def _script(text: str) -> Script:
    return Script(language="English", lines=[ScriptLine(chapter=1, scene_number=1, text=text)])


def test_same_content_same_hash_across_versions() -> None:
    """The motivating case: revise A -> B -> back to A must match A's original hash."""
    v1 = _script("the rendered line").model_copy(update={"version": 1})
    v3 = _script("the rendered line").model_copy(update={"version": 3})

    assert content_hash(v1) == content_hash(v3)


def test_different_content_different_hash() -> None:
    assert content_hash(_script("line a")) != content_hash(_script("line b"))


def test_a_new_synthesis_of_the_same_text_is_a_different_voice() -> None:
    """The distinction the killed restore lacked: the cutlist cut against THIS mp3."""
    take_1 = VoiceArtifact(script_hash="h", mp3_path="voiceovers/aaa.mp3")
    take_2 = VoiceArtifact(script_hash="h", mp3_path="voiceovers/bbb.mp3")

    assert content_hash(take_1) != content_hash(take_2)


def test_hash_is_deterministic_across_calls() -> None:
    s = _script("stable")
    assert content_hash(s) == content_hash(s)


def test_parents_defaults_empty_so_old_boards_still_load() -> None:
    """Pre-provenance JSON has no parents key — the default keeps it loading."""
    raw = '{"version": 2, "verdict": "ship", "findings": []}'
    loaded = QaReport.model_validate_json(raw)

    assert loaded.parents == {}


def test_parents_roundtrip() -> None:
    qa = QaReport(verdict="ship", findings=[], parents={"render_report": "abc123"})
    again = QaReport.model_validate_json(qa.model_dump_json())

    assert again.parents == {"render_report": "abc123"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_content_hash.py -q`
Expected: ImportError — `cannot import name 'content_hash'`.

- [ ] **Step 3: Implement**

In `board_models.py`, next to the existing `script_hash` function, add:

```python
def content_hash(artifact: BaseModel) -> str:
    """sha256 over the canonical JSON of ``model_dump(exclude={"version"})``.

    One identity for every artifact: content is what it says, version is bookkeeping. A
    script revised A -> B -> back to A hashes like A again (the restore's motivating case),
    while a re-synthesized mp3 of the same text hashes differently (unique path) — the
    cutlist cut against THAT voice, which is exactly the distinction the review-killed
    restore lacked.
    """
    canonical = json.dumps(
        artifact.model_dump(mode="json", exclude={"version"}),
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

(`json` needs importing at the top of `board_models.py`; `hashlib` is already imported there.)

Then add to EACH of `Script`, `VoiceArtifact`, `Cutlist`, `ContactSheet`, `RenderReport`, `QaReport` (NOT `Storyline`, NOT `SceneReview`) the field:

```python
    # Which parent artifact instances this was built from: chain name -> content_hash of the
    # parent AS IT WAS at build time. Empty = pre-provenance board (unknown, never coherent).
    parents: dict[str, str] = Field(default_factory=dict)
```

- [ ] **Step 4: Run the new tests**

Run: `uv run pytest tests/test_content_hash.py -q`
Expected: exit 0.

- [ ] **Step 5: Full gates**

Run: `uv run pytest -q` then `uv run mypy src` then `uv run ruff check src tests`
Expected: exit 0 / "Success: no issues found" / "All checks passed!". (Existing exact-equality
dict tests do not touch `parents`, so nothing else should break; if a `model_dump` comparison
test fails, it compares full dumps — update that test to include `"parents": {}`.)

- [ ] **Step 6: Commit**

```bash
git add src/laura/short_creator/board_models.py tests/test_content_hash.py
git commit -m "feat(short-creator): one content identity per artifact, and room to record parents

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: parents-based staleness in `Board.status()`

**Files:**
- Modify: `services/local-api/src/laura/short_creator/board.py` (function `_is_stale` and its call in `status()`)
- Test: `services/local-api/tests/test_chain_coherence.py` (append)

**Interfaces:**
- Consumes: `content_hash` from Task 1; existing `_is_stale(artifact, current_script_hash)` and `status()` in `board.py`.
- Produces: module function `_parents_stale(load: Callable[[str], BaseModel | None], artifact: BaseModel) -> bool | None` in `board.py`; `status()` prefers it whenever the artifact's `parents` is non-empty.

- [ ] **Step 1: Write the failing tests**

Append to `services/local-api/tests/test_chain_coherence.py`:

```python
# --- staleness generalizes to parents: any drifted parent makes the artifact stale ---------
# script_hash-based staleness only saw the script. With parents, a reverted VOICE (script
# unchanged) correctly marks the render stale — the case the review proved script_hash-based
# checks could never see.


def test_parents_all_matching_reports_fresh(tmp_path: Path) -> None:
    from laura.short_creator.board_models import content_hash

    board = _board(tmp_path)
    script = _script("the rendered line")
    board.save("script", script)
    current_script = board.load("script")
    assert current_script is not None
    board.save(
        "render_report",
        RenderReport(
            export_id="e1",
            video_s=100.0,
            width=1920,
            height=1080,
            parents={"script": content_hash(current_script)},
        ),
    )

    assert board.status()["artifacts"]["render_report"]["stale"] is False


def test_a_single_drifted_parent_reports_stale(tmp_path: Path) -> None:
    from laura.short_creator.board_models import content_hash

    board = _board(tmp_path)
    board.save("script", _script("the rendered line"))
    old = board.load("script")
    assert old is not None
    old_hash = content_hash(old)
    render = RenderReport(
        export_id="e1",
        video_s=100.0,
        width=1920,
        height=1080,
        parents={"script": old_hash},
    )
    board.save("render_report", render)
    # The script moves on; put the render back the way the cap guard does.
    board.save("script", _script("a different line"))
    board.revert("render_report", 1)

    assert board.status()["artifacts"]["render_report"]["stale"] is True


def test_a_missing_parent_reports_unknown(tmp_path: Path) -> None:
    board = _board(tmp_path)
    board.save(
        "render_report",
        RenderReport(
            export_id="e1",
            video_s=100.0,
            width=1920,
            height=1080,
            parents={"cutlist": "somehash"},
        ),
    )

    assert board.status()["artifacts"]["render_report"]["stale"] is None


def test_empty_parents_falls_back_to_script_hash_logic(tmp_path: Path) -> None:
    """Old boards keep the behaviour they shipped with — no parents, script_hash decides."""
    board = _board(tmp_path)
    script = _script("the line that was rendered")
    board.save("script", script)
    board.save("render_report", _render(script_hash_=script_hash(script.lines)))

    assert board.status()["artifacts"]["render_report"]["stale"] is False
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `uv run pytest tests/test_chain_coherence.py -q`
Expected: exit 1 — `test_a_single_drifted_parent_reports_stale` and
`test_a_missing_parent_reports_unknown` fail (parents are ignored today, so the drifted-voice
case reads from `script_hash` and the missing-parent case reads `False`/`None` wrongly).
`test_parents_all_matching_reports_fresh` may pass by accident via its script_hash default ""
→ the assert on `False` fails too (stale is None). Confirm which fail; at least two must.

- [ ] **Step 3: Implement**

In `board.py`, below `_is_stale`, add (and import `Callable` from `collections.abc` plus
`content_hash` in the existing `board_models` import block):

```python
def _parents_stale(
    load: Callable[[str], BaseModel | None], artifact: BaseModel
) -> bool | None:
    """Staleness via the parents chain: any drifted parent means stale.

    True — at least one recorded parent is present and its content hash differs.
    False — every recorded parent is present and matches.
    None — at least one recorded parent is missing (nothing to compare against).
    Only meaningful for artifacts with non-empty ``parents``; callers gate on that.
    """
    parents = getattr(artifact, "parents", None)
    if not isinstance(parents, dict) or not parents:
        return None
    saw_missing = False
    for name, recorded in parents.items():
        current = load(name)
        if current is None:
            saw_missing = True
            continue
        if content_hash(current) != recorded:
            return True
    return None if saw_missing else False
```

In `status()`, replace the current stale computation for each artifact:

```python
            if hasattr(cur, "script_hash") or bool(getattr(cur, "parents", None)):
                parents_verdict = _parents_stale(self.load, cur) if cur is not None else None
                if getattr(cur, "parents", None):
                    entry["stale"] = parents_verdict
                else:
                    entry["stale"] = _is_stale(cur, current_hash)
```

(Exact splice point: today the block reads `if hasattr(cur, "script_hash"): entry["stale"] =
_is_stale(cur, current_hash)` — replace that block with the above. A drifted parent must WIN
over a matching script_hash, which the `parents`-first ordering guarantees.)

- [ ] **Step 4: Run the coherence tests**

Run: `uv run pytest tests/test_chain_coherence.py -q`
Expected: exit 0 — including all pre-existing script_hash-based tests (fallback intact).

- [ ] **Step 5: Full gates**

Run: `uv run pytest -q`, `uv run mypy src`, `uv run ruff check src tests`
Expected: exit 0 / Success / All checks passed.

- [ ] **Step 6: Commit**

```bash
git add src/laura/short_creator/board.py tests/test_chain_coherence.py
git commit -m "feat(short-creator): staleness follows the parents chain when it exists

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `Board.restore_coherent_suffix()`

**Files:**
- Modify: `services/local-api/src/laura/short_creator/board.py`
- Test: `services/local-api/tests/test_restore_suffix.py` (create)

**Interfaces:**
- Consumes: `content_hash` (Task 1), existing `Board.load/versions/revert`, `_CHAIN`, `_SINGLETONS`.
- Produces: `Board.restore_coherent_suffix(self) -> list[str]` — restored chain names in chain order; `[]` when nothing was restored. Task 5 calls it.

- [ ] **Step 1: Write the failing tests**

Create `services/local-api/tests/test_restore_suffix.py`:

```python
"""The full-suffix restore: bring back the longest archived suffix the board still matches.

Successor to the review-killed single-link restore (removed in 41ecc51). That one keyed a
render's identity on script text alone and resurrected a film cut from an abandoned cutlist
as stale=False — reproduced live. This walk keys every link on the content hashes of the
exact parent INSTANCES it was built from (spec 2026-07-20-provenance-chain-design.md §3), so
the killer case inverts into a feature: reverting the cutlist brings back ITS OWN render.
"""

from __future__ import annotations

from pathlib import Path

from laura.short_creator.board import Board
from laura.short_creator.board_models import (
    BoardMeta,
    ContactSheet,
    ContactSheetTile,
    Cutlist,
    CutSegment,
    QaReport,
    RenderReport,
    Script,
    ScriptLine,
    VoiceArtifact,
    content_hash,
)


def _board(tmp_path: Path) -> Board:
    return Board.create(
        tmp_path / "board",
        BoardMeta(
            session_id="s1",
            asset_id="a1",
            created_utc="2026-07-20T00:00:00+00:00",
            task="demo",
            language="English",
            target_seconds=174.0,
        ),
    )


def _script(text: str) -> Script:
    return Script(language="English", lines=[ScriptLine(chapter=1, scene_number=1, text=text)])


def _cut(order0_end: int = 240) -> Cutlist:
    return Cutlist(
        segments=[CutSegment(order=0, scene_number=1, start_frame=0, end_frame_exclusive=order0_end)]
    )


def _sheet() -> ContactSheet:
    return ContactSheet(
        png_path="s.png",
        cols=1,
        rows=1,
        tiles=[ContactSheetTile(order=0, scene_number=1, frame=100, label="1")],
    )


def _seed_full_chain(board: Board, text: str) -> None:
    """script -> voice -> cutlist -> sheet -> render -> qa, each stamping parents directly.

    Deliberately stamps SUBSETS of the spec table (no storyline on this board): the walk is
    generic over whatever parents an artifact recorded — it checks exactly those. The full
    spec-table stamps are asserted at the write sites (Task 4), where they are produced.
    """
    board.save("script", _script(text))
    script = board.load("script")
    assert script is not None
    voice = VoiceArtifact(
        script_hash="cache-key",
        mp3_path=f"voiceovers/{text[:8]}.mp3",
        parents={"script": content_hash(script)},
    )
    board.save("voice", voice)
    cur_voice = board.load("voice")
    assert cur_voice is not None
    board.save(
        "cutlist",
        _cut().model_copy(
            update={"parents": {"script": content_hash(script), "voice": content_hash(cur_voice)}}
        ),
    )
    cur_cut = board.load("cutlist")
    assert cur_cut is not None
    board.save(
        "contact_sheet", _sheet().model_copy(update={"parents": {"cutlist": content_hash(cur_cut)}})
    )
    board.save(
        "render_report",
        RenderReport(
            export_id=f"e-{text[:8]}",
            video_s=100.0,
            width=1920,
            height=1080,
            parents={"voice": content_hash(cur_voice), "cutlist": content_hash(cur_cut)},
        ),
    )
    cur_render = board.load("render_report")
    assert cur_render is not None
    board.save(
        "qa_report",
        QaReport(verdict="ship", findings=[], parents={"render_report": content_hash(cur_render)}),
    )


def test_revise_and_revert_back_revives_the_whole_suffix_including_qa(tmp_path: Path) -> None:
    """The motivating case, end to end: A -> B -> back to A brings everything back."""
    board = _board(tmp_path)
    _seed_full_chain(board, "the rendered line")
    board.save("script", _script("a different draft"))
    board.save("script", _script("the rendered line"))
    assert board.load("voice") is None and board.load("qa_report") is None

    restored = board.restore_coherent_suffix()

    assert restored == ["voice", "cutlist", "contact_sheet", "render_report", "qa_report"]
    render = board.load("render_report")
    assert isinstance(render, RenderReport) and render.export_id == "e-the rend"
    assert board.resume_point([]) == "done"


def test_reverting_the_cutlist_brings_back_its_own_render(tmp_path: Path) -> None:
    """The review's killer case, inverted into a feature."""
    board = _board(tmp_path)
    _seed_full_chain(board, "one stable script")
    # A second cutlist round on the SAME script/voice, with its own sheet/render/qa.
    script = board.load("script")
    voice = board.load("voice")
    assert script is not None and voice is not None
    board.save(
        "cutlist",
        _cut(order0_end=480).model_copy(
            update={"parents": {"script": content_hash(script), "voice": content_hash(voice)}}
        ),
    )
    cut_v2 = board.load("cutlist")
    assert cut_v2 is not None
    board.save(
        "render_report",
        RenderReport(
            export_id="e-second-cut",
            video_s=200.0,
            width=1920,
            height=1080,
            parents={"voice": content_hash(voice), "cutlist": content_hash(cut_v2)},
        ),
    )
    # The user goes back to cutlist v1 (documented follow-up flow) — sheet/render/qa wiped.
    board.revert("cutlist", 1)
    assert board.load("render_report") is None

    restored = board.restore_coherent_suffix()

    assert "render_report" in restored
    render = board.load("render_report")
    assert isinstance(render, RenderReport)
    assert render.export_id == "e-one stab", "cutlist v1's OWN render, not the second cut's"


def test_partial_suffix_stops_at_the_first_unmatched_link(tmp_path: Path) -> None:
    """Voice matches, cutlist does not (voice was re-synthesized) -> only voice returns."""
    board = _board(tmp_path)
    _seed_full_chain(board, "steady text")
    script = board.load("script")
    assert script is not None
    # Re-synthesize: new mp3, same script -> cutlist's recorded voice hash no longer matches.
    board.save(
        "voice",
        VoiceArtifact(
            script_hash="cache-key",
            mp3_path="voiceovers/retake.mp3",
            parents={"script": content_hash(script)},
        ),
    )
    board.save("script", _script("elsewhere"))
    board.save("script", _script("steady text"))
    assert board.load("voice") is None

    restored = board.restore_coherent_suffix()

    assert restored == ["voice"]
    voice = board.load("voice")
    assert isinstance(voice, VoiceArtifact)
    assert voice.mp3_path == "voiceovers/retake.mp3", "newest matching archive wins"
    # The archived cutlist recorded the FIRST voice's hash; the restored voice is the retake,
    # so the cutlist's parents mismatch and the walk correctly ends after the voice.
    assert board.load("cutlist") is None


def test_empty_parents_never_restore(tmp_path: Path) -> None:
    """Pre-provenance archives are unknown, and unknown is not coherent."""
    board = _board(tmp_path)
    board.save("script", _script("old world"))
    board.save("render_report", RenderReport(export_id="e", video_s=9.0, width=1920, height=1080))
    board.save("script", _script("new world"))
    board.save("script", _script("old world"))
    assert board.load("render_report") is None

    assert board.restore_coherent_suffix() == []
    assert board.load("render_report") is None


def test_an_unreadable_archive_is_skipped_not_fatal(tmp_path: Path) -> None:
    board = _board(tmp_path)
    _seed_full_chain(board, "resilient")
    board.save("script", _script("other"))
    board.save("script", _script("resilient"))
    corrupt = board.root / "versions" / "voice.v1.json"
    corrupt.write_text("{ not json", encoding="utf-8")

    restored = board.restore_coherent_suffix()

    assert restored == [], "the only voice archive is corrupt; nothing below can match either"


def test_a_present_link_is_never_touched(tmp_path: Path) -> None:
    board = _board(tmp_path)
    _seed_full_chain(board, "hands off")

    assert board.restore_coherent_suffix() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_restore_suffix.py -q`
Expected: AttributeError — `'Board' object has no attribute 'restore_coherent_suffix'`.

- [ ] **Step 3: Implement**

In `board.py`, add to `Board` (above `set_status`):

```python
    def restore_coherent_suffix(self) -> list[str]:
        """Bring back the longest archived suffix whose parents match the board — in order.

        Walks the chain; present links are skipped, a missing link is restored from its
        newest archived version whose EVERY recorded parent is present on the board with a
        matching content hash. Empty ``parents`` (pre-provenance archive, or a root) never
        restores — unknown is not coherent. The first missing link with no matching archive
        ends the walk. Checking happens on the peeked archive file BEFORE any revert, so a
        non-match is never even momentarily current; upstream-first order means each revert's
        downstream invalidation only touches links that are already missing, and every child
        checked afterwards points at the exact instance just restored.

        Successor to the review-killed single-link restore (41ecc51): script text alone could
        not identify a render; the parent-instance hashes can.
        """
        restored: list[str] = []
        for name in _CHAIN:
            if self.load(name) is not None:
                continue
            candidate_version = self._newest_matching_version(name)
            if candidate_version is None:
                break
            self.revert(name, candidate_version)
            restored.append(name)
        return restored

    def _newest_matching_version(self, name: str) -> int | None:
        """The newest archived version of ``name`` whose parents all match the board."""
        model_type = _SINGLETONS[name]
        for version in sorted(self.versions(name), reverse=True):
            path = self.root / "versions" / f"{name}.v{version}.json"
            try:
                candidate = model_type.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # unreadable archive: skip, never fatal
            parents = getattr(candidate, "parents", None)
            if not isinstance(parents, dict) or not parents:
                continue  # pre-provenance or root: unknown is not coherent
            if _parents_stale(self.load, candidate) is False:
                return version
        return None
```

(`_parents_stale` is Task 2's function: `False` means every parent present and matching —
exactly the restore condition. `True` and `None` both refuse.)

- [ ] **Step 4: Run the restore tests**

Run: `uv run pytest tests/test_restore_suffix.py -q`
Expected: exit 0.

- [ ] **Step 5: Full gates**

Run: `uv run pytest -q`, `uv run mypy src`, `uv run ruff check src tests`
Expected: exit 0 / Success / All checks passed.

- [ ] **Step 6: Commit**

```bash
git add src/laura/short_creator/board.py tests/test_restore_suffix.py
git commit -m "feat(short-creator): restore the longest archived suffix the board still matches

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: stamp `parents` at the six write sites

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_tools.py`
- Test: `services/local-api/tests/test_production_tools_write.py` (append), `services/local-api/tests/test_production_tools_cutlist.py` (append), `services/local-api/tests/test_production_tools_render.py` (append)

**Interfaces:**
- Consumes: `content_hash` from Task 1 (import in `production_tools.py` as
  `from .board_models import content_hash as _content_hash` next to the existing
  `script_hash as _script_hash` import).
- Produces: every artifact written by the six tools carries the spec §2 parents. No signature
  changes — the stamps are internal to each tool.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_production_tools_write.py`:

```python
# --- every write stamps its parents: the chain of custody the restore walks ---------------


def test_save_script_chapter_stamps_the_storyline_parent(tmp_path: Path) -> None:
    from laura.short_creator.board_models import content_hash

    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}
    specs["save_storyline"].func(red_thread="r", chapters=[_chapter()])
    storyline = board.load("storyline")
    assert storyline is not None

    specs["save_script_chapter"].func(chapter=1, lines=[{"scene_number": 1, "text": "a line"}])

    script = board.load("script")
    assert script is not None
    assert script.parents == {"storyline": content_hash(storyline)}


def test_save_qa_report_stamps_the_render_parent(tmp_path: Path) -> None:
    from laura.short_creator.board_models import RenderReport, content_hash

    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    board.save(
        "render_report",
        RenderReport(export_id="e1", video_s=10.0, width=1920, height=1080),
    )
    render = board.load("render_report")
    assert render is not None
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    specs["save_qa_report"].func(verdict="ship", findings=[])

    qa = board.load("qa_report")
    assert qa is not None
    assert qa.parents == {"render_report": content_hash(render)}
```

Append to `tests/test_production_tools_cutlist.py` (uses that file's existing helpers
`_seed_two_scenes`, `_board`, `_storyline`, `_script`, `_save_voice`, `_words`):

```python
def test_build_cutlist_stamps_storyline_script_and_voice_parents(tmp_path: Path) -> None:
    from laura.short_creator.board_models import content_hash

    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    board.save("storyline", _storyline())
    board.save("script", _script())
    _save_voice(board, tmp_path, _words())
    storyline = board.load("storyline")
    script = board.load("script")
    voice = board.load("voice")
    assert storyline is not None and script is not None and voice is not None
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["build_cutlist"].func()
    assert out["ok"] is True, out

    cutlist = board.load("cutlist")
    assert cutlist is not None
    assert cutlist.parents == {
        "storyline": content_hash(storyline),
        "script": content_hash(script),
        "voice": content_hash(voice),
    }
```

Append to `tests/test_production_tools_render.py` (uses that file's `_build_board_to_cutlist`
and `_FakeRenderSegments`):

```python
def test_render_and_voice_and_sheet_stamp_their_parents(tmp_path: Path) -> None:
    from laura.short_creator.board_models import content_hash

    db, asset_id, board = _build_board_to_cutlist(tmp_path, scene2_roi=None, voice_s=3.4)
    storyline = board.load("storyline")
    script = board.load("script")
    voice = board.load("voice")
    cutlist = board.load("cutlist")
    assert storyline and script and voice and cutlist
    fake = _FakeRenderSegments(status="ready")
    deps = ProductionDeps(render_segments=fake)
    specs = {
        s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    }

    out = specs["render_production"].func()
    assert out["ok"] is True, out

    render = board.load("render_report")
    assert render is not None
    assert render.parents == {
        "storyline": content_hash(storyline),
        "script": content_hash(script),
        "voice": content_hash(voice),
        "cutlist": content_hash(cutlist),
    }
    # The voice artifact in this builder is seeded directly; synthesize_script_voice's own
    # stamp is asserted in test_production_tools_cutlist.py's synthesis test below.
```

The `contact_sheet` stamp: `save_contact_sheet` shells out to ffmpeg, and its existing tests
live in `tests/test_contact_sheet_framing.py` with fakes for frame extraction and grid
composition. Add the stamp assertion THERE, inside (or mirroring) that file's existing
happy-path test — after the tool reports ok, assert:

```python
    from laura.short_creator.board_models import content_hash

    cutlist_now = board.load("cutlist")
    sheet = board.load("contact_sheet")
    assert cutlist_now is not None and sheet is not None
    assert sheet.parents == {"cutlist": content_hash(cutlist_now)}
```

(Reuse that file's own board/cutlist fixtures verbatim; only the four assertion lines are new.)

Also append to `tests/test_production_tools_cutlist.py` a synthesis-stamp test IF that file
has a fake voice backend (`_FakeVoiceBackend` with `synthesize`); otherwise put it wherever
`synthesize_script_voice` is currently tested (search: `grep -rn "synthesize_script_voice"
tests/`):

```python
def test_synthesize_stamps_storyline_and_script_parents(tmp_path: Path) -> None:
    from laura.short_creator.board_models import content_hash

    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    board.save("storyline", _storyline())
    board.save("script", _script())
    storyline = board.load("storyline")
    script = board.load("script")
    assert storyline is not None and script is not None
    deps = ProductionDeps(voice_backend=_FakeVoiceBackend(tmp_path))
    specs = {
        s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    }

    out = specs["synthesize_script_voice"].func()
    assert out["ok"] is True, out

    voice = board.load("voice")
    assert voice is not None
    assert voice.parents == {
        "storyline": content_hash(storyline),
        "script": content_hash(script),
    }
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `uv run pytest tests/test_production_tools_write.py tests/test_production_tools_cutlist.py tests/test_production_tools_render.py -q`
Expected: exit 1, the new tests fail with `assert {} == {...}` (parents empty today).

- [ ] **Step 3: Implement the six stamps**

In `production_tools.py`, add the import next to the existing hash imports:

```python
from .board_models import content_hash as _content_hash
```

Then, at each write site, build the parents dict from the ALREADY-LOADED artifacts (every
site already loads what it reads — reuse those variables, do NOT reload):

1. `save_script_chapter` — the order guard already loads the storyline; keep it in a variable
   (`storyline_for_guard = board.load("storyline")`, guard on `isinstance(...)`) and stamp:

```python
            merged_script = Script(
                language=language,
                lines=merged,
                parents={"storyline": _content_hash(storyline_for_guard)},
            )
            version = board.save("script", merged_script)
```

2. `synthesize_script_voice` — where the `VoiceArtifact` is constructed (fresh synthesis
   path only; the cache-hit path returns the existing artifact untouched):

```python
                parents={
                    "storyline": _content_hash(storyline),
                    "script": _content_hash(script),
                },
```

3. `build_cutlist` — the save becomes:

```python
            board.save(
                "cutlist",
                Cutlist(
                    segments=segments,
                    script_hash=script_hash(ordered_lines),
                    parents={
                        "storyline": _content_hash(storyline),
                        "script": _content_hash(script),
                        "voice": _content_hash(voice),
                    },
                ),
            )
```

4. `save_contact_sheet` — the `ContactSheet(...)` construction gains:

```python
                parents={"cutlist": _content_hash(cutlist)},
```

5. `render_production` — the `RenderReport(...)` construction gains:

```python
                parents={
                    "storyline": _content_hash(storyline),
                    "script": _content_hash(script),
                    "voice": _content_hash(voice),
                    "cutlist": _content_hash(cutlist),
                },
```

   (`storyline`/`script` are in scope there via the `ordered_lines` computation; verify the
   variable names at the site and reuse them exactly.)

6. `save_qa_report` — the order guard already loads the render; keep it
   (`render_for_guard = board.load("render_report")`, guard on `isinstance`) and stamp:

```python
                qa_report = QaReport(
                    verdict=verdict,  # type: ignore[arg-type]
                    findings=[QaFinding(**f) for f in findings],
                    parents={"render_report": _content_hash(render_for_guard)},
                )
```

- [ ] **Step 4: Run the three test files**

Run: `uv run pytest tests/test_production_tools_write.py tests/test_production_tools_cutlist.py tests/test_production_tools_render.py tests/test_contact_sheet_framing.py -q`
Expected: exit 0.

- [ ] **Step 5: Full gates**

Run: `uv run pytest -q`, `uv run mypy src`, `uv run ruff check src tests`
Expected: exit 0 / Success / All checks passed. (Watch for exact-equality dict asserts on
tool replies — the stamps do not change replies, only stored artifacts, so none should break.)

- [ ] **Step 6: Commit**

```bash
git add src/laura/short_creator/production_tools.py tests/test_production_tools_write.py tests/test_production_tools_cutlist.py tests/test_production_tools_render.py tests/test_contact_sheet_framing.py
git commit -m "feat(short-creator): every write stamps the parent instances it was built from

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

(Drop `test_contact_sheet_framing.py` from the add if the sheet test landed elsewhere.)

---

### Task 5: entry wiring in `run_production`

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_orchestrator.py`
- Test: `services/local-api/tests/test_production_orchestrator.py` (append)

**Interfaces:**
- Consumes: `Board.restore_coherent_suffix()` (Task 3); `run_production`'s existing
  `event_sink` parameter and result dict.
- Produces: result key `restored: list[str]` (always present, `[]` when nothing restored);
  event `{"type": "restored", "artifacts": [...]}` on the sink when non-empty.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_production_orchestrator.py` (reuses `_seed_scene`, `_make_execute`,
`_script_artifact` from the file; needs the full-chain seeding, so define a small local
helper mirroring `test_restore_suffix.py`'s `_seed_full_chain` — copy it in, adjusted to
this file's imports):

```python
def test_run_production_restores_the_matching_suffix_and_reports_it(tmp_path: Path) -> None:
    """Entry restore: the resume contract reads DONE, the result names what came back."""
    from laura.short_creator.board_models import VoiceArtifact, content_hash

    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})
    root = production_orchestrator.board_root_for(db, asset_id, "sess-suffix")
    board = Board.create(
        root,
        BoardMeta(
            session_id="sess-suffix",
            asset_id=asset_id,
            created_utc="2026-07-20T00:00:00+00:00",
            task="demo",
            language="English",
            target_seconds=174.0,
        ),
    )
    final = _script_artifact("the rendered line")
    board.save("script", final)
    script_now = board.load("script")
    assert script_now is not None
    board.save(
        "voice",
        VoiceArtifact(
            script_hash="k",
            mp3_path="voiceovers/a.mp3",
            parents={"script": content_hash(script_now)},
        ),
    )
    board.save("script", _script_artifact("a different draft"))
    board.save("script", final.model_copy(deep=True))
    assert board.load("voice") is None

    events: list[dict[str, object]] = []
    execute, _calls = _make_execute({"A": ("ok", False)})
    result = production_orchestrator.run_production(
        db,
        config,
        asset_id=asset_id,
        session_id="sess-suffix",
        task="demo",
        target_seconds=174,
        execute=execute,
        event_sink=events.append,
    )

    assert result["restored"] == ["voice"]
    assert board.load("voice") is not None
    assert {"type": "restored", "artifacts": ["voice"]} in events


def test_run_production_reports_empty_restored_when_nothing_came_back(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})
    execute, _calls = _make_execute({"A": ("ok", False)})

    result = production_orchestrator.run_production(
        db, config, asset_id=asset_id, session_id="sess-plain", task="demo", execute=execute
    )

    assert result["restored"] == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_production_orchestrator.py -q`
Expected: exit 1 — KeyError `'restored'`.

- [ ] **Step 3: Implement**

In `run_production`, at the spot where the removed single-link restore's explanatory comment
sits (after `Board.open`/`Board.create`, before `build_production_task`), replace that
comment block with:

```python
    # Full-suffix restore (spec 2026-07-20-provenance-chain-design.md): bring back the
    # longest archived suffix whose parent-instance hashes match the board. Runs BEFORE
    # build_production_task so the resume contract reads DONE for what came back — the
    # task-text lie that killed the single-link restore is structurally impossible here.
    restored = board.restore_coherent_suffix()
    if restored and event_sink is not None:
        try:
            event_sink({"type": "restored", "artifacts": list(restored)})
        except Exception:  # noqa: BLE001 — observability must never fail the run
            logger.warning("restored-event sink failed; continuing")
```

And add to the returned result dict:

```python
        "restored": restored,
```

- [ ] **Step 4: Run the orchestrator tests**

Run: `uv run pytest tests/test_production_orchestrator.py -q`
Expected: exit 0.

- [ ] **Step 5: Full gates**

Run: `uv run pytest -q`, `uv run mypy src`, `uv run ruff check src tests`
Expected: exit 0 / Success / All checks passed.

- [ ] **Step 6: Commit**

```bash
git add src/laura/short_creator/production_orchestrator.py tests/test_production_orchestrator.py
git commit -m "feat(short-creator): resume restores the coherent suffix and says so

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: full verification + docs bookkeeping

**Files:**
- Modify: `tasks/todo.md` (repo root)

- [ ] **Step 1: Full backend gates one last time**

From `services/local-api/`:
Run: `uv run pytest -q` → exit 0; `uv run mypy src` → "Success: no issues found";
`uv run ruff check src tests` → "All checks passed!".

- [ ] **Step 2: Frontend untouched — confirm, don't assume**

Run (repo root): `git status --short apps/desktop`
Expected: no modified files. If any appear, STOP — this plan must not touch the frontend
(spec §Nicht in diesem Scope); revert stray changes.

- [ ] **Step 3: Tick the portion**

In `tasks/todo.md`, under Portion 20's "20.A — Provenienz & Kohärenz", update the deliberately-
open line to done:

```markdown
- [x] `cutlist`/`contact_sheet`/`render_report`/`qa_report` tragen `parents` (Content-Hash-
      Kette); `Board.restore_coherent_suffix()` restauriert beim Resume den längsten
      kohärenten Suffix bis inkl. QA — Spec 2026-07-20-provenance-chain-design.md
```

- [ ] **Step 4: Commit**

```bash
git add tasks/todo.md
git commit -m "docs(tasks): provenance chain + full-suffix restore shipped

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
