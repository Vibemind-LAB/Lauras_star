// Typed client for the local API (see services/local-api, docs/04-api.md).

export interface Health {
  status: string;
  version: string;
  pipeline_version: string;
  schema_version: number;
}

export interface Project {
  id: string;
  name: string;
  sequence_rate_num: number;
  sequence_rate_den: number;
  drop_frame: boolean;
  workspace_root: string;
  created_at: string;
}

export interface NewProject {
  name: string;
  sequence_rate_num: number;
  sequence_rate_den: number;
  drop_frame: boolean;
}

export interface AssetFile {
  id: string;
  asset_id: string;
  kind: string;
  path: string;
  size_bytes: number | null;
  is_proxy: boolean;
  is_waveform: boolean;
  is_audio_extract: boolean;
  checksum: string | null;
}

export interface Asset {
  id: string;
  project_id: string;
  type: string;
  display_name: string;
  source_path: string;
  sha256: string | null;
  duration_frames: number | null;
  rate_num: number | null;
  rate_den: number | null;
  audio_sample_rate: number | null;
  start_timecode: string | null;
  width: number | null;
  height: number | null;
  codec_video: string | null;
  codec_audio: string | null;
  is_vfr: boolean;
  synthetic: boolean;
  ai_effect: string | null;
  created_at: string;
  files: AssetFile[];
}

export interface AiProvenanceManifest {
  schema: string;
  asset_id: string;
  project_id?: string;
  synthetic?: boolean;
  ai_effect?: string | null;
  media_path?: string;
  media_sha256?: string;
  created_at?: string;
  source?: Record<string, unknown>;
}

export interface ImportAccepted {
  asset_id: string;
  job_id: string;
  /** Extra asset ids when a playlist/channel URL fanned out into several assets. */
  extra_asset_ids: string[];
}

/** Quality vocabulary the backend maps to a yt-dlp format selector. */
export type ImportFormat = "best" | "1080" | "720" | "audio";

/** Browser whose cookie store yt-dlp reads for private/login-walled sources. */
export type CookiesFromBrowser = "chrome" | "edge" | "firefox" | "brave";

export interface UrlImportOptions {
  format?: ImportFormat;
  cookiesFromBrowser?: CookiesFromBrowser;
}

export interface ImportStatus {
  phase: "queued" | "downloading" | "verifying" | "analyzing" | "ready" | "error" | "cancelled";
  downloaded_bytes: number | null;
  total_bytes: number | null;
  speed_bps: number | null;
  eta_seconds: number | null;
  error: string | null;
}

export interface JobStatus {
  id: string;
  queue: string;
  kind: string;
  status: string;
  attempt: number;
  max_attempts: number;
  result_json: string | null;
  error_json: string | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
}

export interface Waveform {
  version: number;
  sample_rate: number;
  samples_per_pixel: number;
  length: number;
  peaks: number[];
}

export interface Scene {
  id: string;
  project_id: string;
  source_timeline_id: string;
  name: string;
  order_index: number;
  seq_in_frame: number;
  seq_out_frame_exclusive: number;
  scene_timeline_id?: string | null;
  music_asset_id?: string | null;
  music_gain_percent?: number;
  /** Source video + representative frame — only set by listProjectScenes (assemble bin). */
  asset_id?: string | null;
  thumb_frame?: number | null;
}

export function hasFile(asset: Asset, kind: string): boolean {
  return asset.files.some((f) => f.kind === kind);
}

export interface Shot {
  id: string;
  src_in_frame: number;
  src_out_frame_exclusive: number;
  confidence: number | null;
  method: string | null;
  thumbnail_path: string | null;
  black_ratio: number | null;
  static_score: number | null;
  phash: string | null;
  blur_score: number | null;
  keep: boolean;
  drop_reason: string | null;
}

export interface DroppedShot {
  src_in_frame: number;
  src_out_frame_exclusive: number;
  drop_reason: string;
}

/** A per-cut split-edit (L/J) recommendation. "hard" = no offset; "L"/"J" = audio after/before. */
export interface SplitCut {
  seq_cut: number;
  video_frame: number;
  audio_frame: number;
  offset: number;
  kind: "hard" | "L" | "J";
}

/**
 * On-the-fly rough-cut quality, blended by the request's cut_bias. All scores are in [0, 1]:
 * overall (the weighted blend), visual_exactness (cuts on the true luma peak) and
 * editorial_cleanliness (cuts not bisecting a spoken word). n_cuts is the inter-clip cut count,
 * n_split_cuts the recommended L/J edits among them.
 */
export interface RoughCutQuality {
  overall: number;
  visual_exactness: number;
  editorial_cleanliness: number;
  n_cuts: number;
  n_split_cuts: number;
}

export interface BuildFromShotsResult {
  timeline: Timeline;
  dropped: DroppedShot[];
  split_cuts: SplitCut[];
  /** Null when it can't be computed (no editorial alignment / no readable video). */
  quality: RoughCutQuality | null;
}

