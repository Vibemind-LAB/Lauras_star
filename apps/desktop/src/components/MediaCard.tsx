import { type ReactElement, type ReactNode, useEffect, useState } from "react";
import type { ImportStatus } from "../api";
import { ImportProgress } from "./ImportProgress";
export function MediaCard({
  title, meta, thumbnail, status, onClick, onRetry, onCancel, menu,
}: {
  title: string;
  meta?: string;
  thumbnail?: Promise<string> | string | null;
  status?: ImportStatus | null;
  onClick: () => void;
  onRetry: () => void;
  onCancel?: () => void;
  menu?: ReactNode;
}): ReactElement {
  const [src, setSrc] = useState<string | null>(typeof thumbnail === "string" ? thumbnail : null);
  useEffect(() => {
    if (thumbnail == null || typeof thumbnail === "string") return;
    let active = true;
    void thumbnail.then((url) => { if (active) setSrc(url); }).catch(() => undefined);
    return () => { active = false; };
  }, [thumbnail]);
  return (
    <div className="flex flex-col overflow-hidden rounded-lg border border-bezel bg-surface-1">
      <button type="button" onClick={onClick} className="flex aspect-video w-full items-center justify-center bg-black text-content-faint">
        {src ? <img src={src} alt="" className="h-full w-full object-cover" /> : <span className="text-xs">no thumbnail</span>}
      </button>
      <div className="flex items-start justify-between gap-2 p-2">
        <button type="button" onClick={onClick} className="min-w-0 text-left">
          <div className="truncate text-sm text-content-strong">{title}</div>
          {meta && <div className="truncate text-[11px] text-content-faint">{meta}</div>}
        </button>
        {menu}
      </div>
      {status && <div className="px-2 pb-2"><ImportProgress status={status} onRetry={onRetry} onCancel={onCancel} /></div>}
    </div>
  );
}
