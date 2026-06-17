import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type AnalysisRun, type Asset, type LauraClient } from "../api";
import { MediaSidebar } from "./MediaSidebar";

const analysisRun = (status: string): AnalysisRun =>
  ({
    id: "run-1",
    asset_id: "a1",
    pipeline_version: "v",
    status,
    started_at: null,
    finished_at: null,
    diagnostics: {},
  });

// jsdom cannot load real images, so assetFrameUrl returns a promise that never
// resolves — the same "never-resolving stub" pattern used by SceneInspector.test.tsx
// and SceneStrip.test.tsx to prevent object-URL lifecycle issues under jsdom.
function makeClient(
  overrides: Partial<{
    getLatestAnalysis: ReturnType<typeof vi.fn>;
    getAssetProvenance: ReturnType<typeof vi.fn>;
    startAnalysis: ReturnType<typeof vi.fn>;
  }> = {},
): LauraClient {
  return {
    assetFrameUrl: vi.fn().mockReturnValue(new Promise<string>(() => undefined)),
    getLatestAnalysis: vi.fn().mockResolvedValue(null),
    getAssetProvenance: vi.fn().mockResolvedValue(null),
    startAnalysis: vi.fn().mockResolvedValue({ analysis_run_id: "run-1" }),
    ...overrides,
  } as unknown as LauraClient;
}

function makeAsset(id: string, displayName: string): Asset {
  return {
    id,
    project_id: "p1",
    type: "video",
    display_name: displayName,
    source_path: `/videos/${displayName}`,
    sha256: null,
    duration_frames: 1800,
    rate_num: 30,
    rate_den: 1,
    audio_sample_rate: 48000,
    start_timecode: null,
    width: 1920,
    height: 1080,
    codec_video: "h264",
    codec_audio: "aac",
    is_vfr: false,
    synthetic: false,
    ai_effect: null,
    created_at: "2025-01-01T00:00:00Z",
    files: [],
  };
}

const ASSETS: Asset[] = [
  makeAsset("a1", "clip-alpha.mp4"),
  makeAsset("a2", "clip-beta.mp4"),
];

