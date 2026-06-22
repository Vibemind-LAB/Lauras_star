import { type FormEvent, type ReactElement, useState } from "react";

import { hasFile, type Asset, type LauraClient } from "../api";
import { MediaCard } from "./MediaCard";
import { useImportStatus } from "../hooks/useImportStatus";

function DownloadCard({ client, asset }: { client: LauraClient; asset: Asset }): ReactElement {
  const settled = hasFile(asset, "waveform") || hasFile(asset, "proxy");
  const status = useImportStatus(client, settled ? null : asset.id);
  return (
    <MediaCard
      title={asset.display_name}
      meta={hasFile(asset, "proxy") ? "fertig" : "lädt…"}
      thumbnail={client.fileObjectUrl(asset.id, "poster").catch(() => "")}
      status={status}
      onClick={() => undefined}
      onRetry={() => void client.retryImport(asset.id)}
      onCancel={() => void client.cancelImport(asset.id)}
    />
  );
}

export function DownloadView({
  client,
  disabled,
  assets,
  onUrl,
}: {
  client: LauraClient;
  disabled: boolean;
  assets: Asset[];
  onUrl: (url: string) => void;
}): ReactElement {
  const [url, setUrl] = useState("");
  const submit = (e: FormEvent): void => {
    e.preventDefault();
    const v = url.trim();
    if (v) {
      onUrl(v);
      setUrl("");
    }
  };
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto p-4">
      <form onSubmit={submit} className="mb-3 flex max-w-md gap-1">
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="Video-URL einfügen (http/s, Magnet)…"
          disabled={disabled}
          className="min-w-0 flex-1 rounded bg-surface-2 px-2 py-1 text-xs text-content-strong placeholder:text-content-faint"
        />
        <button
          type="submit"
          disabled={disabled || url.trim() === ""}
          className="shrink-0 rounded bg-accent px-3 py-1 text-xs text-white disabled:opacity-40"
        >
          + Download
        </button>
      </form>
      {assets.length === 0 ? (
        <div className="flex flex-1 items-center justify-center text-sm text-content-faint">
          Noch keine Downloads — füge eine URL hinzu oder ziehe einen Link ins Fenster.
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
          {assets.map((a) => (
            <DownloadCard key={a.id} client={client} asset={a} />
          ))}
        </div>
      )}
    </div>
  );
}

