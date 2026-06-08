import { type ReactElement, useCallback, useEffect, useState } from "react";

import type { Export, LauraClient } from "../api";
import { formatBytes } from "../import/format";
import { MediaCard } from "./MediaCard";

const FORMATS = ["mp4", "otio", "edl", "fcpxml", "srt"] as const;

function exportMeta(e: Export): string {
  if (e.status === "ready") return formatBytes(e.size_bytes ?? 0);
  if (e.status === "error") return e.error ?? "Fehler";
  return "rendert…";
}

export function ExportView({
  client,
  projectId,
  timelineId,
}: {
  client: LauraClient;
  projectId: string | null;
  timelineId: string | null;
}): ReactElement {
  const [format, setFormat] = useState<string>("mp4");
  const [exports, setExports] = useState<Export[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    if (!projectId) return;
    try {
      setError(null);
      setExports(await client.listExports(projectId));
    } catch (e) {
      setError(String(e));
    }
  }, [client, projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!exports.some((e) => e.status === "rendering")) return;
    const t = setInterval(() => void load(), 1500);
    return () => clearInterval(t);
  }, [exports, load]);

  const onExport = useCallback(async (): Promise<void> => {
    if (!timelineId) return;
    try {
      await client.renderTimeline(timelineId, format);
      await load();
    } catch (e) {
      setError(String(e));
    }
  }, [client, timelineId, format, load]);

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto p-4">
      <div className="mb-3 flex max-w-md items-center gap-2">
        <select
          value={format}
          onChange={(e) => setFormat(e.target.value)}
          disabled={!timelineId}
          className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-100 disabled:opacity-40"
        >
          {FORMATS.map((f) => (
            <option key={f} value={f}>{f.toUpperCase()}</option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => void onExport()}
          disabled={!timelineId}
          className="rounded bg-sky-600 px-3 py-1 text-xs text-white disabled:opacity-40"
        >
          Exportieren
        </button>
      </div>
      {error && <div className="mb-2 text-xs text-red-400">{error}</div>}
      {exports.length === 0 ? (
        <div className="flex flex-1 items-center justify-center text-sm text-slate-600">
          Noch keine Exporte — wähle ein Format und exportiere die Sequenz.
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
          {exports.map((e) => (
            <MediaCard
              key={e.id}
              title={e.format.toUpperCase()}
              meta={exportMeta(e)}
              onClick={() => undefined}
              onRetry={() => undefined}
            />
          ))}
        </div>
      )}
    </div>
  );
}
