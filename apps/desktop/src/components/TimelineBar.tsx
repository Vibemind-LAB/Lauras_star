import { type ReactElement, useState } from "react";

import { type ExportFormat, type LauraClient, type Timeline, type TimelineClip } from "../api";

const EXPORT_FORMATS: { fmt: ExportFormat; label: string; ext: string }[] = [
  { fmt: "otio", label: "OTIO", ext: "otio" },
  { fmt: "edl", label: "EDL", ext: "edl" },
  { fmt: "fcp7xml", label: "FCP7-XML", ext: "xml" },
  { fmt: "fcpxml", label: "FCPXML", ext: "fcpxml" },
];

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

  if (!timeline) {
    return (
      <div className="flex h-20 items-center border-t border-edge bg-panel px-5 text-xs text-slate-600">
        Rough Cut — wähle ein Projekt.
      </div>
    );
  }

  const total = timeline.clips.reduce((m, c) => Math.max(m, c.seq_out_frame_exclusive), 0);

  async function deleteClip(clip: TimelineClip): Promise<void> {
    if (!timeline) return;
    await client.applyOperation(timeline.id, {
      op: "delete",
      seq_in_frame: clip.seq_in_frame,
      seq_out_frame_exclusive: clip.seq_out_frame_exclusive,
    });
    onChange();
  }

  async function exportAs(fmt: ExportFormat, ext: string): Promise<void> {
    if (!timeline) return;
    setError(null);
    try {
      const result = await client.exportTimeline(timeline.id, fmt);
      if (result.content) {
        await window.laura.saveTextFile(`${timeline.name}.${ext}`, result.content);
      }
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="border-t border-edge bg-panel px-5 py-3">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-slate-500">
          Rough Cut · {timeline.name}
        </span>
        <span className="flex items-center gap-2">
          {timeline.clips.length > 0 &&
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
            {timeline.clips.length} Clips · {total} frames
          </span>
        </span>
      </div>
      {error && <div className="mb-1 text-xs text-red-400">{error}</div>}
      {timeline.clips.length === 0 ? (
        <div className="flex h-12 items-center justify-center rounded-md border border-dashed border-edge text-xs text-slate-600">
          Klicke einen Shot oder Transkript-Satz an, um ihn anzuhängen.
        </div>
      ) : (
        <div className="flex h-12 w-full gap-px overflow-hidden rounded-md">
          {timeline.clips.map((c, i) => {
            const pct = total > 0 ? ((c.seq_out_frame_exclusive - c.seq_in_frame) / total) * 100 : 0;
            return (
              <button
                key={c.id}
                type="button"
                onClick={() => void deleteClip(c)}
                title={`Clip ${i + 1} · src ${c.src_in_frame}–${c.src_out_frame_exclusive} (Klick = löschen)`}
                style={{ width: `${pct}%` }}
                className={`flex items-center justify-center text-[10px] text-slate-100 ${
                  i % 2 === 0 ? "bg-sky-700/50" : "bg-sky-500/40"
                } hover:bg-red-600/50`}
              >
                {i + 1}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
