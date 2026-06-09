import { type DragEvent, type ReactElement, useEffect, useRef, useState } from "react";

import { type LauraClient, type Scene } from "../api";
import { useSequence } from "../hooks/useSequence";
import { SequencePlayer } from "./SequencePlayer";

export function AssembleView({
  client,
  projectId,
  // roughCutId is accepted for API compatibility but the Bin now sources scenes
  // project-wide via listProjectScenes — this param is intentionally unused here.
  roughCutId: _roughCutId,
  onSeekScene,
}: {
  client: LauraClient;
  projectId: string | null;
  roughCutId?: string | null;
  onSeekScene: (sceneId: string) => void;
}): ReactElement {
  const [scenes, setLocalScenes] = useState<Scene[]>([]);
  const [binError, setBinError] = useState<string | null>(null);

  useEffect(() => {
    if (projectId === null) {
      setLocalScenes([]);
      setBinError(null);
      return;
    }
    let cancelled = false;
    client
      .listProjectScenes(projectId)
      .then((s) => {
        if (!cancelled) {
          setLocalScenes(s);
          setBinError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setBinError(err instanceof Error ? err.message : "Fehler beim Laden der Szenen");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [client, projectId]);

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
    <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto bg-ink p-3">
      {/* Sequence player — width-capped so the preview stays a reasonable size and the
          Bin + Sequenz below remain visible (was full-width aspect-video, which on a wide
          window grew ~850px tall and dominated the whole view). */}
      <div className="w-full max-w-2xl shrink-0">
        <SequencePlayer
          client={client}
          projectId={projectId}
          sequenceId={sequence?.timeline_id ?? null}
          reloadKey={sequence?.items.length}
        />
      </div>

      {/* Bin */}
      <div className="text-xs font-medium text-slate-400">Szenen-Bin</div>
      {binError !== null && (
        <div className="text-xs text-red-400">{binError}</div>
      )}
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
