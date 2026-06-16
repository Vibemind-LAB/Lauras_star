import { type ReactElement, useCallback, useEffect, useState } from "react";

import type { CaptionMode, CaptionPosition, CaptionPreset, Export, LauraClient, Project, TimelineClip } from "../api";
import { log } from "../shared/log";
import { formatBytes } from "../import/format";
import { MediaCard } from "./MediaCard";
import type { ExportTarget } from "../App";

const FORMATS = ["mp4", "otio", "edl", "fcpxml", "srt"] as const;

function exportMeta(e: Export): string {
  if (e.status === "ready") return formatBytes(e.size_bytes ?? 0);
  if (e.status === "error") return e.error ?? "Fehler";
  return "rendert…";
}

/** Convert a frame count to mm:ss using integer arithmetic — NDF only (internal frames). */
function framesToMmss(frames: number, rateNum: number, rateDen: number): string {
  if (rateNum <= 0 || rateDen <= 0) return "0:00";
  const totalSeconds = Math.floor((frames * rateDen) / rateNum);
  const mm = Math.floor(totalSeconds / 60);
  const ss = totalSeconds % 60;
  return `${mm}:${ss.toString().padStart(2, "0")}`;
}

/** Derive the highest seq_out_frame_exclusive from a list of flattened clips. */
function totalFrames(clips: TimelineClip[]): number {
  let max = 0;
  for (const c of clips) {
    if (c.seq_out_frame_exclusive > max) max = c.seq_out_frame_exclusive;
  }
  return max;
}

interface SourceInfo {
  clipCount: number;
  duration: string;
}

