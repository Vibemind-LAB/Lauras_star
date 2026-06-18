import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type Segment } from "../api";
import { TranscriptBar } from "./TranscriptBar";

function seg(over: Partial<Segment> = {}): Segment {
  return {
    id: "seg-1",
    speaker_id: null,
    speaker_label: null,
    start_frame: 0,
    end_frame: 30,
    text: "Hallo Welt",
    confidence: null,
    words: [
      { id: "w1", idx: 0, start_frame: 0, end_frame: 15, text: "Hallo", is_punctuation: false },
      { id: "w2", idx: 1, start_frame: 15, end_frame: 30, text: "Welt", is_punctuation: false },
    ],
    alignment_status: "aligned",
    ...over,
  };
}

function renderBar(props: Partial<React.ComponentProps<typeof TranscriptBar>> = {}) {
  return render(
    <TranscriptBar
      client={null}
      assetId="a1"
      assetName="clip"
      segments={[seg()]}
      note={null}
      currentFrame={0}
      onSeek={vi.fn()}
      canAppend={false}
      onAppendSegment={vi.fn()}
      {...props}
    />,
  );
}

describe("TranscriptBar inline edit", () => {
  it("opens an editor prefilled with the segment text and saves the new text", async () => {
    const onEditSegment = vi.fn().mockResolvedValue(undefined);
    renderBar({ onEditSegment });

    fireEvent.click(screen.getByTitle("Transkript bearbeiten"));
    const box = screen.getByLabelText<HTMLTextAreaElement>("Segmenttext bearbeiten");
    expect(box.value).toBe("Hallo Welt");

    fireEvent.change(box, { target: { value: "Hallo Welt!" } });
    fireEvent.click(screen.getByTitle("Speichern"));

    await waitFor(() => expect(onEditSegment).toHaveBeenCalledWith("seg-1", "Hallo Welt!"));
    await waitFor(() => expect(screen.queryByLabelText("Segmenttext bearbeiten")).toBeNull());
  });

  it("saves on Enter (without Shift)", async () => {
    const onEditSegment = vi.fn().mockResolvedValue(undefined);
    renderBar({ onEditSegment });
    fireEvent.click(screen.getByTitle("Transkript bearbeiten"));
    const box = screen.getByLabelText("Segmenttext bearbeiten");
    fireEvent.change(box, { target: { value: "Korrigiert" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(onEditSegment).toHaveBeenCalledWith("seg-1", "Korrigiert"));
  });

  it("Escape cancels without calling onEditSegment", () => {
    const onEditSegment = vi.fn();
    renderBar({ onEditSegment });
    fireEvent.click(screen.getByTitle("Transkript bearbeiten"));
    const box = screen.getByLabelText("Segmenttext bearbeiten");
    fireEvent.keyDown(box, { key: "Escape" });
    expect(screen.queryByLabelText("Segmenttext bearbeiten")).toBeNull();
    expect(onEditSegment).not.toHaveBeenCalled();
  });

  it("shows no edit affordance when onEditSegment is absent (backward compatible)", () => {
    renderBar();
    expect(screen.queryByTitle("Transkript bearbeiten")).toBeNull();
    // words still render as individual seek chips
    expect(screen.getByRole("button", { name: "Hallo" })).toBeTruthy();
  });

  it("renders a stale segment as plain text rather than stale word chips", () => {
    renderBar({ segments: [seg({ alignment_status: "stale", text: "Hallo Welt korrigiert" })] });
    expect(screen.getByRole("button", { name: "Hallo Welt korrigiert" })).toBeTruthy();
    // the old per-word chips are gone
    expect(screen.queryByRole("button", { name: "Hallo" })).toBeNull();
  });
});
