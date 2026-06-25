import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type Asset, type JobStatus, type LauraClient, type ShortsCandidate } from "../api";
import { ShortsView } from "./ShortsView";

vi.mock("./Player", () => ({ Player: () => <div data-testid="player" /> }));

afterEach(cleanup);

const asset: Asset = {
  id: "a1",
  project_id: "p1",
  type: "video",
  display_name: "test.mp4",
  source_path: "/tmp/test.mp4",
  sha256: null,
  duration_frames: 900,
  rate_num: 30,
  rate_den: 1,
  audio_sample_rate: null,
  start_timecode: null,
  width: 1920,
  height: 1080,
  codec_video: null,
  codec_audio: null,
  is_vfr: false,
  synthetic: false,
  ai_effect: null,
  created_at: "2024-01-01T00:00:00Z",
  files: [],
};

const CANDIDATE_A: ShortsCandidate = {
  id: "c1",
  asset_id: "a1",
  source_timeline_id: "tl1",
  order_index: 0,
  start_frame: 0,
  end_frame_exclusive: 450,
  start_boundary: "scene",
  end_boundary: "scene",
  score: 0.85,
  rejected: false,
  reject_reason: null,
  score_breakdown: { motion: 0.9, speech: 0.8, visual: 0.85 },
  qa_passed: true,
  qa_issues: [],
  created_at: "2024-01-01T00:00:00Z",
};

const CANDIDATE_B: ShortsCandidate = {
  id: "c2",
  asset_id: "a1",
  source_timeline_id: "tl1",
  order_index: 1,
  start_frame: 450,
  end_frame_exclusive: 900,
  start_boundary: "scene",
  end_boundary: "scene",
  score: 0.62,
  rejected: true,
  reject_reason: "too short",
  score_breakdown: null,
  qa_passed: false,
  qa_issues: ["duration too short"],
  created_at: "2024-01-01T00:00:00Z",
};

const SUCCEEDED_JOB: JobStatus = {
  id: "j1",
  queue: "default",
  kind: "shorts.extract",
  status: "succeeded",
  attempt: 1,
  max_attempts: 3,
  result_json: null,
  error_json: null,
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:01Z",
  finished_at: "2024-01-01T00:00:01Z",
};

function makeClient(over: Partial<LauraClient> = {}): LauraClient {
  return {
    listShortsCandidates: vi.fn().mockResolvedValue([CANDIDATE_A, CANDIDATE_B]),
    extractShorts: vi.fn().mockResolvedValue({ job_id: "j1", analysis_run_id: "r1" }),
    getJob: vi.fn().mockResolvedValue(SUCCEEDED_JOB),
    ...over,
  } as unknown as LauraClient;
}

