import { type ReactElement, useCallback, useEffect, useRef, useState } from "react";

import type { CaptionMode, CaptionPosition, CaptionPreset, Export, JobStatus, LauraClient, Project, TimelineClip } from "../api";
import { log } from "../shared/log";
import { formatBytes } from "../import/format";
import { CaptionPreview } from "./CaptionPreview";
import type { ExportTarget } from "../App";

/** Human-readable hint shown below the format picker. */
const FORMAT_HINT: Record<string, string> = {
  mp4:    "Fertiges, teilbares Video",
  otio:   "Projektaustausch für ein anderes Schnittprogramm (verlustarm)",
  edl:    "Schnittliste für ein anderes NLE",
  fcpxml: "Schnittliste für ein anderes NLE",
  srt:    "Untertitel-Datei (separat)",
};

function exportMeta(e: Export): string {
  if (e.status === "ready") return formatBytes(e.size_bytes ?? 0);
  if (e.status === "error") return e.error ?? "Fehler";
  return "rendert…";
}

/**
 * Attempt to parse a numeric progress fraction [0..1] from a job's result_json
 * or any future progress field. Backend does not yet emit render progress, so
 * this is purely defensive/forward-compatible. Returns null when no parseable
 * value is found — callers fall back to indeterminate display.
 */
