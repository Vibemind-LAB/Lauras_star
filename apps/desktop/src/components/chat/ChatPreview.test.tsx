import { act, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { LauraClient } from "../../api";
import { renderWithQuery } from "../../test-utils";
import { ChatPreview, type PreviewTarget } from "./ChatPreview";

function client(overrides: Partial<LauraClient> = {}): LauraClient {
  return {
    contactSheetUrl: vi.fn(),
    ...overrides,
  } as unknown as LauraClient;
}

describe("ChatPreview", () => {
  beforeEach(() => {
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
  });

  it("none: shows the empty-state copy", () => {
    const target: PreviewTarget = { kind: "none" };
    renderWithQuery(<ChatPreview target={target} client={client()} />);

    expect(screen.getByText("Noch nichts zu zeigen — bau etwas.")).toBeTruthy();
  });

  it("contact_sheet: loads the sheet via client.contactSheetUrl and renders it as an img", async () => {
    const contactSheetUrl = vi.fn().mockResolvedValue("blob:stub");
    const c = client({ contactSheetUrl });
    const target: PreviewTarget = { kind: "contact_sheet", sessionId: "s1", version: 1 };
    renderWithQuery(<ChatPreview target={target} client={c} />);

    await waitFor(() => expect(screen.getByAltText("Kontaktbogen")).toBeTruthy());
    expect(contactSheetUrl).toHaveBeenCalledWith("s1");
    expect(screen.getByAltText("Kontaktbogen").getAttribute("src")).toBe("blob:stub");
  });

  it("contact_sheet: re-fetches when version bumps and revokes the previous object URL", async () => {
    const contactSheetUrl = vi
      .fn()
      .mockResolvedValueOnce("blob:v1")
      .mockResolvedValueOnce("blob:v2");
    const c = client({ contactSheetUrl });
    const { rerender } = renderWithQuery(
      <ChatPreview target={{ kind: "contact_sheet", sessionId: "s1", version: 1 }} client={c} />,
    );
    await waitFor(() => expect(screen.getByAltText("Kontaktbogen").getAttribute("src")).toBe("blob:v1"));

    rerender(<ChatPreview target={{ kind: "contact_sheet", sessionId: "s1", version: 2 }} client={c} />);

    await waitFor(() => expect(screen.getByAltText("Kontaktbogen").getAttribute("src")).toBe("blob:v2"));
    expect(contactSheetUrl).toHaveBeenCalledTimes(2);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:v1");
  });

  it("contact_sheet: revokes the object URL on unmount", async () => {
    const contactSheetUrl = vi.fn().mockResolvedValue("blob:stub");
    const c = client({ contactSheetUrl });
    const { unmount } = renderWithQuery(
      <ChatPreview target={{ kind: "contact_sheet", sessionId: "s1", version: 1 }} client={c} />,
    );
    await waitFor(() => expect(screen.getByAltText("Kontaktbogen")).toBeTruthy());

    unmount();

    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:stub");
  });

  it("contact_sheet: a failing load shows the failure copy instead of crashing", async () => {
    const c = client({ contactSheetUrl: vi.fn().mockRejectedValue(new Error("404: no sheet")) });
    const target: PreviewTarget = { kind: "contact_sheet", sessionId: "s1", version: 1 };

    await act(async () => {
      renderWithQuery(<ChatPreview target={target} client={c} />);
    });

    expect(screen.getByText("Bogen konnte nicht geladen werden")).toBeTruthy();
    expect(screen.queryByAltText("Kontaktbogen")).toBeNull();
  });

  it("export: renders a video whose src points at the export media lane", () => {
    const target: PreviewTarget = { kind: "export", exportId: "exp-1" };
    const { container } = renderWithQuery(<ChatPreview target={target} client={client()} />);

    const video = container.querySelector("video");
    expect(video).not.toBeNull();
    expect(video?.getAttribute("src")).toBe("laura-media://media/export/exp-1");
  });
});
