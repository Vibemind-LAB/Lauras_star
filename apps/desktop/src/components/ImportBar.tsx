import { type FormEvent, type ReactElement, useState } from "react";

import type { CookiesFromBrowser, ImportFormat } from "../api";

export interface UrlImportRequest {
  urls: string[];
  format: ImportFormat;
  cookiesFromBrowser: CookiesFromBrowser | null;
}

const FORMAT_OPTIONS: ReadonlyArray<{ value: ImportFormat; label: string }> = [
  { value: "best", label: "Beste" },
  { value: "1080", label: "1080p" },
  { value: "720", label: "720p" },
  { value: "audio", label: "audio only" },
];

const COOKIE_OPTIONS: ReadonlyArray<{ value: CookiesFromBrowser | ""; label: string }> = [
  { value: "", label: "Cookies: Aus" },
  { value: "chrome", label: "Chrome" },
  { value: "edge", label: "Edge" },
  { value: "firefox", label: "Firefox" },
];

/** Split a textarea blob into trimmed, non-empty URLs (one per line or comma). */
function parseUrls(raw: string): string[] {
  return raw
    .split(/[\n,]+/)
    .map((u) => u.trim())
    .filter((u) => u.length > 0);
}

export function ImportBar({
  disabled,
  onUrls,
  onPickFiles,
  onPickFolder,
}: {
  disabled: boolean;
  onUrls: (req: UrlImportRequest) => void;
  onPickFiles: () => void;
  onPickFolder: () => void;
}): ReactElement {
  const [text, setText] = useState("");
  const [format, setFormat] = useState<ImportFormat>("best");
  const [cookies, setCookies] = useState<CookiesFromBrowser | "">("");

  const urls = parseUrls(text);
  const submit = (e: FormEvent): void => {
    e.preventDefault();
    if (urls.length === 0) return;
    onUrls({ urls, format, cookiesFromBrowser: cookies === "" ? null : cookies });
    setText("");
  };

  return (
    <div className="flex flex-col gap-1.5">
      <form onSubmit={submit} className="flex flex-col gap-1">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Paste URLs — one per line (YouTube, Drive, playlist …)"
          disabled={disabled}
          rows={2}
          className="min-h-0 w-full resize-y rounded bg-surface-2 px-2 py-1 text-xs text-content-strong placeholder:text-content-faint"
        />
        <div className="flex gap-1">
          <select
            value={format}
            onChange={(e) => setFormat(e.target.value as ImportFormat)}
            disabled={disabled}
            aria-label="Quality"
            className="min-w-0 flex-1 rounded bg-surface-2 px-1.5 py-1 text-xs text-content-strong disabled:opacity-40"
          >
            {FORMAT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <select
            value={cookies}
            onChange={(e) => setCookies(e.target.value as CookiesFromBrowser | "")}
            disabled={disabled}
            aria-label="Cookies from browser"
            className="min-w-0 flex-1 rounded bg-surface-2 px-1.5 py-1 text-xs text-content-strong disabled:opacity-40"
          >
            {COOKIE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <button
            type="submit"
            disabled={disabled || urls.length === 0}
            className="shrink-0 rounded bg-accent px-3 py-1 text-xs text-white disabled:opacity-40"
          >
            Load
          </button>
        </div>
      </form>
      <div className="flex gap-1">
        <button
          type="button"
          onClick={onPickFiles}
          disabled={disabled}
          className="flex-1 rounded bg-surface-2 px-2 py-1 text-xs text-content-strong hover:bg-surface-2 disabled:opacity-40"
        >
          + File(s)
        </button>
        <button
          type="button"
          onClick={onPickFolder}
          disabled={disabled}
          className="flex-1 rounded bg-surface-2 px-2 py-1 text-xs text-content-strong hover:bg-surface-2 disabled:opacity-40"
        >
          + Folder
        </button>
      </div>
    </div>
  );
}
