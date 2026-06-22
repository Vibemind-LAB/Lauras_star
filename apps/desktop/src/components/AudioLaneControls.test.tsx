import { fireEvent, render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type Asset, type LauraClient, type TimelineAudioClip } from "../api";
import { AudioLaneControls } from "./AudioLaneControls";

const assets = [
  { id: "v1", display_name: "video.mp4", type: "video", codec_audio: null },
  { id: "a1", display_name: "voice.wav", type: "audio", codec_audio: "pcm_s16le" },
] as unknown as Asset[];

const clip: TimelineAudioClip = {
  id: "ac1",
  timeline_id: "tl1",
  asset_id: "a1",
  seq_in_frame: 10,
  seq_out_frame_exclusive: 40,
  asset_in_frame: 0,
  gain_percent: 90,
  fade_in_frames: 2,
  fade_out_frames: 3,
  mix_mode: "mix",
  ducking_percent: 100,
  label: "VO",
  created_at: "",
};

function client(overrides: Partial<LauraClient> = {}): LauraClient {
  return {
    listTimelineAudioClips: vi.fn().mockResolvedValue([clip]),
    createTimelineAudioClip: vi.fn().mockResolvedValue(clip),
    deleteTimelineAudioClip: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  } as unknown as LauraClient;
}

describe("AudioLaneControls", () => {
  it("lists existing clips and filters to audio-capable assets", async () => {
    const c = client();
    const { getAllByRole, getByText, queryByText } = render(
      <AudioLaneControls client={c} timelineId="tl1" assets={assets} onChange={vi.fn()} />,
    );

    await waitFor(() => expect(c.listTimelineAudioClips).toHaveBeenCalledWith("tl1"));
    expect(getByText("VO")).toBeTruthy();
    expect(getAllByRole("combobox")[0].textContent).toContain("voice.wav");
    expect(queryByText("video.mp4")).toBeNull();
  });

  it("creates an audio clip and refreshes", async () => {
    const c = client({ listTimelineAudioClips: vi.fn().mockResolvedValue([]) });
    const onChange = vi.fn();
    const { getByLabelText, getByText } = render(
      <AudioLaneControls client={c} timelineId="tl1" assets={assets} onChange={onChange} />,
    );

    fireEvent.change(getByLabelText("Audio seq in"), { target: { value: "5" } });
    fireEvent.change(getByLabelText("Audio seq out"), { target: { value: "35" } });
    fireEvent.change(getByLabelText("Audio gain"), { target: { value: "75" } });
    fireEvent.change(getByLabelText("Modus"), { target: { value: "replace_original" } });
    fireEvent.change(getByLabelText("Original ducking"), { target: { value: "25" } });
    fireEvent.click(getByText("Audio einsetzen"));

    await waitFor(() =>
      expect(c.createTimelineAudioClip).toHaveBeenCalledWith("tl1", {
        assetId: "a1",
        seqIn: 5,
        seqOut: 35,
        assetIn: 0,
        gainPercent: 75,
        fadeInFrames: 0,
        fadeOutFrames: 0,
        mixMode: "replace_original",
        duckingPercent: 25,
        label: null,
      }),
    );
    expect(onChange).toHaveBeenCalled();
  });

  it("removes an audio clip", async () => {
    const c = client();
    const onChange = vi.fn();
    const { getByText } = render(
      <AudioLaneControls client={c} timelineId="tl1" assets={assets} onChange={onChange} />,
    );

    await waitFor(() => expect(getByText("VO")).toBeTruthy());
    fireEvent.click(getByText("entfernen"));

    await waitFor(() => expect(c.deleteTimelineAudioClip).toHaveBeenCalledWith("tl1", "ac1"));
    expect(onChange).toHaveBeenCalled();
  });
});
