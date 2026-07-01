import { afterEach, describe, expect, it, vi } from "vitest";

import { type Asset, hasFile, LauraClient } from "./api";

const make = (kinds: string[]): Asset =>
  ({ files: kinds.map((k) => ({ kind: k })) }) as unknown as Asset;

describe("hasFile", () => {
  it("finds a present file kind", () => {
    expect(hasFile(make(["proxy", "waveform"]), "proxy")).toBe(true);
  });

  it("returns false for a missing kind", () => {
    expect(hasFile(make(["poster"]), "proxy")).toBe(false);
  });

  it("returns false when there are no files", () => {
    expect(hasFile(make([]), "proxy")).toBe(false);
  });
});

afterEach(() => vi.restoreAllMocks());

function mockFetch(json: unknown) {
  const fn = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => json,
    text: async () => "",
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("import client methods", () => {
  it("createDemoProject POSTs the demo endpoint", async () => {
    const fn = mockFetch({ id: "p-demo" });
    const c = new LauraClient("http://h", "tok");
    await c.createDemoProject();
    expect(fn).toHaveBeenCalledWith(
      "http://h/projects/demo",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("importAssetFromUrl posts source_url", async () => {
    const fn = mockFetch({ asset_id: "a", job_id: "j", extra_asset_ids: [] });
    const c = new LauraClient("http://h", "tok");
    await c.importAssetFromUrl("p1", "https://x/y.mp4");
    expect(fn).toHaveBeenCalledWith(
      "http://h/projects/p1/assets/import",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ source_url: "https://x/y.mp4" }) }),
    );
  });

  it("importAssetFromUrl threads format + cookies into the body", async () => {
    const fn = mockFetch({ asset_id: "a", job_id: "j", extra_asset_ids: [] });
    const c = new LauraClient("http://h", "tok");
    await c.importAssetFromUrl("p1", "https://youtu.be/x", {
      format: "1080",
      cookiesFromBrowser: "chrome",
    });
    const [, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      source_url: "https://youtu.be/x",
      format: "1080",
      cookies_from_browser: "chrome",
    });
  });

  it("importAssetFromUrl returns extra_asset_ids for a playlist fan-out", async () => {
    mockFetch({ asset_id: "a", job_id: "j", extra_asset_ids: ["b", "c"] });
    const c = new LauraClient("http://h", "tok");
    const accepted = await c.importAssetFromUrl("p1", "https://yt/playlist");
    expect([accepted.asset_id, ...accepted.extra_asset_ids]).toEqual(["a", "b", "c"]);
  });

  it("getImportStatus GETs the status", async () => {
    const fn = mockFetch({ phase: "downloading", downloaded_bytes: 1, total_bytes: 2 });
    const c = new LauraClient("http://h", "tok");
    const st = await c.getImportStatus("a1");
    expect(fn).toHaveBeenCalledWith("http://h/assets/a1/import-status", expect.anything());
    expect(st.phase).toBe("downloading");
  });

  it("GETs asset provenance", async () => {
    const fn = mockFetch({ schema: "laura.ai.provenance.v1", asset_id: "a1" });
    const c = new LauraClient("http://h", "tok");
    await c.getAssetProvenance("a1");
    expect(fn).toHaveBeenCalledWith("http://h/assets/a1/provenance", expect.anything());
  });

  it("retryImport posts import-retry", async () => {
    const fn = mockFetch({ asset_id: "a1", job_id: "j" });
    const c = new LauraClient("http://h", "tok");
    await c.retryImport("a1");
    expect(fn).toHaveBeenCalledWith(
      "http://h/assets/a1/import-retry",
      expect.objectContaining({ method: "POST" }),
    );
  });
});

describe("LauraClient.startAnalysis", () => {
  it("forwards the detector option in the POST body", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ analysis_run_id: "r1" }),
    } as unknown as Response);

    const client = new LauraClient("http://localhost:8765", "tok");
    await client.startAnalysis("asset-1", { detector: "histogram" });

    expect(fetchSpy).toHaveBeenCalledOnce();
    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body["detector"]).toBe("histogram");
  });

  it("defaults to no detector key when option is omitted", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ analysis_run_id: "r2" }),
    } as unknown as Response);

    const client = new LauraClient("http://localhost:8765", "tok");
    await client.startAnalysis("asset-2");

    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body["detector"]).toBeUndefined();
  });
});

