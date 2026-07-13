# Agent-Vision-Short v2 — Foundations (Board + zoom_hybrid) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Slice 1+2 der Spec [2026-07-13-agent-vision-short-v2-design.md](../specs/2026-07-13-agent-vision-short-v2-design.md): das versionierte **Production Board** (Schemas, Store, Invalidierung, Revert, Resume) und der Renderer-Fit-Modus **`zoom_hybrid`** (voll/Blur → geeaster Push in die ROI) — beides LLM-frei, voll unit-testbar.

**Architecture:** Board = reiner Datei-Store (`board_models.py` pydantic-Schemas + `board.py` Store mit append-only `versions/`). Zoom = reine Mathematik + Filtergraph-Builder in `laura/render/zoom.py`; `render_clips_mp4` bekommt einen `zoom_specs`-Zweig (pro Segment: Blur-Voll-Phase, `xfade` in einen animierten `crop`-Push); `shorts_render`-Handler und `tool_render_segments` reichen eine `zoom`-Option durch.

**Tech Stack:** Python 3.11 · uv · pytest · pydantic v2 · ffmpeg `filter_complex` (bestehende Muster in `laura/render/mp4.py` und `laura/render/reel.py`).

## Global Constraints

- Alle Schnittdaten in **Ganzzahl-Frames, end-exclusive** (`end_frame_exclusive > start_frame`); Sekunden nur als Projektion.
- ROI normiert (0–1); Fenster-Mathematik: **exaktes out-Seitenverhältnis, Mindesthöhe 55 % der Quellhöhe, vollständig im Quellrahmen geklemmt, gerade Ganzzahlen**.
- Übergang: **smoothstep**, Default **0,6 s**; Zoom endet an der Segmentgrenze (nie Überhang).
- Fehlende/ungültige ROI oder zu kurzes Segment → **Fallback volles Bild (Blur), nie Crash, nie Center-Crop**.
- Bestehende Render-Pfade bleiben **byte-identisch** (Blur-Default unverändert; `reel_blur_fill_graph`-Default-Labels unverändert).
- `zoom_hybrid` v1: nur mit `vertical=True`, **schließt Video-Transitions (xfade/fade) aus** → `ValueError`.
- mypy strict + ruff sauber; kein `print` (projektlokaler Logger); Tests mit `uv run pytest` aus `services/local-api/`.
- Commits: Conventional Commits, **explizite Pfade bei `git add`** (nie `-A`); Codex-Sperrgebiet (`services/ai-runtimes/`, `ai/runtime_*`, `api/ai_runtimes.py`) nicht anfassen.
- Doku-Prosa Deutsch; Code-Identifier/Kommentare/Commits Englisch.

**Arbeitsverzeichnis aller Kommandos:** `services/local-api/`.

> **Design-Amendment (2026-07-13, nach Task-7-Review):** ffmpegs `crop` wertet
> `w`/`h` nur EINMAL bei Filter-Konfiguration aus — animierte Fenstergrößen-Ausdrücke
> (Task 6 `zoom_crop_exprs`/`smooth_progress_expr`, Task 7 Zoom-Branch) brechen den
> Graphen zur Laufzeit (-22, empirisch belegt). **v1 rendert den Zoom-Branch statisch:**
> `crop={end_w}:{end_h}:{end_x}:{end_y}` (Werte aus `ZoomSpec.end_win`), Übergang bleibt
> der bestehende `xfade`-Dissolve mit unveränderter Timing-Algebra. `zoom_crop_exprs` und
> `smooth_progress_expr` sind ersatzlos entfernt (samt ihren Tests); `ZoomSpec` behält
> `start_win` (wird vom Degenerat-Check in `zoom_spec_from_option` genutzt). Die
> Schnittstellen von Task 7 (`zoom_hybrid_segment_parts`, `zoom_concat_graph`,
> `[vcat]`/`[abase]`) und alles in Task 8–11 bleiben unverändert gültig; Task 10 ist
> der empirische Gate. Animierter Push = spätere Iteration (zoompan-Kandidat).

---

### Task 1: Board-Schemas (`board_models.py`)

**Files:**
- Create: `services/local-api/src/laura/short_creator/board_models.py`
- Test: `services/local-api/tests/test_production_board_models.py`

**Interfaces:**
- Produces: pydantic-Modelle `Roi`, `BestWindow`, `SceneReview`, `Chapter`, `Storyline`, `ScriptLine`, `Script`, `VoiceArtifact`, `CutSegment`, `Cutlist`, `RenderCheck`, `RenderReport`, `QaFinding`, `QaReport`, `BoardMeta` — exakt wie unten; Task 2–4 validieren/serialisieren ausschließlich über diese Typen.

- [ ] **Step 1: Write the failing tests**

```python
"""Schema tests for the v2 production board artifacts."""

import pytest
from pydantic import ValidationError

from laura.short_creator.board_models import (
    BestWindow,
    BoardMeta,
    Chapter,
    CutSegment,
    Cutlist,
    QaReport,
    Roi,
    SceneReview,
    Script,
    ScriptLine,
    Storyline,
)


def _review(**overrides):
    base = dict(
        scene_number=3,
        src_start_frame=100,
        src_end_frame_exclusive=220,
        description="Agent farm dashboard with running agents",
        whats_happening="cursor scrolls the agent list",
        hook_score=7,
        best_window=BestWindow(offset_s=1.0, duration_s=4.0),
        roi=Roi(x=0.1, y=0.2, w=0.5, h=0.4),
    )
    base.update(overrides)
    return SceneReview(**base)


def test_scene_review_roundtrip() -> None:
    r = _review()
    again = SceneReview.model_validate_json(r.model_dump_json())
    assert again == r
    assert again.version == 1 and again.degraded is False


def test_scene_review_rejects_non_exclusive_frames() -> None:
    with pytest.raises(ValidationError):
        _review(src_end_frame_exclusive=100)


def test_roi_must_stay_inside_frame() -> None:
    with pytest.raises(ValidationError):
        Roi(x=0.8, y=0.0, w=0.5, h=0.5)
    with pytest.raises(ValidationError):
        Roi(x=0.0, y=0.0, w=0.0, h=0.5)


def test_hook_score_range() -> None:
    with pytest.raises(ValidationError):
        _review(hook_score=11)


def test_storyline_roles_are_closed_set() -> None:
    ok = Storyline(
        red_thread="one app runs your whole team",
        arc=[Chapter(chapter=1, role="hook", message="stop scrolling", scene_numbers=[1], target_seconds=3.0)],
    )
    assert ok.version == 1
    with pytest.raises(ValidationError):
        Chapter(chapter=1, role="outro", message="x", scene_numbers=[1], target_seconds=3.0)


def test_cutlist_orders_must_be_contiguous() -> None:
    seg = dict(scene_number=1, start_frame=0, end_frame_exclusive=120, roi=None, zoom_start_s=None)
    with pytest.raises(ValidationError):
        Cutlist(segments=[CutSegment(order=0, **seg), CutSegment(order=2, **seg)])
    ok = Cutlist(segments=[CutSegment(order=0, **seg), CutSegment(order=1, **seg)])
    assert [s.order for s in ok.segments] == [0, 1]


def test_script_needs_lines() -> None:
    with pytest.raises(ValidationError):
        Script(language="de", lines=[])
    s = Script(language="de", lines=[ScriptLine(chapter=1, scene_number=1, text="Stopp!")])
    assert s.lines[0].text == "Stopp!"


def test_qa_report_verdict_literal() -> None:
    with pytest.raises(ValidationError):
        QaReport(verdict="maybe", findings=[])
    assert QaReport(verdict="ship", findings=[]).verdict == "ship"


def test_board_meta_defaults() -> None:
    m = BoardMeta(session_id="s1", asset_id="a1", created_utc="2026-07-13T12:00:00Z",
                  task="overview short", target_seconds=60.0)
    assert m.format == "insta" and m.status == "active"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_production_board_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'laura.short_creator.board_models'`

- [ ] **Step 3: Write the implementation**

