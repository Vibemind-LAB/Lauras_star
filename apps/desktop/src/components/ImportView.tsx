import { type ReactElement } from "react";

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
}: {
  client: LauraClient;
  asset: Asset;
  isDuplicate: boolean;
}): ReactElement {
  const settled = asset.files?.some((f) => f.kind === "waveform" || f.kind === "proxy") ?? false;
  const status = useImportStatus(client, settled ? null : asset.id);
  return (
    <MediaCard
      title={asset.display_name}
      meta={assetMeta(asset)}
      thumbnail={client.fileObjectUrl(asset.id, "poster").catch(() => "")}
      status={status}
      onClick={() => undefined}
      onRetry={() => void client.retryImport(asset.id)}
      onCancel={() => void client.cancelImport(asset.id)}
      menu={isDuplicate ? <DuplicateBadge /> : undefined}
    />
  );
}

export function ImportView({
  client,
  disabled,
  assets,
  onUrls,
  onPickFiles,
  onPickFolder,
}: {
  client: LauraClient;
  disabled: boolean;
  assets: Asset[];
  onUrls: (req: UrlImportRequest) => void;
  onPickFiles: () => void;
  onPickFolder: () => void;
}): ReactElement {
  const duplicateHashes = buildDuplicateHashes(assets);

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
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
          {assets.map((a) => (
            <ImportCard
              key={a.id}
              client={client}
              asset={a}
              isDuplicate={a.sha256 != null && duplicateHashes.has(a.sha256)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