describe("LauraClient.buildRoughCutFromShots", () => {
  it("POSTs to the correct URL and returns dropped list", async () => {
    const mockResponse = {
      timeline: {
        id: "t",
        project_id: "p",
        name: "RC",
        kind: "rough_cut",
        created_at: "",
        clips: [],
      },
      dropped: [{ src_in_frame: 0, src_out_frame_exclusive: 9, drop_reason: "black" }],
    };

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockResponse),
    } as unknown as Response);

    const client = new LauraClient("http://localhost:8765", "test-token");
    const result = await client.buildRoughCutFromShots("p", "a");

    expect(fetchSpy).toHaveBeenCalledOnce();
    const [calledUrl] = fetchSpy.mock.calls[0] as [string, ...unknown[]];
    expect(calledUrl).toBe("http://localhost:8765/projects/p/timelines/from-shots");
    expect(result.dropped[0].drop_reason).toBe("black");
  });

  it("threads cut_bias into the body and omits it when not given", async () => {
    const withBias = mockFetch({ timeline: {}, dropped: [], split_cuts: [], quality: null });
    const c = new LauraClient("http://h", "tok");
    await c.buildRoughCutFromShots("p", "a", "tl", { cutBias: 0.7 });
    const [, init] = withBias.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      asset_id: "a",
      timeline_id: "tl",
      cut_bias: 0.7,
    });

    const noBias = mockFetch({ timeline: {}, dropped: [], split_cuts: [], quality: null });
    await c.buildRoughCutFromShots("p", "a");
    const [, init2] = noBias.mock.calls[0] as [string, RequestInit];
    const body2 = JSON.parse(init2.body as string) as Record<string, unknown>;
    expect("cut_bias" in body2).toBe(false);
  });

  it("returns the quality summary and split_cuts from the response", async () => {
    mockFetch({
      timeline: { id: "t", project_id: "p", name: "RC", kind: "rough_cut", created_at: "", clips: [] },
      dropped: [],
      split_cuts: [{ seq_cut: 50, video_frame: 50, audio_frame: 53, offset: 3, kind: "L" }],
      quality: {
        overall: 0.8,
        visual_exactness: 0.9,
        editorial_cleanliness: 0.6,
        n_cuts: 3,
        n_split_cuts: 1,
      },
    });
    const c = new LauraClient("http://h", "tok");
    const res = await c.buildRoughCutFromShots("p", "a");
    expect(res.quality?.overall).toBe(0.8);
    expect(res.split_cuts[0].kind).toBe("L");
  });
});

