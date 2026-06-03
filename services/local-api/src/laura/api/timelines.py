"""Timeline, export, and interchange-validation endpoints (docs/04-api.md, 07-interchange.md)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse

from .. import audit
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
from ..interchange.captions import join_words, segments_to_srt, segments_to_vtt
from ..interchange.edl import timeline_to_edl
from ..interchange.fcp7_xml import timeline_to_fcp7_xml
from ..interchange.fcpx_xml import timeline_to_fcpx_xml
from ..interchange.otio_io import otio_string_to_timeline, timeline_to_otio_string
from ..interchange.timeline import Timeline, timeline_from_rows
from ..interchange.validate import validate_export
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
    SetClipsRequest,
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
    project = repos.get_project(db, timeline_row["project_id"])
    assert project is not None
    clip_rows = repos.list_timeline_clips(db, timeline_row["id"])
    assets = {
        aid: a
        for aid in {c["asset_id"] for c in clip_rows}
        if (a := repos.get_asset(db, aid)) is not None
    }
    speakers = {
        sid: s
        for sid in {c["speaker_id"] for c in clip_rows if c.get("speaker_id")}
        if (s := repos.get_speaker(db, sid)) is not None
    }
    return timeline_from_rows(timeline_row, clip_rows, project, assets, speakers)


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
    return FromShotsOut(timeline=_timeline_out(db, fresh), dropped=dropped)


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

    export = repos.create_export(
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
