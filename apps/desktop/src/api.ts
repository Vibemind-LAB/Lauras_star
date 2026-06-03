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
  created_at: string;
  files: AssetFile[];
}

export interface ImportAccepted {
  asset_id: string;
  job_id: string;
}

export interface Waveform {
  version: number;
  sample_rate: number;
  samples_per_pixel: number;
  length: number;
  peaks: number[];
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
  speaker_id: string | null;
  origin_word_start_id: string | null;
  origin_word_end_id: string | null;
  speed_num: number;
  speed_den: number;
}

export interface Timeline {
  id: string;
  project_id: string;
  name: string;
  kind: string;
  created_at: string;
  clips: TimelineClip[];
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
    | "trim";
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
}

export interface Segment {
  id: string;
  speaker_label: string | null;
  start_frame: number;
  end_frame: number;
  text: string;
  words: Word[];
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

  listProjects(): Promise<Project[]> {
    return this.request<Project[]>("/projects");
  }

  createProject(body: NewProject): Promise<Project> {
    return this.request<Project>("/projects", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  listAssets(projectId: string): Promise<Asset[]> {
    return this.request<Asset[]>(`/projects/${projectId}/assets`);
  }

  getAsset(assetId: string): Promise<Asset> {
    return this.request<Asset>(`/assets/${assetId}`);
  }

  importAsset(projectId: string, sourcePath: string): Promise<ImportAccepted> {
    return this.request<ImportAccepted>(`/projects/${projectId}/assets/import`, {
      method: "POST",
      body: JSON.stringify({ source_path: sourcePath }),
    });
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
}