function parseJobProgress(job: JobStatus): number | null {
  // result_json may carry partial progress in some future backends.
  for (const raw of [job.result_json]) {
    if (typeof raw !== "string" || raw === "") continue;
    try {
      const parsed = JSON.parse(raw) as unknown;
      if (typeof parsed !== "object" || parsed === null) continue;
      const obj = parsed as Record<string, unknown>;
      // Try "percent" (0-100) first, then "fraction" (0-1), then "processed"/"total".
      if (typeof obj.percent === "number") return Math.min(1, Math.max(0, obj.percent / 100));
      if (typeof obj.fraction === "number") return Math.min(1, Math.max(0, obj.fraction));
      if (typeof obj.processed === "number" && typeof obj.total === "number" && obj.total > 0) {
        return Math.min(1, Math.max(0, obj.processed / obj.total));
      }
    } catch {
      // Malformed JSON — ignore.
    }
  }
  return null;
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
  // Provisional default; the effect below upgrades it to the first source that actually has
  // clips (so a freshly-imported asset exports its populated rough cut, not the empty sequence).
  const [selectedTargetId, setSelectedTargetId] = useState<string | null>(
    exportTargets[0]?.id ?? null,
  );
  // Clip count per target (all targets), used to pick a non-empty default + label each option.
  const [targetCounts, setTargetCounts] = useState<Record<string, number>>({});
  // Set once the user picks a source explicitly, so auto-selection stops overriding them.
  const userPickedRef = useRef(false);
  const [format, setFormat] = useState<string>("mp4");
  const [exports, setExports] = useState<Export[]>([]);
  const [error, setError] = useState<string | null>(null);
  /**
   * Maps export_id → job_id for renders fired in the current session.
   * Exports from a previous session have no entry here and show plain "rendert…".
   */
  const [jobByExport, setJobByExport] = useState<Record<string, string>>({});
  /**
   * Caches the latest JobStatus fetched per job_id during the active poll loop.
   * Key = job_id, value = latest JobStatus snapshot.
   */
  const jobStatusCacheRef = useRef<Record<string, JobStatus>>({});
  /** Re-render trigger for the job-status cache — incremented after each poll batch. */
  const [jobPollTick, setJobPollTick] = useState<number>(0);
  const [reelHook, setReelHook] = useState<string>("");
  // reelDisclosure removed — KI disclosure is mandatory (D5 / EU AI Act).
  const [reelCaptions, setReelCaptions] = useState<boolean>(true);
  const [captionPreset, setCaptionPreset] = useState<CaptionPreset>("reels");
  const [captionMode, setCaptionMode] = useState<CaptionMode>("karaoke");
  const [captionPosition, setCaptionPosition] = useState<CaptionPosition>("bottom");
  const [captionFontsize, setCaptionFontsize] = useState<number>(72);
  const [captionSafeMargin, setCaptionSafeMargin] = useState<number>(250);
  /** Optional hard cap on the reel length in seconds (platform max-durations); null = no cap. */
  const [reelMaxDuration, setReelMaxDuration] = useState<number | null>(null);
  const [reelBusy, setReelBusy] = useState<boolean>(false);
  const [exportBusy, setExportBusy] = useState<boolean>(false);
  const [sourceInfo, setSourceInfo] = useState<SourceInfo | null>(null);
  /** asset_id of the first flattened clip — used for the caption preview poster frame. */
  const [posterAssetId, setPosterAssetId] = useState<string | null>(null);

  // Kind-aware clip fetch: a sequence resolves its scene references via /flattened, whereas a
  // rough-cut timeline carries its clips directly (getSequenceFlattened returns [] for it).
  const fetchTargetClips = useCallback(
    async (t: ExportTarget): Promise<TimelineClip[]> =>
      t.kind === "sequence"
        ? client.getSequenceFlattened(t.id)
        : (await client.getTimeline(t.id)).clips,
    [client],
  );

  // Fetch the clip count of every target so we can default to a non-empty source and label
  // each option. Targets are few (sequence + per-asset rough cut); each fetch is local + cheap.
  useEffect(() => {
    let cancelled = false;
    userPickedRef.current = false; // a new target set -> auto-selection may run again
    void (async () => {
      const entries = await Promise.all(
        exportTargets.map(async (t) => {
          try {
            return [t.id, (await fetchTargetClips(t)).length] as const;
          } catch {
            return [t.id, 0] as const;
          }
        }),
      );
      if (!cancelled) setTargetCounts(Object.fromEntries(entries));
    })();
    return () => {
      cancelled = true;
    };
  }, [fetchTargetClips, exportTargets]);

  // Keep selectedTargetId valid and default to the first NON-EMPTY source — so landing on
  // Export right after import selects the populated rough cut instead of the still-empty
  // assembled sequence (which would export 0 clips). An explicit user pick wins.
  useEffect(() => {
    setSelectedTargetId((prev) => {
      const valid = prev != null && exportTargets.some((t) => t.id === prev);
      if (userPickedRef.current && valid) return prev;
      const firstNonEmpty = exportTargets.find((t) => (targetCounts[t.id] ?? 0) > 0);
      if (firstNonEmpty) return firstNonEmpty.id;
      return valid ? prev : (exportTargets[0]?.id ?? null);
    });
  }, [exportTargets, targetCounts]);

  // Derive the active target object.
  const activeTarget = exportTargets.find((t) => t.id === selectedTargetId) ?? null;
  // The timeline id used for both renderTimeline and renderReel.
  const timelineId = activeTarget?.id ?? null;

  // Fetch clip count + duration for the selected timeline whenever it changes.
  useEffect(() => {
    if (!activeTarget) {
      setSourceInfo(null);
      setPosterAssetId(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const clips = await fetchTargetClips(activeTarget);
        if (cancelled) return;
        // Capture first clip's asset_id for the caption preview poster frame.
        setPosterAssetId(clips[0]?.asset_id ?? null);
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
        if (!cancelled) {
          setSourceInfo(null);
          setPosterAssetId(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeTarget, fetchTargetClips, project]);

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
    setExportBusy(true);
    try {
      const result = await client.renderTimeline(timelineId, format);
      setJobByExport((prev) => ({ ...prev, [result.export_id]: result.job_id }));
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setExportBusy(false);
    }
  }, [client, timelineId, format, load]);

  const onExportReel = useCallback(async (): Promise<void> => {
    if (!timelineId) return;
    setReelBusy(true);
    try {
      const result = await client.renderReel(timelineId, {
        hookText: reelHook.trim() || null,
        disclosureText: "KI · synthetisch",
        vertical: true,
        captions: reelCaptions,
        captionPreset,
        captionMode,
        captionPosition,
        captionFontsize,
        captionSafeMargin,
        maxDurationSeconds: reelMaxDuration,
      });
      setJobByExport((prev) => ({ ...prev, [result.export_id]: result.job_id }));
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
    reelCaptions,
    captionPreset,
    captionMode,
    captionPosition,
    captionFontsize,
    captionSafeMargin,
    reelMaxDuration,
    load,
  ]);

  // Poll job status for all in-flight renders that have a known job_id this session.
  // Runs on the same 1.5s cadence as the export list poll; piggybacks on the exports
  // useEffect timer rather than adding a second interval.
  useEffect(() => {
    const renderingExportIds = exports
      .filter((e) => e.status === "rendering")
      .map((e) => e.id);

    // Collect job_ids we need to poll this cycle.
    const jobIds = renderingExportIds
      .map((eid) => jobByExport[eid])
      .filter((jid): jid is string => typeof jid === "string");

    if (jobIds.length === 0) return;

    let cancelled = false;
    const poll = (): void => {
      const fetches = jobIds.map((jid) =>
        client.getJob(jid).then((job) => {
          if (cancelled) return;
          jobStatusCacheRef.current = { ...jobStatusCacheRef.current, [jid]: job };
        }).catch((err: unknown) => {
          log.error("ExportView job poll failed", jid, err instanceof Error ? err.message : String(err));
        }),
      );
      void Promise.allSettled(fetches).then(() => {
        if (!cancelled) setJobPollTick((t) => t + 1);
      });
    };

    poll();
    const intervalId = setInterval(poll, 1500);
    return () => {
      cancelled = true;
      clearInterval(intervalId);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps -- jobStatusCacheRef is a ref, intentionally excluded
  }, [client, exports, jobByExport]);

  const onCancelJob = useCallback(async (jobId: string): Promise<void> => {
    try {
      await client.cancelJob(jobId);
      await load();
    } catch (e) {
      setError(String(e));
    }
  }, [client, load]);

  // Header description: "Exportiere: <label> · N Clips · mm:ss"
  function renderSourceHeader(): ReactElement | null {
    if (!activeTarget) return null;
    const parts: string[] = [activeTarget.label];
    if (sourceInfo != null) {
      parts.push(`${sourceInfo.clipCount} Clips`);
      parts.push(sourceInfo.duration);
    }
    return (
      <p className="mb-3 text-xs text-content-muted">
        <span className="font-medium text-content-strong">Exportiere:</span>{" "}
        {parts.join(" · ")}
      </p>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto p-4">
      {/* Source selector — only shown when there are multiple choices */}
      {exportTargets.length > 1 && (
        <div className="mb-3 flex max-w-md flex-col gap-1">
          <span className="text-xs font-medium text-content-muted">Quelle</span>
          <div className="flex flex-col gap-1">
            {exportTargets.map((t) => {
              const count = targetCounts[t.id];
              return (
                <label key={t.id} className="flex cursor-pointer items-center gap-2 text-xs text-content-strong">
                  <input
                    type="radio"
                    name="export-source"
                    value={t.id}
                    checked={selectedTargetId === t.id}
                    onChange={() => {
                      userPickedRef.current = true;
                      setSelectedTargetId(t.id);
                    }}
                    className="accent-accent"
                  />
                  {t.label}
                  {count != null && (
                    <span className="text-content-faint">{count > 0 ? `· ${count} Clips` : "· leer"}</span>
                  )}
                </label>
              );
            })}
          </div>
        </div>
      )}

      {renderSourceHeader()}

      <div className="mb-3 flex max-w-md flex-col gap-1">
        <div className="flex items-center gap-2">
          <select
            value={format}
            onChange={(e) => setFormat(e.target.value)}
            disabled={!timelineId}
            className="rounded bg-surface-2 px-2 py-1 text-xs text-content-strong disabled:opacity-40"
          >
            <optgroup label="Fertiges Video">
              <option value="mp4">MP4</option>
            </optgroup>
            <optgroup label="Für anderes Schnittprogramm">
              <option value="otio">OTIO</option>
              <option value="edl">EDL</option>
              <option value="fcpxml">FCPXML</option>
            </optgroup>
            <optgroup label="Untertitel">
              <option value="srt">SRT</option>
            </optgroup>
          </select>
          <button
            type="button"
            onClick={() => void onExport()}
            disabled={!timelineId || exportBusy}
            className="rounded bg-accent px-3 py-1 text-xs text-white disabled:opacity-40"
          >
            {exportBusy ? "rendert…" : "Exportieren"}
          </button>
        </div>
        {format in FORMAT_HINT && (
          <p className="text-[10px] text-content-muted">{FORMAT_HINT[format]}</p>
        )}
      </div>
      {/* One-click platform presets — set caption state AND call renderReel with the correct
          values in the same click. We pass the override values directly to client.renderReel
          so that the render is not subject to stale-closure timing from setState. The setState
          calls keep the caption controls visually in sync for subsequent manual edits. */}
      <div className="mb-2 flex max-w-md gap-2">
        {(
          [
            { label: "Reels",  preset: "reels"  as const },
            { label: "TikTok", preset: "tiktok" as const },
            { label: "Shorts", preset: "shorts" as const },
          ] satisfies { label: string; preset: CaptionPreset }[]
        ).map(({ label, preset }) => (
          <button
            key={preset}
            type="button"
            disabled={!timelineId || reelBusy}
            onClick={() => {
              if (!timelineId) return;
              // Sync controls so manual controls reflect the last-used preset.
              setCaptionPreset(preset);
              setCaptionPosition("bottom");
              setReelCaptions(true);
              // Fire render with the override values directly — avoids stale-closure issue.
              setReelBusy(true);
              void client.renderReel(timelineId, {
                hookText: reelHook.trim() || null,
                disclosureText: "KI · synthetisch",
                vertical: true,
                captions: true,
                captionPreset: preset,
                captionMode,
                captionPosition: "bottom",
                captionFontsize,
                captionSafeMargin,
                maxDurationSeconds: reelMaxDuration,
              }).then((result) => {
                setJobByExport((prev) => ({ ...prev, [result.export_id]: result.job_id }));
                return load();
              }).catch((e: unknown) => {
                log.error("renderReel (preset) failed", e);
                setError(String(e));
              }).finally(() => {
                setReelBusy(false);
              });
            }}
            className="rounded bg-violet-700 px-3 py-1 text-xs text-white hover:bg-violet-600 disabled:opacity-40"
          >
            {label}
          </button>
        ))}
      </div>
      <div className="mb-4 flex max-w-md flex-col gap-2 rounded border border-bezel p-3">
        <span className="text-xs font-semibold text-content-muted">Reel 9:16</span>
        <input
          type="text"
          value={reelHook}
          onChange={(e) => setReelHook(e.target.value)}
          placeholder="Hook-Text (optional)"
          disabled={!timelineId}
          className="rounded bg-surface-2 px-2 py-1 text-xs text-content-strong placeholder:text-content-faint disabled:opacity-40"
        />
        <label className="flex items-center gap-2 text-xs text-content-muted">
          Max. Dauer (Sek.)
          <input
            type="number"
            min={1}
            max={600}
            value={reelMaxDuration ?? ""}
            onChange={(e) =>
              setReelMaxDuration(e.target.value === "" ? null : Math.max(1, Number(e.target.value)))}
            placeholder="kein Limit"
            disabled={!timelineId}
            className="w-28 rounded bg-surface-2 px-2 py-1 text-xs text-content-strong placeholder:text-content-faint disabled:opacity-40"
          />
        </label>
        <div
          className="flex items-center gap-2 text-xs text-content-muted"
          aria-label="KI-Kennzeichnung verpflichtend"
        >
          <span aria-hidden className="text-content-strong">●</span>
          KI-Kennzeichnung wird immer eingeblendet (EU AI Act)
        </div>
        <label className="flex items-center gap-2 text-xs text-content-muted">
          <input
            type="checkbox"
            checked={reelCaptions}
            onChange={(e) => setReelCaptions(e.target.checked)}
            disabled={!timelineId}
            className="disabled:opacity-40"
          />
          Untertitel (Captions) einbrennen
        </label>
        <span className="text-xs text-content-muted">Captions werden aus dem Transkript der Timeline generiert.</span>
        {/* Live 9:16 caption preview — updates as controls change, no render needed. */}
        <CaptionPreview
          client={client}
          posterAssetId={posterAssetId}
          posterFrame={0}
          hook={reelHook}
          disclosure={true}
          captionsOn={reelCaptions}
          mode={captionMode}
          position={captionPosition}
          fontsize={captionFontsize}
          safeMargin={captionSafeMargin}
        />
        <div className="grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-1 text-xs text-content-muted">
            Caption-Preset
            <select
              aria-label="Caption-Preset"
              value={captionPreset}
              onChange={(e) => setCaptionPreset(e.target.value as CaptionPreset)}
              disabled={!timelineId || !reelCaptions}
              className="rounded bg-surface-2 px-2 py-1 text-xs text-content-strong disabled:opacity-40"
            >
              <option value="reels">Reels 9:16</option>
              <option value="tiktok">TikTok 9:16</option>
              <option value="shorts">Shorts 9:16</option>
              <option value="wide">16:9</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-content-muted">
            Caption-Modus
            <select
              aria-label="Caption-Modus"
              value={captionMode}
              onChange={(e) => setCaptionMode(e.target.value as CaptionMode)}
              disabled={!timelineId || !reelCaptions}
              className="rounded bg-surface-2 px-2 py-1 text-xs text-content-strong disabled:opacity-40"
            >
              <option value="karaoke">Karaoke</option>
              <option value="normal">Normal</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-content-muted">
            Caption-Position
            <select
              aria-label="Caption-Position"
              value={captionPosition}
              onChange={(e) => setCaptionPosition(e.target.value as CaptionPosition)}
              disabled={!timelineId || !reelCaptions}
              className="rounded bg-surface-2 px-2 py-1 text-xs text-content-strong disabled:opacity-40"
            >
              <option value="bottom">Unten</option>
              <option value="middle">Mitte</option>
              <option value="top">Oben</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-content-muted">
            Caption-Groesse
            <input
              aria-label="Caption-Groesse"
              type="number"
              min={24}
              max={160}
              value={captionFontsize}
              onChange={(e) => setCaptionFontsize(Number(e.target.value))}
              disabled={!timelineId || !reelCaptions}
              className="rounded bg-surface-2 px-2 py-1 text-xs text-content-strong disabled:opacity-40"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-content-muted">
            Safe-Zone
            <input
              aria-label="Safe-Zone"
              type="number"
              min={0}
              max={800}
              value={captionSafeMargin}
              onChange={(e) => setCaptionSafeMargin(Number(e.target.value))}
              disabled={!timelineId || !reelCaptions}
              className="rounded bg-surface-2 px-2 py-1 text-xs text-content-strong disabled:opacity-40"
            />
          </label>
        </div>
        <button
          type="button"
          onClick={() => void onExportReel()}
          disabled={!timelineId || reelBusy}
          className="self-start rounded bg-accent px-3 py-1 text-xs text-white disabled:opacity-40"
        >
          {reelBusy ? "rendert…" : "Reel 9:16"}
        </button>
      </div>
      {error && <div className="mb-2 text-xs text-status-err">{error}</div>}
      {exports.length === 0 ? (
        <div className="flex flex-1 items-center justify-center text-sm text-content-faint">
          Noch keine Exporte — wähle ein Format und exportiere die Sequenz.
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
          {exports.map((e) => {
            const isReady = e.status === "ready" && Boolean(e.path);
            const isRendering = e.status === "rendering";
            const knownJobId = jobByExport[e.id];
            // Resolve cached job status. jobPollTick is read here (not just in deps) to
            // force React to re-render the map on each poll cycle as the ref updates.
            const cachedJob: JobStatus | undefined =
              jobPollTick >= 0 && typeof knownJobId === "string"
                ? jobStatusCacheRef.current[knownJobId]
                : undefined;
            const progress: number | null =
              cachedJob !== undefined ? parseJobProgress(cachedJob) : null;

            const openExport = isReady
              ? (): void => {
                  if (!window.laura) return;
                  void window.laura.openPath(e.path!).then((err) => {
                    if (err) setError(err);
                  });
                }
              : (): void => undefined;

            // Build the card meta string — add "%" hint when progress is known.
            const metaText = isRendering && progress !== null
              ? `rendert… ${Math.round(progress * 100)}%`
              : exportMeta(e);

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
                  className="rounded bg-surface-2 px-2 py-0.5 text-[10px] text-content-strong hover:bg-surface-2"
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
                  className="rounded bg-surface-2 px-2 py-0.5 text-[10px] text-content-strong hover:bg-surface-2"
                >
                  Pfad kopieren
                </button>
              </div>
            ) : isRendering && typeof knownJobId === "string" ? (
              <button
                type="button"
                onClick={() => void onCancelJob(knownJobId)}
                className="shrink-0 self-start rounded bg-red-700 px-2 py-0.5 text-[10px] text-white hover:bg-red-600"
              >
                Abbrechen
              </button>
            ) : undefined;

            // Progress bar node — shown below title/meta for in-flight renders this session.
            const progressBar = isRendering && typeof knownJobId === "string" ? (
              <div className="mx-2 mb-2 h-1 overflow-hidden rounded-full bg-surface-2">
                {progress !== null ? (
                  <div
                    className="h-full rounded-full bg-accent transition-all duration-500"
                    style={{ width: `${Math.round(progress * 100)}%` }}
                  />
                ) : (
                  /* Indeterminate shimmer — no numeric progress available from backend yet. */
                  <div className="h-full w-1/3 animate-pulse rounded-full bg-accent/60" />
                )}
              </div>
            ) : undefined;

            return (
              <div key={e.id} className="flex flex-col overflow-hidden rounded-lg border border-bezel bg-surface-1">
                <button
                  type="button"
                  onClick={openExport}
                  className="flex aspect-video w-full items-center justify-center bg-black text-content-faint"
                >
                  <span className="text-xs">kein Vorschaubild</span>
                </button>
                <div className="flex items-start justify-between gap-2 p-2">
                  <button type="button" onClick={openExport} className="min-w-0 text-left">
                    <div className="truncate text-sm text-content-strong">{e.format.toUpperCase()}</div>
                    {metaText && <div className="truncate text-[11px] text-content-faint">{metaText}</div>}
                  </button>
                  {exportMenu}
                </div>
                {progressBar}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}



