import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type AiRuntimeEvent, type LauraClient } from "../api";
import { RuntimeStatusPanel } from "./RuntimeStatusPanel";

function client(overrides: Partial<LauraClient> = {}): LauraClient {
  return {
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
    ...overrides,
  } as unknown as LauraClient;
}

describe("RuntimeStatusPanel", () => {
  it("lists runtimes with their status", async () => {
    render(<RuntimeStatusPanel client={client()} />);

    expect(await screen.findByText("Stub Lipsync")).toBeTruthy();
    expect(screen.getByText("ready")).toBeTruthy();
  });

  it("refreshes a runtime", async () => {
    const refreshAiRuntime = vi.fn().mockResolvedValue({});
    render(<RuntimeStatusPanel client={client({ refreshAiRuntime })} />);

    await screen.findByText("Stub Lipsync");
    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));

    await waitFor(() => expect(refreshAiRuntime).toHaveBeenCalledWith("rt-1"));
  });

  it("starts and stops container runtimes", async () => {
    const startAiRuntime = vi.fn().mockResolvedValue({});
    const stopAiRuntime = vi.fn().mockResolvedValue({});
    render(
      <RuntimeStatusPanel
        client={client({
          listAiRuntimes: vi.fn().mockResolvedValue([
            {
              id: "rt-2",
              kind: "container",
              effect: "reenact",
              display_name: "LivePortrait",
              status: { state: "stopped", ready: false },
              capabilities: {},
              base_url: null,
              container_image: "liveportrait:latest",
              container_name: "laura-liveportrait",
              port: 8766,
              workspace_mount: null,
              model_mount: null,
              requires_gpu: true,
              enabled: true,
              license_status: "accepted",
              last_health_at: null,
              created_at: "",
              updated_at: "",
            },
          ]),
          startAiRuntime,
          stopAiRuntime,
        })}
      />,
    );

    await screen.findByText("LivePortrait");
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(startAiRuntime).toHaveBeenCalledWith("rt-2"));

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(stopAiRuntime).toHaveBeenCalledWith("rt-2"));
  });

  it("shows runtime events on demand", async () => {
    const events: AiRuntimeEvent[] = [
      {
        id: "ev-1",
        runtime_id: "rt-1",
        event_type: "health",
        level: "info",
        message: "Healthy",
        payload: {},
        created_at: "2026-06-18T10:00:00Z",
      },
    ];
    const listAiRuntimeEvents = vi.fn().mockResolvedValue(events);
    render(<RuntimeStatusPanel client={client({ listAiRuntimeEvents })} />);

    await screen.findByText("Stub Lipsync");
    fireEvent.click(screen.getByRole("button", { name: "Events" }));

    expect(await screen.findByText("Healthy")).toBeTruthy();
    expect(listAiRuntimeEvents).toHaveBeenCalledWith("rt-1");
  });

  it("re-fetches runtimes and expanded events when reloadKey changes", async () => {
    const listAiRuntimes = vi
      .fn()
      .mockResolvedValueOnce([
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
      ])
      .mockResolvedValueOnce([
        {
          id: "rt-1",
          kind: "stub",
          effect: "lipsync",
          display_name: "Stub Lipsync",
          status: { state: "running", ready: true },
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
      ]);
    const listAiRuntimeEvents = vi
      .fn()
      .mockResolvedValueOnce([
        {
          id: "ev-1",
          runtime_id: "rt-1",
          event_type: "health",
          level: "info",
          message: "Healthy",
          payload: {},
          created_at: "2026-06-18T10:00:00Z",
        },
      ])
      .mockResolvedValueOnce([
        {
          id: "ev-2",
          runtime_id: "rt-1",
          event_type: "health",
          level: "info",
          message: "Restarted",
          payload: {},
          created_at: "2026-06-18T10:05:00Z",
        },
      ]);

    const rendered = render(
      <RuntimeStatusPanel client={client({ listAiRuntimes, listAiRuntimeEvents })} reloadKey={0} />,
    );

    await screen.findByText("Stub Lipsync");
    fireEvent.click(screen.getByRole("button", { name: "Events" }));
    expect(await screen.findByText("Healthy")).toBeTruthy();

    rendered.rerender(
      <RuntimeStatusPanel client={client({ listAiRuntimes, listAiRuntimeEvents })} reloadKey={1} />,
    );

    expect(await screen.findByText("running")).toBeTruthy();
    expect(await screen.findByText("Restarted")).toBeTruthy();
    await waitFor(() => expect(listAiRuntimes).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(listAiRuntimeEvents).toHaveBeenCalledTimes(2));
  });

  it("re-fetches events after reload when the events panel was collapsed", async () => {
    const listAiRuntimeEvents = vi
      .fn()
      .mockResolvedValueOnce([
        {
          id: "ev-1",
          runtime_id: "rt-1",
          event_type: "health",
          level: "info",
          message: "Healthy",
          payload: {},
          created_at: "2026-06-18T10:00:00Z",
        },
      ])
      .mockResolvedValueOnce([
        {
          id: "ev-2",
          runtime_id: "rt-1",
          event_type: "health",
          level: "info",
          message: "Fresh after reload",
          payload: {},
          created_at: "2026-06-18T10:05:00Z",
        },
      ]);

    const rendered = render(
      <RuntimeStatusPanel client={client({ listAiRuntimeEvents })} reloadKey={0} />,
    );

    await screen.findByText("Stub Lipsync");

    fireEvent.click(screen.getByRole("button", { name: "Events" }));
    expect(await screen.findByText("Healthy")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Events" }));
    await waitFor(() => expect(screen.queryByText("Healthy")).toBeNull());

    rendered.rerender(
      <RuntimeStatusPanel client={client({ listAiRuntimeEvents })} reloadKey={1} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Events" }));

    expect(await screen.findByText("Fresh after reload")).toBeTruthy();
    await waitFor(() => expect(listAiRuntimeEvents).toHaveBeenCalledTimes(2));
  });

  it("re-fetches expanded events after a runtime action", async () => {
    const refreshAiRuntime = vi.fn().mockResolvedValue({});
    const listAiRuntimes = vi
      .fn()
      .mockResolvedValue([
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
      ]);
    const listAiRuntimeEvents = vi
      .fn()
      .mockResolvedValueOnce([
        {
          id: "ev-1",
          runtime_id: "rt-1",
          event_type: "health",
          level: "info",
          message: "Healthy",
          payload: {},
          created_at: "2026-06-18T10:00:00Z",
        },
      ])
      .mockResolvedValueOnce([
        {
          id: "ev-2",
          runtime_id: "rt-1",
          event_type: "health",
          level: "info",
          message: "Refreshed",
          payload: {},
          created_at: "2026-06-18T10:01:00Z",
        },
      ]);

    render(
      <RuntimeStatusPanel
        client={client({ listAiRuntimes, listAiRuntimeEvents, refreshAiRuntime })}
      />,
    );

    await screen.findByText("Stub Lipsync");
    fireEvent.click(screen.getByRole("button", { name: "Events" }));
    expect(await screen.findByText("Healthy")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));

    expect(await screen.findByText("Refreshed")).toBeTruthy();
    await waitFor(() => expect(refreshAiRuntime).toHaveBeenCalledWith("rt-1"));
    await waitFor(() => expect(listAiRuntimeEvents).toHaveBeenCalledTimes(2));
  });
});
