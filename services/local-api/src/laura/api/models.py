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
    # URL-ingest comfort options (yt-dlp only; ignored for source_path imports):
    # quality vocabulary best|1080|720|audio, and a browser to read cookies from for
    # private/age-restricted/login-walled sources (chrome|edge|firefox|brave).
    format: str | None = None
    cookies_from_browser: str | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> AssetImport:
        if bool(self.source_path) == bool(self.source_url):
            raise ValueError("provide exactly one of source_path or source_url")
        return self


class ImportAccepted(BaseModel):
    asset_id: str
    job_id: str
    # Additional asset ids when a playlist/channel URL fanned out into multiple assets
    # (asset_id/job_id stay the first entry for backward compatibility).
    extra_asset_ids: list[str] = Field(default_factory=list)


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
    detector: str = "adaptive"  # shot detector: adaptive|content|histogram|transnet|hybrid


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
    black_ratio: float | None = None
    static_score: float | None = None
    phash: str | None = None
    blur_score: float | None = None
    keep: bool = True
    drop_reason: str | None = None


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
    """Build a rough cut with one contiguous clip per kept shot of an asset."""

    asset_id: str
    run_id: str | None = None        # default: the asset's latest analysis run
    timeline_id: str | None = None   # populate this timeline (must be empty); else create new
    name: str | None = Field(default=None, max_length=200)
    lane: int = Field(default=0, ge=0)
    quality: bool = True                # drop weak shots + merge micro by default
    drop_black: bool | None = None      # per-filter overrides (None = follow `quality`)
    drop_static: bool | None = None
    drop_duplicates: bool | None = None
    drop_blur: bool | None = None
    merge_min_frames: int = Field(default=0, ge=0)
    align_editorial: bool = True        # snap clip cuts to transcript word-gaps (Stage 2)
    editorial_window: int = Field(default=12, ge=0)  # max frames a cut may move (~0.4s@30fps)
    # Picture-vs-sound preference for joint visual+editorial placement: 0 = picture-first (keep
    # the frame-exact visual cut), 1 = sound-first (favour the clean word edge). None -> the
    # product default 0.6/0.4 visual/editorial weights. Only used when align_editorial is True.
    cut_bias: float | None = Field(default=None, ge=0.0, le=1.0)


class DroppedShot(BaseModel):
    src_in_frame: int
    src_out_frame_exclusive: int
    drop_reason: str


class SplitCutOut(BaseModel):
    """A per-cut split-edit (L/J) recommendation for an inter-clip cut.

    RECOMMENDATION ONLY: the stored clips are unchanged hard cuts. This surfaces, per cut, the
    independent optimal picture frame (visual peak) and sound frame (real silence / clean
    word-gap) so the UI/editor can SEE which cuts would benefit from an L- or J-cut and by how
    many frames. All frames are source-frame indices of the asset.
    """

    seq_cut: int          # the original (visual) cut frame on the source
    video_frame: int      # recommended picture cut = visual peak
    audio_frame: int      # recommended sound cut = real silence / clean word-gap
    offset: int           # audio_frame - video_frame (0 == hard cut)
    kind: str             # "hard" | "L" (audio after video) | "J" (audio before video)


class RoughCutQualityOut(BaseModel):
    """Headline quality of a built rough cut — one score blending picture and speech.

    Computed on the fly over the built clips (no persistence): ``visual_exactness`` is the share
    of inter-clip cuts landing within one frame of the true luma peak, ``editorial_cleanliness``
    the share not bisecting a spoken word, and ``overall`` their ``cut_bias``-weighted blend (all
    in ``[0, 1]``). ``n_cuts`` is the number of inter-clip cuts scored; ``n_split_cuts`` the count
    of recommended L/J split edits among them. All fields mirror
    :class:`laura.analysis.eval_quality.RoughCutQuality` plus the split-cut tally.
    """

    overall: float
    visual_exactness: float
    editorial_cleanliness: float
    n_cuts: int
    n_split_cuts: int


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
    # Signed per-clip LEADING-edge audio-vs-video offset in samples (invariant #3); 0 = hard cut.
    # Surfaced so undo/redo (GET clips -> PUT clips snapshot) round-trips the L/J split state.
    audio_offset_samples: int = 0


