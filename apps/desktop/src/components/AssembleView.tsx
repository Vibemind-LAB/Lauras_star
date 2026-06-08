import { type DragEvent, type ReactElement, useRef } from "react";

import { type LauraClient } from "../api";
import { useScenes } from "../hooks/useScenes";
import { useSequence } from "../hooks/useSequence";
import { SequencePlayer } from "./SequencePlayer";

export function AssembleView({
  client,
  projectId,
  roughCutId,
  onSeekScene,
}: {
  client: LauraClient;
  projectId: string | null;
  roughCutId: string | null;
  onSeekScene: (sceneId: string) => void;
}): ReactElement {
  const { scenes } = useScenes(client, roughCutId);
  const { sequence, setScenes } = useSequence(client, projectId);
  const ids = (sequence?.items ?? []).map((i) => i.scene_id);

  const dragIndex = useRef<number | null>(null);

  const reorder = (from: number, to: number): void => {
    const next = [...ids];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    void setScenes(next);
  };

  const handleDragStart = (e: DragEvent<HTMLDivElement>, i: number): void => {
    dragIndex.current = i;
    e.dataTransfer.setData("text/plain", String(i));
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>, i: number): void => {
    e.preventDefault();
    const from = dragIndex.current ?? Number(e.dataTransfer.getData("text/plain"));
    if (from !== i) {
      reorder(from, i);
    }
    dragIndex.current = null;
  };

  const handleDragOver = (e: DragEvent<HTMLDivElement>): void => {
    e.preventDefault();
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 bg-ink p-3">
      {/* Sequence player — re-fetches flattened clips whenever the item count changes */}
      <SequencePlayer
        client={client}
        projectId={projectId}
        sequenceId={sequence?.timeline_id ?? null}
        reloadKey={sequence?.items.length}
      />

      {/* Bin */}
      <div className="text-xs font-medium text-slate-400">Szenen-Bin</div>
      <div className="flex gap-2 overflow-x-auto pb-1">
        {scenes.length === 0 ? (
          <div className="text-xs text-slate-600">
            Noch keine Szenen — erst Rough Cut ausführen.
          </div>
        ) : (
          scenes.map((s) => (
            <div
              key={s.id}
              className="w-40 shrink-0 rounded border border-edge bg-slate-800 p-2 text-xs"
            >
              <div className="truncate text-slate-200">{s.name}</div>
              <button
                type="button"
                title={`${s.name} zur Sequenz hinzufügen`}
                onClick={() => void setScenes([...ids, s.id])}
                className="mt-1 rounded bg-sky-600 px-2 py-0.5 text-white hover:bg-sky-500"
              >
                + Sequenz
              </button>
            </div>
          ))
        )}
      </div>

      {/* Sequence track */}
      <div className="mt-2 text-xs font-medium text-slate-400">Sequenz</div>
      <div className="flex gap-2 overflow-x-auto pb-1">
        {(sequence?.items ?? []).length === 0 ? (
          <div className="text-xs text-slate-600">
            Szenen aus dem Bin hinzufügen.
          </div>
        ) : (
          (sequence?.items ?? []).map((it, i) => (
            <div
              key={it.id}
              draggable
              onDragStart={(e) => handleDragStart(e, i)}
              onDragOver={handleDragOver}
              onDrop={(e) => handleDrop(e, i)}
              className="w-40 shrink-0 cursor-grab rounded border border-edge bg-slate-800 p-2 text-xs"
            >
              <button
                type="button"
                onClick={() => onSeekScene(it.scene_id)}
                className="block w-full truncate text-left text-slate-200 hover:text-white"
              >
                {it.scene_name}
              </button>
              <button
                type="button"
                onClick={() => void setScenes(ids.filter((_, j) => j !== i))}
                className="mt-1 rounded bg-slate-700 px-2 py-0.5 text-slate-300 hover:bg-slate-600"
              >
                entfernen
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
