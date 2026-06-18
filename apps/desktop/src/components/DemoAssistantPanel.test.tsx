import { fireEvent, render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type Asset, type DemoDraft, type LauraClient, type Sequence } from "../api";
import { DemoAssistantPanel } from "./DemoAssistantPanel";

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
    codec_audio: "aac",
    is_vfr: false,
    synthetic: false,
    ai_effect: null,
    created_at: "",
    files: [],
  };
}

const draft: DemoDraft = {
  id: "draft-1",
  project_id: "p",
  asset_id: "video-1",
  status: "ready",
  items: [
    {
      src_in_frame: 0,
      src_out_frame_exclusive: 45,
      label: "Intro",
      voiceover_text: "Original demo line",
      thumb_frame: 0,
      confidence: 0.9,
      enabled: true,
    },
  ],
  result: {},
  created_at: "",
  updated_at: "",
  applied_at: null,
};

const sequence: Sequence = { timeline_id: "seq", project_id: "p", items: [] };

function client(overrides: Partial<LauraClient> = {}): LauraClient {
  return {
    createDemoDraft: vi.fn().mockResolvedValue({ draft_id: "draft-1", job_id: "job-1" }),
    getJob: vi.fn().mockResolvedValue({ id: "job-1", status: "succeeded" }),
    getDemoDraft: vi.fn().mockResolvedValue(draft),
    updateDemoDraft: vi.fn().mockResolvedValue(draft),
    applyDemoDraft: vi.fn().mockResolvedValue({ draft, sequence }),
    ...overrides,
  } as unknown as LauraClient;
}

describe("DemoAssistantPanel", () => {
  it("lists only video assets and creates a draft", async () => {
    const c = client();
    const { findByDisplayValue, getByRole, queryByText } = render(
      <DemoAssistantPanel
        client={c}
        assets={[asset("video-1", "video", "screen.mp4"), asset("audio-1", "audio", "voice.wav")]}
        onApplied={vi.fn()}
      />,
    );

    expect(getByRole("combobox").textContent).toContain("screen.mp4");
    expect(queryByText("voice.wav")).toBeNull();

    fireEvent.click(getByRole("button", { name: "Demo-Draft erzeugen" }));

    await findByDisplayValue("Intro");
    expect(c.createDemoDraft).toHaveBeenCalledWith("video-1");
    expect(c.getJob).toHaveBeenCalledWith("job-1");
    expect(c.getDemoDraft).toHaveBeenCalledWith("draft-1");
  });

  it("saves edited draft items before applying the draft to the sequence", async () => {
    const updateDemoDraft = vi.fn().mockImplementation(async (_draftId: string, items: unknown) => ({
      ...draft,
      items,
    }));
    const applyDemoDraft = vi.fn().mockResolvedValue({ draft, sequence });
    const onApplied = vi.fn();
    const c = client({ updateDemoDraft, applyDemoDraft });

    const { findByDisplayValue, getByLabelText, getByRole } = render(
      <DemoAssistantPanel
        client={c}
        assets={[asset("video-1", "video", "screen.mp4")]}
        onApplied={onApplied}
      />,
    );

    fireEvent.click(getByRole("button", { name: "Demo-Draft erzeugen" }));
    const label = await findByDisplayValue("Intro");
    fireEvent.change(label, { target: { value: "Hook" } });
    fireEvent.change(getByLabelText("Demo-Voiceovertext 1"), {
      target: { value: "Shorter hook line" },
    });
    fireEvent.click(getByRole("button", { name: "In Sequenz übernehmen" }));

    await waitFor(() => expect(updateDemoDraft).toHaveBeenCalled());
    expect(updateDemoDraft).toHaveBeenCalledWith(
      "draft-1",
      expect.arrayContaining([
        expect.objectContaining({
          label: "Hook",
          voiceover_text: "Shorter hook line",
          enabled: true,
        }),
      ]),
    );
    expect(applyDemoDraft).toHaveBeenCalledWith("draft-1");
    expect(onApplied).toHaveBeenCalledOnce();
  });
});
