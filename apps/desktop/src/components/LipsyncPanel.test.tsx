import { act, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type Asset, type LauraClient } from "../api";
import { renderWithQuery } from "../test-utils";
import { LipsyncPanel } from "./LipsyncPanel";

function asset(id: string, type: string, name: string): Asset {
  return {
    id,
    project_id: "p",
    type,
    display_name: name,
    source_path: "",
    sha256: null,
    duration_frames: type === "video" ? 120 : null,
    rate_num: 30,
    rate_den: 1,
    audio_sample_rate: 48000,
    start_timecode: null,
    width: type === "video" ? 1920 : null,
    height: type === "video" ? 1080 : null,
    codec_video: type === "video" ? "h264" : null,
    codec_audio: type === "audio" ? "pcm_s16le" : null,
    is_vfr: false,
    synthetic: false,
    ai_effect: null,
    created_at: "",
    files: [],
  };
}

function client(overrides: Partial<LauraClient> = {}): LauraClient {
  return {
    createConsent: vi.fn().mockResolvedValue({
      id: "consent-1",
      project_id: "p",
      subject_label: "Person A",
      confirmed_at: "",
      confirmed_by: null,
      source_asset_id: null,
      note: null,
      revoked_at: null,
    }),
    lipsync: vi.fn().mockResolvedValue({ job_id: "lip-job-1" }),
    getJob: vi.fn().mockResolvedValue({ id: "lip-job-1", status: "succeeded" }),
    ...overrides,
  } as unknown as LauraClient;
}

describe("LipsyncPanel", () => {
  it("keeps lipsync disabled until consent and license are confirmed", async () => {
    const c = client();
    const { getByLabelText, getByRole } = renderWithQuery(
      <LipsyncPanel
        client={c}
        projectId="p"
        timelineId="tl-1"
        assets={[asset("audio-1", "audio", "voice.wav")]}
        onChange={vi.fn()}
      />,
    );

    const submit = getByRole("button", { name: "Lipsync (stub)" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(getByLabelText("Subjekt-Label für Lipsync-Consent"), {
      target: { value: "Person A" },
    });
    fireEvent.click(getByRole("button", { name: "Consent bestätigen" }));
    await waitFor(() => expect(c.createConsent).toHaveBeenCalledWith("p", { subjectLabel: "Person A" }));
    expect((submit as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(getByLabelText("Lizenz und Nutzung bestätigt"));
    fireEvent.change(getByLabelText("Lipsync seq out"), { target: { value: "30" } });
    expect((submit as HTMLButtonElement).disabled).toBe(false);
  });

  it("submits lipsync with audio asset, consent id, frame range, and backend", async () => {
    const lipsync = vi.fn().mockResolvedValue({ job_id: "lip-job-1" });
    const onChange = vi.fn();
    const c = client({ lipsync });
    const { getByLabelText, getByRole, findByText } = renderWithQuery(
      <LipsyncPanel
        client={c}
        projectId="p"
        timelineId="tl-1"
        assets={[asset("audio-1", "audio", "voice.wav"), asset("video-1", "video", "clip.mp4")]}
        onChange={onChange}
      />,
    );

    expect(getByLabelText("Lipsync-Audio auswählen").textContent).toContain("voice.wav");
    expect(getByLabelText("Lipsync-Audio auswählen").textContent).not.toContain("clip.mp4");

    fireEvent.change(getByLabelText("Subjekt-Label für Lipsync-Consent"), {
      target: { value: "Person A" },
    });
    fireEvent.click(getByRole("button", { name: "Consent bestätigen" }));
    await findByText(/Consent für Person A/);

    fireEvent.click(getByLabelText("Lizenz und Nutzung bestätigt"));
    fireEvent.change(getByLabelText("Lipsync seq in"), { target: { value: "5" } });
    fireEvent.change(getByLabelText("Lipsync seq out"), { target: { value: "45" } });
    fireEvent.change(getByLabelText("Lipsync-Backend auswählen"), { target: { value: "vibevideo" } });
    fireEvent.click(getByRole("button", { name: "Lipsync (VibeVideo)" }));

    await waitFor(() =>
      expect(lipsync).toHaveBeenCalledWith("tl-1", {
        seqIn: 5,
        seqOut: 45,
        audioAssetId: "audio-1",
        consentId: "consent-1",
        licenseAccepted: true,
        backend: "vibevideo",
        qualityThreshold: 0.6,
      }));
    // onChange fires on job success (via useJobStatus polling), not at submit time.
    await waitFor(() => expect(onChange).toHaveBeenCalledOnce());
  });

  it("polls the lipsync job to a terminal status and fires onChange once on success", async () => {
    // Use real timers: the getJob mock resolves immediately on each poll.
    // First call returns running, all subsequent calls return succeeded.
    // We rely on the 1500ms setInterval in useJobStatus; to avoid waiting 1.5s
    // in the test we instead use vitest fake timers but wrap all async work in act().
    vi.useFakeTimers({ shouldAdvanceTime: false });
    const getJob = vi
      .fn()
      .mockResolvedValueOnce({ id: "lip-job-1", status: "running" })
      .mockResolvedValue({ id: "lip-job-1", status: "succeeded" });
    const onChange = vi.fn();
    const c = client({ getJob });
    const { getByLabelText, getByRole } = renderWithQuery(
      <LipsyncPanel
        client={c}
        projectId="p"
        timelineId="tl-1"
        assets={[asset("audio-1", "audio", "voice.wav")]}
        onChange={onChange}
      />,
    );
    fireEvent.change(getByLabelText("Subjekt-Label für Lipsync-Consent"), { target: { value: "Person A" } });

    // confirmConsent: let all microtasks (the Promise from createConsent) resolve.
    await act(async () => {
      fireEvent.click(getByRole("button", { name: "Consent bestätigen" }));
    });

    fireEvent.click(getByLabelText("Lizenz und Nutzung bestätigt"));
    fireEvent.change(getByLabelText("Lipsync seq out"), { target: { value: "30" } });

    // submit: let the lipsync() promise resolve and setJobId fire.
    await act(async () => {
      fireEvent.click(getByRole("button", { name: "Lipsync (stub)" }));
    });

    expect(c.lipsync).toHaveBeenCalled();

    // First poll fires immediately (poll() called right away in useJobStatus).
    // Flush it: advance 0ms to let the synchronous setInterval setup run,
    // then let the promise from getJob(..) (running) resolve.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    // After the first immediate poll resolves with "running", the chip shows "Läuft…".
    expect(document.body.textContent).toContain("Läuft…");

    // Advance past the 1500ms interval to trigger the second poll (succeeded).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });

    expect(document.body.textContent).toContain("Fertig ✓");
    expect(onChange).toHaveBeenCalledOnce();
    vi.useRealTimers();
  });
});
