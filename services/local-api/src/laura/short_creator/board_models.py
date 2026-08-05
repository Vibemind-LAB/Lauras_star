"""Pydantic schemas for the v2 production-board artifacts.

Every artifact the agent team exchanges lives on the board as one of these
models.  Validation is the contract: a malformed agent output is rejected at
the tool boundary (the agent sees the error and corrects itself) instead of
propagating silently.  Frame fields follow the project invariant: integer
frames, end-exclusive.
"""

from __future__ import annotations

import hashlib
import json
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


# Script-format labels a screenplay uses and a voice must never speak. Live finding: three
# autonomous runs labelled their lines instead of bracketing them, so the bracket rule missed
# it entirely and every synthesis spoke "Narration:" and "CAPTION:" eight times each. The
# label must be a marker — a standalone token before a colon — so ordinary prose keeps its
# colons ("why it wants each one: filesystem, memory") and the word "caption" stays usable.
_STAGE_DIRECTION_LABEL = re.compile(
    r"(?:^|[\s.;!?\"'-])"
    r"(NARRATION|NARRATOR|CAPTION|SUBTITLE|VOICEOVER|VOICE-OVER|VO|SFX|MUSIC|B-ROLL|BROLL"
    r"|ON-SCREEN|ONSCREEN|TITLE|TEXT ON SCREEN|CUT TO|SCENE)\s*:",
    re.IGNORECASE,
)


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


def stage_direction_label(text: str) -> str | None:
    """The screenplay label in *text* that a voice must never speak, or None.

    Checked on the WRITE path (``save_script_chapter``), deliberately not in the model
    validator: the model also runs on every load, and rejecting there would make a board
    written before this rule unreadable. Stripping the label is not enough either — in
    "CAPTION: Cold open, org chart on screen" the whole clause after the label is screen
    description, so the line has to be rewritten, not trimmed.
    """
    match = _STAGE_DIRECTION_LABEL.search(text)
    return match.group(1) if match is not None else None


def script_text(lines: list[ScriptLine]) -> str:
    """The spoken text of the given (ordered) lines, joined — the basis of the identity below.

    Callers pass lines already in a stable, playback-meaningful order (see
    ``_lines_in_storyline_order``).
    """
    return " ".join(line.text for line in lines)


def lines_in_storyline_order(script: Script, storyline: Storyline) -> list[ScriptLine]:
    """The script's lines reordered to follow the STORYLINE's playback order.

    For each chapter in ``storyline.arc`` (in arc order), for each scene_number in that
    chapter's ``scene_numbers`` (in list order), appends the matching line(s) — same chapter +
    scene_number, preserving their relative order for multiple lines per scene. This is the
    order the VIDEO plays in (``build_cutlist`` walks the arc the same way), so it is also the
    order the voice, captions and zoom timing must walk.

    Lines the storyline does not reference (a stale chapter/scene left over from an earlier
    draft) are appended at the end, in their original order — never dropped, since the voice
    must contain every line the author wrote. Window references collapse to their scene: a
    scene listed several times in a chapter speaks its line(s) once, at its FIRST occurrence.

    Lives here, with the models and :func:`script_hash`, because this ordering DEFINES the text
    identity every derived artifact records: the write sites hash the played order, so any
    check site must reproduce exactly this ordering or fresh artifacts read as stale. (Review
    finding: the board hashed the stored order instead and cried wolf on every chapter whose
    storyline reordered its scenes.)
    """
    by_key: dict[tuple[int, int], list[ScriptLine]] = {}
    for line in script.lines:
        by_key.setdefault((line.chapter, line.scene_number), []).append(line)

    placed: set[tuple[int, int]] = set()
    ordered: list[ScriptLine] = []
    for chapter in sorted(storyline.arc, key=lambda c: c.chapter):
        for entry in chapter.scene_numbers:
            key = (chapter.chapter, as_scene_window(entry)[0])
            if key in by_key and key not in placed:
                ordered.extend(by_key[key])
                placed.add(key)

    for line in script.lines:
        if (line.chapter, line.scene_number) not in placed:
            ordered.append(line)
    return ordered


def script_hash(lines: list[ScriptLine]) -> str:
    """sha256 over :func:`script_text` — the identity every derived artifact is checked against.

    Originally just the voice-synthesis cache key: unchanged ordered text hits the cache, a
    storyline reorder busts it. It now serves double duty as provenance. A render that records
    the hash it was made from can be told apart from one whose script has moved on — the live
    failure where a board carried script v39 beside a render built from v14.
    """
    return hashlib.sha256(script_text(lines).encode("utf-8")).hexdigest()


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