describe("MediaSidebar", () => {
  it("renders one row per asset with the asset's display_name", () => {
    const client = makeClient();
    render(
      <MediaSidebar
        client={client}
        assets={ASSETS}
        selectedAssetId={null}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("clip-alpha.mp4")).toBeTruthy();
    expect(screen.getByText("clip-beta.mp4")).toBeTruthy();
  });

  it("renders the empty state when no assets are passed", () => {
    const client = makeClient();
    render(
      <MediaSidebar
        client={client}
        assets={[]}
        selectedAssetId={null}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText(/Keine Videos/)).toBeTruthy();
  });

  it("shows the asset count in the header", () => {
    const client = makeClient();
    render(
      <MediaSidebar
        client={client}
        assets={ASSETS}
        selectedAssetId={null}
        onSelect={vi.fn()}
      />,
    );
    // The count badge sits next to "Projekt-Medien"
    expect(screen.getByText("2")).toBeTruthy();
  });

  it("calls onSelect with the asset id when a row is clicked", () => {
    const client = makeClient();
    const onSelect = vi.fn();
    render(
      <MediaSidebar
        client={client}
        assets={ASSETS}
        selectedAssetId={null}
        onSelect={onSelect}
      />,
    );
    fireEvent.click(screen.getByText("clip-beta.mp4"));
    expect(onSelect).toHaveBeenCalledWith("a2");
  });

  it("calls onSelect with the first asset's id when that row is clicked", () => {
    const client = makeClient();
    const onSelect = vi.fn();
    render(
      <MediaSidebar
        client={client}
        assets={ASSETS}
        selectedAssetId={null}
        onSelect={onSelect}
      />,
    );
    fireEvent.click(screen.getByText("clip-alpha.mp4"));
    expect(onSelect).toHaveBeenCalledWith("a1");
  });

  it("calls startAnalysis with the correct assetId and options when Analysieren is clicked", async () => {
    const startAnalysis = vi.fn().mockResolvedValue({ analysis_run_id: "run-42" });
    const client = makeClient({ startAnalysis });
    const { findAllByRole } = render(
      <MediaSidebar
        client={client}
        assets={ASSETS}
        selectedAssetId={null}
        onSelect={vi.fn()}
      />,
    );
    // getLatestAnalysis resolves null → items render "Analysieren" buttons; wait for them
    const buttons = await findAllByRole("button", { name: "Analysieren" });
    // Click the first item's Analysieren button
    fireEvent.click(buttons[0]);
    expect(startAnalysis).toHaveBeenCalledWith("a1", {
      scene: true,
      asr: true,
      diarize: false,
      align: false,
      detector: "adaptive",
    });
  });

  it("keeps polling past the old 3-minute cap until terminal — no bogus Timeout", async () => {
    // Regression: the old schedulePoll gave up after POLL_MAX (120 * 1.5s = 180s) with
    // "Timeout — bitte erneut versuchen". A 30-min video's transcription runs longer, so
    // the poll must continue until the run is actually terminal.
    vi.useFakeTimers();
    try {
      let calls = 0;
      const getLatestAnalysis = vi.fn(async () => {
        calls += 1;
        // calls===1 is the mount probe; stay "running" well past the old 120-poll cap.
        return calls > 130 ? analysisRun("succeeded") : analysisRun("running");
      });
      const client = makeClient({ getLatestAnalysis });
      render(
        <MediaSidebar
          client={client}
          assets={[makeAsset("a1", "long-clip.mp4")]}
          selectedAssetId={null}
          onSelect={vi.fn()}
        />,
      );
      // Flush the mount probe so the "Analysieren" button renders.
      await act(async () => {
        await Promise.resolve();
      });
      const btn = screen.getByRole("button", { name: "Analysieren" });
      await act(async () => {
        fireEvent.click(btn);
        // Advance far past the old 180s cap so >120 poll iterations elapse.
        await vi.advanceTimersByTimeAsync(300_000);
      });

      expect(screen.queryByText(/Timeout/)).toBeNull();
      expect(screen.getByText("✓ analysiert")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("highlights the selected asset row", async () => {
    const client = makeClient();
    const { findByText } = render(
      <MediaSidebar
        client={client}
        assets={ASSETS}
        selectedAssetId="a1"
        onSelect={vi.fn()}
      />,
    );
    const el = await findByText("clip-alpha.mp4");
    // The closest row element should have the active ring class
    const row = el.closest("[aria-pressed]");
    expect(row).toBeTruthy();
    expect(row?.getAttribute("aria-pressed")).toBe("true");
  });

  it("marks synthetic AI assets with their effect label", () => {
    const client = makeClient();
    const synthetic: Asset = {
      ...makeAsset("synthetic-1", "lipsync-output.mp4"),
      synthetic: true,
      ai_effect: "lipsync",
    };
    render(
      <MediaSidebar
        client={client}
        assets={[synthetic]}
        selectedAssetId={null}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("KI · lipsync")).toBeTruthy();
  });

  it("shows provenance details for the selected synthetic asset", async () => {
    const getAssetProvenance = vi.fn().mockResolvedValue({
      schema: "laura.ai.provenance.v1",
      asset_id: "synthetic-1",
      ai_effect: "lipsync",
      media_sha256: "0123456789abcdef",
      source: { timeline_id: "tl-1" },
    });
    const client = makeClient({ getAssetProvenance });
    const synthetic: Asset = {
      ...makeAsset("synthetic-1", "lipsync-output.mp4"),
      synthetic: true,
      ai_effect: "lipsync",
    };

    render(
      <MediaSidebar
        client={client}
        assets={[synthetic]}
        selectedAssetId="synthetic-1"
        onSelect={vi.fn()}
      />,
    );

    expect(await screen.findByText("Provenance")).toBeTruthy();
    expect(screen.getByText("sha256 0123456789ab")).toBeTruthy();
    expect(getAssetProvenance).toHaveBeenCalledWith("synthetic-1");
  });
});
