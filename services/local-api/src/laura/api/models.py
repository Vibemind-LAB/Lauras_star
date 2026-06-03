"""Pydantic request/response models for the API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sequence_rate_num: int = Field(gt=0, description="e.g. 30000 for 29.97")
    sequence_rate_den: int = Field(default=1, gt=0, description="e.g. 1001 for 29.97")
    drop_frame: bool = False


class ProjectOut(BaseModel):
    id: str
    name: str
    sequence_rate_num: int
    sequence_rate_den: int
    drop_frame: bool
    workspace_root: str
    created_at: str


class HealthOut(BaseModel):
    status: str
    version: str
    pipeline_version: str
    schema_version: int


class AssetImport(BaseModel):
    source_path: str | None = Field(default=None, min_length=1)
    source_url: str | None = Field(default=None, min_length=1)
    display_name: str | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> AssetImport:
        if bool(self.source_path) == bool(self.source_url):
            raise ValueError("provide exactly one of source_path or source_url")
        return self


class ImportAccepted(BaseModel):
    asset_id: str
    job_id: str


class AssetFileOut(BaseModel):
    id: str
    asset_id: str
    kind: str
    path: str
    size_bytes: int | None = None
    is_proxy: bool = False
    is_waveform: bool = False
    is_audio_extract: bool = False
    checksum: str | None = None


class AssetOut(BaseModel):
    id: str
    project_id: str
    type: str
    display_name: str
    source_path: str
    sha256: str | None = None
    duration_frames: int | None = None
    rate_num: int | None = None
    rate_den: int | None = None
    audio_sample_rate: int | None = None
    start_timecode: str | None = None
    width: int | None = None
    height: int | None = None
    codec_video: str | None = None
    codec_audio: str | None = None
    is_vfr: bool = False
    online: bool = True
    created_at: str
    files: list[AssetFileOut] = Field(default_factory=list)


class JobOut(BaseModel):
    id: str
    queue: str
    kind: str
    status: str
    attempt: int
    max_attempts: int
    result_json: str | None = None
    error_json: str | None = None
    created_at: str
    updated_at: str
    finished_at: str | None = None


class AnalysisStart(BaseModel):
    scene: bool = True
    asr: bool = True
    diarize: bool = False
    align: bool = False
    model: str = "base"
    language: str | None = None


class AnalysisAccepted(BaseModel):
    analysis_run_id: str
    job_id: str


class AnalysisRunOut(BaseModel):
    id: str
    asset_id: str
    pipeline_version: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class ShotOut(BaseModel):
    id: str
    src_in_frame: int
    src_out_frame_exclusive: int
    confidence: float | None = None
    method: str | None = None
    thumbnail_path: str | None = None


class WordOut(BaseModel):
    id: str
    idx: int
    start_sample: int
    end_sample: int
    start_frame: int
    end_frame: int
    text: str
    confidence: float | None = None
    is_punctuation: bool = False


class SegmentOut(BaseModel):
    id: str
    speaker_id: str | None = None
    speaker_label: str | None = None
    start_sample: int
    end_sample: int
    start_frame: int
    end_frame: int
    text: str
    confidence: float | None = None
    words: list[WordOut] = Field(default_factory=list)


class TimelineCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = "rough_cut"


class FromShotsRequest(BaseModel):
    """Build a rough cut with one contiguous clip per detected shot of an asset."""

    asset_id: str
    run_id: str | None = None        # default: the asset's latest analysis run
    timeline_id: str | None = None   # populate this timeline (must be empty); else create new
    name: str | None = Field(default=None, max_length=200)
    lane: int = Field(default=0, ge=0)


class ClipOut(BaseModel):
    id: str
    asset_id: str
    src_in_frame: int
    src_out_frame_exclusive: int
    seq_in_frame: int
    seq_out_frame_exclusive: int
    lane: int
    speaker_id: str | None = None
    origin_word_start_id: str | None = None
    origin_word_end_id: str | None = None
    speed_num: int = 1
    speed_den: int = 1


class TimelineOut(BaseModel):
    id: str
    project_id: str
    name: str
    kind: str
    created_at: str
    clips: list[ClipOut] = Field(default_factory=list)


class ClipSourceOut(BaseModel):
    """Jump-back anchor for a timeline clip: where it came from in the source."""

    clip_id: str
    asset_id: str
    src_in_frame: int
    src_out_frame_exclusive: int
    origin_word_start_id: str | None = None
    origin_word_end_id: str | None = None
    segment_id: str | None = None        # transcript segment the origin words belong to
    word_start_frame: int | None = None  # source frame of the first origin word
    word_end_frame: int | None = None    # source frame (exclusive) of the last origin word


class TimelineImportRequest(BaseModel):
    content: str
    format: str = "otio"
    name: str | None = None


class TimelineImportOut(BaseModel):
    timeline: TimelineOut
    matched_media: int   # distinct source files relinked to existing project assets
    offline_media: int   # distinct source files turned into offline placeholders


class ExportRequest(BaseModel):
    format: str
    options: dict[str, Any] = Field(default_factory=dict)


class ExportOut(BaseModel):
    id: str
    timeline_id: str
    format: str
    status: str
    output_path: str | None = None
    lossy: bool = False
    drops: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    content: str | None = None


class ValidateRequest(BaseModel):
    timeline_id: str
    format: str


class ValidateOut(BaseModel):
    format: str
    ok: bool
    lossy: bool
    warnings: list[str] = Field(default_factory=list)
    drops: list[str] = Field(default_factory=list)


class OperationRequest(BaseModel):
    op: str  # append_from_words|append_clip|insert_clip|delete|lift|set_speed|split|trim
    asset_id: str | None = None
    src_in_frame: int | None = None
    src_out_frame_exclusive: int | None = None
    word_start_id: str | None = None
    word_end_id: str | None = None
    seq_in_frame: int | None = None
    seq_out_frame_exclusive: int | None = None
    at_seq_frame: int | None = None
    lane: int = 0
    speed_num: int | None = None
    speed_den: int | None = None
    new_src_in_frame: int | None = None          # trim: new source in point
    new_src_out_frame_exclusive: int | None = None  # trim: new source out point


class ClipIn(BaseModel):
    """A timeline clip as accepted by the wholesale set-clips endpoint (undo/redo)."""

    asset_id: str
    src_in_frame: int
    src_out_frame_exclusive: int
    seq_in_frame: int
    seq_out_frame_exclusive: int
    lane: int = 0
    speaker_id: str | None = None
    origin_word_start_id: str | None = None
    origin_word_end_id: str | None = None
    speed_num: int = 1
    speed_den: int = 1


class SetClipsRequest(BaseModel):
    clips: list[ClipIn] = Field(default_factory=list)


# --- enterprise ----------------------------------------------------------
class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class OrgOut(BaseModel):
    id: str
    name: str
    created_at: str


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str | None = None
    role: str = "editor"


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str | None = None
    created_at: str
    role: str


class KeyCreate(BaseModel):
    name: str | None = None
    role: str = "editor"
    user_id: str | None = None


class KeyCreated(BaseModel):
    id: str
    prefix: str
    role: str
    key: str  # full key — shown ONCE
    created_at: str


class AuditEventOut(BaseModel):
    id: str
    org_id: str | None = None
    principal_kind: str
    principal_id: str | None = None
    action: str
    entity_type: str | None = None
    entity_id: str | None = None
    created_at: str


class SearchRequest(BaseModel):
    project_id: str
    query: str = Field(min_length=1)
    limit: int = Field(default=50, ge=1, le=500)
    mode: str = "lexical"  # "lexical" (LIKE) | "semantic" (Qdrant; falls back if absent)


class SearchResult(BaseModel):
    segment_id: str
    asset_id: str
    asset_name: str
    start_frame: int
    end_frame: int
    text: str
    speaker_label: str | None = None
    score: float | None = None  # semantic similarity (None for lexical)


class SegmentUpdate(BaseModel):
    text: str | None = None
    speaker_id: str | None = None


class RenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
