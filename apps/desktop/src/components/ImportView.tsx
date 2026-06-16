import { type KeyboardEvent, type ReactElement, useCallback, useMemo, useRef, useState } from "react";

import type { Asset, LauraClient } from "../api";
import { ImportBar, type UrlImportRequest } from "./ImportBar";
import { MediaCard } from "./MediaCard";
import { useImportStatus } from "../hooks/useImportStatus";
import { formatBytes } from "../import/format";
import { framesToTimecode } from "../shared/timecode";

/** Round a frame-rate fraction to a display string like "30 fps" or "29.97 fps". */
function formatFps(rateNum: number, rateDen: number): string {
  const fps = rateNum / rateDen;
  // Show one decimal place only when the result is not a whole number.
  const display = Number.isInteger(fps) ? fps.toFixed(0) : fps.toFixed(2).replace(/\.?0+$/, "");
  return `${display} fps`;
}

/** Sum size_bytes across all AssetFiles, ignoring nulls. */
function totalSizeBytes(files: Asset["files"]): number {
  return files.reduce<number>((acc, f) => acc + (f.size_bytes ?? 0), 0);
}

function assetMeta(a: Asset): string {
  const parts: string[] = [];

  const isAudio = a.type === "audio";

  if (!isAudio && a.width && a.height) {
    parts.push(`${a.width}×${a.height}`);
  } else if (isAudio) {
    parts.push("Audio");
  }

  if (a.rate_num && a.rate_den) {
    if (!isAudio) {
      parts.push(formatFps(a.rate_num, a.rate_den));
    }
    if (a.duration_frames != null) {
      parts.push(framesToTimecode(a.duration_frames, a.rate_num, a.rate_den));
    }
  }

  if (!isAudio && a.codec_video) {
    parts.push(a.codec_video);
  }

  const size = totalSizeBytes(a.files);
  if (size > 0) {
    parts.push(formatBytes(size));
  }

  return parts.join(" · ");
}

/** Build a Set of sha256 hashes that appear more than once in the list. */
function buildDuplicateHashes(assets: Asset[]): Set<string> {
  const seen = new Set<string>();
  const dupes = new Set<string>();
  for (const a of assets) {
    if (a.sha256 == null) continue;
    if (seen.has(a.sha256)) {
      dupes.add(a.sha256);
    } else {
      seen.add(a.sha256);
    }
  }
  return dupes;
}

function DuplicateBadge(): ReactElement {
  return (
    <span
      title="Diese Datei ist bereits im Projekt vorhanden"
      className="shrink-0 rounded bg-amber-900/70 px-1.5 py-0.5 text-[10px] font-medium leading-none text-amber-200"
    >
      bereits importiert
    </span>
  );
}

function ImportCard({
  client,
  asset,
  isDuplicate,
  isSelected,
  tabIndex,
  divRef,
  onSelect,
  onKeyDown,
}: {
  client: LauraClient;
  asset: Asset;
  isDuplicate: boolean;
  isSelected: boolean;
  tabIndex: number;
  divRef: (el: HTMLDivElement | null) => void;
  onSelect: (id: string) => void;
  onKeyDown: (e: KeyboardEvent<HTMLDivElement>) => void;
}): ReactElement {
  const settled = asset.files?.some((f) => f.kind === "waveform" || f.kind === "proxy") ?? false;
  const status = useImportStatus(client, settled ? null : asset.id);
  return (
    <div
      ref={divRef}
      role="option"
      aria-selected={isSelected}
      tabIndex={tabIndex}
      onKeyDown={onKeyDown}
      className={`rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-sky-400 ${
        isSelected ? "ring-2 ring-sky-500" : "ring-0"
      }`}
    >
      <MediaCard
        title={asset.display_name}
        meta={assetMeta(asset)}
        thumbnail={client.fileObjectUrl(asset.id, "poster").catch(() => "")}
        status={status}
        onClick={() => onSelect(asset.id)}
        onRetry={() => void client.retryImport(asset.id)}
        onCancel={() => void client.cancelImport(asset.id)}
        menu={isDuplicate ? <DuplicateBadge /> : undefined}
      />
    </div>
  );
}

type SortKey = "newest" | "name" | "duration";
type FilterKey = "all" | "video" | "audio" | "ai";