/**
 * One accepted L/J split offset for an inter-clip cut (the „Übernehmen" action). `seqCut`
 * identifies the cut (the next clip's source-frame IN, == SplitCut.seq_cut); `offset =
 * audio_frame - video_frame` in frames (> 0 = L-cut, audio later; < 0 = J-cut, audio earlier).
 */
export interface AcceptedSplit {
  seqCut: number;
  offset: number;
}

/** The stored accepted set the backend confirms after an accept (hard |offset|<=1 entries dropped). */
export interface AcceptSplitCutsResult {
  accepted: AcceptedSplit[];
}

/**
 * Picture-vs-sound preference for the joint visual+editorial cut placement: 0 = picture-first
 * (keep the frame-exact visual cut), 1 = sound-first (favour the clean word edge). Omitting it
 * lets the backend apply its product default (0.6 visual / 0.4 editorial weights).
 */
export interface BuildFromShotsOptions {
  cutBias?: number;
}

export interface Word {
  id: string;
  idx: number;
  start_frame: number;
  end_frame: number;
  text: string;
  is_punctuation: boolean;
}

export interface TimelineClip {
  id: string;
  asset_id: string;
  src_in_frame: number;
  src_out_frame_exclusive: number;
  seq_in_frame: number;
  seq_out_frame_exclusive: number;
  lane: number;
  /** "base" for normal clips; "replace" for overlay clips on lane >= 1. */
  role?: string;
  speaker_id: string | null;
  origin_word_start_id: string | null;
  origin_word_end_id: string | null;
  speed_num: number;
  speed_den: number;
  /**
   * Signed per-clip LEADING-edge audio-vs-video offset in SAMPLES (invariant #3); 0 = hard cut.
   * `> 0` = L-cut (audio starts after the picture cut), `< 0` = J-cut (audio before). Drawn on the
   * A1 lane as the audio block's leading-edge shift, and set by the `set_audio_offset` op.
   */
  audio_offset_samples: number;
}

/**
 * A replace-overlay clip returned by POST /timelines/{id}/overlays.
 * Shape mirrors OverlayOut from the backend: `role` is always "replace",
 * `lane` is always >= 1. This is a type alias over TimelineClip for callers
 * that want the narrower semantic name.
 */
export type OverlayClip = TimelineClip;

export interface Timeline {
  id: string;
  project_id: string;
  name: string;
  kind: string;
  created_at: string;
  clips: TimelineClip[];
}

export interface TimelineAudioClip {
  id: string;
  timeline_id: string;
  asset_id: string;
  seq_in_frame: number;
  seq_out_frame_exclusive: number;
  asset_in_frame: number;
  gain_percent: number;
  fade_in_frames: number;
  fade_out_frames: number;
  mix_mode: AudioMixMode;
  ducking_percent: number;
  label: string | null;
  created_at: string;
}

export type AudioMixMode = "mix" | "replace_original" | "mute_original";

export interface TimelineAudioClipCreateInput {
  assetId: string;
  seqIn: number;
  seqOut: number;
  assetIn?: number;
  gainPercent?: number;
  fadeInFrames?: number;
  fadeOutFrames?: number;
  mixMode?: AudioMixMode;
  duckingPercent?: number;
  label?: string | null;
}

export interface TimelineAudioClipUpdateInput {
  seqIn?: number;
  seqOut?: number;
  assetIn?: number;
  gainPercent?: number;
  fadeInFrames?: number;
  fadeOutFrames?: number;
  mixMode?: AudioMixMode;
  duckingPercent?: number;
  label?: string | null;
}

export interface Operation {
  op:
    | "append_from_words"
    | "append_clip"
    | "insert_clip"
    | "delete"
    | "lift"
    | "set_speed"
    | "split"
    | "trim"
    | "move"
    | "set_audio_offset";
  asset_id?: string;
  src_in_frame?: number;
  src_out_frame_exclusive?: number;
  word_start_id?: string;
  word_end_id?: string;
  seq_in_frame?: number;
  seq_out_frame_exclusive?: number;
  at_seq_frame?: number;
  lane?: number;
  speed_num?: number;
  speed_den?: number;
  new_src_in_frame?: number;
  new_src_out_frame_exclusive?: number;
  to_seq_frame?: number;
  /**
   * set_audio_offset: the clip-head L/J audio offset in FRAMES (the UI's native drag unit). The
   * backend projects it onto canonical samples (invariant #3); `> 0` = L-cut, `< 0` = J-cut.
   */
  audio_offset_frames?: number;
}

