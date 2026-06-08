import { fireEvent, render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type Asset, type LauraClient, type Scene } from "../api";
import { SceneMusicControls } from "./SceneMusicControls";

const scene: Scene = { id: "s1", project_id: "p", source_timeline_id: "tl", name: "Szene 1",
  order_index: 0, seq_in_frame: 0, seq_out_frame_exclusive: 30,
  music_asset_id: null, music_gain_percent: 100 };
const assets = [{ id: "m1", display_name: "song.mp3", type: "audio" }] as unknown as Asset[];

function client(over: Partial<LauraClient>): LauraClient {
  return { listAssets: vi.fn().mockResolvedValue(assets),
    setSceneMusic: vi.fn().mockResolvedValue({ ...scene, music_asset_id: "m1" }),
    removeSceneMusic: vi.fn().mockResolvedValue(scene), ...over } as unknown as LauraClient;
}

describe("SceneMusicControls", () => {
  it("sets music for the scene", async () => {
    const c = client({});
    const onChange = vi.fn();
    const { getByText, getByRole } = render(
      <SceneMusicControls client={c} projectId="p" scene={scene} onChange={onChange} />);
    await waitFor(() => expect(c.listAssets).toHaveBeenCalledWith("p"));
    fireEvent.change(getByRole("combobox"), { target: { value: "m1" } });
    fireEvent.click(getByText("Musik setzen"));
    await waitFor(() => expect(c.setSceneMusic).toHaveBeenCalledWith("s1", "m1", 100));
    expect(onChange).toHaveBeenCalled();
  });
});
