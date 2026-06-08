"""Timeline, export, and interchange-validation endpoints (docs/04-api.md, 07-interchange.md)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse

from .. import audit
from ..analysis.editorial import Word
from ..analysis.eval_cut import FrameLoader, load_gray_frames_ffmpeg
from ..analysis.eval_quality import evaluate_rough_cut
from ..analysis.joint import bias_to_weights, joint_place
from ..analysis.semantic import sentence_end_frames, speaker_turn_frames
from ..analysis.silence import detect_silence
from ..analysis.splitedit import plan_split_cuts
from ..auth import Principal, require_permission
from ..db import repos
from ..db.database import Database
from ..editing.operations import (
    EditClip,
    append_clip,
    delete_range,
    insert_clip,
    lift_range,
    move_clip,
    ordered,
    set_speed,
    split_clip,
    trim_clip,
)
from ..editing.otio_sync import build_model
from ..editing.word_cut import map_asset_range_to_seq
from ..interchange.captions import join_words, segments_to_srt, segments_to_vtt
from ..interchange.edl import timeline_to_edl
from ..interchange.fcp7_xml import timeline_to_fcp7_xml
from ..interchange.fcpx_xml import timeline_to_fcpx_xml
from ..interchange.otio_io import otio_string_to_timeline, timeline_to_otio_string
from ..interchange.timeline import Timeline
from ..interchange.validate import validate_export
from ..jobs.queues import queue_for
from ..jobs.runner import enqueue
from .models import (
    ClipOut,
    ClipSourceOut,
    DroppedShot,
    ExportOut,
    ExportRequest,
    FromShotsOut,
    FromShotsRequest,
    OperationRequest,
    RenameRequest,
    RenderExportOut,
    RenderRequest,
    RoughCutQualityOut,
    SetClipsRequest,
    SplitCutOut,
    TimelineCreate,
    TimelineImportOut,
    TimelineImportRequest,
    TimelineOut,
    ValidateOut,
    ValidateRequest,
)
from .pagination import PageParams
from .security import require_token

router = APIRouter(tags=["timelines"], dependencies=[Depends(require_token)])

_EXT = {"otio": "otio", "edl": "edl", "fcp7xml": "xml", "fcpxml": "fcpxml"}
_WRITERS = {
    "otio": timeline_to_otio_string,
    "edl": timeline_to_edl,
    "fcp7xml": timeline_to_fcp7_xml,
    "fcpxml": timeline_to_fcpx_xml,
}


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _timeline_out(db: Database, row: dict[str, Any]) -> TimelineOut:
    clips = [ClipOut(**c) for c in repos.list_timeline_clips(db, row["id"])]
    return TimelineOut(
        id=row["id"], project_id=row["project_id"], name=row["name"],
        kind=row["kind"], created_at=row["created_at"], clips=clips,
    )


def _build_model(db: Database, timeline_row: dict[str, Any]) -> Timeline:
    return build_model(db, timeline_row)


@router.post(
    "/projects/{project_id}/timelines",
    response_model=TimelineOut,
    status_code=status.HTTP_201_CREATED,
)
def create_timeline(project_id: str, body: TimelineCreate, request: Request) -> TimelineOut:
    db = _db(request)
    if repos.get_project(db, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    row = repos.create_timeline(db, project_id=project_id, name=body.name, kind=body.kind)
    return _timeline_out(db, row)


@router.post(
    "/projects/{project_id}/timelines/import",
    response_model=TimelineImportOut,
    status_code=status.HTTP_201_CREATED,
)
def import_timeline(
    project_id: str, body: TimelineImportRequest, request: Request
) -> TimelineImportOut:
    """Import an editorial timeline (OTIO) and relink its clips to project assets by
    source path; unmatched media becomes an offline placeholder asset to resolve later.
    EDL/FCP7 import is planned (no reader yet)."""
    db = _db(request)
    project = repos.get_project(db, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    fmt = body.format.lower()
    if fmt != "otio":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"import supports 'otio' only (got {fmt!r}); EDL/FCP7 import is planned",
        )
    try:
        model = otio_string_to_timeline(
            body.content,
            rate_num=project["sequence_rate_num"],
            rate_den=project["sequence_rate_den"],
            drop_frame=bool(project["drop_frame"]),
        )
    except Exception as exc:  # noqa: BLE001 - any parser error is a client error
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"could not parse OTIO: {exc}"
        ) from exc

    resolved: dict[str, str] = {}   # source key -> asset id (dedupes repeated media)
    matched = 0
    offline = 0
    rows: list[dict[str, Any]] = []
    for clip in model.ordered():
        key = clip.source_url or clip.name or "offline"
        asset_id = resolved.get(key)
        if asset_id is None:
            existing = (
                repos.find_asset_by_source_path(db, project_id, clip.source_url)
                if clip.source_url
                else None
            )
            if existing is not None:
                asset_id = existing["id"]
                matched += 1
            else:
                placeholder = repos.create_asset(
                    db, project_id=project_id, type="video",
                    display_name=Path(key).name or key, source_path=key, online=False,
                )
                asset_id = placeholder["id"]
                offline += 1
            resolved[key] = asset_id
        rows.append({
            "asset_id": asset_id,
            "src_in_frame": clip.src_in_frame,
            "src_out_frame_exclusive": clip.src_out_frame_exclusive,
            "seq_in_frame": clip.seq_in_frame,
            "seq_out_frame_exclusive": clip.seq_out_frame_exclusive,
            "lane": clip.lane,
            "speed_num": clip.speed_num,
            "speed_den": clip.speed_den,
        })

    name = body.name or model.name or "Imported"
    created = repos.create_timeline(db, project_id=project_id, name=name, kind="rough_cut")
    repos.replace_timeline_clips(db, created["id"], rows)
    fresh = repos.get_timeline(db, created["id"])
    assert fresh is not None
    repos.update_timeline_otio(
        db, fresh["id"], timeline_to_otio_string(_build_model(db, fresh))
    )
    return TimelineImportOut(
        timeline=_timeline_out(db, fresh), matched_media=matched, offline_media=offline,
    )


def _resolve_video_path(
    db: Database, asset_id: str, asset: dict[str, Any]
) -> str | None:
    """The readable video for the joint visual signal — proxy preferred, else original/source.

    Mirrors ``eval_cut_cli.resolve_video``: the CFR editorial proxy is the right surface for the
    diff signal (it is what the shot detector ran on), falling back to an ``original`` derived
    file and finally the asset's ``source_path``. Returns ``None`` when nothing on disk is
    readable, in which case joint placement degrades to the editorial-only choice.
    """
    by_kind = {f["kind"]: f["path"] for f in repos.list_asset_files(db, asset_id)}
    for kind in ("proxy", "original"):
        path = by_kind.get(kind)
        if path and Path(path).is_file():
            return str(path)
    src = asset.get("source_path")
    if src and Path(src).is_file():
        return str(src)
    return None


def _detect_asset_silence(
    video_path: Path | str | None, asset: dict[str, Any]
) -> list[tuple[int, int]]:
    """Real audio silences of the asset as source-frame ranges, or ``[]`` when unavailable.

    Runs :func:`laura.analysis.silence.detect_silence` on the resolved editorial video and maps
    the seconds-based ``silencedetect`` output into the asset's source-frame space via its frame
    rate (``rate_num / rate_den``). Returns ``[]`` when there is no readable video or the asset
    rate is unknown — silence is purely additive, so its absence just leaves placement to the
    word-gap proxy.
    """
    if video_path is None:
        return []
    rate_num = asset.get("rate_num")
    rate_den = asset.get("rate_den")
    if not rate_num or not rate_den:
        return []
    return detect_silence(video_path, rate_num=rate_num, rate_den=rate_den)


def _align_rows_editorial(
    rows: list[dict[str, Any]],
    words: list[Word],
    *,
    window: int,
    w_visual: float = 0.6,
    w_editorial: float = 0.4,
    silence: list[tuple[int, int]] | None = None,
    sentence_frames: set[int] | None = None,
    speaker_frames: set[int] | None = None,
    video_path: Path | str | None = None,
    total_frames: int | None = None,
    frame_loader: FrameLoader = load_gray_frames_ffmpeg,
) -> None:
    """Stage 2: place each clip's source IN by JOINT visual+editorial quality, in place.

    For every clip after the first, its ``src_in_frame`` is the cut between it and the previous
    clip. Instead of two separate snaps (visual peak, then an all-or-nothing editorial drag),
    :func:`laura.analysis.joint.joint_place` picks the single frame in ``[cut-window, cut+window]``
    that maximises ``w_visual*visual_score + w_editorial*editorial_score`` — so a clean word edge
    only displaces the frame-exact visual peak when the blend says the trade is worth it. The
    per-frame visual signal is decoded from the asset's video around the cut via ``frame_loader``
    (``video_path``/``total_frames`` supplied by the caller); with no video it gracefully reduces
    to the editorial-only choice, and with no words to the visual-only choice.

    ``silence`` is an optional list of end-exclusive source-frame ranges ``[start, end)`` of real
    audio silence (from :func:`laura.analysis.silence.detect_silence`). A candidate cut that lands
    inside a silence scores above a mere word edge, so cuts prefer genuine pauses (breaths,
    inter-sentence beats) over ASR word boundaries. With no silence info the placement is exactly
    the word-gap behaviour.

    ``sentence_frames`` / ``speaker_frames`` are optional source-frame sets of sentence boundaries
    and diarization speaker turns (from :mod:`laura.analysis.semantic`). A candidate cut on a
    speaker turn or sentence end outscores a bare silence / word edge, so cuts prefer natural
    narrative seams. With neither (no punctuation, no diarization) placement is unchanged.

    The match is applied to BOTH sides of the cut only when the two clips are truly contiguous in
    the source (``prev.src_out_frame_exclusive == cur.src_in_frame``) — otherwise the cut sits
    across a source gap and only the current clip's IN is moved, never inventing source frames.

    The very first clip's start (typically source frame 0) is never touched. After all IN frames
    settle, the sequence is repacked back-to-back from the new source lengths so clips stay
    contiguous and end-exclusive on the timeline. Empty ``words`` (and no readable video) leaves
    each cut exactly where the visual stage put it — placement only ever refines.
    """
    if window <= 0:
        return
    if not words and not silence and video_path is None:
        return
    for i in range(1, len(rows)):
        cur = rows[i]
        prev = rows[i - 1]
        cut = cur["src_in_frame"]
        aligned, _score = joint_place(
            cut,
            words,
            window=window,
            w_visual=w_visual,
            w_editorial=w_editorial,
            silence=silence,
            sentence_frames=sentence_frames,
            speaker_frames=speaker_frames,
            video_path=video_path,
            total_frames=total_frames,
            frame_loader=frame_loader,
        )
        if aligned == cut:
            continue
        # Keep both source ranges non-empty; skip a move that would collapse a clip.
        if aligned <= prev["src_in_frame"] or aligned >= cur["src_out_frame_exclusive"]:
            continue
        contiguous = prev["src_out_frame_exclusive"] == cut
        cur["src_in_frame"] = aligned
        if contiguous:
            prev["src_out_frame_exclusive"] = aligned

    # Repack the sequence from the (possibly changed) source lengths: contiguous, end-exclusive.
    offset = rows[0]["seq_in_frame"] if rows else 0
    for row in rows:
        length = row["src_out_frame_exclusive"] - row["src_in_frame"]
        row["seq_in_frame"] = offset
        row["seq_out_frame_exclusive"] = offset + length
        offset += length


def _plan_split_cuts(
    rows: list[dict[str, Any]],
    words: list[Word],
    silence: list[tuple[int, int]] | None,
    *,
    window: int,
    video_path: Path | str | None = None,
    total_frames: int | None = None,
    frame_loader: FrameLoader = load_gray_frames_ffmpeg,
) -> list[SplitCutOut]:
    """L/J split-edit recommendations for the inter-clip cuts of a (hard-cut) rough cut.

    The inter-clip cuts are each clip's ``src_in_frame`` after the first (the boundary between it
    and the previous clip). For each, :func:`laura.analysis.splitedit.plan_split_cut` computes the
    independent optimal picture frame (visual peak) and sound frame (real silence / clean
    word-gap), classifies the result (hard / L / J) and the offset, and we surface it as a
    :class:`SplitCutOut`. RECOMMENDATION ONLY — the stored clips are not changed; this just tells
    the UI/editor which cuts would benefit from a split edit and by how many frames.
    """
    cuts = [row["src_in_frame"] for row in rows[1:]]
    if not cuts:
        return []
    planned = plan_split_cuts(
        cuts,
        words,
        silence,
        window=window,
        video_path=video_path,
        total_frames=total_frames,
        frame_loader=frame_loader,
    )
    return [
        SplitCutOut(
            seq_cut=sc.seq_cut,
            video_frame=sc.video_frame,
            audio_frame=sc.audio_frame,
            offset=sc.offset,
            kind=sc.kind,
        )
        for sc in planned
    ]


def _eval_quality(
    rows: list[dict[str, Any]],
    words: list[Word],
    split_cuts: list[SplitCutOut],
    *,
    window: int,
    w_visual: float,
    w_editorial: float,
    video_path: Path | str | None,
    total_frames: int | None = None,
    frame_loader: FrameLoader = load_gray_frames_ffmpeg,
) -> RoughCutQualityOut | None:
    """On-the-fly headline quality of the built rough cut — visual exactness × editorial clean.

    The cuts scored are the inter-clip cuts (each clip's ``src_in_frame`` after the first), exactly
    the boundaries placement and the split-cut planner work on. :func:`evaluate_rough_cut` blends
    :func:`laura.analysis.eval_cut.evaluate_boundaries` (frame-exactness of the picture) with
    :func:`laura.analysis.editorial.editorial_metrics` (share of cuts not mid-word), weighted by
    the request's ``cut_bias`` (``w_visual``/``w_editorial``) so the score reflects the trade-off.

    Graceful ``None`` when there is nothing to score (no inter-clip cut) or no readable video to
    measure visual exactness against — the visual half needs to decode frames, so without a video
    file we report no quality rather than a misleading editorial-only number. Any decode/IO error is
    swallowed to ``None`` too: a quality read-out must never break the build itself.
    """
    cuts = [row["src_in_frame"] for row in rows[1:]]
    if not cuts or video_path is None:
        return None
    try:
        q = evaluate_rough_cut(
            video_path,
            cuts,
            words,
            window=window,
            w_visual=w_visual,
            w_editorial=w_editorial,
            frame_loader=frame_loader,
        )
    except Exception:  # noqa: BLE001 - a quality read-out must never break the build
        return None
    return RoughCutQualityOut(
        overall=q.overall,
        visual_exactness=q.visual_exactness,
        editorial_cleanliness=q.editorial_clean,
        n_cuts=q.n_cuts,
        n_split_cuts=sum(1 for sc in split_cuts if sc.kind != "hard"),
    )


@router.post(
    "/projects/{project_id}/timelines/from-shots",
    response_model=FromShotsOut,
    status_code=status.HTTP_201_CREATED,
)
def timeline_from_shots(
    project_id: str, body: FromShotsRequest, request: Request
) -> FromShotsOut:
    """Build a rough cut from an asset's detected shots: one contiguous clip per kept shot,
    in source order, packed back-to-back on the sequence (end-exclusive, speed 1/1).

    Weak shots (black, static, duplicate, blurry) are filtered when ``quality=True`` (default).
    The ``dropped`` list in the response contains the filtered shots so the UI can re-include.

    When a transcript/silence exists the response also carries ``split_cuts``: per inter-clip cut,
    an L/J split-edit recommendation (independent optimal picture frame = visual peak, sound frame
    = real silence / clean word-gap, with the offset and hard/L/J classification). This is
    RECOMMENDATION ONLY — the stored clips remain hard cuts; it just surfaces which cuts would
    benefit from a split edit and by how many frames.

    Non-destructive: pass an empty ``timeline_id`` to fill it; otherwise a new ``rough_cut``
    is created so a hand-made cut is never clobbered."""
    db = _db(request)
    if repos.get_project(db, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    asset = repos.get_asset(db, body.asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    if asset["project_id"] != project_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "asset belongs to another project"
        )

    run_id = body.run_id
    if run_id is None:
        run = repos.get_latest_analysis_run(db, body.asset_id)
        if run is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "asset has no analysis run"
            )
        run_id = run["id"]

    def enabled(override: bool | None) -> bool:
        return body.quality if override is None else override

    reasons_on = {
        "black": enabled(body.drop_black),
        "static": enabled(body.drop_static),
        "duplicate": enabled(body.drop_duplicates),
        "blur": enabled(body.drop_blur),
    }
    dropped: list[DroppedShot] = []
    rows: list[dict[str, Any]] = []
    offset = 0
    for shot in repos.list_shots(db, body.asset_id, run_id):  # ordered by src_in_frame
        reason = shot.get("drop_reason") if not shot.get("keep", True) else None
        if reason is not None and reasons_on.get(reason, False):
            dropped.append(DroppedShot(
                src_in_frame=shot["src_in_frame"],
                src_out_frame_exclusive=shot["src_out_frame_exclusive"],
                drop_reason=reason,
            ))
            continue
        length = shot["src_out_frame_exclusive"] - shot["src_in_frame"]
        if length <= 0:
            continue
        merged = (
            body.merge_min_frames > 0 and length < body.merge_min_frames and bool(rows)
        )
        if merged:
            rows[-1]["src_out_frame_exclusive"] = shot["src_out_frame_exclusive"]
            rows[-1]["seq_out_frame_exclusive"] += length
            offset += length
            continue
        rows.append({
            "asset_id": body.asset_id,
            "src_in_frame": shot["src_in_frame"],
            "src_out_frame_exclusive": shot["src_out_frame_exclusive"],
            "seq_in_frame": offset,
            "seq_out_frame_exclusive": offset + length,
            "lane": body.lane,
            "speed_num": 1,
            "speed_den": 1,
        })
        offset += length
    if not rows:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "no shots left after filtering"
        )

    split_cuts: list[SplitCutOut] = []
    quality: RoughCutQualityOut | None = None
    if body.align_editorial:
        word_rows = repos.list_words_for_run(db, body.asset_id, run_id)
        # Thread the stored per-word text (with any ASR punctuation) and the segment's diarization
        # speaker label through the in-memory Word so semantic placement can find sentence ends and
        # speaker turns — no new schema column. Both are graceful: NULL text/speaker -> no signal.
        words = [
            Word(
                start_frame=w["start_frame"],
                end_frame=w["end_frame"],
                text=w.get("text"),
                speaker=w.get("speaker_label"),
            )
            for w in word_rows
        ]
        # Compute the semantic frame sets once for this asset (like silence below): the source
        # frames that end a sentence (".?!…" / long clause pause) and where the speaker changes.
        # Empty when the transcript carries no punctuation / no diarization -> no behaviour change.
        sentence_frames = sentence_end_frames(words)
        speaker_frames = speaker_turn_frames(words)
        w_visual, w_editorial = bias_to_weights(body.cut_bias)
        video_path = _resolve_video_path(db, body.asset_id, asset)
        # Detect real audio silences once for this asset so cuts can prefer genuine pauses
        # (breaths / inter-sentence beats) over mere ASR word edges. Converted to the asset's
        # source-frame space via its rate; gracefully empty when no audio / rate is unknown.
        silence = _detect_asset_silence(video_path, asset)
        _align_rows_editorial(
            rows,
            words,
            window=body.editorial_window,
            w_visual=w_visual,
            w_editorial=w_editorial,
            silence=silence,
            sentence_frames=sentence_frames,
            speaker_frames=speaker_frames,
            video_path=video_path,
            total_frames=asset.get("duration_frames"),
        )
        # L/J split-edit recommendations (RECOMMENDATION ONLY — the stored clips stay hard cuts).
        # For each inter-clip cut, plan the independent optimal picture frame (visual peak) and
        # sound frame (real silence / clean word-gap) so the UI/editor can SEE which cuts would
        # benefit from an L- or J-cut and by how many frames. Skipped when there is nothing to
        # plan against (no transcript and no detected silence) so the field stays empty.
        if (words or silence) and body.editorial_window > 0:
            split_cuts = _plan_split_cuts(
                rows,
                words,
                silence,
                window=body.editorial_window,
                video_path=video_path,
                total_frames=asset.get("duration_frames"),
            )
        # Headline quality of the just-built rough cut, blended by the SAME cut_bias weights so the
        # score reflects the chosen picture-vs-sound trade-off. Graceful None when there's nothing
        # to score or no readable video (evaluate_boundaries needs >=1 window; clamp 0 -> 1).
        quality = _eval_quality(
            rows,
            words,
            split_cuts,
            window=max(1, body.editorial_window),
            w_visual=w_visual,
            w_editorial=w_editorial,
            video_path=video_path,
            total_frames=asset.get("duration_frames"),
        )

    if body.timeline_id is not None:
        target = repos.get_timeline(db, body.timeline_id)
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")
        if target["project_id"] != project_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "timeline belongs to another project"
            )
        if repos.list_timeline_clips(db, body.timeline_id):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "timeline already has clips; omit timeline_id to create a new one",
            )
    else:
        target = repos.create_timeline(
            db, project_id=project_id, name=body.name or "Rough Cut (Szenen)", kind="rough_cut"
        )

    repos.replace_timeline_clips(db, target["id"], rows)
    fresh = repos.get_timeline(db, target["id"])
    assert fresh is not None
    repos.update_timeline_otio(db, fresh["id"], timeline_to_otio_string(_build_model(db, fresh)))
    return FromShotsOut(
        timeline=_timeline_out(db, fresh), dropped=dropped, split_cuts=split_cuts,
        quality=quality,
    )


@router.get("/projects/{project_id}/timelines", response_model=list[TimelineOut])
def list_timelines(
    project_id: str, request: Request, response: Response, page: PageParams
) -> list[TimelineOut]:
    db = _db(request)
    response.headers["X-Total-Count"] = str(repos.count_timelines(db, project_id))
    rows = repos.list_timelines(db, project_id, limit=page.limit, offset=page.offset)
    return [_timeline_out(db, r) for r in rows]


@router.get("/timelines/{timeline_id}", response_model=TimelineOut)
def get_timeline(timeline_id: str, request: Request) -> TimelineOut:
    db = _db(request)
    row = repos.get_timeline(db, timeline_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")
    return _timeline_out(db, row)


@router.get(
    "/timelines/{timeline_id}/clips/{clip_id}/source", response_model=ClipSourceOut
)
def clip_source(timeline_id: str, clip_id: str, request: Request) -> ClipSourceOut:
    """Resolve a clip back to its source anchor: asset + source frames, plus the
    transcript segment and word frames when the clip originated from words (jump-back)."""
    db = _db(request)
    if repos.get_timeline(db, timeline_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")
    clip = next(
        (c for c in repos.list_timeline_clips(db, timeline_id) if c["id"] == clip_id), None
    )
    if clip is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "clip not found")

    ws = clip.get("origin_word_start_id")
    we = clip.get("origin_word_end_id")
    seg_id: str | None = None
    word_start: int | None = None
    word_end: int | None = None
    if ws and (w0 := repos.get_word(db, ws)) is not None:
        seg_id = w0.get("segment_id")
        word_start = w0.get("start_frame")
    if we and (w1 := repos.get_word(db, we)) is not None:
        word_end = w1.get("end_frame")

    return ClipSourceOut(
        clip_id=clip_id,
        asset_id=clip["asset_id"],
        src_in_frame=clip["src_in_frame"],
        src_out_frame_exclusive=clip["src_out_frame_exclusive"],
        origin_word_start_id=ws,
        origin_word_end_id=we,
        segment_id=seg_id,
        word_start_frame=word_start,
        word_end_frame=word_end,
    )


def _timeline_caption_segments(db: Database, timeline_id: str) -> list[dict[str, Any]]:
    """Caption cues from a rough cut: one cue per transcript-derived clip, timed at the
    clip's SEQUENCE position (not the source) so the subtitles match the edit."""
    segments: list[dict[str, Any]] = []
    for clip in repos.list_timeline_clips(db, timeline_id):  # ordered by seq_in, lane
        ws, we = clip.get("origin_word_start_id"), clip.get("origin_word_end_id")
        if not ws or not we:
            continue
        words = repos.get_words_in_range(db, ws, we)
        if not words:
            continue
        segment = repos.get_segment(db, words[0]["segment_id"])
        segments.append({
            "start_frame": clip["seq_in_frame"],
            "end_frame": clip["seq_out_frame_exclusive"],
            "text": join_words(words),
            "speaker_label": segment.get("speaker_label") if segment else None,
        })
    return segments


def _timeline_captions(db: Database, timeline_id: str, fmt: str) -> str:
    row = repos.get_timeline(db, timeline_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")
    project = repos.get_project(db, row["project_id"])
    assert project is not None
    segments = _timeline_caption_segments(db, timeline_id)
    rate_num = project["sequence_rate_num"]
    rate_den = project["sequence_rate_den"]
    return (
        segments_to_srt(segments, rate_num, rate_den)
        if fmt == "srt"
        else segments_to_vtt(segments, rate_num, rate_den)
    )


@router.get("/timelines/{timeline_id}/captions.srt")
def timeline_captions_srt(timeline_id: str, request: Request) -> PlainTextResponse:
    return PlainTextResponse(
        _timeline_captions(_db(request), timeline_id, "srt"),
        media_type="application/x-subrip",
    )


@router.get("/timelines/{timeline_id}/captions.vtt")
def timeline_captions_vtt(timeline_id: str, request: Request) -> PlainTextResponse:
    return PlainTextResponse(
        _timeline_captions(_db(request), timeline_id, "vtt"), media_type="text/vtt"
    )


@router.patch("/timelines/{timeline_id}", response_model=TimelineOut)
def rename_timeline(
    timeline_id: str,
    body: RenameRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> TimelineOut:
    db = _db(request)
    if repos.get_timeline(db, timeline_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")
    repos.rename_timeline(db, timeline_id, body.name)
    audit.record(db, principal, "timeline.rename", entity_type="timeline", entity_id=timeline_id)
    row = repos.get_timeline(db, timeline_id)
    assert row is not None
    return _timeline_out(db, row)


@router.delete("/timelines/{timeline_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_timeline(
    timeline_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> None:
    db = _db(request)
    if repos.get_timeline(db, timeline_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")
    repos.delete_timeline(db, timeline_id)
    audit.record(db, principal, "timeline.delete", entity_type="timeline", entity_id=timeline_id)


def _require(value: Any, message: str) -> Any:
    if value is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message)
    return value


def _apply(db: Database, current: list[EditClip], body: OperationRequest) -> list[EditClip]:
    op = body.op
    if op == "append_from_words":
        w0 = repos.get_word(db, _require(body.word_start_id, "word_start_id required"))
        w1 = repos.get_word(db, _require(body.word_end_id, "word_end_id required"))
        if w0 is None or w1 is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "word not found")
        if w0["asset_id"] != w1["asset_id"]:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                "words must be from the same asset")
        clip = EditClip(
            asset_id=w0["asset_id"],
            src_in_frame=min(w0["start_frame"], w1["start_frame"]),
            src_out_frame_exclusive=max(w0["end_frame"], w1["end_frame"]),
            seq_in_frame=0, seq_out_frame_exclusive=0, lane=body.lane,
            origin_word_start_id=body.word_start_id, origin_word_end_id=body.word_end_id,
        )
        return append_clip(current, clip)

    if op in {"append_clip", "insert_clip"}:
        clip = EditClip(
            asset_id=_require(body.asset_id, "asset_id required"),
            src_in_frame=_require(body.src_in_frame, "src_in_frame required"),
            src_out_frame_exclusive=_require(body.src_out_frame_exclusive,
                                             "src_out_frame_exclusive required"),
            seq_in_frame=0, seq_out_frame_exclusive=0, lane=body.lane,
        )
        if op == "append_clip":
            return append_clip(current, clip)
        return insert_clip(current, clip, _require(body.at_seq_frame, "at_seq_frame required"))

    if op in {"delete", "lift"}:
        seq_in = _require(body.seq_in_frame, "seq_in_frame required")
        seq_out = _require(body.seq_out_frame_exclusive, "seq_out_frame_exclusive required")
        fn = delete_range if op == "delete" else lift_range
        return fn(current, seq_in, seq_out)

    if op == "set_speed":
        at = _require(body.at_seq_frame, "at_seq_frame required")
        sn = _require(body.speed_num, "speed_num required")
        sd = _require(body.speed_den, "speed_den required")
        try:
            return set_speed(current, at, sn, sd)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    if op == "split":
        at = _require(body.at_seq_frame, "at_seq_frame required")
        try:
            return split_clip(current, at)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    if op == "trim":
        at = _require(body.at_seq_frame, "at_seq_frame required")
        si = _require(body.new_src_in_frame, "new_src_in_frame required")
        so = _require(body.new_src_out_frame_exclusive, "new_src_out_frame_exclusive required")
        try:
            return trim_clip(current, at, si, so)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    if op == "move":
        at = _require(body.at_seq_frame, "at_seq_frame required")
        to = _require(body.to_seq_frame, "to_seq_frame required")
        try:
            return move_clip(current, at, to)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    if op == "delete_words":
        w0 = repos.get_word(db, _require(body.word_start_id, "word_start_id required"))
        w1 = repos.get_word(db, _require(body.word_end_id, "word_end_id required"))
        if w0 is None or w1 is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "word not found")
        if w0["asset_id"] != w1["asset_id"]:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "words must be from the same asset"
            )
        lo = min(w0["start_frame"], w1["start_frame"])
        hi = max(w0["end_frame"], w1["end_frame"])
        span = map_asset_range_to_seq(current, asset_id=w0["asset_id"], src_lo=lo, src_hi=hi)
        if span is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "selected words are not present in this timeline",
            )
        return delete_range(current, span[0], span[1])

    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown op: {op}")


