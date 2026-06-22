import { fireEvent, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { type LauraClient, type Scene, type Sequence } from "../api";
import { AssembleView } from "./AssembleView";

// Capture props passed to SequencePlayer so tests can assert on them.
export const seqPlayerProps: { audioClips?: unknown; rateNum?: number } = {};
vi.mock("./SequencePlayer", () => ({
  SequencePlayer: (props: { audioClips?: unknown; rateNum?: number }) => {
    seqPlayerProps.audioClips = props.audioClips;
    seqPlayerProps.rateNum = props.rateNum;
    return <div data-testid="sequence-player" />;
  },
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
    listVoiceoverVoices: vi.fn().mockResolvedValue([]),
    createVoiceover: vi.fn().mockResolvedValue({ job_id: "voice-job-1" }),
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

  it("keeps only sequence tools in Zusammenfügen — no VO/Lipsync/Reenact", async () => {
    const c = client({});
    const { queryByText, findByRole } = render(
      <AssembleView client={c} projectId="p" roughCutId={null} onSeekScene={vi.fn()} />,
    );
    fireEvent.click(await findByRole("button", { name: "Tools" }));
    expect(queryByText("Reenact (Identitäts-Ebene)")).toBeNull();
    expect(queryByText("Lipsync (Deepfake)")).toBeNull();
    // Sequenz-Tools bleiben:
    expect(queryByText("Demo-Draft")).not.toBeNull();
  });

  it("transcript rail in Zusammenfügen has no voiceover button", async () => {
    const c = client({});
    const { queryByRole, findByText } = render(
      <AssembleView client={c} projectId="p" roughCutId={null} onSeekScene={vi.fn()} />,
    );
    await findByText(/Szenen-Bin/);
    expect(queryByRole("button", { name: "Stimme erzeugen" })).toBeNull();
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

  it("passes loaded audio clips and frame rate to the SequencePlayer", async () => {
    const audioClip = {
      id: "ac1", timeline_id: "seq", asset_id: "a1", lane: 2,
      seq_in_frame: 0, seq_out_frame_exclusive: 30,
      src_in_frame: 0, src_out_frame_exclusive: 30,
      gain_db: 0, fade_in_frames: 0, fade_out_frames: 0,
    };
    const c = client({
      listTimelineAudioClips: vi.fn().mockResolvedValue([audioClip]),
    });
    render(<AssembleView client={c} projectId="p" roughCutId="rc" onSeekScene={vi.fn()} rateNum={30} rateDen={1} />);
    await waitFor(() => expect(Array.isArray(seqPlayerProps.audioClips)).toBe(true));
    expect(seqPlayerProps.rateNum).toBe(30);
  });

  it("shows sequence duration in work area and sequence tools (not KI-Status) in tools tab", async () => {
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
    const { findByText, getByRole, queryByText } = render(
      <AssembleView client={c} projectId="p" roughCutId="rc" onSeekScene={vi.fn()} />);

    expect(await findByText("Gesamtdauer 75 f")).toBeTruthy();
    fireEvent.click(getByRole("button", { name: "Tools" }));

    // KI-Status section was removed in E4
    expect(queryByText("KI-Status")).toBeNull();
    // Sequence tools remain
    expect(queryByText("Demo-Draft")).not.toBeNull();
  });
});