export function ExportView({
  client,
  projectId,
  project,
  exportTargets,
}: {
  client: LauraClient;
  projectId: string | null;
  project: Project | null;
  exportTargets: ExportTarget[];
}): ReactElement {
  // Default to the first target (sequence preferred, then rough cut per the caller's ordering).
  const [selectedTargetId, setSelectedTargetId] = useState<string | null>(
    exportTargets[0]?.id ?? null,
  );
  const [format, setFormat] = useState<string>("mp4");
  const [exports, setExports] = useState<Export[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [reelHook, setReelHook] = useState<string>("");
  const [reelDisclosure, setReelDisclosure] = useState<boolean>(true);
  const [reelCaptions, setReelCaptions] = useState<boolean>(true);
  const [captionPreset, setCaptionPreset] = useState<CaptionPreset>("reels");
  const [captionMode, setCaptionMode] = useState<CaptionMode>("karaoke");
  const [captionPosition, setCaptionPosition] = useState<CaptionPosition>("bottom");
  const [captionFontsize, setCaptionFontsize] = useState<number>(72);
  const [captionSafeMargin, setCaptionSafeMargin] = useState<number>(250);
  const [reelBusy, setReelBusy] = useState<boolean>(false);
  const [sourceInfo, setSourceInfo] = useState<SourceInfo | null>(null);

  // Keep selectedTargetId in sync when targets list changes (project switch / sequence loads).
  useEffect(() => {
    setSelectedTargetId((prev) => {
      // Keep current selection if still valid.
      if (prev != null && exportTargets.some((t) => t.id === prev)) return prev;
      // Else default to first available.
      return exportTargets[0]?.id ?? null;
    });
  }, [exportTargets]);

  // Derive the active target object.
  const activeTarget = exportTargets.find((t) => t.id === selectedTargetId) ?? null;
  // The timeline id used for both renderTimeline and renderReel.
  const timelineId = activeTarget?.id ?? null;

  // Fetch clip count + duration for the selected timeline whenever it changes.
  useEffect(() => {
    if (!timelineId) {
      setSourceInfo(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const clips = await client.getSequenceFlattened(timelineId);
        if (cancelled) return;
        if (clips.length === 0) {
          setSourceInfo({ clipCount: 0, duration: "0:00" });
          return;
        }
        const dur = project
          ? framesToMmss(totalFrames(clips), project.sequence_rate_num, project.sequence_rate_den)
          : "—";
        setSourceInfo({ clipCount: clips.length, duration: dur });
      } catch {
        // Non-fatal — header will show label without counts.
        if (!cancelled) setSourceInfo(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, timelineId, project]);

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

  const onExportReel = useCallback(async (): Promise<void> => {
    if (!timelineId) return;
    setReelBusy(true);
    try {
      await client.renderReel(timelineId, {
        hookText: reelHook.trim() || null,
        disclosureText: reelDisclosure ? "KI · synthetisch" : "",
        vertical: true,
        captions: reelCaptions,
        captionPreset,
        captionMode,
        captionPosition,
        captionFontsize,
        captionSafeMargin,
      });
      await load();
    } catch (e) {
      log.error("renderReel failed", e);
      setError(String(e));
    } finally {
      setReelBusy(false);
    }
  }, [
    client,
    timelineId,
    reelHook,
    reelDisclosure,
    reelCaptions,
    captionPreset,
    captionMode,
    captionPosition,
    captionFontsize,
    captionSafeMargin,
    load,
  ]);

  // Header description: "Exportiere: <label> · N Clips · mm:ss"
  function renderSourceHeader(): ReactElement | null {
    if (!activeTarget) return null;
    const parts: string[] = [activeTarget.label];
    if (sourceInfo != null) {
      parts.push(`${sourceInfo.clipCount} Clips`);
      parts.push(sourceInfo.duration);
    }
    return (
      <p className="mb-3 text-xs text-slate-300">
        <span className="font-medium text-slate-100">Exportiere:</span>{" "}
        {parts.join(" · ")}
      </p>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto p-4">
      {/* Source selector — only shown when there are multiple choices */}
      {exportTargets.length > 1 && (
        <div className="mb-3 flex max-w-md flex-col gap-1">
          <span className="text-xs font-medium text-slate-400">Quelle</span>
          <div className="flex flex-col gap-1">
            {exportTargets.map((t) => (
              <label key={t.id} className="flex cursor-pointer items-center gap-2 text-xs text-slate-200">
                <input
                  type="radio"
                  name="export-source"
                  value={t.id}
                  checked={selectedTargetId === t.id}
                  onChange={() => setSelectedTargetId(t.id)}
                  className="accent-sky-500"
                />
                {t.label}
              </label>
            ))}
          </div>
        </div>
      )}

      {renderSourceHeader()}

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
      <div className="mb-4 flex max-w-md flex-col gap-2 rounded border border-slate-700 p-3">
        <span className="text-xs font-semibold text-slate-300">Reel 9:16</span>
        <input
          type="text"
          value={reelHook}
          onChange={(e) => setReelHook(e.target.value)}
          placeholder="Hook-Text (optional)"
          disabled={!timelineId}
          className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-100 placeholder:text-slate-500 disabled:opacity-40"
        />
        <label className="flex items-center gap-2 text-xs text-slate-300">
          <input
            type="checkbox"
            checked={reelDisclosure}
            onChange={(e) => setReelDisclosure(e.target.checked)}
            disabled={!timelineId}
            className="disabled:opacity-40"
          />
          KI-Kennzeichnung einblenden
        </label>
        <label className="flex items-center gap-2 text-xs text-slate-300">
          <input
            type="checkbox"
            checked={reelCaptions}
            onChange={(e) => setReelCaptions(e.target.checked)}
            disabled={!timelineId}
            className="disabled:opacity-40"
          />
          Untertitel (Captions) einbrennen
        </label>
        <span className="text-xs text-slate-400">Captions werden aus dem Transkript der Timeline generiert.</span>
        <div className="grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-1 text-xs text-slate-300">
            Caption-Preset
            <select
              aria-label="Caption-Preset"
              value={captionPreset}
              onChange={(e) => setCaptionPreset(e.target.value as CaptionPreset)}
              disabled={!timelineId || !reelCaptions}
              className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-100 disabled:opacity-40"
            >
              <option value="reels">Reels 9:16</option>
              <option value="tiktok">TikTok 9:16</option>
              <option value="shorts">Shorts 9:16</option>
              <option value="wide">16:9</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-300">
            Caption-Modus
            <select
              aria-label="Caption-Modus"
              value={captionMode}
              onChange={(e) => setCaptionMode(e.target.value as CaptionMode)}
              disabled={!timelineId || !reelCaptions}
              className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-100 disabled:opacity-40"
            >
              <option value="karaoke">Karaoke</option>
              <option value="normal">Normal</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-300">
            Caption-Position
            <select
              aria-label="Caption-Position"
              value={captionPosition}
              onChange={(e) => setCaptionPosition(e.target.value as CaptionPosition)}
              disabled={!timelineId || !reelCaptions}
              className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-100 disabled:opacity-40"
            >
              <option value="bottom">Unten</option>
              <option value="middle">Mitte</option>
              <option value="top">Oben</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-300">
            Caption-Groesse
            <input
              aria-label="Caption-Groesse"
              type="number"
              min={24}
              max={160}
              value={captionFontsize}
              onChange={(e) => setCaptionFontsize(Number(e.target.value))}
              disabled={!timelineId || !reelCaptions}
              className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-100 disabled:opacity-40"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-300">
            Safe-Zone
            <input
              aria-label="Safe-Zone"
              type="number"
              min={0}
              max={800}
              value={captionSafeMargin}
              onChange={(e) => setCaptionSafeMargin(Number(e.target.value))}
              disabled={!timelineId || !reelCaptions}
              className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-100 disabled:opacity-40"
            />
          </label>
        </div>
        <button
          type="button"
          onClick={() => void onExportReel()}
          disabled={!timelineId || reelBusy}
          className="self-start rounded bg-sky-600 px-3 py-1 text-xs text-white disabled:opacity-40"
        >
          {reelBusy ? "rendert…" : "Reel 9:16"}
        </button>
      </div>
      {error && <div className="mb-2 text-xs text-red-400">{error}</div>}
      {exports.length === 0 ? (
        <div className="flex flex-1 items-center justify-center text-sm text-slate-600">
          Noch keine Exporte — wähle ein Format und exportiere die Sequenz.
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
          {exports.map((e) => {
            const isReady = e.status === "ready" && Boolean(e.path);
            const openExport = isReady
              ? (): void => {
                  if (!window.laura) return;
                  void window.laura.openPath(e.path!).then((err) => {
                    if (err) setError(err);
                  });
                }
              : (): void => undefined;
            const exportMenu = isReady ? (
              <div className="flex shrink-0 flex-col gap-1">
                <button
                  type="button"
                  onClick={() => {
                    if (!window.laura) return;
                    void window.laura.revealPath(e.path!).then((err) => {
                      if (err) setError(err);
                    });
                  }}
                  className="rounded bg-slate-700 px-2 py-0.5 text-[10px] text-slate-200 hover:bg-slate-600"
                >
                  Im Ordner zeigen
                </button>
                <button
                  type="button"
                  onClick={() => {
                    void navigator.clipboard.writeText(e.path!).catch((err: unknown) => {
                      setError(String(err));
                    });
                  }}
                  className="rounded bg-slate-700 px-2 py-0.5 text-[10px] text-slate-200 hover:bg-slate-600"
                >
                  Pfad kopieren
                </button>
              </div>
            ) : undefined;
            return (
              <MediaCard
                key={e.id}
                title={e.format.toUpperCase()}
                meta={exportMeta(e)}
                onClick={openExport}
                onRetry={() => undefined}
                menu={exportMenu}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}
