import { fireEvent, render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type LauraClient, type Scene, type Sequence } from "../api";
import { AssembleView } from "./AssembleView";

const scenes: Scene[] = [
  { id: "s1", project_id: "p", source_timeline_id: "rc", name: "Szene 1", order_index: 0,
    seq_in_frame: 0, seq_out_frame_exclusive: 30 },
  { id: "s2", project_id: "p", source_timeline_id: "rc", name: "Szene 2", order_index: 1,
    seq_in_frame: 30, seq_out_frame_exclusive: 60 }];
const seq: Sequence = { timeline_id: "seq", project_id: "p",
  items: [{ id: "i1", scene_id: "s1", scene_name: "Szene 1", order_index: 0 }] };

function client(over: Partial<LauraClient>): LauraClient {
  return { listScenes: vi.fn().mockResolvedValue(scenes),
    getProjectSequence: vi.fn().mockResolvedValue(seq),
    setSequenceScenes: vi.fn().mockResolvedValue(seq), ...over } as unknown as LauraClient;
}

describe("AssembleView", () => {
  it("adds a bin scene to the sequence (PUT with appended id)", async () => {
    const c = client({});
    const { getByTitle } = render(
      <AssembleView client={c} projectId="p" roughCutId="rc" onSeekScene={vi.fn()} />);
    await waitFor(() => expect(c.getProjectSequence).toHaveBeenCalledWith("p"));
    await waitFor(() => expect(c.listScenes).toHaveBeenCalledWith("rc"));
    fireEvent.click(getByTitle("Szene 2 zur Sequenz hinzufügen"));
    await waitFor(() => expect(c.setSequenceScenes).toHaveBeenCalledWith("seq", ["s1", "s2"]));
  });
});
