import { type ReactElement } from "react";

import type { Asset, LauraClient } from "../api";
import { ImportBar, type UrlImportRequest } from "./ImportBar";
import { MediaCard } from "./MediaCard";
import { useImportStatus } from "../hooks/useImportStatus";

function assetMeta(a: Asset): string {
  const kind = a.type === "audio" ? "Audio" : "Video";
  const res = a.width && a.height ? `${a.width}×${a.height}` : "";
  return [kind, res].filter(Boolean).join(" · ");
}

function ImportCard({ client, asset }: { client: LauraClient; asset: Asset }): ReactElement {
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
            <ImportCard key={a.id} client={client} asset={a} />
          ))}
        </div>
      )}
    </div>
  );
}