export interface Segment {
  id: string;
  speaker_id?: string | null;
  speaker_label: string | null;
  start_sample?: number;
  end_sample?: number;
  start_frame: number;
  end_frame: number;
  text: string;
  confidence?: number | null;
  words: Word[];
  alignment_status?: TranscriptAlignmentStatus;
  alignment_job_id?: string | null;
  alignment_language?: string | null;
  alignment_error?: string | null;
  alignment_updated_at?: string | null;
}

export type TranscriptAlignmentStatus = "aligned" | "stale" | "aligning" | "failed" | string;

export interface SequenceTranscriptWord {
  id: string;
  idx: number;
  segment_id: string;
  asset_id: string;
  source_start_frame: number;
  source_end_frame: number;
  seq_in_frame: number;
  seq_out_frame_exclusive: number;
  text: string;
  confidence: number | null;
  is_punctuation: boolean;
}

export interface SequenceTranscriptBlock {
  segment_id: string;
  asset_id: string;
  speaker_label: string | null;
  source_start_frame: number;
  source_end_frame: number;
  seq_in_frame: number;
  seq_out_frame_exclusive: number;
  text: string;
  words: SequenceTranscriptWord[];
  alignment_status?: TranscriptAlignmentStatus;
  alignment_job_id?: string | null;
  alignment_language?: string | null;
  alignment_error?: string | null;
  alignment_updated_at?: string | null;
}

export interface TranscriptSegmentUpdate {
  text?: string;
  speakerId?: string | null;
}

export interface TranscriptRealignOptions {
  segmentIds?: string[];
  language?: string;
}

export interface TranscriptRealignAccepted {
  job_id: string;
}

export interface VoiceoverOptions {
  segmentId?: string;
  text?: string;
  seqIn: number;
  seqOut: number;
  language?: string;
  backend?: string;
  gainPercent?: number;
  fadeInFrames?: number;
  fadeOutFrames?: number;
}

export interface VoiceoverAccepted {
  job_id: string;
}

export interface LipsyncOptions {
  seqIn: number;
  seqOut: number;
  audioAssetId: string;
  consentId: string;
  licenseAccepted: boolean;
  backend?: string;
  qualityThreshold?: number;
}

export interface LipsyncAccepted {
  job_id: string;
}

export type CaptionPreset = "reels" | "tiktok" | "shorts" | "wide";
export type CaptionMode = "karaoke" | "normal";
export type CaptionPosition = "bottom" | "middle" | "top";

export interface ReelRenderOptions {
  hookText: string | null;
  disclosureText: string | null;
  vertical?: boolean;
  captions?: boolean;
  captionPreset?: CaptionPreset;
  captionMode?: CaptionMode;
  captionPosition?: CaptionPosition;
  captionFontsize?: number;
  captionSafeMargin?: number;
}

export interface AnalysisRun {
  id: string;
  asset_id: string;
  pipeline_version: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  diagnostics: Record<string, unknown>;
}

export interface AnalysisOptions {
  scene?: boolean;
  asr?: boolean;
  diarize?: boolean;
  align?: boolean;
  detector?: string;
}

export type SearchMode = "lexical" | "semantic";

export interface SearchResult {
  segment_id: string;
  asset_id: string;
  asset_name: string;
  start_frame: number;
  end_frame: number;
  text: string;
  speaker_label: string | null;
  score: number | null;
}

export interface Export {
  id: string;
  project_id: string;
  timeline_id: string | null;
  format: string;
  status: "rendering" | "ready" | "error";
  path: string | null;
  size_bytes: number | null;
  error: string | null;
  created_at: string;
}

export type ExportFormat = "otio" | "edl" | "fcp7xml" | "fcpxml";

export interface ExportResult {
  id: string;
  format: string;
  output_path: string | null;
  content: string | null;
  lossy: boolean;
  drops: string[];
  warnings: string[];
}

export interface SequenceItem {
  id: string;
  scene_id: string;
  scene_name: string;
  order_index: number;
  transition_after_kind: SequenceTransitionKind;
  transition_after_frames: number;
}

export type SequenceTransitionKind = "hard" | "dip_black" | "fade_black" | "crossfade";

export interface Sequence {
  timeline_id: string;
  project_id: string;
  items: SequenceItem[];
}

export interface SequenceTransitionUpdate {
  kind: SequenceTransitionKind;
  durationFrames: number;
}

export interface DemoDraftItem {
  src_in_frame: number;
  src_out_frame_exclusive: number;
  label: string;
  voiceover_text: string;
  thumb_frame: number;
  confidence: number;
  enabled: boolean;
}

export interface DemoDraft {
  id: string;
  project_id: string;
  asset_id: string;
  status: string;
  items: DemoDraftItem[];
  result: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  applied_at: string | null;
}

export interface DemoDraftAccepted {
  draft_id: string;
  job_id: string;
}

export interface DemoDraftApplyResult {
  draft: DemoDraft;
  sequence: Sequence;
}

