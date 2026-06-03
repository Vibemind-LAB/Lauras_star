import { type ReactElement, useEffect, useState } from "react";

import {
  type ExportFormat,
  type LauraClient,
  type Operation,
  type Timeline,
  type TimelineClip,
} from "../api";

const EXPORT_FORMATS: { fmt: ExportFormat; label: string; ext: string }[] = [
  { fmt: "otio", label: "OTIO", ext: "otio" },
  { fmt: "edl", label: "EDL", ext: "edl" },
  { fmt: "fcp7xml", label: "FCP7-XML", ext: "xml" },
  { fmt: "fcpxml", label: "FCPXML", ext: "fcpxml" },
];

const TRIM_STEP = 5; // frames per trim click

function ClipThumb({
  client,
  clip,
  index,
  total,
  selected,
  onSelect,
}: {
  client: LauraClient;
  clip: TimelineClip;
  index: number;
  total: number;
  selected: boolean;
  onSelect: () => void;
}): ReactElement {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    client
      .assetFrameUrl(clip.asset_id, clip.src_in_frame)
      .then((u) => {
        if (!active) {
          URL.revokeObjectURL(u);
          return;
        }
        objectUrl = u;
        setUrl(u);
      })
      .catch(() => {
        /* fall back to the colour block */
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [client, clip.asset_id, clip.src_in_frame]);

  const pct = total > 0 ? ((clip.seq_out_frame_exclusive - clip.seq_in_frame) / total) * 100 : 0;
  const retimed = clip.speed_num !== clip.speed_den;
  return (
    <button
      type="button"
      onClick={onSelect}
      title={`Clip ${index + 1} · src ${clip.src_in_frame}–${clip.src_out_frame_exclusive}${
        retimed ? ` · ${clip.speed_num}/${clip.speed_den}×` : ""
      } (Klick = auswählen)`}
      style={{ width: `${pct}%` }}
      className={`relative flex items-center justify-center overflow-hidden ${
        url ? "" : index % 2 === 0 ? "bg-sky-700/50" : "bg-sky-500/40"
      } ${selected ? "z-10 ring-2 ring-inset ring-amber-400" : "hover:brightness-125"}`}
    >
      {url && <img src={url} alt="" className="absolute inset-0 h-full w-full object-cover" />}
      <span className="relative rounded bg-ink/70 px-1 text-[10px] leading-tight text-slate-100">
        {index + 1}
        {retimed ? "⏩" : ""}
      </span>
    </button>
  );
}

export function TimelineBar({
  client,
  timeline,
  onChange,
}: {
  client: LauraClient;
  timeline: Timeline | null;
  onChange: () => void;
}): ReactElement {
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [history, setHistory] = useState<TimelineClip[][]>([]);
  const [future, setFuture] = useState<TimelineClip[][]>([]);

  // Reset edit history only when switching to a different timeline.
  const tlId = timeline?.id ?? null;
  useEffect(() => {
    setHistory([]);
    setFuture([]);
    setSelected(null);
  }, [tlId]);

  if (!timeline) {
    return (
      <div className="flex h-20 items-center border-t border-edge bg-panel px-5 text-xs text-slate-600">
        Rough Cut — wähle ein Projekt.
      </div>
    );
  }

  const tl = timeline;
  const total = tl.clips.reduce((m, c) => Math.max(m, c.seq_out_frame_exclusive), 0);
  const sel = tl.clips.find((c) => c.id === selected) ?? null;

  async function runOp(op: Operation): Promise<void> {
    const snapshot = tl.clips;
    setError(null);
    try {
      await client.applyOperation(tl.id, op);
      setHistory((h) => [...h, snapshot]);
      setFuture([]);
      onChange();
    } catch (e) {
      setError(String(e));
    }
  }

  async function restore(clips: TimelineClip[], pushTo: "history" | "future"): Promise<void> {
    const cur = tl.clips;
    setError(null);
    try {
      await client.setClips(tl.id, clips);
      if (pushTo === "future") setFuture((f) => [...f, cur]);
      else setHistory((h) => [...h, cur]);
      onChange();
    } catch (e) {
      setError(String(e));
    }
  }

  async function undo(): Promise<void> {
    if (history.length === 0) return;
    const prev = history[history.length - 1];
    setHistory((h) => h.slice(0, -1));
    await restore(prev, "future");
  }

  async function redo(): Promise<void> {
    if (future.length === 0) return;
    const next = future[future.length - 1];
    setFuture((f) => f.slice(0, -1));
    await restore(next, "history");
  }

  async function splitSelected(): Promise<void> {
    if (!sel) return;
    const mid = Math.floor((sel.seq_in_frame + sel.seq_out_frame_exclusive) / 2);
    await runOp({ op: "split", at_seq_frame: mid });
  }

  async function trimSelected(delta: number): Promise<void> {
    if (!sel) return;
    const newOut = Math.max(sel.src_in_frame + 1, sel.src_out_frame_exclusive + delta);
    await runOp({
      op: "trim",
      at_seq_frame: sel.seq_in_frame,
      new_src_in_frame: sel.src_in_frame,
      new_src_out_frame_exclusive: newOut,
    });
  }

  async function duplicateSelected(): Promise<void> {
    if (!sel) return;
    await runOp({
      op: "insert_clip",
      asset_id: sel.asset_id,
      src_in_frame: sel.src_in_frame,
      src_out_frame_exclusive: sel.src_out_frame_exclusive,
      at_seq_frame: sel.seq_out_frame_exclusive,
      lane: sel.lane,
    });
  }

  async function deleteSelected(): Promise<void> {
    if (!sel) return;
    await runOp({
      op: "delete",
      seq_in_frame: sel.seq_in_frame,
      seq_out_frame_exclusive: sel.seq_out_frame_exclusive,
    });
    setSelected(null);
  }

  async function exportAs(fmt: ExportFormat, ext: string): Promise<void> {
    setError(null);
    try {
      const result = await client.exportTimeline(tl.id, fmt);
      if (result.content) {
        await window.laura.saveTextFile(`${tl.name}.${ext}`, result.content);
      }
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="border-t border-edge bg-panel px-5 py-3">
      <div className="mb-1 flex items-center justify-between">
        <span className="flex items-center gap-2">
          <span className="text-xs uppercase tracking-wide text-slate-500">
            Rough Cut · {tl.name}
          </span>
          <button
            type="button"
            onClick={() => void undo()}
            disabled={history.length === 0}
            title="Rückgängig"
            className="rounded bg-ink px-2 py-0.5 text-xs text-slate-300 hover:bg-edge disabled:opacity-30"
          >
            ↶ Undo
          </button>
          <button
            type="button"
            onClick={() => void redo()}
            disabled={future.length === 0}
            title="Wiederholen"
            className="rounded bg-ink px-2 py-0.5 text-xs text-slate-300 hover:bg-edge disabled:opacity-30"
          >
            ↷ Redo
          </button>
        </span>
        <span className="flex items-center gap-2">
          {tl.clips.length > 0 &&
            EXPORT_FORMATS.map((f) => (
              <button
                key={f.fmt}
                type="button"
                onClick={() => void exportAs(f.fmt, f.ext)}
                className="rounded bg-ink px-2 py-0.5 text-xs text-slate-300 hover:bg-edge"
              >
                {f.label}
              </button>
            ))}
          <span className="text-xs text-slate-500">
            {tl.clips.length} Clips · {total} frames
          </span>
        </span>
      </div>
      {error && <div className="mb-1 text-xs text-red-400">{error}</div>}
      {tl.clips.length === 0 ? (
        <div className="flex h-12 items-center justify-center rounded-md border border-dashed border-edge text-xs text-slate-600">
          Klicke einen Shot oder Transkript-Satz an, um ihn anzuhängen.
        </div>
      ) : (
        <div className="flex h-12 w-full gap-px overflow-hidden rounded-md">
          {tl.clips.map((c, i) => (
            <ClipThumb
              key={c.id}
              client={client}
              clip={c}
              index={i}
              total={total}
              selected={c.id === selected}
              onSelect={() => setSelected(c.id === selected ? null : c.id)}
            />
          ))}
        </div>
      )}
      {sel && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
          <span className="text-slate-500">
            Clip src {sel.src_in_frame}–{sel.src_out_frame_exclusive}:
          </span>
          <button
            type="button"
            onClick={() => void splitSelected()}
            className="rounded bg-ink px-2 py-0.5 text-slate-200 hover:bg-edge"
          >
            Split (Mitte)
          </button>
          <button
            type="button"
            onClick={() => void trimSelected(-TRIM_STEP)}
            className="rounded bg-ink px-2 py-0.5 text-slate-200 hover:bg-edge"
          >
            Trim −{TRIM_STEP}
          </button>
          <button
            type="button"
            onClick={() => void trimSelected(TRIM_STEP)}
            className="rounded bg-ink px-2 py-0.5 text-slate-200 hover:bg-edge"
          >
            Trim +{TRIM_STEP}
          </button>
          <button
            type="button"
            onClick={() => void duplicateSelected()}
            className="rounded bg-ink px-2 py-0.5 text-slate-200 hover:bg-edge"
          >
            Duplizieren
          </button>
          <button
            type="button"
            onClick={() => void deleteSelected()}
            className="rounded bg-ink px-2 py-0.5 text-red-300 hover:bg-red-600/40"
          >
            Löschen
          </button>
        </div>
      )}
    </div>
  );
}
