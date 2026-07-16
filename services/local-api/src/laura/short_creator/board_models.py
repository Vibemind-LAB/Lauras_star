"""Pydantic schemas for the v2 production-board artifacts.

Every artifact the agent team exchanges lives on the board as one of these
models.  Validation is the contract: a malformed agent output is rejected at
the tool boundary (the agent sees the error and corrects itself) instead of
propagating silently.  Frame fields follow the project invariant: integer
frames, end-exclusive.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Platform format presets: (vertical, (out_w, out_h)). "x" is the native 16:9 pass-through.
# The canvas belongs to the format, and the format belongs to the production — a screen
# recording delivered to YouTube must not be cropped to a reel just because the reel is
# the default.
FORMAT_PRESETS: dict[str, tuple[bool, tuple[int, int]]] = {
    "insta": (True, (1080, 1920)),
    "x": (False, (1920, 1080)),
    "linkedin": (True, (1080, 1080)),
}

Format = Literal["insta", "x", "linkedin"]


def canvas_for(fmt: Format) -> tuple[bool, tuple[int, int]]:
    """``(vertical, (out_w, out_h))`` for a delivery format."""
    return FORMAT_PRESETS[fmt]


class Roi(BaseModel):
    """Normalized region of interest (fractions of source width/height)."""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _inside_frame(self) -> Roi:
        if self.x + self.w > 1.0 + 1e-9 or self.y + self.h > 1.0 + 1e-9:
            raise ValueError("roi exceeds frame bounds")
        return self


class BestWindow(BaseModel):
    """One strong moment inside a scene, relative to the scene start.

    ``roi`` is this window's own region of interest; ``None`` defers to the review's
    scene-level ``roi`` (which is also all a pre-``windows`` review JSON ever had).
    """

    model_config = ConfigDict(extra="forbid")

    offset_s: float = Field(ge=0.0)
    duration_s: float = Field(gt=0.0)
    roi: Roi | None = None


class SceneReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_number: int = Field(ge=1)
    src_start_frame: int = Field(ge=0)
    src_end_frame_exclusive: int
    description: str
    whats_happening: str
    hook_score: int = Field(ge=0, le=10)
    best_window: BestWindow
    windows: list[BestWindow] = Field(default_factory=list)
    roi: Roi | None = None
    legibility_notes: str = ""
    degraded: bool = False
    model: str = ""
    version: int = Field(default=1, ge=1)
    created_utc: str = ""

    @model_validator(mode="after")
    def _frames_end_exclusive(self) -> SceneReview:
        if self.src_end_frame_exclusive <= self.src_start_frame:
            raise ValueError("src_end_frame_exclusive must be > src_start_frame")
        return self

    @model_validator(mode="after")
    def _windows_consistent(self) -> SceneReview:
        """Normalize + guard the windows list: a review without one (every pre-``windows``
        JSON, every plain ``SceneReview(best_window=...)`` construction) gets
        ``[best_window]``; a provided list must lead with ``best_window`` and its windows
        must not overlap (touching is fine — the end-exclusive analog on the float axis)."""
        if not self.windows:
            self.windows = [self.best_window]
        if self.windows[0] != self.best_window:
            raise ValueError("windows[0] must equal best_window")
        spans = sorted((w.offset_s, w.offset_s + w.duration_s) for w in self.windows)
        for (_start, end), (next_start, _next_end) in zip(spans, spans[1:], strict=False):
            if next_start < end - 1e-9:
                raise ValueError("windows must not overlap")
        return self


class SceneWindowRef(BaseModel):
    """Storyline reference to one review window of a scene (0-based; 0 = ``best_window``)."""

    model_config = ConfigDict(extra="forbid")

    scene: int = Field(ge=1)
    window: int = Field(default=0, ge=0)


def as_scene_window(entry: int | SceneWindowRef) -> tuple[int, int]:
    """A storyline scene entry as ``(scene_number, window_index)``; plain ints are window 0."""
    if isinstance(entry, SceneWindowRef):
        return entry.scene, entry.window
    return entry, 0


class Chapter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter: int = Field(ge=1)
    role: Literal["hook", "problem", "feature", "payoff_cta"]
    message: str
    scene_numbers: list[int | SceneWindowRef] = Field(min_length=1)
    target_seconds: float = Field(gt=0.0)


class Storyline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1)
    red_thread: str
    arc: list[Chapter] = Field(min_length=1)

    @field_validator("arc")
    @classmethod
    def _no_duplicate_scene_windows(cls, arc: list[Chapter]) -> list[Chapter]:
        """The same (scene, window) pair may appear only once in the whole storyline —
        reusing a scene requires a different window of it."""
        seen: dict[tuple[int, int], int] = {}
        for chapter in arc:
            for entry in chapter.scene_numbers:
                key = as_scene_window(entry)
                first = seen.get(key)
                if first is not None:
                    scene, window = key
                    raise ValueError(
                        f"scene {scene} window {window} is referenced more than once "
                        f"(chapters {first} and {chapter.chapter}); reuse a scene only "
                        "with a different window"
                    )
                seen[key] = chapter.chapter
        return arc


class ScriptLine(BaseModel):
    """One spoken line. ``text`` is narration only — it is read out verbatim by TTS."""

    model_config = ConfigDict(extra="forbid")

    chapter: int = Field(ge=1)
    scene_number: int = Field(ge=1)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def _text_is_speakable(self) -> ScriptLine:
        """Reject what the voice would read out wrong. Messages tell the agent the fix.

        Live finding: the scene_author prefixed EVERY line with its scene number
        ("3 59 Agenten jetzt sichtbar?", scene_number=3) — all valid strings, so nothing
        caught it, and TTS would have said "drei neunundfuenfzig Agenten". Stage
        directions in brackets are the other classic: the contract forbids them, but
        only prose asked for it.
        """
        text = self.text.strip()
        if not text:
            raise ValueError("text is blank — write the spoken line, or drop the entry")
        if re.match(rf"^{self.scene_number}(\s|$)", text):
            raise ValueError(
                f"text starts with its own scene number ({self.scene_number}) — the voice "
                f"would read it out. Drop the number; if it belongs to the sentence, spell "
                f"it out or reorder."
            )
        if re.search(r"[(\[]", text):
            raise ValueError(
                "text contains brackets — narration only, no stage directions or notes"
            )
        return self


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
    def _frames_end_exclusive(self) -> CutSegment:
        if self.end_frame_exclusive <= self.start_frame:
            raise ValueError("end_frame_exclusive must be > start_frame")
        return self


class Cutlist(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1)
    segments: list[CutSegment] = Field(min_length=1)

    @model_validator(mode="after")
    def _orders_contiguous(self) -> Cutlist:
        orders = [s.order for s in self.segments]
        if sorted(orders) != list(range(len(orders))):
            raise ValueError("segment orders must be 0..n-1 without gaps")
        return self


class ContactSheetTile(BaseModel):
    """One cutlist segment's sampled frame inside the contact-sheet grid."""

    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=0)
    scene_number: int = Field(ge=1)
    frame: int = Field(ge=0)  # sampled SOURCE frame (the segment window's middle)
    label: str


class ContactSheet(BaseModel):
    """One grid PNG over the cutlist (each segment's middle frame, tiles in segment order) —
    the visual pre-render checkpoint a user signs off on before render_production runs."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1)
    png_path: str
    cols: int = Field(gt=0)
    rows: int = Field(gt=0)
    labeled: bool = True  # False when no usable font was found (tiles unlabeled, sheet still valid)
    tiles: list[ContactSheetTile] = Field(min_length=1)


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
    format: Format = "insta"
    target_seconds: float = Field(gt=0.0)
    status: str = "active"
