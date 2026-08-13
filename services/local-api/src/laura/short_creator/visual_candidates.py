"""Generate deterministic source-window candidates for a visual-only recut."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from math import ceil, floor

from laura.short_creator.board_models import (
    SceneReview,
    ScriptLine,
    VisualBeatPlan,
    VisualPlan,
    VisualRecutRequest,
    VisualSceneCandidate,
    VisualSceneChoice,
    VisualShotCandidate,
    VoiceArtifact,
)
from laura.short_creator.voice_concat import INTER_SCENE_GAP_S

_DEFAULT_FPS = 30.0
_MAX_CANDIDATES_PER_BEAT = 4
_MAX_SCENE_DURATION_S = 10
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


def _canonical_fps(fps: float) -> str:
    return format(Decimal(str(fps)).normalize(), "f")


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


def _scene_duration_frames(duration_s: int, fps: float) -> int:
    return max(1, round(duration_s * fps))


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


def _scene_description(scene: SceneMaterial) -> str:
    review_description = scene.review.description.strip() if scene.review is not None else ""
    return (
        review_description
        or scene.description.strip()
        or scene.transcript.strip()
        or f"Rough-Cut scene {scene.scene_number}"
    )


def _window_transcript(scene: SceneMaterial, window: CandidateWindow) -> str:
    overlapping = [
        span
        for span in scene.transcript_spans
        if span.start_frame < window.end_frame_exclusive
        and span.end_frame_exclusive > window.start_frame
    ]
    if not overlapping:
        return scene.transcript.strip()
    return max(
        overlapping,
        key=lambda span: (
            _overlap(window.transcript_snippet, span.text),
            -span.start_frame,
        ),
    ).text.strip()


def _scene_windows(
    scene: SceneMaterial,
    *,
    narration_text: str,
    fps: float,
) -> list[CandidateWindow]:
    if fps <= 0 or scene.src_end_frame_exclusive <= scene.src_start_frame:
        return []
    review = scene.review
    if review is not None and not review.degraded:
        reviewed = [
            CandidateWindow(
                start_frame=max(
                    scene.src_start_frame,
                    scene.src_start_frame + floor(review_window.offset_s * fps),
                ),
                end_frame_exclusive=min(
                    scene.src_end_frame_exclusive,
                    scene.src_start_frame
                    + floor((review_window.offset_s + review_window.duration_s) * fps),
                ),
                window_index=index,
                transcript_snippet=narration_text,
            )
            for index, review_window in enumerate(
                review.windows[:_MAX_CANDIDATES_PER_BEAT]
            )
        ]
        valid_reviewed = [
            window
            for window in reviewed
            if window.end_frame_exclusive - window.start_frame >= fps
        ]
        if valid_reviewed:
            return _deduplicate(valid_reviewed)

    scene_length = scene.src_end_frame_exclusive - scene.src_start_frame
    window_length = min(scene_length, floor(_MAX_SCENE_DURATION_S * fps))
    if window_length < fps:
        return []
    latest_start = scene.src_end_frame_exclusive - window_length
    windows = [
        CandidateWindow(
            start_frame=(
                latest_start
                if anchor_fraction == 1.0
                else scene.src_start_frame
                + floor((latest_start - scene.src_start_frame) * anchor_fraction)
            ),
            end_frame_exclusive=(
                latest_start
                if anchor_fraction == 1.0
                else scene.src_start_frame
                + floor((latest_start - scene.src_start_frame) * anchor_fraction)
            )
            + window_length,
            window_index=index,
            transcript_snippet=narration_text,
        )
        for index, anchor_fraction in enumerate((0.0, 0.33, 0.67, 1.0))
    ]
    return _deduplicate(windows)


def _scene_choice(
    *,
    rough_cut_order: int,
    scene: SceneMaterial,
    narration_text: str,
    fps: float,
) -> VisualSceneChoice:
    description = _scene_description(scene)
    candidates: list[VisualSceneCandidate] = []
    for window in _scene_windows(scene, narration_text=narration_text, fps=fps):
        transcript_snippet = _window_transcript(scene, window)
        max_duration_s = min(
            _MAX_SCENE_DURATION_S,
            floor((window.end_frame_exclusive - window.start_frame) / fps),
        )
        score = round(
            0.8 * _overlap(narration_text, f"{description} {transcript_snippet}")
            + 0.2 * (scene.review.hook_score / 10.0 if scene.review is not None else 0.5),
            6,
        )
        candidate_id = _canonical_hash(
            {
                "rough_cut_order": rough_cut_order,
                "scene_number": scene.scene_number,
                "src_end_frame_exclusive": window.end_frame_exclusive,
                "src_start_frame": window.start_frame,
            }
        )
        candidates.append(
            VisualSceneCandidate(
                candidate_id=candidate_id,
                rough_cut_order=rough_cut_order,
                scene_number=scene.scene_number,
                window_index=window.window_index,
                src_start_frame=window.start_frame,
                src_end_frame_exclusive=window.end_frame_exclusive,
                thumb_frame=window.start_frame
                + (window.end_frame_exclusive - window.start_frame) // 2,
                max_duration_s=max_duration_s,
                description=description,
                transcript_snippet=transcript_snippet,
                rationale="covers this Rough-Cut scene with source-grounded visual evidence",
                score=score,
            )
        )
    candidates.sort(key=lambda candidate: (-candidate.score, candidate.window_index))
    if not candidates:
        raise InsufficientVisualCandidates(
            f"Rough-Cut scene {scene.scene_number} has no one-second source window"
        )
    recommended = candidates[0]
    return VisualSceneChoice(
        rough_cut_order=rough_cut_order,
        scene_number=scene.scene_number,
        description=description,
        transcript=scene.transcript.strip(),
        rationale="keeps this Rough-Cut scene available for the visual decision",
        candidates=candidates,
        recommended_candidate_id=recommended.candidate_id,
        recommended_included=False,
        recommended_duration_s=1,
    )


def _recommend_scene_coverage(
    choices: list[VisualSceneChoice],
    *,
    voice_total_frames: int,
    fps: float,
) -> list[VisualSceneChoice]:
    capacities = [
        max(candidate.max_duration_s for candidate in choice.candidates)
        for choice in choices
    ]
    capacity_frames = [
        _scene_duration_frames(capacity, fps) for capacity in capacities
    ]
    if sum(capacity_frames) < voice_total_frames:
        raise InsufficientVisualCandidates("Rough-Cut scenes cannot cover the Voice")

    ranked_indices = sorted(
        range(len(choices)),
        key=lambda index: (
            -max(candidate.score for candidate in choices[index].candidates),
            choices[index].rough_cut_order,
        ),
    )
    one_second_frames = _scene_duration_frames(1, fps)
    if one_second_frames * len(choices) <= voice_total_frames:
        included_indices = list(ranked_indices)
    else:
        included_indices = list(ranked_indices[: min(3, len(choices))])
        next_rank = len(included_indices)
        while (
            sum(capacity_frames[index] for index in included_indices)
            < voice_total_frames
            and next_rank < len(ranked_indices)
        ):
            included_indices.append(ranked_indices[next_rank])
            next_rank += 1

    included_index_set = set(included_indices)
    durations = [1] * len(choices)
    requested_frames = one_second_frames * len(included_indices)
    final_timeline_index = max(
        included_indices,
        key=lambda index: choices[index].rough_cut_order,
    )
    allocation_order = [
        final_timeline_index,
        *[index for index in ranked_indices if index != final_timeline_index],
    ]
    for index in allocation_order:
        if index not in included_index_set:
            continue
        while (
            requested_frames < voice_total_frames
            and durations[index] < capacities[index]
        ):
            previous_frames = _scene_duration_frames(durations[index], fps)
            durations[index] += 1
            requested_frames += (
                _scene_duration_frames(durations[index], fps) - previous_frames
            )
        if requested_frames >= voice_total_frames:
            break
    if requested_frames < voice_total_frames:
        raise InsufficientVisualCandidates("Rough-Cut scenes cannot cover the Voice")

    return [
        choice.model_copy(
            update={
                "recommended_candidate_id": next(
                    candidate.candidate_id
                    for candidate in choice.candidates
                    if candidate.max_duration_s >= durations[index]
                ),
                "recommended_included": index in included_index_set,
                "recommended_duration_s": durations[index],
            }
        )
        for index, choice in enumerate(choices)
    ]


def build_rough_cut_visual_plan(
    *,
    request: VisualRecutRequest,
    scenes: list[SceneMaterial],
    narration_text: str,
    voice_total_frames: int,
    fps: float,
) -> VisualPlan:
    """Build one decision row per Rough-Cut scene without changing scene order."""
    if fps <= 0:
        raise ValueError("fps must be positive")
    if voice_total_frames <= 0:
        raise ValueError("voice_total_frames must be positive")
    request_hash = _canonical_hash(request.model_dump(mode="json", exclude={"version"}))
    choices = [
        _scene_choice(
            rough_cut_order=order,
            scene=scene,
            narration_text=narration_text,
            fps=fps,
        )
        for order, scene in enumerate(scenes)
    ]
    recommended = _recommend_scene_coverage(
        choices,
        voice_total_frames=voice_total_frames,
        fps=fps,
    )
    proposal_hash = _canonical_hash(
        {
            "request_hash": request_hash,
            "voice_total_frames": voice_total_frames,
            "fps": _canonical_fps(fps),
            "rough_cut_scene_count": len(scenes),
            "scene_choices": [
                {
                    "rough_cut_order": choice.rough_cut_order,
                    "candidate_ids": [
                        candidate.candidate_id for candidate in choice.candidates
                    ],
                    "recommended_candidate_id": choice.recommended_candidate_id,
                    "recommended_included": choice.recommended_included,
                    "recommended_duration_s": choice.recommended_duration_s,
                }
                for choice in recommended
            ],
        }
    )
    return VisualPlan(
        version=2,
        proposal_hash=proposal_hash,
        request_hash=request_hash,
        beats=[],
        scene_choices=recommended,
        rough_cut_scene_count=len(scenes),
        voice_total_frames=voice_total_frames,
        fps=fps,
    )


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
