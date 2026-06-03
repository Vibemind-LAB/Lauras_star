import { type ReactElement, useEffect, useState } from "react";

import { type LauraClient, type Shot } from "../api";

function ShotThumb({
  client,
  shot,
  index,
  onAppend,
}: {
  client: LauraClient;
  shot: Shot;
  index: number;
  onAppend?: (shot: Shot) => void;
}): ReactElement {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    if (shot.thumbnail_path) {
      client
        .shotThumbnailUrl(shot.id)
        .then((u) => {
          if (!active) {
            URL.revokeObjectURL(u);
            return;
          }
          objectUrl = u;
          setUrl(u);
        })
        .catch(() => {
          /* no thumbnail on disk -> keep the colour fallback */
        });
    }
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [client, shot.id, shot.thumbnail_path]);

  return (
    <button
      type="button"
      disabled={!onAppend}
      onClick={() => onAppend?.(shot)}
      title={
        `Shot ${index + 1}: ${shot.src_in_frame}–${shot.src_out_frame_exclusive}` +
        (onAppend ? " (Klick = an Rough Cut anhängen)" : "")
      }
      className={`relative h-9 w-16 shrink-0 overflow-hidden rounded border border-edge ${
        onAppend ? "hover:ring-2 hover:ring-emerald-500/60" : "cursor-default"
      }`}
    >
      {url ? (
        <img src={url} alt={`Shot ${index + 1}`} className="h-full w-full object-cover" />
      ) : (
        <span
          className={`block h-full w-full ${index % 2 === 0 ? "bg-sky-700/40" : "bg-sky-500/30"}`}
        />
      )}
      <span className="absolute bottom-0 left-0 bg-ink/70 px-1 text-[10px] leading-tight text-slate-200">
        {index + 1}
      </span>
    </button>
  );
}

export function ShotStrip({
  client,
  shots,
  onAppend,
}: {
  client: LauraClient;
  shots: Shot[];
  onAppend?: (shot: Shot) => void;
}): ReactElement {
  if (shots.length === 0) {
    return <div className="text-xs text-slate-600">keine Shots</div>;
  }
  return (
    <div className="flex w-full gap-1 overflow-x-auto pb-1">
      {shots.map((s, i) => (
        <ShotThumb key={s.id} client={client} shot={s} index={i} onAppend={onAppend} />
      ))}
    </div>
  );
}
