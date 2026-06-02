"""Pydantic request/response models for the API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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
    source_path: str = Field(min_length=1)
    display_name: str | None = None


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


class ClipOut(BaseModel):
    id: str
    asset_id: str
    src_in_frame: int
    src_out_frame_exclusive: int
    seq_in_frame: int
    seq_out_frame_exclusive: int
    lane: int
    speaker_id: str | None = None


class TimelineOut(BaseModel):
    id: str
    project_id: str
    name: str
    kind: str
    created_at: str
    clips: list[ClipOut] = Field(default_factory=list)


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
    op: str  # append_from_words | append_clip | insert_clip | delete | lift
    asset_id: str | None = None
    src_in_frame: int | None = None
    src_out_frame_exclusive: int | None = None
    word_start_id: str | None = None
    word_end_id: str | None = None
    seq_in_frame: int | None = None
    seq_out_frame_exclusive: int | None = None
    at_seq_frame: int | None = None
    lane: int = 0
