import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type LauraClient } from "../api";
import { RuntimeSetupPanel } from "./RuntimeSetupPanel";

function client(overrides: Partial<LauraClient> = {}): LauraClient {
  return {
    createAiRuntime: vi.fn().mockResolvedValue({ id: "rt-1" }),
    refreshAiRuntime: vi.fn().mockResolvedValue({ id: "rt-1" }),
    ...overrides,
  } as unknown as LauraClient;
}

describe("RuntimeSetupPanel", () => {
  it("creates an external HTTP runtime and refreshes it", async () => {
    const createAiRuntime = vi.fn().mockResolvedValue({ id: "rt-1" });
    const refreshAiRuntime = vi.fn().mockResolvedValue({ id: "rt-1" });
    const onCreated = vi.fn();

    render(
      <RuntimeSetupPanel
        client={client({ createAiRuntime, refreshAiRuntime } as Partial<LauraClient>)}
        onCreated={onCreated}
      />,
    );

    fireEvent.change(screen.getByLabelText("Runtime-Name"), {
      target: { value: "Local Lipsync" },
    });
    fireEvent.change(screen.getByLabelText("Runtime-Art"), {
      target: { value: "external_http" },
    });
    fireEvent.change(screen.getByLabelText("Effekt"), {
      target: { value: "lipsync" },
    });
    fireEvent.change(screen.getByLabelText("Base-URL"), {
      target: { value: "http://127.0.0.1:8901" },
    });
    fireEvent.click(screen.getByRole("button", { name: /runtime registrieren/i }));

    await waitFor(() =>
      expect(createAiRuntime).toHaveBeenCalledWith(
        expect.objectContaining({
          kind: "external_http",
          effect: "lipsync",
          displayName: "Local Lipsync",
          baseUrl: "http://127.0.0.1:8901",
        }),
      ),
    );
    expect(refreshAiRuntime).toHaveBeenCalledWith("rt-1");
    expect(onCreated).toHaveBeenCalled();
  });

  it("creates a container runtime with image, port, GPU and model path", async () => {
    const createAiRuntime = vi.fn().mockResolvedValue({ id: "rt-2" });

    render(<RuntimeSetupPanel client={client({ createAiRuntime } as Partial<LauraClient>)} />);

    fireEvent.change(screen.getByLabelText("Runtime-Art"), {
      target: { value: "container" },
    });
    fireEvent.change(screen.getByLabelText("Runtime-Name"), {
      target: { value: "LivePortrait" },
    });
    fireEvent.change(screen.getByLabelText("Effekt"), {
      target: { value: "reenact" },
    });
    fireEvent.change(screen.getByLabelText("Container-Image"), {
      target: { value: "laura-runtime-liveportrait:local" },
    });
    fireEvent.change(screen.getByLabelText("Port"), {
      target: { value: "8899" },
    });
    fireEvent.change(screen.getByLabelText("Modellpfad"), {
      target: { value: "E:/LauraModels/liveportrait" },
    });
    fireEvent.click(screen.getByLabelText("GPU verwenden"));
    fireEvent.click(screen.getByLabelText("Lizenz akzeptiert"));
    fireEvent.click(screen.getByRole("button", { name: /runtime registrieren/i }));

    await waitFor(() =>
      expect(createAiRuntime).toHaveBeenCalledWith(
        expect.objectContaining({
          kind: "container",
          effect: "reenact",
          displayName: "LivePortrait",
          containerImage: "laura-runtime-liveportrait:local",
          containerName: "laura-reenact-liveportrait",
          port: 8899,
          modelMount: "E:/LauraModels/liveportrait",
          requiresGpu: true,
          licenseStatus: "accepted",
        }),
      ),
    );
  });
});
