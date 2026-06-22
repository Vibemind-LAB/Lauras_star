import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ContinuousTranscript } from "./ContinuousTranscript";
import { type CutWord } from "../shared/transcriptProjection";
import { type Scene } from "../api";

const scenes: Scene[] = [
  { id: "s1", project_id: "p", source_timeline_id: "t", name: "Szene 1",
    order_index: 0, seq_in_frame: 0, seq_out_frame_exclusive: 100 },
  { id: "s2", project_id: "p", source_timeline_id: "t", name: "Szene 2",
    order_index: 1, seq_in_frame: 100, seq_out_frame_exclusive: 200 },
];
const words: CutWord[] = [
  { id: "w1", text: "hallo", srcFrame: 0, srcEndFrame: 10, seqStart: 0, seqEnd: 10 },
  { id: "w2", text: "welt", srcFrame: 20, srcEndFrame: 30, seqStart: 20, seqEnd: 30 },
  { id: "w3", text: "zwei", srcFrame: 100, srcEndFrame: 110, seqStart: 100, seqEnd: 110 },
];

describe("ContinuousTranscript", () => {
  it("renders every scene label and every word", () => {
    render(
      <ContinuousTranscript words={words} scenes={scenes} selection={null}
        onSelectionChange={vi.fn()} onDeleteSelection={vi.fn()}
        onCutAt={vi.fn()} onSeek={vi.fn()} />,
    );
    expect(screen.getByText("Szene 1")).toBeTruthy();
    expect(screen.getByText("Szene 2")).toBeTruthy();
    expect(screen.getByText("hallo")).toBeTruthy();
    expect(screen.getByText("zwei")).toBeTruthy();
  });

  it("clicking a word seeks to its seqStart", () => {
    const onSeek = vi.fn();
    render(
      <ContinuousTranscript words={words} scenes={scenes} selection={null}
        onSelectionChange={vi.fn()} onDeleteSelection={vi.fn()}
        onCutAt={vi.fn()} onSeek={onSeek} />,
    );
    fireEvent.click(screen.getByText("welt"));
    expect(onSeek).toHaveBeenCalledWith(20);
  });

  it("drag from one word to another reports an ordered selection", () => {
    const onSelectionChange = vi.fn();
    render(
      <ContinuousTranscript words={words} scenes={scenes} selection={null}
        onSelectionChange={onSelectionChange} onDeleteSelection={vi.fn()}
        onCutAt={vi.fn()} onSeek={vi.fn()} />,
    );
    fireEvent.mouseDown(screen.getByText("welt"));   // start at w2 (seqStart 20)
    fireEvent.mouseEnter(screen.getByText("hallo")); // drag back over w1 (seqStart 0)
    fireEvent.mouseUp(screen.getByText("hallo"));
    // ordered by seqStart: start=w1, end=w2
    expect(onSelectionChange).toHaveBeenLastCalledWith({ startWordId: "w1", endWordId: "w2" });
  });

  it("clicking the caret between two words cuts at the right word's seqStart", () => {
    const onCutAt = vi.fn();
    render(
      <ContinuousTranscript words={words} scenes={scenes} selection={null}
        onSelectionChange={vi.fn()} onDeleteSelection={vi.fn()}
        onCutAt={onCutAt} onSeek={vi.fn()} />,
    );
    // caret before "welt" cuts at 20
    fireEvent.click(screen.getByTestId("caret-w2"));
    expect(onCutAt).toHaveBeenCalledWith(20);
  });

  it("delete button on an active selection calls onDeleteSelection", () => {
    const onDeleteSelection = vi.fn();
    render(
      <ContinuousTranscript words={words} scenes={scenes}
        selection={{ startWordId: "w1", endWordId: "w2" }}
        onSelectionChange={vi.fn()} onDeleteSelection={onDeleteSelection}
        onCutAt={vi.fn()} onSeek={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /löschen/i }));
    expect(onDeleteSelection).toHaveBeenCalledWith("w1", "w2");
  });
});
