import { type FormEvent, type ReactElement, useState } from "react";

export function ImportBar({
  disabled,
  onUrl,
  onPickFiles,
  onPickFolder,
}: {
  disabled: boolean;
  onUrl: (url: string) => void;
  onPickFiles: () => void;
  onPickFolder: () => void;
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
    <div className="flex flex-col gap-1.5">
      <form onSubmit={submit} className="flex gap-1">
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="URL einfügen (http/s, Magnet)…"
          disabled={disabled}
          className="min-w-0 flex-1 rounded bg-slate-800 px-2 py-1 text-xs text-slate-100 placeholder:text-slate-600"
        />
        <button
          type="submit"
          disabled={disabled || url.trim() === ""}
          className="shrink-0 rounded bg-sky-600 px-2 py-1 text-xs text-white disabled:opacity-40"
        >
          Laden
        </button>
      </form>
      <div className="flex gap-1">
        <button
          type="button"
          onClick={onPickFiles}
          disabled={disabled}
          className="flex-1 rounded bg-slate-700 px-2 py-1 text-xs text-slate-100 hover:bg-slate-600 disabled:opacity-40"
        >
          + Datei(en)
        </button>
        <button
          type="button"
          onClick={onPickFolder}
          disabled={disabled}
          className="flex-1 rounded bg-slate-700 px-2 py-1 text-xs text-slate-100 hover:bg-slate-600 disabled:opacity-40"
        >
          + Ordner
        </button>
      </div>
    </div>
  );
}