class TimelineOut(BaseModel):
    id: str
    project_id: str
    name: str
    kind: str
    created_at: str
    clips: list[ClipOut] = Field(default_factory=list)


class FromShotsOut(BaseModel):
    timeline: TimelineOut
    dropped: list[DroppedShot] = Field(default_factory=list)
    # Per-cut L/J split-edit recommendations for the inter-clip cuts (recommendation only — the
    # stored clips remain hard cuts). Empty when there is no transcript/silence to plan against.
    split_cuts: list[SplitCutOut] = Field(default_factory=list)
    # On-the-fly quality summary of the built rough cut (overall + visual exactness + editorial
    # cleanliness, blended by the request's cut_bias). None when it can't be computed gracefully
    # (no editorial alignment, or no readable video to score visual exactness against).
    quality: RoughCutQualityOut | None = None


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
    # append_from_words|append_clip|insert_clip|delete|lift|set_speed|split|trim|move|
    # delete_words|set_audio_offset
    op: str
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
    to_seq_frame: int | None = None              # move: target sequence position
    # set_audio_offset: the LEADING-edge L/J audio offset of the clip at at_seq_frame, expressed in
    # FRAMES (the UI's native drag unit). The backend projects it onto canonical samples via the
    # project sequence rate (invariant #3), mirroring the accept endpoint; > 0 = L-cut (audio
    # later), < 0 = J-cut (audio earlier), |offset| < 1 frame is clamped to a hard cut.
    audio_offset_frames: int | None = Field(
        default=None, ge=-100_000, le=100_000
    )


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
    # Signed per-clip LEADING-edge audio-vs-video offset in samples (invariant #3); 0 = hard cut.
    # Restored verbatim from a snapshot so undo/redo brings back the L/J split as the live column.
    audio_offset_samples: int = 0


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


class ImportStatusOut(BaseModel):
    phase: str  # queued | downloading | verifying | analyzing | ready | cancelled | error
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    speed_bps: float | None = None
    eta_seconds: float | None = None
    error: str | None = None


# --- render-pipeline exports ------------------------------------------------
class SceneOut(BaseModel):
    id: str
    project_id: str
    source_timeline_id: str
    name: str
    order_index: int
    seq_in_frame: int
    seq_out_frame_exclusive: int
    scene_timeline_id: str | None = None
    music_asset_id: str | None = None
    music_gain_percent: int = 100
    # Enrichment populated only by list_project_scenes (the assemble bin): the source
    # video this scene came from and a representative source frame for a thumbnail.
    asset_id: str | None = None
    thumb_frame: int | None = None


class GenerateScenesRequest(BaseModel):
    asset_id: str
    gap_frames: int | None = Field(default=None, ge=0)


class SplitSceneRequest(BaseModel):
    at_seq_frame: int = Field(ge=0)


class MergeScenesRequest(BaseModel):
    scene_id: str


class RenameSceneRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class SetSceneMusicRequest(BaseModel):
    asset_id: str
    gain_percent: int = Field(default=100, ge=0, le=400)


class RenderRequest(BaseModel):
    format: str = "mp4"


class RenderExportOut(BaseModel):
    id: str
    project_id: str
    timeline_id: str | None = None
    format: str
    status: str
    path: str | None = None
    size_bytes: int | None = None
    error: str | None = None
    created_at: str | None = None


# --- sequence (stage 5) ------------------------------------------------------
class SequenceItemOut(BaseModel):
    id: str
    scene_id: str
    scene_name: str
    order_index: int


class SequenceOut(BaseModel):
    timeline_id: str
    project_id: str
    items: list[SequenceItemOut] = Field(default_factory=list)


class SetSequenceScenesRequest(BaseModel):
    scene_ids: list[str]
