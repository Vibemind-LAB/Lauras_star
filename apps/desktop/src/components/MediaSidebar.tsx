import { type ReactElement, useEffect, useRef, useState } from "react";

import { type AnalysisRun, type Asset, type LauraClient } from "../api";
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
      className="h-9 w-16 shrink-0 overflow-hidden rounded border border-edge"
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

const POLL_INTERVAL_MS = 1500;
const POLL_MAX = 120; // ~3 minutes

function MediaSidebarItem({
  client,
  asset,
  index,
  isSelected,
  onSelect,
}: {
  client: LauraClient;
  asset: Asset;
  index: number;
  isSelected: boolean;
  onSelect: (assetId: string) => void;
}): ReactElement {
  const [analysisState, setAnalysisState] = useState<ItemAnalysisState>({ kind: "loading" });
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

  function schedulePoll(count: number): void {
    if (count >= POLL_MAX) {
      if (mountedRef.current) {
        setAnalysisState({ kind: "failed", message: "Timeout — bitte erneut versuchen" });
      }
      return;
    }
    pollRef.current = setTimeout(() => {
      if (!mountedRef.current) return;
      client
        .getLatestAnalysis(asset.id)
        .then((run: AnalysisRun | null) => {
          if (!mountedRef.current) return;
          if (!run) {
            setAnalysisState({ kind: "running", label: "analysiere…" });
            schedulePoll(count + 1);
          } else if (run.status === "succeeded") {
            setAnalysisState({ kind: "done" });
          } else if (run.status === "failed") {
            setAnalysisState({ kind: "failed", message: "Analyse fehlgeschlagen" });
          } else {
            setAnalysisState({ kind: "running", label: `${run.status}…` });
            schedulePoll(count + 1);
          }
        })
        .catch((err: unknown) => {
          if (!mountedRef.current) return;
          log.warn("MediaSidebar: poll failed", err);
          setAnalysisState({ kind: "failed", message: "Verbindungsfehler" });
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
  const rowInactive = "hover:bg-edge";

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
          className="truncate text-xs text-slate-200"
          title={asset.display_name}
        >
          {asset.display_name}
        </div>
        <div className="mt-0.5">
          {analysisState.kind === "loading" && (
            <span className="text-[10px] text-slate-500">…</span>
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
            <span className="text-[10px] text-slate-400">{analysisState.label}</span>
          )}
          {analysisState.kind === "done" && (
            <span className="text-[10px] text-emerald-400">✓ analysiert</span>
          )}
          {analysisState.kind === "failed" && (
            <>
              <span className="mr-1 text-[10px] text-red-400">{analysisState.message}</span>
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
      </div>
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
}

export function MediaSidebar({
  client,
  assets,
  selectedAssetId,
  onSelect,
}: MediaSidebarProps): ReactElement {
  return (
    <aside className="flex w-56 shrink-0 flex-col gap-1 overflow-y-auto border-r border-edge bg-ink p-2">
      <div className="flex items-center justify-between pb-1">
        <span className="text-xs font-semibold text-slate-300">Projekt-Medien</span>
        <span className="text-[10px] text-slate-500">{assets.length}</span>
      </div>
      {assets.length === 0 ? (
        <p className="text-[11px] text-slate-500">
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
          />
        ))
      )}
    </aside>
  );
}
