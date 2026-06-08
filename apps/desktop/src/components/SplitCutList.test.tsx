import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type LauraClient, type SplitCut } from "../api";
import { SplitCutList } from "./SplitCutList";

afterEach(cleanup);

const SPLITS: SplitCut[] = [
  { seq_cut: 50, video_frame: 50, audio_frame: 53, offset: 3, kind: "L" },
  { seq_cut: 90, video_frame: 90, audio_frame: 87, offset: -3, kind: "J" },
  // A hard cut must never appear in the list nor be sent.
  { seq_cut: 120, video_frame: 120, audio_frame: 120, offset: 0, kind: "hard" },
];

function client(over: Partial<LauraClient> = {}): LauraClient {
  return {
    acceptSplitCuts: vi.fn().mockResolvedValue({ accepted: [] }),
    ...over,
  } as unknown as LauraClient;
}

describe("SplitCutList", () => {
  it("lists only the recommended (non-hard) splits", () => {
    const { queryByTestId } = render(
      <SplitCutList client={client()} projectId="p" timelineId="tl" splitCuts={SPLITS} />,
    );
    expect(queryByTestId("split-cut-50")).toBeTruthy();
    expect(queryByTestId("split-cut-90")).toBeTruthy();
    expect(queryByTestId("split-cut-120")).toBeNull(); // hard cut is excluded
  });

  it("renders nothing when there is no recommended split", () => {
    const { container } = render(
      <SplitCutList
        client={client()}
        projectId="p"
        timelineId="tl"
        splitCuts={[{ seq_cut: 1, video_frame: 1, audio_frame: 1, offset: 0, kind: "hard" }]}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("posts the clicked split with the right {seqCut, offset} on Übernehmen", async () => {
    const acceptSplitCuts = vi
      .fn()
      .mockResolvedValue({ accepted: [{ seqCut: 50, offset: 3 }] });
    const { getAllByText } = render(
      <SplitCutList
        client={client({ acceptSplitCuts })}
        projectId="p"
        timelineId="tl"
        splitCuts={SPLITS}
      />,
    );
    // First per-row „Übernehmen" is the L-cut at seq_cut 50.
    fireEvent.click(getAllByText("Übernehmen")[0]);
    await waitFor(() =>
      expect(acceptSplitCuts).toHaveBeenCalledWith("p", "tl", [{ seqCut: 50, offset: 3 }]),
    );
  });

  it("shows the applied badge after the backend confirms the stored set", async () => {
    const acceptSplitCuts = vi
      .fn()
      .mockResolvedValue({ accepted: [{ seqCut: 50, offset: 3 }] });
    const { getAllByText, getByTestId } = render(
      <SplitCutList
        client={client({ acceptSplitCuts })}
        projectId="p"
        timelineId="tl"
        splitCuts={SPLITS}
      />,
    );
    fireEvent.click(getAllByText("Übernehmen")[0]);
    await waitFor(() => expect(getByTestId("split-cut-applied-50")).toBeTruthy());
    expect(getByTestId("split-cut-applied-50").textContent).toContain("L-Cut aktiv");
  });

  it("Alle übernehmen posts every recommended split (hard excluded)", async () => {
    const acceptSplitCuts = vi.fn().mockResolvedValue({
      accepted: [
        { seqCut: 50, offset: 3 },
        { seqCut: 90, offset: -3 },
      ],
    });
    const { getByText } = render(
      <SplitCutList
        client={client({ acceptSplitCuts })}
        projectId="p"
        timelineId="tl"
        splitCuts={SPLITS}
      />,
    );
    fireEvent.click(getByText("Alle übernehmen"));
    await waitFor(() =>
      expect(acceptSplitCuts).toHaveBeenCalledWith("p", "tl", [
        { seqCut: 50, offset: 3 },
        { seqCut: 90, offset: -3 },
      ]),
    );
  });

  it("re-posts without an entry on Zurücknehmen (take back)", async () => {
    const acceptSplitCuts = vi
      .fn()
      .mockResolvedValueOnce({ accepted: [{ seqCut: 50, offset: 3 }] })
      .mockResolvedValueOnce({ accepted: [] });
    const { getAllByText, getByText } = render(
      <SplitCutList
        client={client({ acceptSplitCuts })}
        projectId="p"
        timelineId="tl"
        splitCuts={SPLITS}
      />,
    );
    fireEvent.click(getAllByText("Übernehmen")[0]);
    await waitFor(() => expect(getByText("Zurücknehmen")).toBeTruthy());
    fireEvent.click(getByText("Zurücknehmen"));
    // Taking the only applied split back re-posts an empty set.
    await waitFor(() => expect(acceptSplitCuts).toHaveBeenLastCalledWith("p", "tl", []));
  });

  it("includes the honest exports-only framing caption", () => {
    const { getByText } = render(
      <SplitCutList client={client()} projectId="p" timelineId="tl" splitCuts={SPLITS} />,
    );
    expect(getByText(/OTIO-Export/)).toBeTruthy();
    expect(getByText(/hart geschnitten/)).toBeTruthy();
  });
});
