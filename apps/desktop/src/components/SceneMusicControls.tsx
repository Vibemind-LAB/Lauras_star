import { type ReactElement, useEffect, useState } from "react";

import { type Asset, type LauraClient, type Scene } from "../api";

export function SceneMusicControls({
  client, projectId, scene, onChange,
}: {
  client: LauraClient;
  projectId: string | null;
  scene: Scene;
  onChange: () => void;
}): ReactElement {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [pick, setPick] = useState<string>(scene.music_asset_id ?? "");
  const [gain, setGain] = useState<number>(scene.music_gain_percent ?? 100);

  useEffect(() => {
    if (!projectId) return;
    void client.listAssets(projectId).then(setAssets).catch(() => undefined);
  }, [client, projectId]);

  const apply = async (): Promise<void> => {
    if (!pick) return;
    await client.setSceneMusic(scene.id, pick, gain);
    onChange();
  };
  const clear = async (): Promise<void> => {
    await client.removeSceneMusic(scene.id);
    onChange();
  };

  return (
    <div className="flex items-center gap-2 border-t border-edge px-3 py-2 text-xs">
      <span className="text-slate-400">Musik</span>
      <select value={pick} onChange={(e) => setPick(e.target.value)}
        className="rounded bg-slate-800 px-2 py-1 text-slate-100">
        <option value="">— keine —</option>
        {assets.map((a) => <option key={a.id} value={a.id}>{a.display_name}</option>)}
      </select>
      <label className="flex items-center gap-1 text-slate-400">
        Gain
        <input type="range" min={0} max={400} value={gain}
          onChange={(e) => setGain(Number(e.target.value))} />
        <span className="w-10 tabular-nums">{gain}%</span>
      </label>
      <button type="button" onClick={() => void apply()} disabled={!pick}
        className="rounded bg-sky-600 px-2 py-1 text-white disabled:opacity-40">Musik setzen</button>
      {scene.music_asset_id && (
        <button type="button" onClick={() => void clear()}
          className="rounded bg-slate-700 px-2 py-1 text-slate-200">entfernen</button>
      )}
    </div>
  );
}