```python
"""Pydantic schemas for the v2 production-board artifacts.

Every artifact the agent team exchanges lives on the board as one of these
models.  Validation is the contract: a malformed agent output is rejected at
the tool boundary (the agent sees the error and corrects itself) instead of
propagating silently.  Frame fields follow the project invariant: integer
frames, end-exclusive.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Roi(BaseModel):
    """Normalized region of interest (fractions of source width/height)."""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _inside_frame(self) -> "Roi":
        if self.x + self.w > 1.0 + 1e-9 or self.y + self.h > 1.0 + 1e-9:
            raise ValueError("roi exceeds frame bounds")
        return self


class BestWindow(BaseModel):
    """Strongest moment inside a scene, relative to the scene start."""

    model_config = ConfigDict(extra="forbid")

    offset_s: float = Field(ge=0.0)
    duration_s: float = Field(gt=0.0)


class SceneReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_number: int = Field(ge=1)
    src_start_frame: int = Field(ge=0)
    src_end_frame_exclusive: int
    description: str
    whats_happening: str
    hook_score: int = Field(ge=0, le=10)
    best_window: BestWindow
    roi: Roi | None = None
    legibility_notes: str = ""
    degraded: bool = False
    model: str = ""
    version: int = Field(default=1, ge=1)
    created_utc: str = ""

    @model_validator(mode="after")
    def _frames_end_exclusive(self) -> "SceneReview":
        if self.src_end_frame_exclusive <= self.src_start_frame:
            raise ValueError("src_end_frame_exclusive must be > src_start_frame")
        return self


class Chapter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter: int = Field(ge=1)
    role: Literal["hook", "problem", "feature", "payoff_cta"]
    message: str
    scene_numbers: list[int] = Field(min_length=1)
    target_seconds: float = Field(gt=0.0)


class Storyline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1)
    red_thread: str
    arc: list[Chapter] = Field(min_length=1)


class ScriptLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter: int = Field(ge=1)
    scene_number: int = Field(ge=1)
    text: str = Field(min_length=1)


class Script(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1)
    language: str
    lines: list[ScriptLine] = Field(min_length=1)


class VoiceArtifact(BaseModel):
    """Synthesis result, cached by script hash (re-voice only on text change)."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1)
    script_hash: str
    mp3_path: str
    timings_path: str | None = None
    voice_s: float | None = None


class CutSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=0)
    scene_number: int = Field(ge=1)
    start_frame: int = Field(ge=0)
    end_frame_exclusive: int
    roi: Roi | None = None
    zoom_start_s: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _frames_end_exclusive(self) -> "CutSegment":
        if self.end_frame_exclusive <= self.start_frame:
            raise ValueError("end_frame_exclusive must be > start_frame")
        return self


class Cutlist(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1)
    segments: list[CutSegment] = Field(min_length=1)

    @model_validator(mode="after")
    def _orders_contiguous(self) -> "Cutlist":
        orders = [s.order for s in self.segments]
        if sorted(orders) != list(range(len(orders))):
            raise ValueError("segment orders must be 0..n-1 without gaps")
        return self


class RenderCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    ok: bool
    note: str = ""


class RenderReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1)
    export_id: str
    video_s: float = Field(gt=0.0)
    voice_s: float | None = None
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    checks: list[RenderCheck] = Field(default_factory=list)


class QaFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["info", "minor", "major"]
    where: str
    note: str


class QaReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1)
    verdict: Literal["ship", "revise"]
    findings: list[QaFinding] = Field(default_factory=list)


class BoardMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    asset_id: str
    created_utc: str
    task: str
    format: str = "insta"
    target_seconds: float = Field(gt=0.0)
    status: str = "active"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_production_board_models.py -v`
Expected: PASS (9 Tests)

- [ ] **Step 5: Lint + typecheck + commit**

```bash
uv run ruff check src/laura/short_creator/board_models.py tests/test_production_board_models.py
uv run mypy src/laura/short_creator/board_models.py
git add services/local-api/src/laura/short_creator/board_models.py services/local-api/tests/test_production_board_models.py
git commit -m "feat(short-creator): production-board artifact schemas (v2)"
```

---

### Task 2: Board-Store — Lifecycle + Szenen-Reviews (`board.py`)

**Files:**
- Create: `services/local-api/src/laura/short_creator/board.py`
- Test: `services/local-api/tests/test_production_board.py`

**Interfaces:**
- Consumes: alle Modelle aus Task 1.
- Produces: `Board` mit `create(root, meta)`, `open(root)`, `meta()`, `save_scene_review(review) -> int`, `scene_reviews() -> list[SceneReview]`; Modul-Funktion `downstream_of(name) -> tuple[str, ...]`; Konstante `_CHAIN`. Task 3/4 erweitern DIESELBE Klasse.

- [ ] **Step 1: Write the failing tests**

```python
"""Store tests: lifecycle, scene-review versioning."""

from pathlib import Path

import pytest

from laura.short_creator.board import Board, downstream_of
from laura.short_creator.board_models import BestWindow, BoardMeta, Roi, SceneReview


def _meta() -> BoardMeta:
    return BoardMeta(session_id="s1", asset_id="a1", created_utc="2026-07-13T12:00:00Z",
                     task="overview short", target_seconds=60.0)


def _review(n: int = 1, desc: str = "dashboard") -> SceneReview:
    return SceneReview(
        scene_number=n, src_start_frame=0, src_end_frame_exclusive=120,
        description=desc, whats_happening="scrolling", hook_score=5,
        best_window=BestWindow(offset_s=0.5, duration_s=3.0),
        roi=Roi(x=0.1, y=0.1, w=0.5, h=0.5),
    )


def test_create_open_meta_roundtrip(tmp_path: Path) -> None:
    board = Board.create(tmp_path / "board", _meta())
    again = Board.open(tmp_path / "board")
    assert again.meta().session_id == "s1"
    assert (tmp_path / "board" / "scene_reviews").is_dir()
    assert (tmp_path / "board" / "versions").is_dir()


def test_open_missing_board_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Board.open(tmp_path / "nope")


def test_scene_review_save_versions_and_archives(tmp_path: Path) -> None:
    board = Board.create(tmp_path / "board", _meta())
    assert board.save_scene_review(_review(1, "first look")) == 1
    assert board.save_scene_review(_review(1, "second look")) == 2
    reviews = board.scene_reviews()
    assert len(reviews) == 1
    assert reviews[0].description == "second look" and reviews[0].version == 2
    archived = tmp_path / "board" / "versions" / "scene_1.v1.json"
    assert archived.is_file() and "first look" in archived.read_text(encoding="utf-8")


def test_scene_reviews_sorted_by_number(tmp_path: Path) -> None:
    board = Board.create(tmp_path / "board", _meta())
    board.save_scene_review(_review(10))
    board.save_scene_review(_review(2))
    assert [r.scene_number for r in board.scene_reviews()] == [2, 10]


def test_downstream_of() -> None:
    assert downstream_of("scene_reviews") == (
        "storyline", "script", "voice", "cutlist", "render_report", "qa_report")
    assert downstream_of("cutlist") == ("render_report", "qa_report")
    assert downstream_of("qa_report") == ()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_production_board.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'laura.short_creator.board'`

- [ ] **Step 3: Write the implementation**

```python
"""Versioned artifact store for v2 production sessions (the "Production Board").

Layout under ``<workspace>/agent-runs/<session_id>/board/``::

    meta.json                       BoardMeta
    scene_reviews/scene_<n>.json    one SceneReview per scene
    storyline.json .. qa_report.json  singleton artifacts (see _SINGLETONS)
    versions/<stem>.v<k>.json       append-only archive of every replaced version

Writes are atomic (tmp + replace) and pydantic-validated.  Invalidation always
runs *downstream* along ``_CHAIN`` — never upstream — so cached upstream work
(above all: scene reviews) survives every adjust/revert.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from laura.short_creator.board_models import (
    BoardMeta,
    Cutlist,
    QaReport,
    RenderReport,
    SceneReview,
    Script,
    Storyline,
    VoiceArtifact,
)

_CHAIN: tuple[str, ...] = ("storyline", "script", "voice", "cutlist", "render_report", "qa_report")
_SINGLETONS: dict[str, type[BaseModel]] = {
    "storyline": Storyline,
    "script": Script,
    "voice": VoiceArtifact,
    "cutlist": Cutlist,
    "render_report": RenderReport,
    "qa_report": QaReport,
}


def downstream_of(name: str) -> tuple[str, ...]:
    """Artifacts invalidated by a change to ``name`` (chain order preserved)."""
    if name == "scene_reviews":
        return _CHAIN
    return _CHAIN[_CHAIN.index(name) + 1 :]


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


class Board:
    """One production session's artifact store."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def create(cls, root: Path, meta: BoardMeta) -> "Board":
        (root / "scene_reviews").mkdir(parents=True, exist_ok=True)
        (root / "versions").mkdir(parents=True, exist_ok=True)
        _write_atomic(root / "meta.json", meta.model_dump_json(indent=2))
        return cls(root)

    @classmethod
    def open(cls, root: Path) -> "Board":
        if not (root / "meta.json").is_file():
            raise FileNotFoundError(f"no board at {root}")
        return cls(root)

    def meta(self) -> BoardMeta:
        raw = (self.root / "meta.json").read_text(encoding="utf-8")
        return BoardMeta.model_validate_json(raw)

    # -- scene reviews ---------------------------------------------------

    def save_scene_review(self, review: SceneReview) -> int:
        stem = f"scene_{review.scene_number}"
        path = self.root / "scene_reviews" / f"{stem}.json"
        current_version = 0
        if path.is_file():
            old = SceneReview.model_validate_json(path.read_text(encoding="utf-8"))
            current_version = old.version
            self._archive(stem, old.version, path)
        version = max([current_version, *self.versions(stem)]) + 1
        stamped = review.model_copy(update={"version": version})
        _write_atomic(path, stamped.model_dump_json(indent=2))
        return version

    def scene_reviews(self) -> list[SceneReview]:
        folder = self.root / "scene_reviews"
        reviews = [
            SceneReview.model_validate_json(p.read_text(encoding="utf-8"))
            for p in folder.glob("scene_*.json")
        ]
        return sorted(reviews, key=lambda r: r.scene_number)

    # -- shared internals --------------------------------------------------

    def versions(self, stem: str) -> list[int]:
        """Archived version numbers for a singleton name or ``scene_<n>`` stem."""
        prefix = f"{stem}.v"
        out: list[int] = []
        for p in (self.root / "versions").glob(f"{stem}.v*.json"):
            digits = p.name[len(prefix) : -len(".json")]
            if digits.isdigit():
                out.append(int(digits))
        return sorted(out)

    def _archive(self, stem: str, version: int, path: Path) -> None:
        dest = self.root / "versions" / f"{stem}.v{version}.json"
        _write_atomic(dest, path.read_text(encoding="utf-8"))
```

