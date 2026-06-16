import { type KeyboardEvent, type ReactElement, useCallback, useRef } from "react";

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
  const duplicateHashes = buildDuplicateHashes(assets);

  // Roving tabIndex: track which grid index currently "owns" tabIndex=0.
  // We derive it from selectedAssetId so it stays in sync with App-level state.
  const focusedIndex = assets.findIndex((a) => a.id === selectedAssetId);
  // If nothing is selected, first card gets tabIndex=0.
  const rovingIdx = focusedIndex >= 0 ? focusedIndex : 0;

  // Refs to each card div so we can call .focus() on arrow-key movement.
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
          next = Math.min(index + 1, assets.length - 1);
          break;
        case "ArrowLeft":
          next = Math.max(index - 1, 0);
          break;
        case "ArrowDown":
          next = Math.min(index + cols, assets.length - 1);
          break;
        case "ArrowUp":
          next = Math.max(index - cols, 0);
          break;
        case "Enter":
        case " ":
          onSelectAsset(assets[index].id);
          e.preventDefault();
          return;
        default:
          return;
      }
      e.preventDefault();
      if (next !== index) {
        onSelectAsset(assets[next].id);
        cardRefs.current[next]?.focus();
      }
    },
    [assets, onSelectAsset],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto p-4">
      <div className="mb-3 max-w-md">
        <ImportBar disabled={disabled} onUrls={onUrls} onPickFiles={onPickFiles} onPickFolder={onPickFolder} />
      </div>
      {assets.length === 0 ? (
        <div className="flex flex-1 items-center justify-center text-sm text-slate-600">
          Dateien/Ordner/Links hier ablegen oder importieren.
        </div>
      ) : (
        <div
          ref={gridRef}
          role="listbox"
          aria-label="Medien-Bin"
          className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4"
        >
          {assets.map((a, i) => (
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
