"""Generate deterministic source-window candidates for a visual-only recut."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from math import ceil, floor

from laura.short_creator.board_models import (
    SceneReview,
    ScriptLine,
    VisualBeatPlan,
    VisualPlan,
    VisualRecutRequest,
    VisualShotCandidate,
    VoiceArtifact,
)
from laura.short_creator.voice_concat import INTER_SCENE_GAP_S

_DEFAULT_FPS = 30.0
_MAX_CANDIDATES_PER_BEAT = 4
_TOKEN_RE = re.compile(r"[\w']+", re.UNICODE)


class InsufficientVisualCandidates(ValueError):
    """Raised when a spoken beat has no source window that can contain it."""


@dataclass(frozen=True)
class TranscriptSpan:
    start_frame: int
    end_frame_exclusive: int
    text: str


@dataclass(frozen=True)
class SceneMaterial:
    scene_number: int
    src_start_frame: int
    src_end_frame_exclusive: int
    description: str
    transcript: str
    transcript_spans: tuple[TranscriptSpan, ...]
    review: SceneReview | None


@dataclass(frozen=True)
class CandidateWindow:
    start_frame: int
    end_frame_exclusive: int
    window_index: int
    transcript_snippet: str


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.casefold()))


def _overlap(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _capacity_frames(beat_duration_s: float, fps: float) -> int:
    return ceil(max(4.0, beat_duration_s + INTER_SCENE_GAP_S) * fps)


def _window_from_start(
    scene: SceneMaterial,
    start_frame: int,
    capacity_frames: int,
    *,
    window_index: int,
    transcript_snippet: str,
) -> CandidateWindow:
    scene_length = scene.src_end_frame_exclusive - scene.src_start_frame
    window_length = min(scene_length, capacity_frames)
    latest_start = scene.src_end_frame_exclusive - window_length
    legal_start = min(max(start_frame, scene.src_start_frame), latest_start)
    return CandidateWindow(
        start_frame=legal_start,
        end_frame_exclusive=legal_start + window_length,
        window_index=window_index,
        transcript_snippet=transcript_snippet,
    )


def _deduplicate(windows: list[CandidateWindow]) -> list[CandidateWindow]:
    seen: set[tuple[int, int]] = set()
    unique: list[CandidateWindow] = []
    for window in windows:
        frame_range = (window.start_frame, window.end_frame_exclusive)
        if frame_range not in seen:
            seen.add(frame_range)
            unique.append(window)
    return unique


def coverage_windows(
    scene: SceneMaterial,
    *,
    beat_text: str,
    beat_duration_s: float,
    fps: float = _DEFAULT_FPS,
) -> list[CandidateWindow]:
    """Return review-led or transcript-led source windows, always in frame coordinates."""
    if scene.src_end_frame_exclusive <= scene.src_start_frame:
        return []
    capacity_frames = _capacity_frames(beat_duration_s, fps)
    review = scene.review
    if review is not None and not review.degraded:
        windows = [
            _window_from_start(
                scene,
                scene.src_start_frame + floor(window.offset_s * fps),
                capacity_frames,
                window_index=index,
                transcript_snippet=scene.transcript,
            )
            for index, window in enumerate(review.windows[:_MAX_CANDIDATES_PER_BEAT])
        ]
        return _deduplicate(windows)

    ranked_spans = sorted(
        scene.transcript_spans,
        key=lambda span: (
            -_overlap(beat_text, span.text),
            span.start_frame,
            span.end_frame_exclusive,
        ),
    )
    windows = [
        _window_from_start(
            scene,
            span.start_frame,
            capacity_frames,
            window_index=index,
            transcript_snippet=span.text,
        )
        for index, span in enumerate(ranked_spans[:_MAX_CANDIDATES_PER_BEAT])
    ]
    scene_length = scene.src_end_frame_exclusive - scene.src_start_frame
    window_length = min(scene_length, capacity_frames)
    latest_start = scene.src_end_frame_exclusive - window_length
    for anchor_fraction in (0.0, 0.33, 0.67, 1.0):
        if len(windows) >= _MAX_CANDIDATES_PER_BEAT:
            break
        start_frame = (
            latest_start
            if anchor_fraction == 1.0
            else scene.src_start_frame
            + floor((latest_start - scene.src_start_frame) * anchor_fraction)
        )
        windows.append(
            _window_from_start(
                scene,
                start_frame,
                capacity_frames,
                window_index=len(windows),
                transcript_snippet=scene.transcript,
            )
        )
    return _deduplicate(windows)[:_MAX_CANDIDATES_PER_BEAT]


def _score_candidate(
    *,
    line: ScriptLine,
    scene: SceneMaterial,
    window: CandidateWindow,
    required_frames: int,
) -> float:
    review = scene.review
    hook_score = review.hook_score / 10.0 if review is not None else 0.5
    non_degraded = 1.0 if review is not None and not review.degraded else 0.0
    capacity = min(
        1.0,
        (window.end_frame_exclusive - window.start_frame) / required_frames,
    )
    semantic = _overlap(line.text, f"{scene.description} {window.transcript_snippet}")
    return round(0.5 * semantic + 0.25 * hook_score + 0.15 * non_degraded + 0.1 * capacity, 6)


def _candidate_for_window(
    *,
    beat_index: int,
    line: ScriptLine,
    duration_s: float,
    scene: SceneMaterial,
    window: CandidateWindow,
    required_frames: int,
) -> VisualShotCandidate | None:
    if window.end_frame_exclusive - window.start_frame < required_frames:
        return None
    candidate_id = _canonical_hash(
        {
            "beat_index": beat_index,
            "scene_number": scene.scene_number,
            "src_end_frame_exclusive": window.end_frame_exclusive,
            "src_start_frame": window.start_frame,
        }
    )
    score = _score_candidate(
        line=line,
        scene=scene,
        window=window,
        required_frames=required_frames,
    )
    return VisualShotCandidate(
        candidate_id=candidate_id,
        beat_id=f"beat-{beat_index + 1}",
        voice_segment_index=beat_index,
        scene_number=scene.scene_number,
        window_index=window.window_index,
        src_start_frame=window.start_frame,
        src_end_frame_exclusive=window.end_frame_exclusive,
        thumb_frame=window.start_frame + (window.end_frame_exclusive - window.start_frame) // 2,
        description=scene.description,
        transcript_snippet=window.transcript_snippet,
        rationale="matches narration using saved visual evidence and source capacity",
        score=score,
    )


def _is_near_duplicate(left: VisualShotCandidate, right: VisualShotCandidate) -> bool:
    if left.scene_number != right.scene_number:
        return False
    overlap = max(
        0,
        min(left.src_end_frame_exclusive, right.src_end_frame_exclusive)
        - max(left.src_start_frame, right.src_start_frame),
    )
    shorter_window = min(
        left.src_end_frame_exclusive - left.src_start_frame,
        right.src_end_frame_exclusive - right.src_start_frame,
    )
    return shorter_window > 0 and overlap / shorter_window >= 0.8


def _recommend(candidates: list[VisualShotCandidate], previous: VisualShotCandidate | None) -> str:
    top = candidates[0]
    if previous is None:
        return top.candidate_id
    if not _is_near_duplicate(top, previous):
        return top.candidate_id
    for candidate in candidates[1:]:
        if (
            not _is_near_duplicate(candidate, previous)
            and candidate.score >= top.score * 0.85
        ):
            return candidate.candidate_id
    return top.candidate_id


def build_visual_plan(
    *,
    request: VisualRecutRequest,
    ordered_lines: list[ScriptLine],
    voice: VoiceArtifact,
    scenes: list[SceneMaterial],
    fps: float,
) -> VisualPlan:
    """Build a stable, visual-only proposal from immutable narration and voice inputs."""
    if voice.segments is None or len(voice.segments) != len(ordered_lines):
        raise ValueError("voice must contain exactly one segment for each script line")

    request_hash = _canonical_hash(
        request.model_dump(mode="json", exclude={"version"})
    )
    beats: list[VisualBeatPlan] = []
    previous: VisualShotCandidate | None = None
    for beat_index, (line, segment) in enumerate(zip(ordered_lines, voice.segments, strict=True)):
        required_frames = _capacity_frames(segment.duration_s, fps)
        candidates = [
            candidate
            for scene in scenes
            for window in coverage_windows(
                scene,
                beat_text=line.text,
                beat_duration_s=segment.duration_s,
                fps=fps,
            )
            if (
                candidate := _candidate_for_window(
                    beat_index=beat_index,
                    line=line,
                    duration_s=segment.duration_s,
                    scene=scene,
                    window=window,
                    required_frames=required_frames,
                )
            ) is not None
        ]
        candidates.sort(key=lambda candidate: (-candidate.score, candidate.candidate_id))
        candidates = candidates[:_MAX_CANDIDATES_PER_BEAT]
        beat_id = f"beat-{beat_index + 1}"
        if not candidates:
            raise InsufficientVisualCandidates(
                f"{beat_id} cannot be covered by the available scenes"
            )
        recommended_candidate_id = _recommend(candidates, previous)
        recommended = next(
            candidate
            for candidate in candidates
            if candidate.candidate_id == recommended_candidate_id
        )
        beats.append(
            VisualBeatPlan(
                beat_id=beat_id,
                voice_segment_index=beat_index,
                narration_text=line.text,
                duration_s=segment.duration_s,
                candidates=candidates,
                recommended_candidate_id=recommended_candidate_id,
            )
        )
        previous = recommended

    proposal_hash = _canonical_hash(
        {
            "request_hash": request_hash,
            "beats": [
                {
                    "beat_id": beat.beat_id,
                    "candidate_ids": [candidate.candidate_id for candidate in beat.candidates],
                }
                for beat in beats
            ],
        }
    )
    return VisualPlan(proposal_hash=proposal_hash, request_hash=request_hash, beats=beats)