/**
 * Narrow the accept-split-cuts response (wire shape `{ accepted: [{ seq_cut, offset }] }`) into the
 * typed camelCase `AcceptedSplit[]`. Defensive: any malformed entry is skipped rather than thrown,
 * mirroring the backend's graceful "missing split = hard cut" stance. No `any` — narrows `unknown`.
 */
function parseAcceptedSplits(raw: unknown): AcceptedSplit[] {
  if (typeof raw !== "object" || raw === null) return [];
  const accepted = (raw as { accepted?: unknown }).accepted;
  if (!Array.isArray(accepted)) return [];
  const out: AcceptedSplit[] = [];
  for (const entry of accepted) {
    if (typeof entry !== "object" || entry === null) continue;
    const { seq_cut, offset } = entry as { seq_cut?: unknown; offset?: unknown };
    if (typeof seq_cut === "number" && typeof offset === "number") {
      out.push({ seqCut: seq_cut, offset });
    }
  }
  return out;
}

export interface ConsentRecord {
  id: string;
  project_id: string;
  subject_label: string;
  confirmed_at: string | null;
  confirmed_by: string | null;
  source_asset_id: string | null;
  note: string | null;
  revoked_at: string | null;
}

export class LauraClient {
  constructor(
    private readonly baseUrl: string,
    private readonly token: string,
  ) {}

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        "X-Laura-Token": this.token,
        ...(init?.headers ?? {}),
      },
    });
    if (!res.ok) {
      throw new Error(`${res.status}: ${await res.text()}`);
    }
    return (await res.json()) as T;
  }

  private async del(path: string): Promise<void> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      method: "DELETE",
      headers: { "X-Laura-Token": this.token },
    });
    if (!res.ok) {
      throw new Error(`${res.status}: ${await res.text()}`);
    }
  }

  health(): Promise<Health> {
    return this.request<Health>("/healthz");
  }

  searchTranscript(
    projectId: string,
    query: string,
    mode: SearchMode = "lexical",
  ): Promise<SearchResult[]> {
    return this.request<SearchResult[]>("/search", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId, query, mode }),
    });
  }

  deleteProject(id: string): Promise<void> {
    return this.del(`/projects/${id}`);
  }

  renameProject(id: string, name: string): Promise<Project> {
    return this.request<Project>(`/projects/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    });
  }

  deleteAsset(id: string): Promise<void> {
    return this.del(`/assets/${id}`);
  }

  deleteTimeline(id: string): Promise<void> {
    return this.del(`/timelines/${id}`);
  }

  exportTimeline(timelineId: string, format: ExportFormat): Promise<ExportResult> {
    return this.request<ExportResult>(`/timelines/${timelineId}/exports`, {
      method: "POST",
      body: JSON.stringify({ format }),
    });
  }

  renderTimeline(timelineId: string, format: string): Promise<{ export_id: string; job_id: string }> {
    return this.request<{ export_id: string; job_id: string }>(`/timelines/${timelineId}/render`, {
      method: "POST",
      body: JSON.stringify({ format }),
    });
  }

  renderReel(
    timelineId: string,
    opts: ReelRenderOptions,
  ): Promise<{ export_id: string; job_id: string }> {
    return this.request<{ export_id: string; job_id: string }>(
      `/timelines/${timelineId}/render-reel`,
      {
        method: "POST",
          body: JSON.stringify({
            hook_text: opts.hookText,
            disclosure_text: opts.disclosureText,
            vertical: opts.vertical ?? true,
            captions: opts.captions ?? false,
            caption_preset: opts.captionPreset ?? "reels",
            caption_mode: opts.captionMode ?? "karaoke",
            caption_position: opts.captionPosition ?? "bottom",
            caption_fontsize: opts.captionFontsize ?? 72,
            caption_safe_margin: opts.captionSafeMargin ?? 250,
          }),
        },
      );
  }

  listExports(projectId: string): Promise<Export[]> {
    return this.request<Export[]>(`/projects/${projectId}/exports`);
  }

  listProjects(): Promise<Project[]> {
    return this.request<Project[]>("/projects");
  }

  createProject(body: NewProject): Promise<Project> {
    return this.request<Project>("/projects", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  createDemoProject(): Promise<Project> {
    return this.request<Project>("/projects/demo", { method: "POST" });
  }

  createDemoDraft(assetId: string): Promise<DemoDraftAccepted> {
    return this.request<DemoDraftAccepted>(`/assets/${assetId}/demo-drafts`, { method: "POST" });
  }

  getDemoDraft(draftId: string): Promise<DemoDraft> {
    return this.request<DemoDraft>(`/demo-drafts/${draftId}`);
  }

  updateDemoDraft(draftId: string, items: DemoDraftItem[]): Promise<DemoDraft> {
    return this.request<DemoDraft>(`/demo-drafts/${draftId}`, {
      method: "PATCH",
      body: JSON.stringify({ items }),
    });
  }

  applyDemoDraft(draftId: string): Promise<DemoDraftApplyResult> {
    return this.request<DemoDraftApplyResult>(`/demo-drafts/${draftId}/apply`, {
      method: "POST",
    });
  }

  listAssets(projectId: string): Promise<Asset[]> {
    return this.request<Asset[]>(`/projects/${projectId}/assets`);
  }

  getAsset(assetId: string): Promise<Asset> {
    return this.request<Asset>(`/assets/${assetId}`);
  }

  getAssetProvenance(assetId: string): Promise<AiProvenanceManifest> {
    return this.request<AiProvenanceManifest>(`/assets/${assetId}/provenance`);
  }

  importAsset(projectId: string, sourcePath: string): Promise<ImportAccepted> {
    return this.request<ImportAccepted>(`/projects/${projectId}/assets/import`, {
      method: "POST",
      body: JSON.stringify({ source_path: sourcePath }),
    });
  }

  importAssetFromUrl(
    projectId: string,
    url: string,
    opts: UrlImportOptions = {},
  ): Promise<ImportAccepted> {
    const body: Record<string, string> = { source_url: url };
    if (opts.format) body.format = opts.format;
    if (opts.cookiesFromBrowser) body.cookies_from_browser = opts.cookiesFromBrowser;
    return this.request<ImportAccepted>(`/projects/${projectId}/assets/import`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  getImportStatus(assetId: string): Promise<ImportStatus> {
    return this.request<ImportStatus>(`/assets/${assetId}/import-status`);
  }

  getJob(jobId: string): Promise<JobStatus> {
    return this.request<JobStatus>(`/jobs/${jobId}`);
  }

  listJobs(limit = 50): Promise<JobStatus[]> {
    return this.request<JobStatus[]>(`/jobs?limit=${limit}`);
  }

  cancelJob(jobId: string): Promise<JobStatus> {
    return this.request<JobStatus>(`/jobs/${jobId}/cancel`, { method: "POST" });
  }

  retryJob(jobId: string): Promise<{ job_id: string }> {
    return this.request<{ job_id: string }>(`/jobs/${jobId}/retry`, { method: "POST" });
  }

  retryImport(assetId: string): Promise<ImportAccepted> {
    return this.request<ImportAccepted>(`/assets/${assetId}/import-retry`, { method: "POST" });
  }

  async cancelImport(assetId: string): Promise<void> {
    const res = await fetch(`${this.baseUrl}/assets/${assetId}/import-cancel`, {
      method: "POST",
      headers: { "X-Laura-Token": this.token },
    });
    if (!res.ok) {
      throw new Error(`${res.status}: ${await res.text()}`);
    }
  }

  getWaveform(assetId: string): Promise<Waveform> {
    return this.request<Waveform>(`/assets/${assetId}/files/waveform`);
  }

  /** Fetch a derived file (poster/proxy) with the token and return an object URL. */
  async fileObjectUrl(assetId: string, kind: string): Promise<string> {
    const res = await fetch(`${this.baseUrl}/assets/${assetId}/files/${kind}`, {
      headers: { "X-Laura-Token": this.token },
      // Force a fresh, full GET. A partial cache entry (e.g. a large proxy whose load
      // was interrupted) makes Chromium revalidate with a Range request; a reset
      // mid-stream then surfaces in the renderer as "TypeError: Failed to fetch".
      cache: "no-store",
    });
    if (!res.ok) {
      throw new Error(`${res.status}: ${await res.text()}`);
    }
    return URL.createObjectURL(await res.blob());
  }

  startAnalysis(assetId: string, opts: AnalysisOptions = {}): Promise<{ analysis_run_id: string }> {
    return this.request<{ analysis_run_id: string }>(`/assets/${assetId}/analysis`, {
      method: "POST",
      body: JSON.stringify({ scene: true, asr: true, diarize: false, ...opts }),
    });
  }

  async getLatestAnalysis(assetId: string): Promise<AnalysisRun | null> {
    const res = await fetch(`${this.baseUrl}/assets/${assetId}/analysis/latest`, {
      headers: { "X-Laura-Token": this.token },
    });
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
    return (await res.json()) as AnalysisRun;
  }

  getShots(assetId: string): Promise<Shot[]> {
    return this.request<Shot[]>(`/assets/${assetId}/shots`);
  }

  /** Fetch a shot's thumbnail JPEG with the token and return an object URL. */
  async shotThumbnailUrl(shotId: string): Promise<string> {
    const res = await fetch(`${this.baseUrl}/shots/${shotId}/thumbnail`, {
      headers: { "X-Laura-Token": this.token },
      cache: "no-store",
    });
    if (!res.ok) {
      throw new Error(`${res.status}: ${await res.text()}`);
    }
    return URL.createObjectURL(await res.blob());
  }

  /** A JPEG of a given source frame of an asset (token), as an object URL — for
   *  timeline-clip thumbnails. */
  async assetFrameUrl(assetId: string, frame: number): Promise<string> {
    const res = await fetch(`${this.baseUrl}/assets/${assetId}/frame/${Math.max(0, frame)}`, {
      headers: { "X-Laura-Token": this.token },
      cache: "no-store",
    });
    if (!res.ok) {
      throw new Error(`${res.status}: ${await res.text()}`);
    }
    return URL.createObjectURL(await res.blob());
  }

  getTranscript(assetId: string): Promise<Segment[]> {
    return this.request<Segment[]>(`/assets/${assetId}/transcript`);
  }

  getSequenceTranscript(sequenceId: string): Promise<SequenceTranscriptBlock[]> {
    return this.request<SequenceTranscriptBlock[]>(`/sequences/${sequenceId}/transcript`);
  }

  updateTranscriptSegment(segmentId: string, update: TranscriptSegmentUpdate): Promise<Segment> {
    const body: Record<string, unknown> = {};
    if (update.text !== undefined) body.text = update.text;
    if (update.speakerId !== undefined) body.speaker_id = update.speakerId;
    return this.request<Segment>(`/transcript/segments/${segmentId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  }

  realignTranscript(
    assetId: string,
    opts: TranscriptRealignOptions = {},
  ): Promise<TranscriptRealignAccepted> {
    const body: Record<string, unknown> = {};
    if (opts.segmentIds !== undefined) body.segment_ids = opts.segmentIds;
    if (opts.language !== undefined) body.language = opts.language;
    return this.request<TranscriptRealignAccepted>(`/assets/${assetId}/transcript:realign`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  createVoiceover(timelineId: string, opts: VoiceoverOptions): Promise<VoiceoverAccepted> {
    const body: Record<string, unknown> = {
      seq_in_frame: opts.seqIn,
      seq_out_frame_exclusive: opts.seqOut,
    };
    if (opts.segmentId !== undefined) body.segment_id = opts.segmentId;
    if (opts.text !== undefined) body.text = opts.text;
    if (opts.language !== undefined) body.language = opts.language;
    if (opts.backend !== undefined) body.backend = opts.backend;
    if (opts.gainPercent !== undefined) body.gain_percent = opts.gainPercent;
    if (opts.fadeInFrames !== undefined) body.fade_in_frames = opts.fadeInFrames;
    if (opts.fadeOutFrames !== undefined) body.fade_out_frames = opts.fadeOutFrames;
    return this.request<VoiceoverAccepted>(`/timelines/${timelineId}/voiceover`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  lipsync(timelineId: string, opts: LipsyncOptions): Promise<LipsyncAccepted> {
    const body: Record<string, unknown> = {
      seq_in_frame: opts.seqIn,
      seq_out_frame_exclusive: opts.seqOut,
      audio_asset_id: opts.audioAssetId,
      consent_id: opts.consentId,
      license_accepted: opts.licenseAccepted,
    };
    if (opts.backend !== undefined) body.backend = opts.backend;
    if (opts.qualityThreshold !== undefined) body.quality_threshold = opts.qualityThreshold;
    return this.request<LipsyncAccepted>(`/timelines/${timelineId}/lipsync`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  async getCaptions(assetId: string, fmt: "srt" | "vtt"): Promise<string> {
    const res = await fetch(`${this.baseUrl}/assets/${assetId}/captions.${fmt}`, {
      headers: { "X-Laura-Token": this.token },
    });
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
    return res.text();
  }

  listTimelines(projectId: string): Promise<Timeline[]> {
    return this.request<Timeline[]>(`/projects/${projectId}/timelines`);
  }

  createTimeline(projectId: string, name: string, kind = "rough_cut"): Promise<Timeline> {
    return this.request<Timeline>(`/projects/${projectId}/timelines`, {
      method: "POST",
      body: JSON.stringify({ name, kind }),
    });
  }

  applyOperation(timelineId: string, op: Operation): Promise<Timeline> {
    return this.request<Timeline>(`/timelines/${timelineId}/operations`, {
      method: "POST",
      body: JSON.stringify(op),
    });
  }

  /** Replace a timeline's clips wholesale — used by undo/redo to restore a snapshot. */
  setClips(timelineId: string, clips: TimelineClip[]): Promise<Timeline> {
    return this.request<Timeline>(`/timelines/${timelineId}/clips`, {
      method: "PUT",
      body: JSON.stringify({ clips }),
    });
  }

  buildRoughCutFromShots(
    projectId: string,
    assetId: string,
    timelineId?: string,
    opts: BuildFromShotsOptions = {},
  ): Promise<BuildFromShotsResult> {
    const body: Record<string, unknown> = { asset_id: assetId, timeline_id: timelineId };
    if (opts.cutBias !== undefined) body.cut_bias = opts.cutBias;
    return this.request<BuildFromShotsResult>(`/projects/${projectId}/timelines/from-shots`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  /**
   * Accept (or take back) the recommended L/J split edits for a timeline — the „Übernehmen"
   * action. The full `accepted` set is the source of truth and is applied wholesale (idempotent):
   * re-posting the same set is a no-op, and omitting an entry takes that split back to a hard cut.
   *
   * Acceptance is migration-free: the offsets persist only in the timeline's OTIO blob metadata
   * and flow into the NLE exports — the internal (hard-cut) editing timeline is NOT changed. The
   * backend returns the stored set (read back from the blob) so the UI can confirm the live state.
   */
  async acceptSplitCuts(
    projectId: string,
    timelineId: string,
    accepted: AcceptedSplit[],
  ): Promise<AcceptSplitCutsResult> {
    const body = {
      accepted: accepted.map((a) => ({ seq_cut: a.seqCut, offset: a.offset })),
    };
    const raw = await this.request<unknown>(
      `/projects/${projectId}/timelines/${timelineId}/split-cuts`,
      { method: "POST", body: JSON.stringify(body) },
    );
    return { accepted: parseAcceptedSplits(raw) };
  }

  generateScenes(timelineId: string, assetId: string, gapFrames?: number): Promise<Scene[]> {
    return this.request<Scene[]>(`/timelines/${timelineId}/scenes:generate`, {
      method: "POST",
      body: JSON.stringify({ asset_id: assetId, gap_frames: gapFrames ?? null }),
    });
  }

  listScenes(timelineId: string): Promise<Scene[]> {
    return this.request<Scene[]>(`/timelines/${timelineId}/scenes`);
  }

  getAssetRoughCut(projectId: string, assetId: string): Promise<Timeline> {
    return this.request<Timeline>(`/projects/${projectId}/assets/${assetId}/rough-cut`);
  }

  /** Fetch a timeline (with its clips) by id. Works for any kind — unlike
   *  getSequenceFlattened, which only resolves kind="sequence" timelines. */
  getTimeline(timelineId: string): Promise<Timeline> {
    return this.request<Timeline>(`/timelines/${timelineId}`);
  }

  listProjectScenes(projectId: string): Promise<Scene[]> {
    return this.request<Scene[]>(`/projects/${projectId}/scenes`);
  }

  splitScene(timelineId: string, sceneId: string, atSeqFrame: number): Promise<Scene[]> {
    return this.request<Scene[]>(`/timelines/${timelineId}/scenes/${sceneId}/split`, {
      method: "POST",
      body: JSON.stringify({ at_seq_frame: atSeqFrame }),
    });
  }

  mergeScenes(timelineId: string, sceneId: string): Promise<Scene[]> {
    return this.request<Scene[]>(`/timelines/${timelineId}/scenes/merge`, {
      method: "POST",
      body: JSON.stringify({ scene_id: sceneId }),
    });
  }

  renameScene(sceneId: string, name: string): Promise<Scene> {
    return this.request<Scene>(`/scenes/${sceneId}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    });
  }

  openScene(sceneId: string): Promise<Timeline> {
    return this.request<Timeline>(`/scenes/${sceneId}/open`, { method: "POST" });
  }

  setSceneMusic(sceneId: string, assetId: string, gainPercent: number): Promise<Scene> {
    return this.request<Scene>(`/scenes/${sceneId}/music`, {
      method: "PUT",
      body: JSON.stringify({ asset_id: assetId, gain_percent: gainPercent }),
    });
  }

  removeSceneMusic(sceneId: string): Promise<Scene> {
    return this.request<Scene>(`/scenes/${sceneId}/music`, { method: "DELETE" });
  }

  deleteWords(timelineId: string, wordStartId: string, wordEndId: string): Promise<Timeline> {
    return this.request<Timeline>(`/timelines/${timelineId}/operations`, {
      method: "POST",
      body: JSON.stringify({ op: "delete_words", word_start_id: wordStartId, word_end_id: wordEndId }),
    });
  }

  getProjectSequence(projectId: string): Promise<Sequence> {
    return this.request<Sequence>(`/projects/${projectId}/sequence`);
  }

  setSequenceScenes(sequenceId: string, sceneIds: string[]): Promise<Sequence> {
    return this.request<Sequence>(`/sequences/${sequenceId}/scenes`, {
      method: "PUT",
      body: JSON.stringify({ scene_ids: sceneIds }),
    });
  }

  updateSequenceTransition(
    sequenceId: string,
    itemId: string,
    body: SequenceTransitionUpdate,
  ): Promise<Sequence> {
    return this.request<Sequence>(`/sequences/${sequenceId}/items/${itemId}/transition`, {
      method: "PATCH",
      body: JSON.stringify({
        kind: body.kind,
        duration_frames: body.durationFrames,
      }),
    });
  }

  getSequenceFlattened(sequenceId: string): Promise<TimelineClip[]> {
    return this.request<TimelineClip[]>(`/sequences/${sequenceId}/flattened`);
  }

  listTimelineAudioClips(timelineId: string): Promise<TimelineAudioClip[]> {
    return this.request<TimelineAudioClip[]>(`/timelines/${timelineId}/audio-clips`);
  }

  createTimelineAudioClip(
    timelineId: string,
    opts: TimelineAudioClipCreateInput,
  ): Promise<TimelineAudioClip> {
    const body: Record<string, unknown> = {
      asset_id: opts.assetId,
      seq_in_frame: opts.seqIn,
      seq_out_frame_exclusive: opts.seqOut,
    };
    if (opts.assetIn !== undefined) body.asset_in_frame = opts.assetIn;
    if (opts.gainPercent !== undefined) body.gain_percent = opts.gainPercent;
    if (opts.fadeInFrames !== undefined) body.fade_in_frames = opts.fadeInFrames;
    if (opts.fadeOutFrames !== undefined) body.fade_out_frames = opts.fadeOutFrames;
    if (opts.mixMode !== undefined) body.mix_mode = opts.mixMode;
    if (opts.duckingPercent !== undefined) body.ducking_percent = opts.duckingPercent;
    if (opts.label !== undefined) body.label = opts.label;
    return this.request<TimelineAudioClip>(`/timelines/${timelineId}/audio-clips`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  updateTimelineAudioClip(
    timelineId: string,
    clipId: string,
    opts: TimelineAudioClipUpdateInput,
  ): Promise<TimelineAudioClip> {
    const body: Record<string, unknown> = {};
    if (opts.seqIn !== undefined) body.seq_in_frame = opts.seqIn;
    if (opts.seqOut !== undefined) body.seq_out_frame_exclusive = opts.seqOut;
    if (opts.assetIn !== undefined) body.asset_in_frame = opts.assetIn;
    if (opts.gainPercent !== undefined) body.gain_percent = opts.gainPercent;
    if (opts.fadeInFrames !== undefined) body.fade_in_frames = opts.fadeInFrames;
    if (opts.fadeOutFrames !== undefined) body.fade_out_frames = opts.fadeOutFrames;
    if (opts.mixMode !== undefined) body.mix_mode = opts.mixMode;
    if (opts.duckingPercent !== undefined) body.ducking_percent = opts.duckingPercent;
    if (opts.label !== undefined) body.label = opts.label;
    return this.request<TimelineAudioClip>(`/timelines/${timelineId}/audio-clips/${clipId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  }

  deleteTimelineAudioClip(timelineId: string, clipId: string): Promise<void> {
    return this.del(`/timelines/${timelineId}/audio-clips/${clipId}`);
  }

  /**
   * Add a replacement-lane overlay clip to a timeline. Returns the created clip including
   * `role: "replace"` and `lane >= 1`.
   */
  setOverlay(
    timelineId: string,
    opts: { assetId: string; seqIn: number; seqOut: number; lane?: number; srcIn?: number },
  ): Promise<TimelineClip> {
    return this.request<TimelineClip>(`/timelines/${timelineId}/overlays`, {
      method: "POST",
      body: JSON.stringify({
        asset_id: opts.assetId,
        seq_in_frame: opts.seqIn,
        seq_out_frame_exclusive: opts.seqOut,
        lane: opts.lane ?? 1,
        src_in_frame: opts.srcIn ?? 0,
      }),
    });
  }

  /** Remove an overlay clip from a timeline by clip id. */
  removeOverlay(timelineId: string, clipId: string): Promise<void> {
    return this.del(`/timelines/${timelineId}/overlays/${clipId}`);
  }

  /**
   * Record consent for a subject within a project.
   * POST /projects/{projectId}/consent → 201 ConsentRecord
   */
  createConsent(
    projectId: string,
    opts: { subjectLabel: string },
  ): Promise<ConsentRecord> {
    return this.request<ConsentRecord>(`/projects/${projectId}/consent`, {
      method: "POST",
      body: JSON.stringify({ subject_label: opts.subjectLabel }),
    });
  }

  /**
   * Kick off a reenact (LivePortrait) job on a timeline range.
   * POST /timelines/{timelineId}/reenact → 202 { job_id }
   * consent_id is MANDATORY; revoked/missing consent is rejected server-side.
   */
  reenact(
    timelineId: string,
    opts: {
      seqIn: number;
      seqOut: number;
      portraitAssetId: string;
      consentId: string;
      backend?: string;
    },
  ): Promise<{ job_id: string }> {
    return this.request<{ job_id: string }>(`/timelines/${timelineId}/reenact`, {
      method: "POST",
      body: JSON.stringify({
        seq_in_frame: opts.seqIn,
        seq_out_frame_exclusive: opts.seqOut,
        portrait_asset_id: opts.portraitAssetId,
        consent_id: opts.consentId,
        backend: opts.backend,
      }),
    });
  }
}
