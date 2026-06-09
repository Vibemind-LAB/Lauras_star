import { type DragEvent, type ReactElement, useEffect, useRef, useState } from "react";

import { type Asset, type LauraClient, type Scene } from "../api";
import { useSequence } from "../hooks/useSequence";
import { SequencePlayer } from "./SequencePlayer";

/** A small source-frame thumbnail, fetched as a token-authed JPEG object URL. */
function Thumb({
  client,
  assetId,
  frame,
  className,
}: {
  client: LauraClient;
  assetId: string | null;
  frame: number | null;
  className?: string;
}): ReactElement {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!assetId) {
      setUrl(null);
      return;
    }
    let active = true;
    let objectUrl: string | null = null;
    client
      .assetFrameUrl(assetId, Math.max(0, frame ?? 0))
      .then((u) => {
        if (!active) {
          URL.revokeObjectURL(u);
          return;
        }
        objectUrl = u;
        setUrl(u);
      })
      .catch(() => {
        /* colour fallback stays */
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [client, assetId, frame]);
  return (
    <span
      className={`block shrink-0 overflow-hidden rounded bg-sky-900/40 ${className ?? "h-9 w-16"}`}
    >
      {url ? <img src={url} alt="" className="h-full w-full object-cover" /> : null}
    </span>
  );
}

/**
 * Zusammenfügen (assemble) — mirrors the Feinschnitt layout:
 *   left  = all project scenes, grouped by source video, each with a thumbnail
 *   centre= the sequence player + the ordered "Reihenfolge" (the final cut)
 * Clicking a scene on the left appends it to the Reihenfolge; drag to reorder.
 */
export function AssembleView({
  client,
  projectId,
  // roughCutId accepted for API compatibility; the bin is project-wide.
  roughCutId: _roughCutId,
  onSeekScene,
}: {
  client: LauraClient;
  projectId: string | null;
  roughCutId?: string | null;
  onSeekScene: (sceneId: string) => void;
}): ReactElement {
  const [scenes, setScenesList] = useState<Scene[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [binError, setBinError] = useState<string | null>(null);

  useEffect(() => {
    if (projectId === null) {
      setScenesList([]);
      setAssets([]);
      setBinError(null);
      return;
    }
    let cancelled = false;
    Promise.all([client.listProjectScenes(projectId), client.listAssets(projectId)])
      .then(([s, a]) => {
        if (!cancelled) {
          setScenesList(s);
          setAssets(a);
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
  const items = sequence?.items ?? [];
  const ids = items.map((i) => i.scene_id);

  const sceneById = (id: string): Scene | undefined => scenes.find((s) => s.id === id);
  const assetName = (id: string | null | undefined): string =>
    assets.find((a) => a.id === id)?.display_name ?? "Video";

  // Group scenes by their source video (insertion order preserved).
  const groups: { assetId: string; scenes: Scene[] }[] = [];
  for (const s of scenes) {
    const key = s.asset_id ?? "?";
    const existing = groups.find((g) => g.assetId === key);
    if (existing) existing.scenes.push(s);
    else groups.push({ assetId: key, scenes: [s] });
  }

  // Drag-reorder within the Reihenfolge.
  const dragIndex = useRef<number | null>(null);
  const reorder = (from: number, to: number): void => {
    const next = [...ids];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    void setScenes(next);
  };
  const onDragStart = (e: DragEvent<HTMLDivElement>, i: number): void => {
    dragIndex.current = i;
    e.dataTransfer.setData("text/plain", String(i));
  };
  const onDrop = (e: DragEvent<HTMLDivElement>, i: number): void => {
    e.preventDefault();
    const from = dragIndex.current ?? Number(e.dataTransfer.getData("text/plain"));
    if (from !== i) reorder(from, i);
    dragIndex.current = null;
  };
  const onDragOver = (e: DragEvent<HTMLDivElement>): void => e.preventDefault();

  return (
    <div className="grid min-h-0 flex-1 grid-cols-[240px_1fr] gap-px bg-edge">
      {/* Left: all project scenes, grouped by source video */}
      <aside className="flex min-h-0 flex-col gap-3 overflow-y-auto bg-ink p-2">
        <div className="text-xs font-medium text-slate-300">
          Szenen <span className="text-slate-600">· Klick hängt an</span>
        </div>
        {binError !== null && <div className="text-xs text-red-400">{binError}</div>}
        {scenes.length === 0 ? (
          <div className="text-xs text-slate-600">
            Noch keine Szenen — erst im Rough Cut Szenen erzeugen.
          </div>
        ) : (
          groups.map((g) => (
            <div key={g.assetId} className="flex flex-col gap-1">
              <div className="truncate text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                {assetName(g.assetId)} · {g.scenes.length}
              </div>
              {g.scenes.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  title={`„${s.name}" an die Reihenfolge anhängen`}
                  onClick={() => void setScenes([...ids, s.id])}
                  className="flex items-center gap-2 rounded border border-edge bg-slate-800/50 p-1 text-left text-xs hover:bg-slate-700"
                >
                  <Thumb client={client} assetId={s.asset_id ?? g.assetId} frame={s.thumb_frame ?? 0} />
                  <span className="min-w-0 flex-1 truncate text-slate-200">{s.name}</span>
                  <span className="shrink-0 rounded bg-sky-600 px-1.5 py-0.5 font-medium text-white">
                    +
                  </span>
                </button>
              ))}
            </div>
          ))
        )}
      </aside>

      {/* Centre: sequence player + the ordered Reihenfolge */}
      <section className="flex min-h-0 flex-col gap-2 overflow-y-auto bg-ink p-3">
        <div className="w-full max-w-2xl">
          <SequencePlayer
            client={client}
            projectId={projectId}
            sequenceId={sequence?.timeline_id ?? null}
            reloadKey={items.length}
          />
        </div>

        <div className="text-xs font-medium text-slate-300">
          Reihenfolge{" "}
          <span className="text-slate-600">
            — in dieser Folge werden die Szenen aneinandergehängt (ziehen zum Umordnen)
          </span>
        </div>
        <div className="flex gap-2 overflow-x-auto pb-2">
          {items.length === 0 ? (
            <div className="py-6 text-xs text-slate-600">
              Links eine Szene anklicken, um sie hier anzuhängen.
            </div>
          ) : (
            items.map((it, i) => {
              const sc = sceneById(it.scene_id);
              return (
                <div
                  key={it.id}
                  draggable
                  onDragStart={(e) => onDragStart(e, i)}
                  onDragOver={onDragOver}
                  onDrop={(e) => onDrop(e, i)}
                  className="flex w-28 shrink-0 cursor-grab flex-col gap-1 rounded border border-edge bg-slate-800 p-1 text-xs"
                >
                  <Thumb
                    client={client}
                    assetId={sc?.asset_id ?? null}
                    frame={sc?.thumb_frame ?? 0}
                    className="h-14 w-full"
                  />
                  <button
                    type="button"
                    onClick={() => onSeekScene(it.scene_id)}
                    className="truncate text-left text-slate-200 hover:text-white"
                  >
                    {i + 1}. {it.scene_name}
                  </button>
                  <button
                    type="button"
                    onClick={() => void setScenes(ids.filter((_, j) => j !== i))}
                    className="rounded bg-slate-700 px-1 py-0.5 text-[11px] text-slate-300 hover:bg-slate-600"
                  >
                    entfernen
                  </button>
                </div>
              );
            })
          )}
        </div>
      </section>
    </div>
  );
}
