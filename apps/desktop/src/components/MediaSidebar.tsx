import { type ReactElement, useEffect, useRef, useState } from "react";

import { type AiProvenanceManifest, type AnalysisRun, type Asset, type LauraClient } from "../api";
import { log } from "../shared/log";

// ---------------------------------------------------------------------------
// Per-item thumbnail with object-URL lifecycle (mirrors ShotStrip.tsx pattern)
// ---------------------------------------------------------------------------

function AssetThumb({
  client,
  assetId,
  index,
}: {
  client: LauraClient;
  assetId: string;
  index: number;
}): ReactElement {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    client
      .assetFrameUrl(assetId, 0)
      .then((u) => {
        if (!active) {
          URL.revokeObjectURL(u);
          return;
        }
        objectUrl = u;
        setUrl(u);
      })
      .catch(() => {
        /* network/backend not ready — keep the colour-block fallback */
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [client, assetId]);

  return (
    <span
      className="h-9 w-16 shrink-0 overflow-hidden rounded border border-bezel"
      aria-hidden="true"
    >
      {url ? (
        <img src={url} alt="" className="h-full w-full object-cover" />
      ) : (
        <span
          className={`block h-full w-full ${index % 2 === 0 ? "bg-sky-700/40" : "bg-sky-500/30"}`}
        />
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Per-item analysis status + trigger
// ---------------------------------------------------------------------------

type ItemAnalysisState =
  | { kind: "loading" }
  | { kind: "idle" }
  | { kind: "running"; label: string }
  | { kind: "done" }
  | { kind: "failed"; message: string };

type ProvenanceState =
  | { kind: "hidden" }
  | { kind: "loading" }
  | { kind: "ready"; manifest: AiProvenanceManifest }
  | { kind: "missing"; message: string };

const POLL_INTERVAL_MS = 1500;
// Analysis of a long video (Whisper transcription of a 30-min clip) runs for many
// minutes; poll until the run actually reaches a terminal status instead of giving up
// on a fixed deadline (the old 3-min cap surfaced a bogus "Timeout"). Only consecutive
// *errors* — not slowness — end the poll.
const MAX_CONSECUTIVE_POLL_ERRORS = 5;

function shortSha(manifest: AiProvenanceManifest): string | null {
  if (!manifest.media_sha256) return null;
  return manifest.media_sha256.slice(0, 12);
}

function MediaSidebarItem({
  client,
  asset,
  index,
  isSelected,
  onSelect,
  onDelete,
}: {
  client: LauraClient;
  asset: Asset;
  index: number;
  isSelected: boolean;
  onSelect: (assetId: string) => void;
  onDelete?: (assetId: string) => void;
}): ReactElement {
  const [analysisState, setAnalysisState] = useState<ItemAnalysisState>({ kind: "loading" });
  const [provenanceState, setProvenanceState] = useState<ProvenanceState>({ kind: "hidden" });
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  // Fetch latest analysis run on mount
  useEffect(() => {
    mountedRef.current = true;
    let cancelled = false;
    client
      .getLatestAnalysis(asset.id)
      .then((run: AnalysisRun | null) => {
        if (cancelled) return;
        if (run && run.status === "succeeded") {
          setAnalysisState({ kind: "done" });
        } else if (run && run.status === "failed") {
          setAnalysisState({ kind: "failed", message: "Analyse fehlgeschlagen" });
        } else if (run && run.status !== "succeeded" && run.status !== "failed") {
          // Background analysis already in flight (queued/running): show progress and poll
          // to terminal instead of offering a misleading second "Analysieren" trigger.
          setAnalysisState({ kind: "running", label: `${run.status}…` });
          schedulePoll(0);
        } else {
          setAnalysisState({ kind: "idle" });
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        log.warn("MediaSidebar: getLatestAnalysis failed", err);
        setAnalysisState({ kind: "idle" });
      });
    return () => {
      cancelled = true;
      mountedRef.current = false;
      if (pollRef.current != null) clearTimeout(pollRef.current);
    };
  }, [client, asset.id]);

  useEffect(() => {
    if (!isSelected || !asset.synthetic) {
      setProvenanceState({ kind: "hidden" });
      return;
    }
    let cancelled = false;
    setProvenanceState({ kind: "loading" });
    client
      .getAssetProvenance(asset.id)
      .then((manifest) => {
        if (cancelled) return;
        setProvenanceState({ kind: "ready", manifest });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        log.warn("MediaSidebar: provenance lookup failed", err);
        setProvenanceState({ kind: "missing", message: "Manifest fehlt" });
      });
    return () => {
      cancelled = true;
    };
  }, [asset.id, asset.synthetic, client, isSelected]);

  // Poll until the analysis run reaches a terminal status. `errorStreak` counts only
  // consecutive network failures (not slowness): a long transcription must not be
  // abandoned just because it is taking minutes. The mount effect's cleanup clears
  // pollRef, so an asset switch / unmount stops the chain.
  function schedulePoll(errorStreak: number): void {
    pollRef.current = setTimeout(() => {
      if (!mountedRef.current) return;
      client
        .getLatestAnalysis(asset.id)
        .then((run: AnalysisRun | null) => {
          if (!mountedRef.current) return;
          if (run && run.status === "succeeded") {
            setAnalysisState({ kind: "done" });
          } else if (run && run.status === "failed") {
            setAnalysisState({ kind: "failed", message: "Analyse fehlgeschlagen" });
          } else {
            // queued / running / no run row yet — keep waiting, however long it takes.
            setAnalysisState({ kind: "running", label: run ? `${run.status}…` : "analysiere…" });
            schedulePoll(0);
          }
        })
        .catch((err: unknown) => {
          if (!mountedRef.current) return;
          if (errorStreak + 1 >= MAX_CONSECUTIVE_POLL_ERRORS) {
            log.warn("MediaSidebar: poll failed repeatedly", err);
            setAnalysisState({ kind: "failed", message: "Verbindungsfehler" });
            return;
          }
          // Transient hiccup — keep showing progress and retry.
          setAnalysisState({ kind: "running", label: "analysiere…" });
          schedulePoll(errorStreak + 1);
        });
    }, POLL_INTERVAL_MS);
  }

  function handleAnalyse(): void {
    setAnalysisState({ kind: "running", label: "analysiere…" });
    client
      .startAnalysis(asset.id, {
        scene: true,
        asr: true,
        diarize: false,
        align: false,
        detector: "adaptive",
      })
      .then(() => {
        if (!mountedRef.current) return;
        schedulePoll(0);
      })
      .catch((err: unknown) => {
        if (!mountedRef.current) return;
        log.error("MediaSidebar: startAnalysis failed", err);
        setAnalysisState({ kind: "failed", message: String(err) });
      });
  }

  const rowBase =
    "flex items-center gap-2 rounded px-1 py-1 text-left transition cursor-pointer select-none";
  const rowActive = "bg-sky-600/20 ring-1 ring-sky-500/40";
  const rowInactive = "hover:bg-surface-2";

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSelect(asset.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onSelect(asset.id);
      }}
      aria-pressed={isSelected}
      className={`${rowBase} ${isSelected ? rowActive : rowInactive}`}
    >
      <AssetThumb client={client} assetId={asset.id} index={index} />
      <div className="min-w-0 flex-1">
        <div
          className="truncate text-xs text-content-strong"
          title={asset.display_name}
        >
          {asset.display_name}
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-1">
          {asset.synthetic && (
            <span className="rounded border border-cyan-900/70 bg-cyan-950/40 px-1.5 py-0.5 text-[10px] font-medium text-cyan-200">
              KI · {asset.ai_effect ?? "synthetisch"}
            </span>
          )}
          {analysisState.kind === "loading" && (
            <span className="text-[10px] text-content-faint">…</span>
          )}
          {analysisState.kind === "idle" && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                handleAnalyse();
              }}
              className="rounded bg-sky-700 px-2 py-0.5 text-[10px] text-white hover:bg-sky-600"
            >
              Analysieren
            </button>
          )}
          {analysisState.kind === "running" && (
            <span className="text-[10px] text-content-muted">{analysisState.label}</span>
          )}
          {analysisState.kind === "done" && (
            <span className="text-[10px] text-status-ok">✓ analysiert</span>
          )}
          {analysisState.kind === "failed" && (
            <>
              <span className="mr-1 text-[10px] text-status-err">{analysisState.message}</span>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  handleAnalyse();
                }}
                className="rounded bg-sky-700 px-2 py-0.5 text-[10px] text-white hover:bg-sky-600"
              >
                Analysieren
              </button>
            </>
          )}
        </div>
        {provenanceState.kind !== "hidden" && (
          <div className="mt-1 rounded border border-cyan-950 bg-surface-0/50 px-2 py-1 text-[10px] text-content-muted">
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium text-cyan-200">Provenance</span>
              {provenanceState.kind === "loading" && <span>lädt…</span>}
              {provenanceState.kind === "missing" && (
                <span className="text-status-warn">{provenanceState.message}</span>
              )}
            </div>
            {provenanceState.kind === "ready" && (
              <div className="mt-0.5 flex flex-col gap-0.5">
                <span>{provenanceState.manifest.schema}</span>
                <span>{provenanceState.manifest.ai_effect ?? asset.ai_effect ?? "synthetisch"}</span>
                {shortSha(provenanceState.manifest) && (
                  <span>sha256 {shortSha(provenanceState.manifest)}</span>
                )}
              </div>
            )}
          </div>
        )}
      </div>
      {onDelete && (
        <button
          type="button"
          title="Medium löschen"
          aria-label="Medium löschen"
          onClick={(e) => {
            e.stopPropagation();
            if (window.confirm(`Medium „${asset.display_name}" löschen?`)) onDelete(asset.id);
          }}
          className="shrink-0 rounded px-1 text-sm text-content-faint hover:bg-red-600/40 hover:text-red-200"
        >
          ×
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export interface MediaSidebarProps {
  client: LauraClient;
  assets: Asset[];
  selectedAssetId: string | null;
  onSelect: (assetId: string) => void;
  /** When provided, each media row gets a × delete affordance (confirms first). */
  onDelete?: (assetId: string) => void;
}

export function MediaSidebar({
  client,
  assets,
  selectedAssetId,
  onSelect,
  onDelete,
}: MediaSidebarProps): ReactElement {
  return (
    <aside className="flex w-56 shrink-0 flex-col gap-1 overflow-y-auto border-r border-bezel bg-surface-0 p-2">
      <div className="flex items-center justify-between pb-1">
        <span className="text-xs font-semibold text-content-muted">Projekt-Medien</span>
        <span className="text-[10px] text-content-faint">{assets.length}</span>
      </div>
      {assets.length === 0 ? (
        <p className="text-[11px] text-content-faint">
          Keine Videos — in Download/Import hinzufügen.
        </p>
      ) : (
        assets.map((asset, i) => (
          <MediaSidebarItem
            key={asset.id}
            client={client}
            asset={asset}
            index={i}
            isSelected={asset.id === selectedAssetId}
            onSelect={onSelect}
            onDelete={onDelete}
          />
        ))
      )}
    </aside>
  );
}
