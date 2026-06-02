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
}

export interface Word {
  idx: number;
  start_frame: number;
  end_frame: number;
  text: string;
  is_punctuation: boolean;
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

  health(): Promise<Health> {
    return this.request<Health>("/healthz");
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
}