@router.post("/timelines/{timeline_id}/operations", response_model=TimelineOut)
def apply_operation(timeline_id: str, body: OperationRequest, request: Request) -> TimelineOut:
    db = _db(request)
    row = repos.get_timeline(db, timeline_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")

    current = [EditClip.from_row(c) for c in repos.list_timeline_clips(db, timeline_id)]
    new_clips = _apply(db, current, body)

    repos.replace_timeline_clips(db, timeline_id, [c.to_row() for c in ordered(new_clips)])
    fresh = repos.get_timeline(db, timeline_id)
    assert fresh is not None
    repos.update_timeline_otio(db, timeline_id, timeline_to_otio_string(_build_model(db, fresh)))
    return _timeline_out(db, fresh)


@router.put("/timelines/{timeline_id}/clips", response_model=TimelineOut)
def set_timeline_clips(
    timeline_id: str,
    body: SetClipsRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> TimelineOut:
    """Replace a timeline's clips wholesale — the primitive behind undo/redo (restore a
    saved snapshot). Clips are re-materialised and the OTIO is regenerated."""
    db = _db(request)
    if repos.get_timeline(db, timeline_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")
    rows = [c.model_dump() for c in body.clips]
    repos.replace_timeline_clips(db, timeline_id, rows)
    fresh = repos.get_timeline(db, timeline_id)
    assert fresh is not None
    repos.update_timeline_otio(db, timeline_id, timeline_to_otio_string(_build_model(db, fresh)))
    audit.record(
        db, principal, "timeline.set_clips", entity_type="timeline", entity_id=timeline_id
    )
    return _timeline_out(db, fresh)


@router.post("/interop/validate", response_model=ValidateOut)
def interop_validate(body: ValidateRequest, request: Request) -> ValidateOut:
    db = _db(request)
    row = repos.get_timeline(db, body.timeline_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")
    result = validate_export(_build_model(db, row), body.format)
    return ValidateOut(**result)


@router.post("/timelines/{timeline_id}/render", status_code=status.HTTP_202_ACCEPTED)
def render_timeline(
    timeline_id: str, body: RenderRequest, request: Request
) -> dict[str, str]:
    """Enqueue an MP4 render job for a timeline and return the export record id."""
    db = _db(request)
    tl = repos.get_timeline(db, timeline_id)
    if tl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")
    exp = repos.create_export(
        db, project_id=tl["project_id"], timeline_id=timeline_id, format=body.format
    )
    job_id = enqueue(
        db,
        queue=queue_for("export.render"),
        kind="export.render",
        payload={"export_id": exp["id"]},
        idempotency_key=f"render:{exp['id']}",
    )
    return {"export_id": exp["id"], "job_id": job_id}


@router.get("/projects/{project_id}/exports", response_model=list[RenderExportOut])
def list_project_exports(
    project_id: str, request: Request
) -> list[RenderExportOut]:
    """List all render-pipeline exports for a project."""
    db = _db(request)
    return [RenderExportOut(**e) for e in repos.list_exports(db, project_id)]


@router.post(
    "/timelines/{timeline_id}/exports",
    response_model=ExportOut,
    status_code=status.HTTP_201_CREATED,
)
def export_timeline(
    timeline_id: str, body: ExportRequest, request: Request,
    principal: Annotated[Principal, Depends(require_permission("export:create"))],
) -> ExportOut:
    db = _db(request)
    row = repos.get_timeline(db, timeline_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")

    fmt = body.format.lower()
    if fmt in {"srt", "vtt"}:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "captions are exported via /timelines/{id}/captions.srt|.vtt (rough cut) "
            "or /assets/{id}/captions.srt|.vtt (full transcript)",
        )
    if fmt not in _EXT:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unsupported format: {fmt}")

    model = _build_model(db, row)
    diagnostics = validate_export(model, fmt)
    content = _WRITERS[fmt](model)

    project = repos.get_project(db, row["project_id"])
    assert project is not None
    out_dir = Path(project["workspace_root"]) / "exports" / timeline_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"timeline.{_EXT[fmt]}"
    out_path.write_text(content, encoding="utf-8")

    export = repos.create_interchange_export(
        db, timeline_id=timeline_id, fmt=fmt, status="succeeded",
        output_path=str(out_path), options=body.options, diagnostics=diagnostics,
    )
    audit.record(db, principal, "export.create", entity_type="export", entity_id=export["id"],
                 payload={"timeline_id": timeline_id, "format": fmt})
    return ExportOut(
        id=export["id"], timeline_id=timeline_id, format=fmt, status="succeeded",
        output_path=str(out_path), lossy=diagnostics["lossy"], drops=diagnostics["drops"],
        warnings=diagnostics["warnings"], content=content,
    )