(`json`/`Any` werden in Task 3/4 gebraucht — Import bleibt; falls ruff `F401` meldet, Importe erst in Task 3 ergänzen.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_production_board.py -v`
Expected: PASS (5 Tests)

- [ ] **Step 5: Lint + typecheck + commit**

```bash
uv run ruff check src/laura/short_creator/board.py tests/test_production_board.py
uv run mypy src/laura/short_creator/board.py
git add services/local-api/src/laura/short_creator/board.py services/local-api/tests/test_production_board.py
git commit -m "feat(short-creator): production board store — lifecycle + scene-review versioning"
```

---

### Task 3: Board-Store — Singletons, Invalidierung, Revert

**Files:**
- Modify: `services/local-api/src/laura/short_creator/board.py` (Klasse `Board` erweitern)
- Test: `services/local-api/tests/test_production_board.py` (Tests anhängen)

**Interfaces:**
- Consumes: `Board`, `_CHAIN`, `_SINGLETONS`, `downstream_of` aus Task 2.
- Produces: `Board.save(name, artifact) -> int` (archiviert + stempelt Version + invalidiert Downstream), `Board.load(name) -> BaseModel | None`, `Board.invalidate(name) -> list[str]`, `Board.revert(name, version) -> None`.

- [ ] **Step 1: Write the failing tests (an `test_production_board.py` anhängen)**

```python
from laura.short_creator.board_models import (  # noqa: E402  (append below existing imports)
    Chapter,
    Cutlist,
    CutSegment,
    Script,
    ScriptLine,
    Storyline,
)


def _storyline(thread: str = "one app") -> Storyline:
    return Storyline(red_thread=thread, arc=[
        Chapter(chapter=1, role="hook", message="stop", scene_numbers=[1], target_seconds=3.0)])


def _script() -> Script:
    return Script(language="de", lines=[ScriptLine(chapter=1, scene_number=1, text="Stopp!")])


def _cutlist() -> Cutlist:
    return Cutlist(segments=[CutSegment(order=0, scene_number=1, start_frame=0,
                                        end_frame_exclusive=120)])


def test_singleton_save_load_and_version_stamp(tmp_path: Path) -> None:
    board = Board.create(tmp_path / "board", _meta())
    assert board.save("storyline", _storyline("v1 thread")) == 1
    assert board.save("storyline", _storyline("v2 thread")) == 2
    loaded = board.load("storyline")
    assert isinstance(loaded, Storyline)
    assert loaded.version == 2 and loaded.red_thread == "v2 thread"
    assert board.versions("storyline") == [1]
    assert board.load("qa_report") is None


def test_save_rejects_wrong_type_and_unknown_name(tmp_path: Path) -> None:
    board = Board.create(tmp_path / "board", _meta())
    with pytest.raises(TypeError):
        board.save("storyline", _script())
    with pytest.raises(KeyError):
        board.save("nonsense", _storyline())
    with pytest.raises(KeyError):
        board.load("nonsense")


def test_save_invalidates_downstream(tmp_path: Path) -> None:
    board = Board.create(tmp_path / "board", _meta())
    board.save("storyline", _storyline())
    board.save("script", _script())
    board.save("cutlist", _cutlist())
    removed = board.invalidate("script")
    assert removed == ["cutlist"]
    assert board.load("cutlist") is None
    assert board.versions("cutlist") == [1]  # archived, not lost
    # save() itself invalidates downstream too:
    board.save("cutlist", _cutlist())
    board.save("storyline", _storyline("changed"))
    assert board.load("script") is None and board.load("cutlist") is None


def test_revert_restores_and_invalidates(tmp_path: Path) -> None:
    board = Board.create(tmp_path / "board", _meta())
    board.save("storyline", _storyline("first"))
    board.save("storyline", _storyline("second"))
    board.save("script", _script())
    board.revert("storyline", 1)
    loaded = board.load("storyline")
    assert isinstance(loaded, Storyline) and loaded.red_thread == "first"
    assert board.load("script") is None  # downstream invalidated
    # v2 stays in the archive; saving after revert continues past ALL known versions
    assert 2 in board.versions("storyline")
    assert board.save("storyline", _storyline("third")) == 3


def test_revert_unknown_version_raises(tmp_path: Path) -> None:
    board = Board.create(tmp_path / "board", _meta())
    board.save("storyline", _storyline())
    with pytest.raises(FileNotFoundError):
        board.revert("storyline", 7)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_production_board.py -v`
Expected: FAIL — `AttributeError: 'Board' object has no attribute 'save'`

- [ ] **Step 3: Write the implementation (Methoden in `Board` ergänzen, vor `versions()`)**

```python
    # -- singleton artifacts ----------------------------------------------

    def save(self, name: str, artifact: BaseModel) -> int:
        """Persist a singleton artifact; archives the old version and
        invalidates everything downstream.  Returns the new version."""
        model_type = _SINGLETONS.get(name)
        if model_type is None:
            raise KeyError(f"unknown artifact: {name}")
        if not isinstance(artifact, model_type):
            raise TypeError(
                f"{name} expects {model_type.__name__}, got {type(artifact).__name__}"
            )
        path = self.root / f"{name}.json"
        current_version = 0
        if path.is_file():
            old = model_type.model_validate_json(path.read_text(encoding="utf-8"))
            current_version = int(getattr(old, "version"))
            self._archive(name, current_version, path)
        version = max([current_version, *self.versions(name)]) + 1
        stamped = artifact.model_copy(update={"version": version})
        _write_atomic(path, stamped.model_dump_json(indent=2))
        self.invalidate(name)
        return version

    def load(self, name: str) -> BaseModel | None:
        model_type = _SINGLETONS.get(name)
        if model_type is None:
            raise KeyError(f"unknown artifact: {name}")
        path = self.root / f"{name}.json"
        if not path.is_file():
            return None
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))

    def invalidate(self, name: str) -> list[str]:
        """Archive + remove every present artifact downstream of ``name``."""
        removed: list[str] = []
        for dep in downstream_of(name):
            path = self.root / f"{dep}.json"
            if path.is_file():
                model_type = _SINGLETONS[dep]
                cur = model_type.model_validate_json(path.read_text(encoding="utf-8"))
                self._archive(dep, int(getattr(cur, "version")), path)
                path.unlink()
                removed.append(dep)
        return removed

    def revert(self, name: str, version: int) -> None:
        """Restore an archived version as current; downstream is invalidated."""
        if name not in _SINGLETONS:
            raise KeyError(f"unknown artifact: {name}")
        archived = self.root / "versions" / f"{name}.v{version}.json"
        if not archived.is_file():
            raise FileNotFoundError(f"no archived {name} v{version}")
        path = self.root / f"{name}.json"
        if path.is_file():
            model_type = _SINGLETONS[name]
            cur = model_type.model_validate_json(path.read_text(encoding="utf-8"))
            self._archive(name, int(getattr(cur, "version")), path)
        _write_atomic(path, archived.read_text(encoding="utf-8"))
        self.invalidate(name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_production_board.py -v`
Expected: PASS (10 Tests)

- [ ] **Step 5: Lint + typecheck + commit**

```bash
uv run ruff check src/laura/short_creator/board.py tests/test_production_board.py
uv run mypy src/laura/short_creator/board.py
git add services/local-api/src/laura/short_creator/board.py services/local-api/tests/test_production_board.py
git commit -m "feat(short-creator): board singletons — versioned save/load, downstream invalidation, revert"
```

---

### Task 4: Board — `resume_point` + `status`

**Files:**
- Modify: `services/local-api/src/laura/short_creator/board.py`
- Test: `services/local-api/tests/test_production_board.py` (anhängen)

**Interfaces:**
- Consumes: `Board` aus Task 2/3.
- Produces: `Board.resume_point(expected_scenes: list[int]) -> str` (`"scene_reviews:<n>"` | Kettenname | `"done"`), `Board.status() -> dict[str, Any]` (Form siehe Test — die Session-API in Slice 4 liefert genau dieses Dict aus).

- [ ] **Step 1: Write the failing tests (anhängen)**

```python
def test_resume_point_progression(tmp_path: Path) -> None:
    board = Board.create(tmp_path / "board", _meta())
    assert board.resume_point([1, 2]) == "scene_reviews:1"
    board.save_scene_review(_review(1))
    assert board.resume_point([1, 2]) == "scene_reviews:2"
    board.save_scene_review(_review(2))
    assert board.resume_point([1, 2]) == "storyline"
    board.save("storyline", _storyline())
    assert board.resume_point([1, 2]) == "script"
    board.save("script", _script())
    assert board.resume_point([1, 2]) == "voice"


def test_resume_point_reflects_invalidation(tmp_path: Path) -> None:
    board = Board.create(tmp_path / "board", _meta())
    board.save_scene_review(_review(1))
    board.save("storyline", _storyline())
    board.save("script", _script())
    board.save("storyline", _storyline("changed"))  # invalidates script
    assert board.resume_point([1]) == "script"


def test_status_shape(tmp_path: Path) -> None:
    board = Board.create(tmp_path / "board", _meta())
    board.save_scene_review(_review(1))
    board.save("storyline", _storyline("a"))
    board.save("storyline", _storyline("b"))
    status = board.status()
    assert status["meta"]["session_id"] == "s1"
    assert status["scene_reviews"] == {"count": 1, "scenes": [1]}
    assert status["artifacts"]["storyline"] == {"version": 2, "archived_versions": [1]}
    assert status["artifacts"]["qa_report"] == {"version": None, "archived_versions": []}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_production_board.py -v`
Expected: FAIL — `AttributeError: 'Board' object has no attribute 'resume_point'`

- [ ] **Step 3: Write the implementation (Methoden ergänzen)**

```python
    # -- progress -----------------------------------------------------------

    def resume_point(self, expected_scenes: list[int]) -> str:
        """First missing artifact — where a (re)started session job continues."""
        have = {r.scene_number for r in self.scene_reviews()}
        missing = [n for n in expected_scenes if n not in have]
        if missing:
            return f"scene_reviews:{missing[0]}"
        for name in _CHAIN:
            if self.load(name) is None:
                return name
        return "done"

    def status(self) -> dict[str, Any]:
        """Board summary for the session API (versions + presence)."""
        reviews = self.scene_reviews()
        artifacts: dict[str, Any] = {}
        for name in _CHAIN:
            cur = self.load(name)
            artifacts[name] = {
                "version": int(getattr(cur, "version")) if cur is not None else None,
                "archived_versions": self.versions(name),
            }
        return {
            "meta": json.loads(self.meta().model_dump_json()),
            "scene_reviews": {
                "count": len(reviews),
                "scenes": [r.scene_number for r in reviews],
            },
            "artifacts": artifacts,
        }
```

(Jetzt werden `json` und `Any` benutzt — Importe aus Task 2 sind ab hier ruff-sauber.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_production_board.py -v`
Expected: PASS (13 Tests)

- [ ] **Step 5: Lint + typecheck + commit**

```bash
uv run ruff check src/laura/short_creator/board.py tests/test_production_board.py
uv run mypy src/laura/short_creator/board.py
git add services/local-api/src/laura/short_creator/board.py services/local-api/tests/test_production_board.py
git commit -m "feat(short-creator): board resume_point + status summary"
```

---

### Task 5: Zoom-Mathematik — `roi_to_window` + `start_window`

**Files:**
- Create: `services/local-api/src/laura/render/zoom.py`
- Test: `services/local-api/tests/test_zoom_math.py`

**Interfaces:**
- Consumes: nichts (rein).
- Produces: `MIN_HEIGHT_FRAC = 0.55`, `roi_to_window(roi, *, src_w, src_h, out_w, out_h, min_height_frac=MIN_HEIGHT_FRAC) -> tuple[int, int, int, int]` (x, y, w, h — Pixel, gerade), `start_window(end_win, *, src_w, src_h, out_w, out_h) -> tuple[int, int, int, int]`. Task 6–8 bauen darauf.

- [ ] **Step 1: Write the failing tests**

```python
"""Pure window math for the zoom_hybrid fit mode."""

from laura.render.zoom import MIN_HEIGHT_FRAC, roi_to_window, start_window


def _aspect(win: tuple[int, int, int, int]) -> float:
    return win[2] / win[3]


def test_small_roi_hits_min_height_floor() -> None:
    # tiny roi in the middle of 1080p → window height = 55% of 1080 = 594 → even 594
    win = roi_to_window((0.45, 0.45, 0.1, 0.1), src_w=1920, src_h=1080, out_w=1080, out_h=1920)
    x, y, w, h = win
    assert h == 594 and w == 334  # 594 * (1080/1920) = 334.125 → even 334
    assert abs(_aspect(win) - 1080 / 1920) < 0.01
    assert x >= 0 and y >= 0 and x + w <= 1920 and y + h <= 1080


def test_wide_roi_clamps_to_source() -> None:
    # roi 70% wide needs h = 0.7*1920/0.5625 ≈ 2389 > 1080 → clamp to full height
    win = roi_to_window((0.15, 0.3, 0.7, 0.3), src_w=1920, src_h=1080, out_w=1080, out_h=1920)
    x, y, w, h = win
    assert h == 1080 and w == 606  # 1080 * 0.5625 = 607.5 → even 606
    assert 0 <= x <= 1920 - w and y == 0


def test_corner_roi_is_clamped_inside_frame() -> None:
    win = roi_to_window((0.9, 0.85, 0.1, 0.15), src_w=1920, src_h=1080, out_w=1080, out_h=1920)
    x, y, w, h = win
    assert x + w <= 1920 and y + h <= 1080 and x >= 0 and y >= 0


def test_window_values_are_even() -> None:
    win = roi_to_window((0.33, 0.21, 0.17, 0.13), src_w=1920, src_h=1080, out_w=1080, out_h=1920)
    assert all(v % 2 == 0 for v in win)


def test_square_output_aspect() -> None:
    win = roi_to_window((0.4, 0.4, 0.2, 0.2), src_w=1920, src_h=1080, out_w=1080, out_h=1080)
    assert abs(_aspect(win) - 1.0) < 0.01
    assert win[3] >= int(MIN_HEIGHT_FRAC * 1080)


def test_start_window_is_full_height_centered_on_end_win() -> None:
    end = roi_to_window((0.6, 0.1, 0.25, 0.25), src_w=1920, src_h=1080, out_w=1080, out_h=1920)
    start = start_window(end, src_w=1920, src_h=1080, out_w=1080, out_h=1920)
    sx, sy, sw, sh = start
    assert sh == 1080 and sw == 606 and sy == 0
    end_cx = end[0] + end[2] / 2
    assert abs((sx + sw / 2) - end_cx) <= sw / 2  # centered as far as clamping allows
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_zoom_math.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'laura.render.zoom'`

- [ ] **Step 3: Write the implementation**

```python
"""ROI→window math and filtergraph builders for the ``zoom_hybrid`` fit mode.

``zoom_hybrid`` renders each segment in two phases: the full frame on a
blurred-fill canvas, then — from ``zoom_start_s`` — a smooth push into an exact
out-aspect window around the scene's region of interest.  All window math is
pure and pixel-integer.  A linear blend of two windows that each lie fully
inside the source frame stays inside the frame, so the animated crop never
needs runtime clamping.
"""

from __future__ import annotations

MIN_HEIGHT_FRAC = 0.55


def _even(v: float) -> int:
    return int(v) // 2 * 2


def roi_to_window(
    roi: tuple[float, float, float, float],
    *,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    min_height_frac: float = MIN_HEIGHT_FRAC,
) -> tuple[int, int, int, int]:
    """Expand a normalized ROI box to an exact ``out_w:out_h`` pixel window.

    The window covers the ROI, keeps at least ``min_height_frac`` of the source
    height (legibility floor — never zoom into pixel mush), is centered on the
    ROI center where possible and clamped fully inside the source frame.
    Returns ``(x, y, w, h)`` as even integers.
    """
    rx, ry, rw, rh = roi
    px, py = rx * src_w, ry * src_h
    pw, ph = rw * src_w, rh * src_h
    target = out_w / out_h

    h = max(ph, pw / target, min_height_frac * src_h)
    h = min(h, float(src_h))
    w = h * target
    if w > src_w:
        w = float(src_w)
        h = min(w / target, float(src_h))

    cx, cy = px + pw / 2, py + ph / 2
    x = min(max(cx - w / 2, 0.0), src_w - w)
    y = min(max(cy - h / 2, 0.0), src_h - h)

    w_i = max(_even(w), 2)
    h_i = max(_even(h), 2)
    x_i = min(max(_even(x), 0), src_w - w_i)
    y_i = min(max(_even(y), 0), src_h - h_i)
    return (x_i, y_i, w_i, h_i)


def start_window(
    end_win: tuple[int, int, int, int],
    *,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
) -> tuple[int, int, int, int]:
    """Widest out-aspect window centered on ``end_win`` — where the push starts."""
    x, y, w, h = end_win
    roi = (x / src_w, y / src_h, w / src_w, h / src_h)
    return roi_to_window(
        roi, src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h, min_height_frac=1.0
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_zoom_math.py -v`
Expected: PASS (6 Tests)

- [ ] **Step 5: Lint + typecheck + commit**

```bash
uv run ruff check src/laura/render/zoom.py tests/test_zoom_math.py
uv run mypy src/laura/render/zoom.py
git add services/local-api/src/laura/render/zoom.py services/local-api/tests/test_zoom_math.py
git commit -m "feat(render): roi-to-window math for zoom_hybrid (aspect snap, legibility floor, clamping)"
```

---

### Task 6: Zoom-Mathematik — `ZoomSpec`, Easing-Ausdrücke, Option-Parsing

**Files:**
- Modify: `services/local-api/src/laura/render/zoom.py`
- Test: `services/local-api/tests/test_zoom_math.py` (anhängen)

**Interfaces:**
- Consumes: `roi_to_window`, `start_window` (Task 5).
- Produces: `DEFAULT_TRANSITION_S = 0.6`, frozen dataclass `ZoomSpec(end_win, start_win, zoom_start_s, transition_s)`, `smooth_progress_expr(duration_s) -> str`, `zoom_crop_exprs(spec) -> tuple[str, str, str, str]` (w, h, x, y), `zoom_spec_from_option(option, *, src_w, src_h, out_w, out_h, segment_seconds) -> ZoomSpec | None`. Task 7–9 konsumieren exakt diese Signaturen.

- [ ] **Step 1: Write the failing tests (anhängen)**

```python
from laura.render.zoom import (  # noqa: E402
    DEFAULT_TRANSITION_S,
    ZoomSpec,
    smooth_progress_expr,
    zoom_crop_exprs,
    zoom_spec_from_option,
)


def _spec() -> ZoomSpec:
    end = roi_to_window((0.6, 0.1, 0.25, 0.25), src_w=1920, src_h=1080, out_w=1080, out_h=1920)
    start = start_window(end, src_w=1920, src_h=1080, out_w=1080, out_h=1920)
    return ZoomSpec(end_win=end, start_win=start, zoom_start_s=1.0, transition_s=0.6)


def test_smooth_progress_expr_shape() -> None:
    expr = smooth_progress_expr(0.6)
    assert "clip(t/0.6000,0,1)" in expr and "3-2*" in expr


def test_zoom_crop_exprs_interpolate_between_windows() -> None:
    spec = _spec()
    w, h, x, y = zoom_crop_exprs(spec)
    (sx, sy, sw, sh), (ex, ey, ew, eh) = spec.start_win, spec.end_win
    assert f"({sw}+({ew}-{sw})*" in w and w.startswith("trunc(") and w.endswith("/2)*2")
    assert f"({sh}+({eh}-{sh})*" in h
    assert f"({sx}+({ex}-{sx})*" in x
    assert f"({sy}+({ey}-{sy})*" in y


def test_zoom_spec_from_option_happy_path() -> None:
    spec = zoom_spec_from_option(
        {"roi": {"x": 0.6, "y": 0.1, "w": 0.25, "h": 0.25}, "zoom_start_s": 1.0},
        src_w=1920, src_h=1080, out_w=1080, out_h=1920, segment_seconds=4.0,
    )
    assert spec is not None
    assert spec.zoom_start_s == 1.0 and spec.transition_s == DEFAULT_TRANSITION_S
    assert spec.start_win[3] == 1080  # push starts at full height


def test_zoom_spec_from_option_fallbacks_return_none() -> None:
    kw = dict(src_w=1920, src_h=1080, out_w=1080, out_h=1920, segment_seconds=4.0)
    assert zoom_spec_from_option(None, **kw) is None
    assert zoom_spec_from_option({}, **kw) is None                                   # no roi
    assert zoom_spec_from_option({"roi": {"x": 2.0, "y": 0, "w": 0.5, "h": 0.5}}, **kw) is None
    assert zoom_spec_from_option({"roi": {"x": 0.8, "y": 0, "w": 0.5, "h": 0.5}}, **kw) is None
    assert zoom_spec_from_option({"roi": {"x": 0.1, "y": 0.1, "w": "bad", "h": 0.5}}, **kw) is None
    short = dict(kw, segment_seconds=0.5)  # too short for a visible push
    assert zoom_spec_from_option({"roi": {"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.3}}, **short) is None
    # roi that already needs (almost) the whole frame → nothing to push into
    assert zoom_spec_from_option({"roi": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}}, **kw) is None


def test_zoom_spec_from_option_clamps_timing_to_segment() -> None:
    spec = zoom_spec_from_option(
        {"roi": {"x": 0.6, "y": 0.1, "w": 0.2, "h": 0.2}, "zoom_start_s": 99.0},
        src_w=1920, src_h=1080, out_w=1080, out_h=1920, segment_seconds=2.0,
    )
    assert spec is not None
    assert spec.zoom_start_s + spec.transition_s <= 2.0 + 1e-9
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_zoom_math.py -v`
Expected: FAIL — `ImportError: cannot import name 'ZoomSpec'`

- [ ] **Step 3: Write the implementation (in `zoom.py` ergänzen)**

```python
from dataclasses import dataclass
from typing import Any

DEFAULT_TRANSITION_S = 0.6
_MIN_TRANSITION_S = 0.1
_MIN_ZOOM_SEGMENT_S = 0.8


@dataclass(frozen=True)
class ZoomSpec:
    """Per-segment hybrid-zoom parameters in source-pixel space.

    ``zoom_start_s`` is relative to the segment start; the push runs over
    ``transition_s`` and then holds ``end_win`` until the segment ends.
    """

    end_win: tuple[int, int, int, int]
    start_win: tuple[int, int, int, int]
    zoom_start_s: float
    transition_s: float = DEFAULT_TRANSITION_S


def smooth_progress_expr(duration_s: float) -> str:
    """ffmpeg per-frame eased progress 0→1 over local time ``t`` ∈ [0, duration]."""
    lin = f"clip(t/{duration_s:.4f},0,1)"
    return f"(({lin})*({lin})*(3-2*({lin})))"


def zoom_crop_exprs(spec: ZoomSpec) -> tuple[str, str, str, str]:
    """Crop expressions (w, h, x, y) pushing ``start_win`` → ``end_win``.

    Local time base: the zoom branch is trimmed to begin at ``zoom_start_s``,
    so the push covers t ∈ [0, transition_s].  ``trunc(…/2)*2`` keeps every
    animated value an even integer (yuv420 chroma alignment).
    """
    p = smooth_progress_expr(spec.transition_s)
    sx, sy, sw, sh = spec.start_win
    ex, ey, ew, eh = spec.end_win
    w = f"trunc(({sw}+({ew}-{sw})*{p})/2)*2"
    h = f"trunc(({sh}+({eh}-{sh})*{p})/2)*2"
    x = f"trunc(({sx}+({ex}-{sx})*{p})/2)*2"
    y = f"trunc(({sy}+({ey}-{sy})*{p})/2)*2"
    return (w, h, x, y)


def zoom_spec_from_option(
    option: dict[str, Any] | None,
    *,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    segment_seconds: float,
) -> ZoomSpec | None:
    """Build a renderer ``ZoomSpec`` from one per-segment export option.

    Option shape: ``{"roi": {"x","y","w","h"}, "zoom_start_s": float,
    "transition_s": float?}`` (roi normalized).  Returns ``None`` — meaning
    plain full-frame blur-fill — for ``None``/malformed/out-of-range ROIs and
    for segments too short for a visible push.  Fallback contract: never
    crash, never center-crop.
    """
    if option is None:
        return None
    roi_raw = option.get("roi") or {}
    try:
        rx, ry = float(roi_raw["x"]), float(roi_raw["y"])
        rw, rh = float(roi_raw["w"]), float(roi_raw["h"])
        t0 = float(option.get("zoom_start_s", 0.0))
        td = float(option.get("transition_s", DEFAULT_TRANSITION_S))
    except (KeyError, TypeError, ValueError):
        return None
    if not (0.0 <= rx <= 1.0 and 0.0 <= ry <= 1.0 and 0.0 < rw <= 1.0 and 0.0 < rh <= 1.0):
        return None
    if rx + rw > 1.0 + 1e-9 or ry + rh > 1.0 + 1e-9:
        return None
    if segment_seconds < _MIN_ZOOM_SEGMENT_S or td <= 0.0:
        return None

    t0 = min(max(t0, 0.0), max(segment_seconds - td, 0.0))
    td = min(td, segment_seconds - t0)
    if td < _MIN_TRANSITION_S:
        return None

    end_win = roi_to_window((rx, ry, rw, rh), src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h)
    start = start_window(end_win, src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h)
    if start == end_win:
        return None
    return ZoomSpec(
        end_win=end_win,
        start_win=start,
        zoom_start_s=round(t0, 4),
        transition_s=round(td, 4),
    )
```

(Die `dataclass`/`Any`-Importe an den Dateianfang ziehen — ein `from __future__`-Block, dann stdlib.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_zoom_math.py -v`
Expected: PASS (11 Tests)

- [ ] **Step 5: Lint + typecheck + commit**

```bash
uv run ruff check src/laura/render/zoom.py tests/test_zoom_math.py
uv run mypy src/laura/render/zoom.py
git add services/local-api/src/laura/render/zoom.py services/local-api/tests/test_zoom_math.py
git commit -m "feat(render): ZoomSpec, eased crop expressions, option parsing with fallback contract"
```

---

### Task 7: Filtergraph-Builder — `reel_blur_fill_graph`-Tag + Segment-/Concat-Graph

**Files:**
- Modify: `services/local-api/src/laura/render/reel.py` (nur `reel_blur_fill_graph`: `tag`-Parameter)
- Modify: `services/local-api/src/laura/render/zoom.py`
- Test: `services/local-api/tests/test_zoom_graph.py` (neu)

**Interfaces:**
- Consumes: `ZoomSpec`, `zoom_crop_exprs` (Task 6); `reel_blur_fill_graph(in_label, out_label, *, out_w, out_h)` (bestehend).
- Produces: `reel_blur_fill_graph(..., tag: str = "_rb")` — Default byte-identisch; `zoom_hybrid_segment_parts(input_idx, seg_idx, *, start_frame, end_frame_exclusive, spec, out_w, out_h) -> tuple[list[str], str]`; `zoom_concat_graph(clips, specs, *, audio_flags, has_base_audio, rate_num, rate_den, out_w, out_h) -> tuple[str, str, str | None]` (parts, `"[vcat]"`, `"[abase]"`|None). Task 8 ruft `zoom_concat_graph` aus `render_clips_mp4`.

- [ ] **Step 1: Write the failing tests**

```python
"""Filtergraph builders for zoom_hybrid — exact string assertions."""

from laura.render.reel import reel_blur_fill_graph
from laura.render.zoom import (
    ZoomSpec,
    roi_to_window,
    start_window,
    zoom_concat_graph,
    zoom_hybrid_segment_parts,
)


def _spec() -> ZoomSpec:
    end = roi_to_window((0.6, 0.1, 0.25, 0.25), src_w=1920, src_h=1080, out_w=1080, out_h=1920)
    start = start_window(end, src_w=1920, src_h=1080, out_w=1080, out_h=1920)
    return ZoomSpec(end_win=end, start_win=start, zoom_start_s=1.0, transition_s=0.6)


def test_blur_fill_default_tag_is_byte_identical() -> None:
    graph = reel_blur_fill_graph("[vcat]", "[out]")
    assert "[_rbbg]" in graph and "[_rbfg]" in graph and "[_rbbl]" in graph and "[_rbfl]" in graph


def test_blur_fill_custom_tag() -> None:
    graph = reel_blur_fill_graph("[a]", "[b]", tag="_z3")
    assert "[_z3bg]" in graph and "[_rbbg]" not in graph


def test_segment_without_spec_is_blur_only() -> None:
    parts, label = zoom_hybrid_segment_parts(
        0, 0, start_frame=30, end_frame_exclusive=150, spec=None, out_w=1080, out_h=1920)
    assert label == "[zh0]"
    joined = ";".join(parts)
    assert "trim=start_frame=30:end_frame=150" in joined
    assert "[_z0bg]" in joined          # per-segment blur tag
    assert "xfade" not in joined
    assert joined.endswith("setsar=1[zh0]")


def test_segment_with_spec_builds_hybrid_graph() -> None:
    parts, label = zoom_hybrid_segment_parts(
        2, 2, start_frame=0, end_frame_exclusive=120, spec=_spec(), out_w=1080, out_h=1920)
    assert label == "[zh2]"
    joined = ";".join(parts)
    assert "split=2[zfa2][zza2]" in joined
    assert "[_z2bg]" in joined
    # _fmt_seconds trims trailing zeros: 1.0 → "1", 0.6 → "0.6"
    assert "trim=start=1,setpts=PTS-STARTPTS" in joined  # zoom branch starts at zoom_start_s
    assert "crop=w='trunc((" in joined
    assert "scale=1080:1920:flags=lanczos" in joined
    assert "xfade=transition=fade:duration=0.6:offset=1," in joined


def test_concat_graph_single_and_multi() -> None:
    parts, v, a = zoom_concat_graph(
        [(0, 120)], [None], audio_flags=[False], has_base_audio=False,
        rate_num=30, rate_den=1, out_w=1080, out_h=1920)
    assert v == "[vcat]" and a is None
    assert "[zh0]null[vcat]" in parts

    parts2, v2, a2 = zoom_concat_graph(
        [(0, 120), (120, 240)], [None, _spec()],
        audio_flags=[True, True], has_base_audio=True,
        rate_num=30, rate_den=1, out_w=1080, out_h=1920)
    assert v2 == "[vcat]" and a2 == "[abase]"
    assert "[zh0][zh1]concat=n=2:v=1:a=0[vcat]" in parts2
    assert "[0:a]atrim=start=0:end=4,asetpts=PTS-STARTPTS[zba0]" in parts2
    assert "[zba0][zba1]concat=n=2:v=0:a=1[abase]" in parts2


def test_concat_graph_silent_input_gets_anullsrc() -> None:
    parts, _v, a = zoom_concat_graph(
        [(0, 120)], [None], audio_flags=[False], has_base_audio=True,
        rate_num=30, rate_den=1, out_w=1080, out_h=1920)
    assert a == "[abase]"
    assert "anullsrc=channel_layout=stereo:sample_rate=48000" in parts
    assert "[zba0]anull[abase]" in parts
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_zoom_graph.py -v`
Expected: FAIL — `TypeError: reel_blur_fill_graph() got an unexpected keyword argument 'tag'` bzw. ImportError für die Builder.

- [ ] **Step 3a: `reel.py` — `tag`-Parameter (Default byte-identisch)**

In `reel_blur_fill_graph` die Signatur um `tag: str = "_rb"` erweitern und die vier internen Label-Zuweisungen ersetzen:

```python
def reel_blur_fill_graph(
    in_label: str, out_label: str, *, out_w: int = 1080, out_h: int = 1920, tag: str = "_rb"
) -> str:
```

```python
    # Internal scratch labels — the tag keeps multiple instances of this
    # sub-graph (one per zoom_hybrid segment) collision-free in one graph.
    bg_split = f"[{tag}bg]"
    fg_split = f"[{tag}fg]"
    bg_blurred = f"[{tag}bl]"
    fg_scaled = f"[{tag}fl]"
```

(Docstring um einen `tag`-Satz ergänzen. Default `"_rb"` reproduziert exakt `[_rbbg]`/`[_rbfg]`/`[_rbbl]`/`[_rbfl]` — bestehende Pfade bleiben byte-identisch.)

- [ ] **Step 3b: `zoom.py` — Segment- und Concat-Builder ergänzen**

```python
from laura.render.reel import reel_blur_fill_graph


def _fmt_seconds(value: float) -> str:
    """Trim-friendly seconds formatting (no trailing zeros, plain int stays int)."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


def zoom_hybrid_segment_parts(
    input_idx: int,
    seg_idx: int,
    *,
    start_frame: int,
    end_frame_exclusive: int,
    spec: ZoomSpec | None,
    out_w: int,
    out_h: int,
) -> tuple[list[str], str]:
    """Filtergraph parts rendering ONE trimmed segment as hybrid zoom.

    ``spec=None`` → full-frame blur-fill only (the fallback contract).  All
    internal labels carry ``seg_idx`` so any number of segments compose into
    one ``filter_complex`` without label collisions.  Returns
    ``(parts, out_label)``; the output is exactly ``out_w×out_h``, SAR 1.
    """
    out_label = f"[zh{seg_idx}]"
    composed = f"[zfx{seg_idx}]"
    trim = (
        f"[{input_idx}:v]trim=start_frame={start_frame}:end_frame={end_frame_exclusive},"
        f"setpts=PTS-STARTPTS,settb=AVTB"
    )
    if spec is None:
        src = f"[zsrc{seg_idx}]"
        return (
            [
                f"{trim}{src}",
                reel_blur_fill_graph(src, composed, out_w=out_w, out_h=out_h, tag=f"_z{seg_idx}"),
                f"{composed}setsar=1{out_label}",
            ],
            out_label,
        )

    full_in, zoom_in = f"[zfa{seg_idx}]", f"[zza{seg_idx}]"
    full_out, zoom_out = f"[zfull{seg_idx}]", f"[zzoom{seg_idx}]"
    w, h, x, y = zoom_crop_exprs(spec)
    parts = [
        f"{trim},split=2{full_in}{zoom_in}",
        reel_blur_fill_graph(full_in, composed, out_w=out_w, out_h=out_h, tag=f"_z{seg_idx}"),
        f"{composed}setsar=1{full_out}",
        (
            f"{zoom_in}trim=start={_fmt_seconds(spec.zoom_start_s)},setpts=PTS-STARTPTS,"
            f"crop=w='{w}':h='{h}':x='{x}':y='{y}',"
            f"scale={out_w}:{out_h}:flags=lanczos,setsar=1,settb=AVTB{zoom_out}"
        ),
        (
            f"{full_out}{zoom_out}xfade=transition=fade"
            f":duration={_fmt_seconds(spec.transition_s)}"
            f":offset={_fmt_seconds(spec.zoom_start_s)},settb=AVTB{out_label}"
        ),
    ]
    return parts, out_label


def zoom_concat_graph(
    clips: list[tuple[int, int]],
    specs: list["ZoomSpec | None"],
    *,
    audio_flags: list[bool],
    has_base_audio: bool,
    rate_num: int,
    rate_den: int,
    out_w: int,
    out_h: int,
) -> tuple[str, str, str | None]:
    """Full pre-caption filtergraph for the zoom_hybrid path.

    Returns ``(parts, "[vcat]", "[abase]" | None)``.  Video: concat of the
    per-segment hybrid graphs.  Audio: the classic per-segment atrim concat —
    byte-equivalent semantics to the standard concat path, so the voiceover
    post-mux and loudnorm stages downstream stay unchanged.
    """
    parts: list[str] = []
    v_labels: list[str] = []
    for i, ((fin, fout), spec) in enumerate(zip(clips, specs, strict=True)):
        seg_parts, label = zoom_hybrid_segment_parts(
            i, i, start_frame=fin, end_frame_exclusive=fout,
            spec=spec, out_w=out_w, out_h=out_h,
        )
        parts.extend(seg_parts)
        v_labels.append(label)

    n = len(v_labels)
    if n == 1:
        parts.append(f"{v_labels[0]}null[vcat]")
    else:
        parts.append(f"{''.join(v_labels)}concat=n={n}:v=1:a=0[vcat]")

    a_label: str | None = None
    if has_base_audio:
        for i, (fin, fout) in enumerate(clips):
            if audio_flags[i]:
                start = fin * rate_den / rate_num
                end = fout * rate_den / rate_num
                parts.append(
                    f"[{i}:a]atrim=start={_fmt_seconds(start)}:end={_fmt_seconds(end)},"
                    f"asetpts=PTS-STARTPTS[zba{i}]"
                )
            else:
                dur = (fout - fin) * rate_den / rate_num
                parts.append(
                    "anullsrc=channel_layout=stereo:sample_rate=48000,"
                    f"atrim=duration={_fmt_seconds(dur)},asetpts=PTS-STARTPTS[zba{i}]"
                )
        if n == 1:
            parts.append("[zba0]anull[abase]")
        else:
            parts.append(f"{''.join(f'[zba{i}]' for i in range(n))}concat=n={n}:v=0:a=1[abase]")
        a_label = "[abase]"

    return ";".join(parts), "[vcat]", a_label
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_zoom_graph.py tests/test_reel_filters.py -v`
Expected: PASS — inkl. der BESTEHENDEN reel-Tests (Default-Tag byte-identisch).

- [ ] **Step 5: Lint + typecheck + commit**

```bash
uv run ruff check src/laura/render/zoom.py src/laura/render/reel.py tests/test_zoom_graph.py
uv run mypy src/laura/render/zoom.py src/laura/render/reel.py
git add services/local-api/src/laura/render/zoom.py services/local-api/src/laura/render/reel.py services/local-api/tests/test_zoom_graph.py
git commit -m "feat(render): zoom_hybrid segment/concat filtergraph builders + taggable blur-fill labels"
```

---

### Task 8: `render_clips_mp4` — `zoom_specs`-Zweig

**Files:**
- Modify: `services/local-api/src/laura/render/mp4.py` (`render_clips_mp4`, ~Zeile 444–693)
- Test: `services/local-api/tests/test_render_mp4_zoom.py` (neu)

**Interfaces:**
- Consumes: `zoom_concat_graph`, `ZoomSpec` (Task 7).
- Produces: `render_clips_mp4(..., zoom_specs: list[ZoomSpec | None] | None = None)`. Verhalten: `None` → alle Pfade byte-identisch zu heute; gesetzt → eigener Graph-Zweig (vor dem `has_crossfade`-Branch), Captions/Drawtext obendrauf, Audio wie gehabt. Validierung: Länge = `len(clips)` sonst `ValueError`; `vertical=False` → `ValueError`; `video_transitions` gesetzt → `ValueError`.

- [ ] **Step 1: Write the failing tests**

```python
"""render_clips_mp4 zoom_specs wiring — validation without invoking ffmpeg."""

from pathlib import Path

import pytest

from laura.render.mp4 import render_clips_mp4
from laura.render.transitions import VideoTransition
from laura.render.zoom import ZoomSpec


def _spec() -> ZoomSpec:
    return ZoomSpec(end_win=(1200, 0, 606, 1080), start_win=(657, 0, 606, 1080),
                    zoom_start_s=1.0, transition_s=0.6)


def test_zoom_specs_length_mismatch_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="zoom_specs"):
        render_clips_mp4(
            [(tmp_path / "a.mp4", 0, 60), (tmp_path / "a.mp4", 60, 120)],
            tmp_path / "out.mp4", rate_num=30, rate_den=1,
            vertical=True, zoom_specs=[_spec()],
        )


def test_zoom_requires_vertical(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="vertical"):
        render_clips_mp4(
            [(tmp_path / "a.mp4", 0, 60)], tmp_path / "out.mp4",
            rate_num=30, rate_den=1, vertical=False, zoom_specs=[_spec()],
        )


def test_zoom_excludes_video_transitions(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="transitions"):
        render_clips_mp4(
            [(tmp_path / "a.mp4", 0, 60), (tmp_path / "a.mp4", 60, 120)],
            tmp_path / "out.mp4", rate_num=30, rate_den=1, vertical=True,
            zoom_specs=[_spec(), None],
            video_transitions=[VideoTransition(kind="crossfade", boundary=0, duration_frames=12)],
        )
```

**Hinweis für den Implementierer:** `VideoTransition` liegt bereits im Render-Paket (Import in `mp4.py` nachsehen und den Konstruktor im Test an die echte Signatur anpassen — entscheidend ist nur: irgendeine nicht-leere `video_transitions`-Liste). Die Validierungen müssen VOR jedem Datei-/ffprobe-Zugriff laufen (`_source_has_audio` darf für diese Tests nie erreicht werden — die Testdateien existieren nicht).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_render_mp4_zoom.py -v`
Expected: FAIL — `TypeError: render_clips_mp4() got an unexpected keyword argument 'zoom_specs'`

- [ ] **Step 3: Write the implementation**

3a. Import oben in `mp4.py` ergänzen: `from laura.render.zoom import ZoomSpec, zoom_concat_graph`

3b. Signatur: nach `video_transitions: list[VideoTransition] | None = None,` einfügen: `zoom_specs: list[ZoomSpec | None] | None = None,`

3c. Validierung als ERSTE Zeilen des Funktionskörpers (vor `dest.parent.mkdir`):

```python
    if zoom_specs is not None:
        if len(zoom_specs) != len(clips):
            raise ValueError("zoom_specs must align 1:1 with clips")
        if not vertical:
            raise ValueError("zoom_hybrid requires vertical=True")
        if video_transitions:
            raise ValueError("zoom_hybrid excludes video transitions (v1)")
    use_zoom = zoom_specs is not None
```

3d. Reel-Chain-Aufruf anpassen (bestehende Zeile `reel = reel_video_chain(vertical=vertical and not use_blur_fill, ...)`):

```python
        reel = reel_video_chain(
            vertical=vertical and not use_blur_fill and not use_zoom,
```

3e. Graph-Zweig VOR `if has_crossfade:` einschieben (der `elif has_crossfade:` daraus machen):

```python
        if use_zoom:
            assert zoom_specs is not None
            zparts, v_label, _a_label = zoom_concat_graph(
                [(fin, fout) for _src, fin, fout in clips],
                zoom_specs,
                audio_flags=audio_flags,
                has_base_audio=has_base_audio,
                rate_num=rate_num,
                rate_den=rate_den,
                out_w=out_w,
                out_h=out_h,
            )
            post_caption = ",".join(p for p in (reel, caption_filter) if p)
            if post_caption:
                parts = f"{zparts};{v_label}{post_caption}[out]"
            else:
                parts = f"{zparts};{v_label}null[out]"
        elif has_crossfade:
            ...  # bestehender Code unverändert
```

(Der Audio-Anschluss stimmt automatisch: `zoom_concat_graph` emittiert `[abase]`, exakt das Label, das der nachfolgende Overlay-/Loudnorm-Code erwartet. `use_blur_fill` bleibt für den Zoom-Zweig irrelevant — Blur passiert pro Segment im Zoom-Graph.)

3f. Docstring von `render_clips_mp4` um einen `zoom_specs`-Absatz ergänzen (Verhalten + v1-Ausschlüsse, wie in den Global Constraints).

- [ ] **Step 4: Run tests to verify they pass — inkl. Bestandsschutz**

Run: `uv run pytest tests/test_render_mp4_zoom.py tests/test_render_mp4.py tests/test_render_mp4_filter.py tests/test_render_xfade.py -v`
Expected: PASS — neue Tests grün, alle bestehenden mp4-Tests unverändert grün.

- [ ] **Step 5: Lint + typecheck + commit**

```bash
uv run ruff check src/laura/render/mp4.py tests/test_render_mp4_zoom.py
uv run mypy src/laura/render/mp4.py
git add services/local-api/src/laura/render/mp4.py services/local-api/tests/test_render_mp4_zoom.py
git commit -m "feat(render): zoom_specs branch in render_clips_mp4 (per-segment hybrid zoom)"
```

---

### Task 9: Handler + Tool — `zoom`-Option durchreichen

**Files:**
- Modify: `services/local-api/src/laura/render/shorts_render.py` (`handle_shorts_render`, nach `out_size`-Ermittlung ~Zeile 184)
- Modify: `services/local-api/src/laura/mcp/tools.py` (`tool_render_segments`)
- Test: `services/local-api/tests/test_shorts_render_zoom.py` (neu)

**Interfaces:**
- Consumes: `zoom_spec_from_option` (Task 6), `render_clips_mp4(zoom_specs=…)` (Task 8).
- Produces: Export-Option `zoom: list[dict | None]` (index-aligned mit `segments`); `tool_render_segments(..., zoom: list[dict[str, Any] | None] | None = None)` reicht sie in die Export-Optionen. Slice 3 (Coding-Agent) ruft genau diese Tool-Signatur.

- [ ] **Step 1: Write the failing tests**

Vorbild für Fixture-Aufbau: `tests/test_shorts_render_handler.py` (Projekt+Asset in Tmp-DB anlegen, `JobContext` bauen, `render_clips_mp4` monkeypatchen). Kern-Assertions:

```python
"""shorts.render handler: zoom option → ZoomSpec list → render_clips_mp4."""

# Imports/Fixtures analog test_shorts_render_handler.py übernehmen:
# _make_ctx(db, export_id) baut den JobContext; Projekt 30 fps; Asset mit
# width=1920, height=1080 und source_path auf eine (nicht dekodierte) Dummy-Datei.


def test_zoom_option_becomes_specs(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    def fake_render(clips, dest, **kwargs):
        captured.update(kwargs)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")

    monkeypatch.setattr("laura.render.shorts_render.render_clips_mp4", fake_render)
    export_id = _make_export(  # helper analog Bestandstest: options-dict siehe unten
        options={
            "segments": [[0, 120], [120, 240]],
            "asset_id": ASSET_ID,
            "captions": False,
            "zoom": [
                {"roi": {"x": 0.6, "y": 0.1, "w": 0.25, "h": 0.25}, "zoom_start_s": 1.0},
                None,
            ],
        }
    )
    handle_shorts_render(_make_ctx(export_id))
    specs = captured["zoom_specs"]
    assert specs is not None and len(specs) == 2
    assert specs[0] is not None and specs[0].end_win[3] >= int(0.55 * 1080)
    assert specs[1] is None


def test_zoom_length_mismatch_sets_export_error(tmp_path) -> None:
    export_id = _make_export(options={
        "segments": [[0, 120], [120, 240]], "asset_id": ASSET_ID, "captions": False,
        "zoom": [None],
    })
    with pytest.raises(ValueError, match="zoom"):
        handle_shorts_render(_make_ctx(export_id))
    assert _export_row(export_id)["status"] == "error"


def test_all_none_zoom_collapses_to_no_zoom(monkeypatch, tmp_path) -> None:
    captured: dict = {}
    monkeypatch.setattr("laura.render.shorts_render.render_clips_mp4",
                        lambda clips, dest, **kw: (captured.update(kw), dest.write_bytes(b"x")))
    export_id = _make_export(options={
        "segments": [[0, 120]], "asset_id": ASSET_ID, "captions": False,
        "zoom": [None],
    })
    handle_shorts_render(_make_ctx(export_id))
    assert captured["zoom_specs"] is None


def test_missing_asset_dimensions_falls_back(monkeypatch, tmp_path) -> None:
    # Asset ohne width/height anlegen → zoom_specs None, kein Fehler.
    ...
    assert captured["zoom_specs"] is None
```

(Die `...`-Zeile ist Fixture-Variation, kein Implementierungs-Platzhalter: identisch zu `test_all_none_zoom_collapses_to_no_zoom`, nur mit `width=None, height=None` beim Asset-Insert und einer echten ROI in `zoom`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_shorts_render_zoom.py -v`
Expected: FAIL — `KeyError: 'zoom_specs'` (Handler reicht nichts durch)

- [ ] **Step 3: Write the implementation**

3a. `shorts_render.py` — Import ergänzen: `from laura.render.zoom import ZoomSpec, zoom_spec_from_option`

3b. Nach der `out_size`-Ermittlung (hinter Zeile `out_size = (int(raw_size[0]), int(raw_size[1]))`) einfügen:

```python
    # zoom_hybrid: per-segment options, index-aligned with the segment list.
    # Missing/invalid entries and missing asset dimensions degrade to plain
    # blur-fill — never fail a render over a zoom hint.
    zoom_specs: list[ZoomSpec | None] | None = None
    zoom_raw = opts.get("zoom")
    if zoom_raw is not None:
        if not isinstance(zoom_raw, list) or len(zoom_raw) != len(segment_ranges):
            repos.set_export_error(ctx.db, export_id, "zoom must align 1:1 with segments")
            raise ValueError("zoom must align 1:1 with segments")
        src_w = asset.get("width")
        src_h = asset.get("height")
        if vertical and src_w and src_h:
            fps = rate_num / (rate_den or 1)
            zoom_specs = [
                zoom_spec_from_option(
                    z if isinstance(z, dict) else None,
                    src_w=int(src_w),
                    src_h=int(src_h),
                    out_w=out_size[0],
                    out_h=out_size[1],
                    segment_seconds=(end - start) / fps,
                )
                for z, (start, end) in zip(zoom_raw, segment_ranges, strict=True)
            ]
            if all(s is None for s in zoom_specs):
                zoom_specs = None
        else:
            _log.warning(
                "zoom requested but unusable (vertical=%s, dims=%s×%s) — blur fallback",
                vertical, src_w, src_h,
            )
```

**Achtung Reihenfolge:** Der Block braucht `segment_ranges`, `asset`, `rate_num/rate_den`, `vertical`, `out_size` — im Handler stehen `vertical`/`out_size` VOR `rate_num` nur teilweise; den Block direkt HINTER die `out_size`-Zeilen setzen (dort sind alle fünf definiert).

3c. Den `render_clips_mp4`-Aufruf um `zoom_specs=zoom_specs,` ergänzen (nach `reel_blur_fill=reel_blur_fill,`).

3d. `mcp/tools.py` — `tool_render_segments`: Signatur um Keyword `zoom: list[dict[str, Any] | None] | None = None` erweitern (hinter `voiceover_text`); im Options-Dict-Aufbau:

```python
    if zoom is not None:
        options["zoom"] = zoom
```

Docstring-Satz: „``zoom``: optional per-segment hybrid-zoom hints (``{"roi": {x,y,w,h}, "zoom_start_s": s}`` or ``None``), index-aligned with ``segments``; requires ``vertical=True``."

- [ ] **Step 4: Run tests to verify they pass — inkl. Bestandsschutz**

Run: `uv run pytest tests/test_shorts_render_zoom.py tests/test_shorts_render_handler.py tests/test_shorts_render_api.py -v`
Expected: PASS — Bestandstests unverändert grün (zoom_specs=None-Default).

- [ ] **Step 5: Lint + typecheck + commit**

```bash
uv run ruff check src/laura/render/shorts_render.py src/laura/mcp/tools.py tests/test_shorts_render_zoom.py
uv run mypy src/laura/render/shorts_render.py src/laura/mcp/tools.py
git add services/local-api/src/laura/render/shorts_render.py services/local-api/src/laura/mcp/tools.py services/local-api/tests/test_shorts_render_zoom.py
git commit -m "feat(render): zoom option through shorts.render handler + tool_render_segments"
```

---

### Task 10: E2E-Golden-Test — echter ffmpeg-Render mit Zoom

**Files:**
- Test: `services/local-api/tests/test_zoom_e2e.py` (neu)

**Interfaces:**
- Consumes: `render_clips_mp4(zoom_specs=…)` (Task 8), `zoom_spec_from_option` (Task 6).

- [ ] **Step 1: Write the test (skip-Konvention von `test_reel_e2e.py` übernehmen)**

```python
"""Real-ffmpeg golden check for the zoom_hybrid render path."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from laura.render.mp4 import render_clips_mp4
from laura.render.zoom import zoom_spec_from_option

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)


def _make_source(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "testsrc2=size=1920x1080:rate=30:duration=4",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


def _probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-show_entries", "stream=codec_type,width,height", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)


def test_zoom_hybrid_e2e(tmp_path: Path) -> None:
    src = tmp_path / "src.mp4"
    _make_source(src)
    dest = tmp_path / "out.mp4"
    spec = zoom_spec_from_option(
        {"roi": {"x": 0.6, "y": 0.1, "w": 0.3, "h": 0.3}, "zoom_start_s": 0.8},
        src_w=1920, src_h=1080, out_w=1080, out_h=1920, segment_seconds=2.0,
    )
    assert spec is not None
    render_clips_mp4(
        [(src, 0, 60), (src, 60, 120)], dest,
        rate_num=30, rate_den=1, vertical=True,
        zoom_specs=[spec, None], out_size=(1080, 1920),
    )
    assert dest.is_file() and dest.stat().st_size > 0
    info = _probe(dest)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert abs(float(info["format"]["duration"]) - 4.0) < 0.25
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_zoom_e2e.py -v`
Expected: PASS (~10–30 s). Schlägt der xfade/crop-Graph fehl, gibt ffmpeg die Filterfehler in der Exception aus — Graph-Strings gegen Task 7 prüfen, NICHT den Test lockern.

- [ ] **Step 3: Commit**

```bash
uv run ruff check tests/test_zoom_e2e.py
git add services/local-api/tests/test_zoom_e2e.py
git commit -m "test(render): real-ffmpeg golden check for zoom_hybrid"
```

---

### Task 11: Gesamt-Verifikation

**Files:** keine neuen.

- [ ] **Step 1: Volle Test-Suite**

Run: `uv run pytest -q`
Expected: alles grün (inkl. aller Bestands-Render-Tests — Beweis der Byte-Identität der Altpfade).

- [ ] **Step 2: Typecheck + Lint komplett**

```bash
uv run mypy src
uv run ruff check src tests
```
Expected: 0 Fehler.

- [ ] **Step 3: Ledger/Abschluss**

Kein eigener Commit nötig, wenn Schritte 1–2 nichts ändern. Ergebnis im SDD-Progress-Ledger festhalten (`.superpowers/sdd/progress.md`).