describe("LauraClient sequence transcript methods", () => {
  it("GETs the sequence transcript", async () => {
    const fn = mockFetch([]);
    const c = new LauraClient("http://h", "tok") as unknown as {
      getSequenceTranscript: (sequenceId: string) => Promise<unknown>;
    };
    await c.getSequenceTranscript("seq-1");
    expect(fn).toHaveBeenCalledWith("http://h/sequences/seq-1/transcript", expect.anything());
  });

  it("PATCHes transcript segment text", async () => {
    const fn = mockFetch({ id: "seg-1", text: "Better" });
    const c = new LauraClient("http://h", "tok") as unknown as {
      updateTranscriptSegment: (
        segmentId: string,
        body: { text?: string; speakerId?: string | null },
      ) => Promise<unknown>;
    };
    await c.updateTranscriptSegment("seg-1", { text: "Better" });
    const [, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(fn).toHaveBeenCalledWith(
      "http://h/transcript/segments/seg-1",
      expect.objectContaining({ method: "PATCH" }),
    );
    expect(JSON.parse(init.body as string)).toEqual({ text: "Better" });
  });

  it("POSTs transcript realignment for selected segments", async () => {
    const fn = mockFetch({ job_id: "job-1" });
    const c = new LauraClient("http://h", "tok") as unknown as {
      realignTranscript: (
        assetId: string,
        body: { segmentIds?: string[]; language?: string },
      ) => Promise<unknown>;
    };
    await c.realignTranscript("asset-1", { segmentIds: ["seg-1"], language: "en" });
    const [, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(fn).toHaveBeenCalledWith(
      "http://h/assets/asset-1/transcript:realign",
      expect.objectContaining({ method: "POST" }),
    );
    expect(JSON.parse(init.body as string)).toEqual({
      segment_ids: ["seg-1"],
      language: "en",
    });
  });

  it("GETs a job status by id", async () => {
    const fn = mockFetch({ id: "job-1", status: "succeeded" });
    const c = new LauraClient("http://h", "tok") as unknown as {
      getJob: (jobId: string) => Promise<unknown>;
    };
    await c.getJob("job-1");
    expect(fn).toHaveBeenCalledWith("http://h/jobs/job-1", expect.anything());
  });

  it("lists, cancels, and retries jobs", async () => {
    const listFetch = mockFetch([]);
    const c = new LauraClient("http://h", "tok");
    await c.listJobs(25);
    expect(listFetch).toHaveBeenCalledWith("http://h/jobs?limit=25", expect.anything());

    const cancelFetch = mockFetch({ id: "job-1", status: "cancelled" });
    await c.cancelJob("job-1");
    expect(cancelFetch).toHaveBeenCalledWith(
      "http://h/jobs/job-1/cancel",
      expect.objectContaining({ method: "POST" }),
    );

    const retryFetch = mockFetch({ job_id: "job-2" });
    await c.retryJob("job-1");
    expect(retryFetch).toHaveBeenCalledWith(
      "http://h/jobs/job-1/retry",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("POSTs voiceover generation for a transcript segment range", async () => {
    const fn = mockFetch({ job_id: "voice-job-1" });
    const c = new LauraClient("http://h", "tok");
    await c.createVoiceover("tl-1", {
      segmentId: "seg-1",
      text: "Better line",
      seqIn: 10,
      seqOut: 40,
      gainPercent: 90,
      fadeInFrames: 3,
      fadeOutFrames: 4,
    });
    const [, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(fn).toHaveBeenCalledWith(
      "http://h/timelines/tl-1/voiceover",
      expect.objectContaining({ method: "POST" }),
    );
    expect(JSON.parse(init.body as string)).toEqual({
      segment_id: "seg-1",
      text: "Better line",
      seq_in_frame: 10,
      seq_out_frame_exclusive: 40,
      gain_percent: 90,
      fade_in_frames: 3,
      fade_out_frames: 4,
    });
  });

  it("PATCHes a sequence item transition", async () => {
    const fn = mockFetch({ timeline_id: "seq", project_id: "p", items: [] });
    const c = new LauraClient("http://h", "tok");
    await c.updateSequenceTransition("seq", "item-1", {
      kind: "dip_black",
      durationFrames: 12,
    });
    const [, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(fn).toHaveBeenCalledWith(
      "http://h/sequences/seq/items/item-1/transition",
      expect.objectContaining({ method: "PATCH" }),
    );
    expect(JSON.parse(init.body as string)).toEqual({
      kind: "dip_black",
      duration_frames: 12,
    });
  });

  it("POSTs reel caption direction options", async () => {
    const fn = mockFetch({ export_id: "e1", job_id: "j1" });
    const c = new LauraClient("http://h", "tok");
    await c.renderReel("tl-1", {
      hookText: null,
      disclosureText: "KI",
      vertical: true,
      captions: true,
      captionPreset: "reels",
      captionMode: "normal",
      captionPosition: "top",
      captionFontsize: 84,
      captionSafeMargin: 180,
    });
    const [, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      hook_text: null,
      disclosure_text: "KI",
      vertical: true,
      captions: true,
      caption_preset: "reels",
      caption_mode: "normal",
      caption_position: "top",
      caption_fontsize: 84,
      caption_safe_margin: 180,
      max_duration_seconds: null,
    });
  });

  it("creates, updates, and applies demo drafts", async () => {
    const c = new LauraClient("http://h", "tok");
    const item = {
      src_in_frame: 0,
      src_out_frame_exclusive: 30,
      label: "Intro",
      voiceover_text: "Intro line",
      thumb_frame: 0,
      confidence: 0.8,
      enabled: true,
    };
    const draft = {
      id: "draft-1",
      project_id: "p",
      asset_id: "asset-1",
      status: "ready",
      items: [item],
      result: {},
      created_at: "",
      updated_at: "",
      applied_at: null,
    };

    const createFetch = mockFetch({ draft_id: "draft-1", job_id: "job-1" });
    await c.createDemoDraft("asset-1");
    expect(createFetch).toHaveBeenCalledWith(
      "http://h/assets/asset-1/demo-drafts",
      expect.objectContaining({ method: "POST" }),
    );

    const getFetch = mockFetch(draft);
    await c.getDemoDraft("draft-1");
    expect(getFetch).toHaveBeenCalledWith("http://h/demo-drafts/draft-1", expect.anything());

    const patchFetch = mockFetch(draft);
    await c.updateDemoDraft("draft-1", [item]);
    const [, patchInit] = patchFetch.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(patchInit.body as string)).toEqual({ items: [item] });

    const applyFetch = mockFetch({
      draft,
      sequence: { timeline_id: "seq", project_id: "p", items: [] },
    });
    await c.applyDemoDraft("draft-1");
    expect(applyFetch).toHaveBeenCalledWith(
      "http://h/demo-drafts/draft-1/apply",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("POSTs lipsync with consent, license, audio, and quality gate fields", async () => {
    const fn = mockFetch({ job_id: "lip-job-1" });
    const c = new LauraClient("http://h", "tok");
    await c.lipsync("tl-1", {
      seqIn: 10,
      seqOut: 50,
      audioAssetId: "audio-1",
      consentId: "consent-1",
      licenseAccepted: true,
      backend: "vibevideo",
      qualityThreshold: 0.72,
    });
    const [, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(fn).toHaveBeenCalledWith(
      "http://h/timelines/tl-1/lipsync",
      expect.objectContaining({ method: "POST" }),
    );
    expect(JSON.parse(init.body as string)).toEqual({
      seq_in_frame: 10,
      seq_out_frame_exclusive: 50,
      audio_asset_id: "audio-1",
      consent_id: "consent-1",
      license_accepted: true,
      backend: "vibevideo",
      quality_threshold: 0.72,
    });
  });
});

describe("LauraClient.cutAtFrame", () => {
  it("cutAtFrame posts at_seq_frame and returns clips + scenes", async () => {
    const fn = mockFetch({ clips: [], scenes: [] });
    const c = new LauraClient("http://x", "tok");
    const out = await c.cutAtFrame("tl1", 42);
    expect(out).toEqual({ clips: [], scenes: [] });
    const [url, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://x/timelines/tl1/cut-at-frame");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ at_seq_frame: 42 });
  });
});

describe("LauraClient timeline audio clip methods", () => {
  it("GETs timeline audio clips", async () => {
    const fn = mockFetch([]);
    const c = new LauraClient("http://h", "tok");
    await c.listTimelineAudioClips("tl-1");
    expect(fn).toHaveBeenCalledWith("http://h/timelines/tl-1/audio-clips", expect.anything());
  });

  it("POSTs a timeline audio clip with snake_case frame fields", async () => {
    const fn = mockFetch({ id: "ac-1" });
    const c = new LauraClient("http://h", "tok");
    await c.createTimelineAudioClip("tl-1", {
      assetId: "asset-1",
      seqIn: 10,
      seqOut: 70,
      assetIn: 4,
      gainPercent: 80,
      fadeInFrames: 5,
      fadeOutFrames: 6,
      mixMode: "replace_original",
      duckingPercent: 25,
      label: "VO",
    });
    const [, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(fn).toHaveBeenCalledWith(
      "http://h/timelines/tl-1/audio-clips",
      expect.objectContaining({ method: "POST" }),
    );
    expect(JSON.parse(init.body as string)).toEqual({
      asset_id: "asset-1",
      seq_in_frame: 10,
      seq_out_frame_exclusive: 70,
      asset_in_frame: 4,
      gain_percent: 80,
      fade_in_frames: 5,
      fade_out_frames: 6,
      mix_mode: "replace_original",
      ducking_percent: 25,
      label: "VO",
    });
  });

  it("PATCHes only provided audio clip fields", async () => {
    const fn = mockFetch({ id: "ac-1" });
    const c = new LauraClient("http://h", "tok");
    await c.updateTimelineAudioClip("tl-1", "ac-1", {
      gainPercent: 120,
      mixMode: "mix",
      duckingPercent: 60,
      label: "mix",
    });
    const [, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(fn).toHaveBeenCalledWith(
      "http://h/timelines/tl-1/audio-clips/ac-1",
      expect.objectContaining({ method: "PATCH" }),
    );
    expect(JSON.parse(init.body as string)).toEqual({
      gain_percent: 120,
      mix_mode: "mix",
      ducking_percent: 60,
      label: "mix",
    });
  });

  it("DELETEs a timeline audio clip", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      text: () => Promise.resolve(""),
    } as unknown as Response);
    const c = new LauraClient("http://h", "tok");
    await c.deleteTimelineAudioClip("tl-1", "ac-1");
    expect(fetchSpy).toHaveBeenCalledWith(
      "http://h/timelines/tl-1/audio-clips/ac-1",
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});

describe("LauraClient shorts methods", () => {
  it("listShortsCandidates GETs the correct URL", async () => {
    const fn = mockFetch([]);
    const c = new LauraClient("http://h", "tok");
    await c.listShortsCandidates("asset-1");
    expect(fn).toHaveBeenCalledWith(
      "http://h/assets/asset-1/shorts-candidates",
      expect.anything(),
    );
  });

  it("extractShorts POSTs to the :extract action with opts in the body", async () => {
    const fn = mockFetch({ job_id: "j1", analysis_run_id: "r1" });
    const c = new LauraClient("http://h", "tok");
    await c.extractShorts("asset-2", { min_duration_s: 15, max_duration_s: 60, max_candidates: 5 });
    expect(fn).toHaveBeenCalledWith(
      "http://h/assets/asset-2/shorts-candidates:extract",
      expect.objectContaining({ method: "POST" }),
    );
    const [, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      min_duration_s: 15,
      max_duration_s: 60,
      max_candidates: 5,
    });
  });

  it("extractShorts POSTs an empty body when no opts are given", async () => {
    const fn = mockFetch({ job_id: "j2", analysis_run_id: "r2" });
    const c = new LauraClient("http://h", "tok");
    const result = await c.extractShorts("asset-3");
    expect(fn).toHaveBeenCalledWith(
      "http://h/assets/asset-3/shorts-candidates:extract",
      expect.objectContaining({ method: "POST", body: JSON.stringify({}) }),
    );
    expect(result.job_id).toBe("j2");
  });
});