describe("ShortsView", () => {
  it("renders a guard when no asset is selected", () => {
    const c = makeClient();
    const { getByText } = render(
      <ShortsView
        client={c}
        asset={null}
        seek={null}
        currentFrame={0}
        onSeek={vi.fn()}
        onFrame={vi.fn()}
      />,
    );
    expect(getByText(/Wähle ein Asset/)).toBeTruthy();
  });

  it("renders candidates with time, score, and QA badge", async () => {
    const c = makeClient();
    const { getByText, getAllByText } = render(
      <ShortsView
        client={c}
        asset={asset}
        seek={null}
        currentFrame={0}
        onSeek={vi.fn()}
        onFrame={vi.fn()}
      />,
    );
    // Candidate A: score 0.85, QA passed
    await waitFor(() => expect(getByText("0.85")).toBeTruthy());
    expect(getByText("QA ok")).toBeTruthy();
    // Candidate B: score 0.62, QA failed
    expect(getByText("0.62")).toBeTruthy();
    expect(getAllByText("QA fehler").length).toBeGreaterThan(0);
  });

  it("shows the start timecode for each candidate", async () => {
    const c = makeClient();
    const { getByText } = render(
      <ShortsView
        client={c}
        asset={asset}
        seek={null}
        currentFrame={0}
        onSeek={vi.fn()}
        onFrame={vi.fn()}
      />,
    );
    // candidate A starts at frame 0 → 0:00, ends at frame 450 (15s at 30fps) → 0:15
    await waitFor(() => expect(getByText(/0:00/)).toBeTruthy());
  });

  it("shows empty state when no candidates exist", async () => {
    const c = makeClient({ listShortsCandidates: vi.fn().mockResolvedValue([]) });
    const { getByText } = render(
      <ShortsView
        client={c}
        asset={asset}
        seek={null}
        currentFrame={0}
        onSeek={vi.fn()}
        onFrame={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(getByText(/Noch keine Kandidaten/)).toBeTruthy(),
    );
  });

  it("calls extractShorts with the asset id when the button is clicked", async () => {
    const c = makeClient();
    const { getByText } = render(
      <ShortsView
        client={c}
        asset={asset}
        seek={null}
        currentFrame={0}
        onSeek={vi.fn()}
        onFrame={vi.fn()}
      />,
    );
    await waitFor(() => expect(getByText("0.85")).toBeTruthy());
    fireEvent.click(getByText("Shorts extrahieren"));
    await waitFor(() =>
      expect(c.extractShorts).toHaveBeenCalledWith("a1"),
    );
  });

  it("calls onSeek with start_frame when a candidate row is clicked", async () => {
    const c = makeClient();
    const onSeek = vi.fn();
    const { getByText } = render(
      <ShortsView
        client={c}
        asset={asset}
        seek={null}
        currentFrame={0}
        onSeek={onSeek}
        onFrame={vi.fn()}
      />,
    );
    await waitFor(() => expect(getByText("0.85")).toBeTruthy());
    // Click on the score of candidate A (which has start_frame=0)
    fireEvent.click(getByText("0.85"));
    expect(onSeek).toHaveBeenCalledWith(CANDIDATE_A.start_frame);
  });

  it("shows rejected notice for a rejected candidate", async () => {
    const c = makeClient();
    const { getByText } = render(
      <ShortsView
        client={c}
        asset={asset}
        seek={null}
        currentFrame={0}
        onSeek={vi.fn()}
        onFrame={vi.fn()}
      />,
    );
    await waitFor(() => expect(getByText(/too short/)).toBeTruthy());
  });

  describe("job lifecycle", () => {
    beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
    afterEach(() => vi.useRealTimers());

    it("reloads candidates exactly once after the job succeeds", async () => {
      // getJob returns succeeded immediately on first poll.
      const c = makeClient();
      const { getByText } = render(
        <ShortsView
          client={c}
          asset={asset}
          seek={null}
          currentFrame={0}
          onSeek={vi.fn()}
          onFrame={vi.fn()}
        />,
      );

      // Wait for initial candidate load (first listShortsCandidates call).
      await waitFor(() => expect(getByText("0.85")).toBeTruthy());
      const callsBefore = (c.listShortsCandidates as ReturnType<typeof vi.fn>).mock.calls.length;

      fireEvent.click(getByText("Shorts extrahieren"));
      // extractShorts called → job_id set → useJobStatus polls immediately → succeeded → reload.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(100);
      });

      await waitFor(() => {
        const callsNow = (c.listShortsCandidates as ReturnType<typeof vi.fn>).mock.calls.length;
        expect(callsNow).toBeGreaterThan(callsBefore);
      });

      // Advance another full poll cycle and confirm no additional reload fires (no infinite loop).
      const callsAfterReload = (c.listShortsCandidates as ReturnType<typeof vi.fn>).mock.calls.length;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3000);
      });
      expect((c.listShortsCandidates as ReturnType<typeof vi.fn>).mock.calls.length).toBe(callsAfterReload);
    });

    it("disables the button and shows the running label while the job is in progress", async () => {
      // getJob returns running on first call, then succeeded.
      const RUNNING_JOB = {
        ...SUCCEEDED_JOB,
        status: "running" as const,
        finished_at: null,
      };
      const getJob = vi.fn().mockResolvedValueOnce(RUNNING_JOB).mockResolvedValue(SUCCEEDED_JOB);
      const c = makeClient({ getJob });

      const { getByRole, getByText } = render(
        <ShortsView
          client={c}
          asset={asset}
          seek={null}
          currentFrame={0}
          onSeek={vi.fn()}
          onFrame={vi.fn()}
        />,
      );

      await waitFor(() => expect(getByText("0.85")).toBeTruthy());
      fireEvent.click(getByText("Shorts extrahieren"));

      // After extractShorts resolves, job is polling as "running" → button disabled.
      await waitFor(() => {
        const btn = getByRole("button", { name: /Extrahiere/ });
        expect((btn as HTMLButtonElement).disabled).toBe(true);
      });
    });

    it("shows an error message and re-enables the button when the job fails, and a second click triggers a fresh extract", async () => {
      const FAILED_JOB = {
        ...SUCCEEDED_JOB,
        status: "failed" as const,
        error_json: JSON.stringify({ error: "GPU OOM" }),
        finished_at: "2024-01-01T00:00:02Z",
      };
      const c = makeClient({ getJob: vi.fn().mockResolvedValue(FAILED_JOB) });

      const { getByText, getByRole } = render(
        <ShortsView
          client={c}
          asset={asset}
          seek={null}
          currentFrame={0}
          onSeek={vi.fn()}
          onFrame={vi.fn()}
        />,
      );

      await waitFor(() => expect(getByText("0.85")).toBeTruthy());
      fireEvent.click(getByText("Shorts extrahieren"));

      // Wait for failure message to appear.
      await waitFor(() => expect(getByText("Job fehlgeschlagen")).toBeTruthy());

      // Button should now be re-enabled with the default label.
      const btn = getByRole("button", { name: "Shorts extrahieren" });
      expect((btn as HTMLButtonElement).disabled).toBe(false);

      // Clicking again triggers a fresh extractShorts call.
      fireEvent.click(btn);
      await waitFor(() =>
        expect((c.extractShorts as ReturnType<typeof vi.fn>).mock.calls.length).toBe(2),
      );
    });
  });
});
