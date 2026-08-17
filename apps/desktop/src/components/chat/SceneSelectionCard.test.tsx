import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type LauraClient, type SceneGateStatus } from "../../api";
import { SceneSelectionCard } from "./SceneSelectionCard";

// Note: @testing-library/jest-dom is not installed in this project — native DOM property
// checks (.getAttribute, .disabled) are used instead, consistent with the rest of the suite
// (EditorialToolsBar.test.tsx etc.).

const gate: SceneGateStatus = {
  enabled: true,
  pending: true,
  confirmed: false,
  selection_version: 3,
  recommended: [2],
  candidates: [
    {
      scene_number: 2,
      src_start_frame: 0,
      src_end_frame_exclusive: 300,
      thumb_frame: 150,
      description: "n8n Flow im Bild",
      transcript_snippet: "wir bauen den flow",
      rationale: "Hook",
      recommended: true,
    },
    {
      scene_number: 5,
      src_start_frame: 300,
      src_end_frame_exclusive: 600,
      thumb_frame: 450,
      description: "Terminal",
      transcript_snippet: "deploy läuft",
      rationale: "Beweis",
      recommended: false,
    },
  ],
};

/** `assetFrameUrl` is the async Blob->ObjectURL pattern (api.ts:1313), not a plain URL string —
 * a never-resolving promise here avoids `URL.revokeObjectURL` (unimplemented in jsdom) firing
 * during test cleanup, same pattern as SceneStrip.test.tsx's client stub. */
function client(): LauraClient {
  return {
    assetFrameUrl: vi.fn().mockReturnValue(new Promise<string>(() => undefined)),
  } as unknown as LauraClient;
}

describe("SceneSelectionCard", () => {
  it("preselects recommended tiles and confirms the toggled set", async () => {
    const confirm = vi.fn().mockResolvedValue({ session_id: "s1" });
    render(
      <SceneSelectionCard
        gate={gate}
        assetId="a1"
        sessionId="s1"
        client={client()}
        confirm={confirm}
        onConfirmed={() => {}}
      />,
    );
    // recommended tile is pre-checked
    expect(screen.getByTestId("scene-tile-2").getAttribute("data-selected")).toBe("true");
    expect(screen.getByTestId("scene-tile-5").getAttribute("data-selected")).toBe("false");
    fireEvent.click(screen.getByTestId("scene-tile-5"));
    fireEvent.click(screen.getByRole("button", { name: /Apply selection/ }));
    // the version the tiles were rendered from travels with the confirm
    expect(confirm).toHaveBeenCalledWith("s1", [2, 5], 3);
  });

  it("refuses to confirm an empty selection", () => {
    const confirm = vi.fn();
    render(
      <SceneSelectionCard
        gate={gate}
        assetId="a1"
        sessionId="s1"
        client={client()}
        confirm={confirm}
        onConfirmed={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("scene-tile-2")); // deselect the only pick
    const button = screen.getByRole("button", { name: /Apply selection/ }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("disables the button and both tiles while a confirm is in flight (busy guards double-submit)", async () => {
    let resolveConfirm: (value: { session_id: string }) => void = () => {
      throw new Error("resolveConfirm called before assignment");
    };
    const pending = new Promise<{ session_id: string }>((resolve) => {
      resolveConfirm = resolve;
    });
    const confirm = vi.fn().mockReturnValue(pending);
    const onConfirmed = vi.fn();
    render(
      <SceneSelectionCard
        gate={gate}
        assetId="a1"
        sessionId="s1"
        client={client()}
        confirm={confirm}
        onConfirmed={onConfirmed}
      />,
    );

    const button = screen.getByRole("button", { name: /Apply selection/ }) as HTMLButtonElement;
    fireEvent.click(button);

    expect(confirm).toHaveBeenCalledTimes(1);
    expect(button.disabled).toBe(true);
    // A second click while busy must not fire a second confirm call.
    fireEvent.click(button);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(onConfirmed).not.toHaveBeenCalled();

    await (async () => {
      resolveConfirm({ session_id: "s1" });
      await pending;
    })();
  });
  it("re-arms the pre-selection when the agent proposes a different set of scenes", () => {
    const confirm = vi.fn().mockResolvedValue({ session_id: "s1" });
    const view = render(
      <SceneSelectionCard
        gate={gate}
        assetId="a1"
        sessionId="s1"
        client={client()}
        confirm={confirm}
        onConfirmed={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("scene-tile-5")); // user's own toggle on THIS proposal

    // The user asks for other scenes; propose_scene_selection runs again and the card — still
    // mounted — receives a new candidate set. Scene 5 is gone entirely, scene 7 is the new
    // recommendation. Pinning the first proposal's pre-selection would confirm scene 5, which
    // is no longer on the board.
    const reproposed: SceneGateStatus = {
      ...gate,
      selection_version: 4,
      recommended: [7],
      candidates: [
        { ...gate.candidates![0], scene_number: 7, recommended: true },
        { ...gate.candidates![1], scene_number: 9, recommended: false },
      ],
    };
    view.rerender(
      <SceneSelectionCard
        gate={reproposed}
        assetId="a1"
        sessionId="s1"
        client={client()}
        confirm={confirm}
        onConfirmed={() => {}}
      />,
    );

    expect(screen.getByTestId("scene-tile-7").getAttribute("data-selected")).toBe("true");
    expect(screen.getByTestId("scene-tile-9").getAttribute("data-selected")).toBe("false");
    fireEvent.click(screen.getByRole("button", { name: /Apply selection/ }));
    expect(confirm).toHaveBeenCalledWith("s1", [7], 4);
  });

  it("leaves the user's toggles alone on a re-render with the same proposal", () => {
    const confirm = vi.fn().mockResolvedValue({ session_id: "s1" });
    const props = {
      gate,
      assetId: "a1",
      sessionId: "s1",
      client: client(),
      confirm,
      onConfirmed: () => {},
    };
    const view = render(<SceneSelectionCard {...props} />);
    fireEvent.click(screen.getByTestId("scene-tile-2")); // deselect Laura's pick

    view.rerender(<SceneSelectionCard {...props} gate={{ ...gate }} />);

    expect(screen.getByTestId("scene-tile-2").getAttribute("data-selected")).toBe("false");
  });
});