/** Compact label/select or label/input pair for the toolbar. */
function ToolbarSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
}): ReactElement {
  return (
    <label className="flex items-center gap-1 text-xs text-slate-400">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded border border-slate-700 bg-slate-800 px-1.5 py-0.5 text-xs text-slate-200 focus:outline-none focus:ring-1 focus:ring-sky-500"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function ImportView({
  client,
  disabled,
  assets,
  selectedAssetId,
  onSelectAsset,
  onUrls,
  onPickFiles,
  onPickFolder,
}: {
  client: LauraClient;
  disabled: boolean;
  assets: Asset[];
  selectedAssetId: string | null;
  onSelectAsset: (id: string) => void;
  onUrls: (req: UrlImportRequest) => void;
  onPickFiles: () => void;
  onPickFolder: () => void;
}): ReactElement {
  // --- Toolbar state ---
  const [searchQuery, setSearchQuery] = useState("");
  const [filterKey, setFilterKey] = useState<FilterKey>("all");
  const [sortKey, setSortKey] = useState<SortKey>("newest");

  // Duplicate detection always uses the FULL asset list so a clip is flagged
  // even when its twin is currently filtered out.
  const duplicateHashes = buildDuplicateHashes(assets);

  // --- Derived visible list (search → filter → sort) ---
  const visibleAssets = useMemo<Asset[]>(() => {
    const q = searchQuery.trim().toLowerCase();

    let filtered = assets.filter((a) => {
      // Text search
      if (q && !a.display_name.toLowerCase().includes(q)) return false;
      // Type filter
      if (filterKey === "video" && a.type !== "video") return false;
      if (filterKey === "audio" && a.type !== "audio") return false;
      if (filterKey === "ai" && !a.synthetic) return false;
      return true;
    });

    filtered = [...filtered].sort((a, b) => {
      switch (sortKey) {
        case "name":
          return a.display_name.localeCompare(b.display_name);
        case "duration": {
          // Nulls last (treat null as -1 so they sort after real values when descending)
          const da = a.duration_frames ?? -1;
          const db = b.duration_frames ?? -1;
          return db - da;
        }
        case "newest":
        default:
          return b.created_at.localeCompare(a.created_at);
      }
    });

    return filtered;
  }, [assets, searchQuery, filterKey, sortKey]);

  // Roving tabIndex: derive from visibleAssets so arrow-key nav stays within the
  // filtered/sorted set. If the selected asset is not in view, first visible card
  // gets tabIndex=0.
  const focusedIndex = visibleAssets.findIndex((a) => a.id === selectedAssetId);
  const rovingIdx = focusedIndex >= 0 ? focusedIndex : 0;

  // Refs to each card div so we can call .focus() on arrow-key movement.
  // Indexed into visibleAssets, not assets.
  const cardRefs = useRef<(HTMLDivElement | null)[]>([]);
  // Ref to the grid container so we can read the actual column count at key-down time.
  const gridRef = useRef<HTMLDivElement | null>(null);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLDivElement>, index: number) => {
      const cols = (() => {
        // Read the runtime column count from the computed grid-template-columns style.
        // `getComputedStyle` returns one value per column (e.g. "120px 120px 120px"),
        // so splitting on whitespace-separated values gives the actual column count.
        if (gridRef.current) {
          const style = window.getComputedStyle(gridRef.current);
          const templateCols = style.getPropertyValue("grid-template-columns");
          // Each column is a non-empty space-delimited token.
          const count = templateCols.split(" ").filter(Boolean).length;
          if (count > 0) return count;
        }
        return 2; // safe fallback for the smallest breakpoint
      })();

      let next = index;
      switch (e.key) {
        case "ArrowRight":
          next = Math.min(index + 1, visibleAssets.length - 1);
          break;
        case "ArrowLeft":
          next = Math.max(index - 1, 0);
          break;
        case "ArrowDown":
          next = Math.min(index + cols, visibleAssets.length - 1);
          break;
        case "ArrowUp":
          next = Math.max(index - cols, 0);
          break;
        case "Enter":
        case " ":
          onSelectAsset(visibleAssets[index].id);
          e.preventDefault();
          return;
        default:
          return;
      }
      e.preventDefault();
      if (next !== index) {
        onSelectAsset(visibleAssets[next].id);
        cardRefs.current[next]?.focus();
      }
    },
    [visibleAssets, onSelectAsset],
  );

  const hasAssets = assets.length > 0;
  const hasVisible = visibleAssets.length > 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto p-4">
      <div className="mb-3 max-w-md">
        <ImportBar disabled={disabled} onUrls={onUrls} onPickFiles={onPickFiles} onPickFolder={onPickFolder} />
      </div>

      {hasAssets && (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {/* Search */}
          <label className="flex items-center gap-1 text-xs text-slate-400">
            <span className="sr-only">Suche</span>
            <input
              type="search"
              placeholder="Suchen…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-40 rounded border border-slate-700 bg-slate-800 px-2 py-0.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
            />
          </label>

          {/* Filter */}
          <ToolbarSelect
            label="Typ:"
            value={filterKey}
            onChange={(v) => setFilterKey(v as FilterKey)}
            options={[
              { value: "all", label: "Alle" },
              { value: "video", label: "Video" },
              { value: "audio", label: "Audio" },
              { value: "ai", label: "KI" },
            ]}
          />

          {/* Sort */}
          <ToolbarSelect
            label="Sortierung:"
            value={sortKey}
            onChange={(v) => setSortKey(v as SortKey)}
            options={[
              { value: "newest", label: "Neueste" },
              { value: "name", label: "Name" },
              { value: "duration", label: "Dauer" },
            ]}
          />
        </div>
      )}

      {!hasAssets ? (
        <div className="flex flex-1 items-center justify-center text-sm text-slate-600">
          Dateien/Ordner/Links hier ablegen oder importieren.
        </div>
      ) : !hasVisible ? (
        <div className="flex flex-1 items-center justify-center text-sm text-slate-500">
          Keine Treffer.
        </div>
      ) : (
        <div
          ref={gridRef}
          role="listbox"
          aria-label="Medien-Bin"
          className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4"
        >
          {visibleAssets.map((a, i) => (
            <ImportCard
              key={a.id}
              client={client}
              asset={a}
              isDuplicate={a.sha256 != null && duplicateHashes.has(a.sha256)}
              isSelected={a.id === selectedAssetId}
              tabIndex={i === rovingIdx ? 0 : -1}
              divRef={(el) => { cardRefs.current[i] = el; }}
              onSelect={onSelectAsset}
              onKeyDown={(e) => handleKeyDown(e, i)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
