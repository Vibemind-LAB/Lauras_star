import { fireEvent, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { type LauraClient, type Scene, type Sequence } from "../api";
import { AssembleView } from "./AssembleView";

vi.mock("./SequencePlayer", () => ({
  SequencePlayer: () => <div data-testid="sequence-player" />,
}));

const scenes: Scene[] = [
  { id: "s1", project_id: "p", source_timeline_id: "rc", name: "Szene 1", order_index: 0,
    seq_in_frame: 0, seq_out_frame_exclusive: 30 },
  { id: "s2", project_id: "p", source_timeline_id: "rc", name: "Szene 2", order_index: 1,
    seq_in_frame: 30, seq_out_frame_exclusive: 60 }];
const seq: Sequence = { timeline_id: "seq", project_id: "p",
  items: [{ id: "i1", scene_id: "s1", scene_name: "Szene 1", order_index: 0,
    transition_after_kind: "hard", transition_after_frames: 0 }] };
const seqTwo: Sequence = { timeline_id: "seq", project_id: "p",
  items: [
    { id: "i1", scene_id: "s1", scene_name: "Szene 1", order_index: 0,
      transition_after_kind: "hard", transition_after_frames: 0 },
    { id: "i2", scene_id: "s2", scene_name: "Szene 2", order_index: 1,
      transition_after_kind: "hard", transition_after_frames: 0 },
  ] };
const transcript = [{
  segment_id: "seg-1",
  asset_id: "asset-1",
  speaker_label: null,
  source_start_frame: 0,
  source_end_frame: 30,
  seq_in_frame: 0,
  seq_out_frame_exclusive: 30,
  text: "Original line",
  alignment_status: "aligned",
  alignment_job_id: null,
  alignment_language: null,
  alignment_error: null,
  alignment_updated_at: null,
  words: [{ id: "w1", idx: 0, segment_id: "seg-1", asset_id: "asset-1",
    source_start_frame: 0, source_end_frame: 30, seq_in_frame: 0,
    seq_out_frame_exclusive: 30, text: "Original", confidence: null,
    is_punctuation: false }],
}];

function client(over: Partial<LauraClient>): LauraClient {
  return { listProjectScenes: vi.fn().mockResolvedValue(scenes),
    listAssets: vi.fn().mockResolvedValue([]),
    getAsset: vi.fn().mockResolvedValue({
      id: "a1", project_id: "p", type: "video", display_name: "Video", source_path: "",
      sha256: null, duration_frames: 100, rate_num: 25, rate_den: 1, audio_sample_rate: 48000,
      start_timecode: null, width: 1920, height: 1080, codec_video: null, codec_audio: null,
      is_vfr: false, created_at: "", files: [],
    }),
    assetFrameUrl: vi.fn().mockResolvedValue("blob:thumb"),
    getSequenceFlattened: vi.fn().mockResolvedValue([]),
    listTimelineAudioClips: vi.fn().mockResolvedValue([]),
    createTimelineAudioClip: vi.fn().mockResolvedValue({}),
    deleteTimelineAudioClip: vi.fn().mockResolvedValue(undefined),
    getSequenceTranscript: vi.fn().mockResolvedValue([]),
    getJob: vi.fn().mockResolvedValue({ id: "job-1", status: "succeeded" }),
    createVoiceover: vi.fn().mockResolvedValue({ job_id: "voice-job-1" }),
    listAiRuntimes: vi.fn().mockResolvedValue([
      {
        id: "rt-1",
        kind: "stub",
        effect: "lipsync",
        display_name: "Stub Lipsync",
        status: { state: "ready", ready: true },
        capabilities: { effects: ["lipsync"] },
        base_url: null,
        container_image: null,
        container_name: null,
        port: null,
        workspace_mount: null,
        model_mount: null,
        requires_gpu: false,
        enabled: true,
        license_status: "not_required",
        last_health_at: null,
        created_at: "",
        updated_at: "",
      },
    ]),
    refreshAiRuntime: vi.fn().mockResolvedValue({}),
    startAiRuntime: vi.fn().mockResolvedValue({}),
    stopAiRuntime: vi.fn().mockResolvedValue({}),
    listAiRuntimeEvents: vi.fn().mockResolvedValue([]),
    updateSequenceTransition: vi.fn().mockResolvedValue(seq),
    updateTranscriptSegment: vi.fn().mockResolvedValue({}),
    realignTranscript: vi.fn().mockResolvedValue({ job_id: "job-1" }),
    getProjectSequence: vi.fn().mockResolvedValue(seq),
    setSequenceScenes: vi.fn().mockResolvedValue(seq), ...over } as unknown as LauraClient;
}

describe("AssembleView", () => {
  beforeEach(() => {
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
  });

  it("adds a bin scene to the sequence (PUT with appended id)", async () => {
    const c = client({});
    const { getByTitle } = render(
      <AssembleView client={c} projectId="p" roughCutId="rc" onSeekScene={vi.fn()} />);
    await waitFor(() => expect(c.getProjectSequence).toHaveBeenCalledWith("p"));
    await waitFor(() => expect(c.listProjectScenes).toHaveBeenCalledWith("p"));
    fireEvent.click(getByTitle(/Szene 2.*Reihenfolge anhängen/));
    await waitFor(() => expect(c.setSequenceScenes).toHaveBeenCalledWith("seq", ["s1", "s2"]));
  });

  it("appends every scene of a group in one click (+ alle)", async () => {
    const c = client({
      getProjectSequence: vi.fn().mockResolvedValue({ timeline_id: "seq", project_id: "p", items: [] }),
    });
    const { getByTitle } = render(
      <AssembleView client={c} projectId="p" roughCutId="rc" onSeekScene={vi.fn()} />);
    await waitFor(() => expect(c.listProjectScenes).toHaveBeenCalledWith("p"));
    fireEvent.click(getByTitle(/Alle 2 Szenen/));
    await waitFor(() => expect(c.setSequenceScenes).toHaveBeenCalledWith("seq", ["s1", "s2"]));
  });

  it("updates the transition chip between storyboard scenes", async () => {
    const updateSequenceTransition = vi.fn().mockResolvedValue(seq);
    const c = client({ getProjectSequence: vi.fn().mockResolvedValue(seqTwo), updateSequenceTransition });
    const { findByLabelText } = render(
      <AssembleView client={c} projectId="p" roughCutId="rc" onSeekScene={vi.fn()} />);

    fireEvent.change(await findByLabelText("Transition nach Szene 1"), {
      target: { value: "dip_black" },
    });

    await waitFor(() =>
      expect(updateSequenceTransition).toHaveBeenCalledWith("seq", "i1", {
        kind: "dip_black",
        durationFrames: 12,
      }));
  });

  it("renders the assemble workspace as scene bin, sequence work area, and transcript/tools rail", async () => {
    const c = client({});
    const { getByLabelText } = render(
      <AssembleView client={c} projectId="p" roughCutId="rc" onSeekScene={vi.fn()} />);
    await waitFor(() => expect(c.getProjectSequence).toHaveBeenCalledWith("p"));

    expect(getByLabelText("Szenen-Bin")).toBeTruthy();
    expect(getByLabelText("Sequenz-Arbeitsfläche")).toBeTruthy();
    expect(getByLabelText("Transkript und Werkzeuge")).toBeTruthy();
  });

  it("edits a sequence transcript block and starts realignment for its source asset", async () => {
    const c = client({ getSequenceTranscript: vi.fn().mockResolvedValue(transcript) });
    const { findByDisplayValue, getByRole } = render(
      <AssembleView client={c} projectId="p" roughCutId="rc" onSeekScene={vi.fn()} />);

    const input = await findByDisplayValue("Original line");
    fireEvent.change(input, { target: { value: "Better line" } });
    fireEvent.click(getByRole("button", { name: "Speichern + neu ausrichten" }));

    await waitFor(() =>
      expect(c.updateTranscriptSegment).toHaveBeenCalledWith("seg-1", { text: "Better line" }));
    expect(c.realignTranscript).toHaveBeenCalledWith("asset-1", {
      segmentIds: ["seg-1"],
    });
  });

  it("shows persistent transcript alignment state from the backend", async () => {
    const c = client({
      getSequenceTranscript: vi.fn().mockResolvedValue([{
        ...transcript[0],
        alignment_status: "failed",
        alignment_language: "de",
        alignment_error: "no audio_mono16k extracted; cannot realign transcript",
      }]),
    });
    const { findByText } = render(
      <AssembleView client={c} projectId="p" roughCutId="rc" onSeekScene={vi.fn()} />);

    expect(await findByText("Alignment fehlgeschlagen")).toBeTruthy();
    expect(await findByText("Sprache: de")).toBeTruthy();
    expect(await findByText("no audio_mono16k extracted; cannot realign transcript")).toBeTruthy();
  });

  it("shows a friendly empty transcript state instead of raw 404 JSON", async () => {
    const c = client({
      getSequenceTranscript: vi.fn().mockRejectedValue(new Error('404: {"detail":"Not Found"}')),
    });
    const { findByText, queryByText } = render(
      <AssembleView client={c} projectId="p" roughCutId="rc" onSeekScene={vi.fn()} />);

    expect(await findByText("Sequenz-Transkript ist noch nicht verfügbar.")).toBeTruthy();
    expect(queryByText(/"detail":"Not Found"/)).toBeNull();
  });

  it("polls and displays the realignment job status after saving transcript text", async () => {
    const c = client({
      getSequenceTranscript: vi.fn().mockResolvedValue(transcript),
      getJob: vi.fn().mockResolvedValue({ id: "job-1", status: "succeeded" }),
    });
    const { findByDisplayValue, findByText, getByRole } = render(
      <AssembleView client={c} projectId="p" roughCutId="rc" onSeekScene={vi.fn()} />);

    fireEvent.change(await findByDisplayValue("Original line"), { target: { value: "Better line" } });
    fireEvent.click(getByRole("button", { name: "Speichern + neu ausrichten" }));

    expect(await findByText("Re-Alignment abgeschlossen.")).toBeTruthy();
    expect(c.getJob).toHaveBeenCalledWith("job-1");
  });

  it("generates a voiceover from a transcript block and refreshes the audio lane", async () => {
    const c = client({
      getSequenceTranscript: vi.fn().mockResolvedValue(transcript),
      createVoiceover: vi.fn().mockResolvedValue({ job_id: "voice-job-1" }),
      getJob: vi.fn().mockResolvedValue({ id: "voice-job-1", status: "succeeded" }),
    });
    const { findByLabelText, findByText, getByRole } = render(
      <AssembleView client={c} projectId="p" roughCutId="rc" onSeekScene={vi.fn()} />);

    await findByLabelText("Sequenz-Transcript-Text");
    fireEvent.click(getByRole("button", { name: "Stimme erzeugen" }));

    await waitFor(() =>
      expect(c.createVoiceover).toHaveBeenCalledWith("seq", {
        segmentId: "seg-1",
        text: "Original line",
        seqIn: 0,
        seqOut: 30,
      }));
    expect(await findByText("Voiceover erzeugt und auf A2 platziert.")).toBeTruthy();
    expect(c.listTimelineAudioClips).toHaveBeenCalledTimes(2);
  });

  it("lets editors hide the caption preview overlay without disabling transcript editing", async () => {
    const c = client({ getSequenceTranscript: vi.fn().mockResolvedValue(transcript) });
    const { findByLabelText, getByRole, queryByLabelText } = render(
      <AssembleView client={c} projectId="p" roughCutId="rc" onSeekScene={vi.fn()} />);

    expect(await findByLabelText("Caption-Preview")).toBeTruthy();
    fireEvent.click(getByRole("button", { name: "Caption-Preview aus" }));

    expect(queryByLabelText("Caption-Preview")).toBeNull();
    expect(await findByLabelText("Sequenz-Transcript-Text")).toBeTruthy();
  });

  it("surfaces runtime status in the tools rail and sequence duration in the work area", async () => {
    const c = client({
      getSequenceFlattened: vi.fn().mockResolvedValue([
        { id: "c1", asset_id: "a1", src_in_frame: 0, src_out_frame_exclusive: 30,
          seq_in_frame: 0, seq_out_frame_exclusive: 30, lane: 0, speaker_id: null,
          origin_word_start_id: null, origin_word_end_id: null, speed_num: 1,
          speed_den: 1, audio_offset_samples: 0 },
        { id: "c2", asset_id: "a1", src_in_frame: 30, src_out_frame_exclusive: 75,
          seq_in_frame: 30, seq_out_frame_exclusive: 75, lane: 0, speaker_id: null,
          origin_word_start_id: null, origin_word_end_id: null, speed_num: 1,
          speed_den: 1, audio_offset_samples: 0 },
      ]),
    });
    const { findByText, getByRole } = render(
      <AssembleView client={c} projectId="p" roughCutId="rc" onSeekScene={vi.fn()} />);

    expect(await findByText("Gesamtdauer 75 f")).toBeTruthy();
    fireEvent.click(getByRole("button", { name: "Tools" }));

    expect(await findByText("AI Runtimes")).toBeTruthy();
    expect(await findByText("Stub Lipsync")).toBeTruthy();
  });

  it("re-fetches runtime status in the tools rail when the assemble reload key changes", async () => {
    const c = client({});
    const { findByText, getByRole, getByTitle } = render(
      <AssembleView client={c} projectId="p" roughCutId="rc" onSeekScene={vi.fn()} />,
    );

    fireEvent.click(getByRole("button", { name: "Tools" }));
    expect(await findByText("Stub Lipsync")).toBeTruthy();
    expect(c.listAiRuntimes).toHaveBeenCalledTimes(1);

    fireEvent.click(getByTitle(/Szene 2.*Reihenfolge anhängen/));

    await waitFor(() => expect(c.setSequenceScenes).toHaveBeenCalledWith("seq", ["s1", "s2"]));
    await waitFor(() => expect(c.listAiRuntimes).toHaveBeenCalledTimes(2));
  });
});
