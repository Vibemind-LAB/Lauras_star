import { fireEvent, render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type Asset, type LauraClient } from "../api";
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
    ...overrides,
  } as unknown as LauraClient;
}

describe("LipsyncPanel", () => {
  it("keeps lipsync disabled until consent and license are confirmed", async () => {
    const c = client();
    const { getByLabelText, getByRole } = render(
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
    const { getByLabelText, getByRole, findByText } = render(
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
    expect(onChange).toHaveBeenCalledOnce();
  });
});