class Script(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1)
    language: str
    lines: list[ScriptLine] = Field(min_length=1)
    # Which parent artifact instances this was built from: chain name -> content_hash of the
    # parent AS IT WAS at build time. Empty = pre-provenance board (unknown, never coherent).
    parents: dict[str, str] = Field(default_factory=dict)


class VoiceArtifact(BaseModel):
    """Synthesis result, cached by script hash (re-voice only on text change)."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1)
    script_hash: str
    mp3_path: str
    timings_path: str | None = None
    voice_s: float | None = None
    # Which parent artifact instances this was built from: chain name -> content_hash of the
    # parent AS IT WAS at build time. Empty = pre-provenance board (unknown, never coherent).
    parents: dict[str, str] = Field(default_factory=dict)


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
    # Provenance, same contract as RenderReport: which script this cut was built to carry.
    # Empty means a board written before provenance existed — unknown, not current.
    script_hash: str = ""
    # Which parent artifact instances this was built from: chain name -> content_hash of the
    # parent AS IT WAS at build time. Empty = pre-provenance board (unknown, never coherent).
    parents: dict[str, str] = Field(default_factory=dict)

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
    # Provenance, same contract as RenderReport (empty = pre-provenance board, unknown).
    script_hash: str = ""
    tiles: list[ContactSheetTile] = Field(min_length=1)
    # Which parent artifact instances this was built from: chain name -> content_hash of the
    # parent AS IT WAS at build time. Empty = pre-provenance board (unknown, never coherent).
    parents: dict[str, str] = Field(default_factory=dict)


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
    # What this render was made from. Live finding: a board carried script v39 while its
    # render_report came from v14 — its voice_fits check read OK for a pairing that no longer
    # existed. VoiceArtifact already carried script_hash; the render needs it for the same
    # reason. Empty means a board written before provenance existed: unknown, not current.
    script_hash: str = ""
    # Which parent artifact instances this was built from: chain name -> content_hash of the
    # parent AS IT WAS at build time. Empty = pre-provenance board (unknown, never coherent).
    parents: dict[str, str] = Field(default_factory=dict)
    # What the muxed file actually runs, measured with ffprobe after the render; None when it
    # could not be measured. video_s is the CUT, which is not the same thing: ffmpeg's
    # -shortest trims the mux to the shorter stream, so a cut longer than its voiceover ships
    # short. Live 2026-08-02: a 37.8s cut against a 12.2s voice delivered a 12.2s film.
    delivered_s: float | None = None
    # The delivered length (falling back to video_s when unmeasurable) / the board's
    # target_seconds, rounded to 3 places; None when no usable target. Reporting only — never a
    # member of checks (a failing length gate would provoke render thrashing); QA reads it and
    # weighs the shortfall in its verdict.
    target_ratio: float | None = None


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
    # Which parent artifact instances this was built from: chain name -> content_hash of the
    # parent AS IT WAS at build time. Empty = pre-provenance board (unknown, never coherent).
    parents: dict[str, str] = Field(default_factory=dict)


BoardStatus = Literal["active", "failed", "complete"]


class BoardMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    asset_id: str
    created_utc: str
    task: str
    format: Format = "insta"
    # The spoken/caption language, named as the script should be written ("German", "English").
    # German is the default because that is what this workspace ships; a submission with an
    # international jury asks for English, and a task string cannot argue a system prompt out
    # of a hard-coded language.
    language: str = Field(default="German", min_length=2, max_length=40)
    target_seconds: float = Field(gt=0.0)
    # A lifecycle value, not free text. It was a bare ``str`` that only ``Board.create`` ever
    # wrote, so it read "active" forever — including for 55 minutes after a run had died. A
    # closed set plus ``Board.set_status`` makes it answerable.
    status: BoardStatus = "active"
    # Gate B (script checkpoint, 2026-08-04): when True, ``synthesize_script_voice`` refuses
    # deterministically until ``script_approved_utc`` is set — the production pauses right after
    # the script for the user to approve it in chat (``approve_script``), so voice/cutlist/render
    # never run against text nobody signed off on. Both fields default so every meta.json written
    # before this gate existed still loads unchanged. Only ``run_project_auto_short``'s NEW
    # sessions turn the gate on (auto-overview does not use the production board at all).
    script_gate: bool = False
    # Set once, by ``Board.set_script_approved``, when the user approves the script in chat.
    # ``None`` means "not yet approved" — irrespective of whether the gate is even enabled.
    script_approved_utc: str | None = None
