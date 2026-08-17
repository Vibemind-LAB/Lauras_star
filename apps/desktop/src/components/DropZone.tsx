import { type DragEvent as ReactDragEvent, type ReactElement, useCallback, useEffect, useState } from "react";

import { classifyDrop, type DropItem } from "../import/classifyDrop";

export interface ResolvedImport {
  paths: string[];
  urls: string[];
}

async function resolve(items: DropItem[]): Promise<ResolvedImport> {
  const paths: string[] = [];
  const urls: string[] = [];
  for (const item of items) {
    if (item.kind === "file") paths.push(item.path);
    else if (item.kind === "url") urls.push(item.url);
    else paths.push(...(await window.laura.listMediaInFolder(item.path)));
  }
  return { paths, urls };
}

export function DropZone({ onImport }: { onImport: (r: ResolvedImport) => void }): ReactElement {
  const [active, setActive] = useState(false);

  useEffect(() => {
    const over = (e: DragEvent): void => {
      e.preventDefault();
      setActive(true);
    };
    const leave = (e: DragEvent): void => {
      if (e.relatedTarget === null) setActive(false);
    };
    window.addEventListener("dragover", over);
    window.addEventListener("dragleave", leave);
    return () => {
      window.removeEventListener("dragover", over);
      window.removeEventListener("dragleave", leave);
    };
  }, []);

  const onDrop = useCallback(
    async (e: ReactDragEvent): Promise<void> => {
      e.preventDefault();
      setActive(false);
      const dt = e.dataTransfer;
      const filePaths: string[] = [];
      const directoryPaths: string[] = [];
      for (const f of Array.from(dt.files)) {
        const p = window.laura.pathForFile(f);
        if (f.type === "" && !f.name.includes(".")) directoryPaths.push(p);
        else filePaths.push(p);
      }
      const uriText = dt.getData("text/uri-list") || dt.getData("text/plain");
      const items = classifyDrop({ filePaths, directoryPaths, uriText });
      onImport(await resolve(items));
    },
    [onImport],
  );

  return (
    <div
      onDrop={(e) => void onDrop(e)}
      onDragOver={(e) => e.preventDefault()}
      className={`fixed inset-0 z-50 flex items-center justify-center transition ${
        active ? "bg-surface-0/80 backdrop-blur-sm" : "pointer-events-none opacity-0"
      }`}
    >
      <div className="rounded-2xl border-2 border-dashed border-accent/60 px-12 py-10 text-center">
        <div className="text-lg text-content-strong">Drop files, folders or a link here</div>
        <div className="mt-1 text-sm text-content-faint">
          Video files · whole folders · http(s)/magnet links
        </div>
      </div>
    </div>
  );
}
