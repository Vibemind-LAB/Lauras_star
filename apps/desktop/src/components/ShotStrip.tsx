import { type ReactElement, useEffect, useState } from "react";

import { type LauraClient, type Shot } from "../api";

const DROP_REASON_GLYPH: Record<string, string> = {
  black: "⬛",
  static: "❄",
  duplicate: "⧉",
  blur: "≈",
};

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
  const dropped = shot.keep === false;

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

  const baseTitle = `Shot ${index + 1}: ${shot.src_in_frame}–${shot.src_out_frame_exclusive}`;
  const appendHint = onAppend
    ? dropped
      ? ` · verworfen: ${shot.drop_reason ?? "unbekannt"} (Klick = wieder aufnehmen)`
      : " (Klick = an Rough Cut anhängen)"
    : "";
  const title = baseTitle + appendHint;

  return (
    <button
      type="button"
      disabled={!onAppend}
      onClick={() => onAppend?.(shot)}
      title={title}
      className={`relative h-9 w-16 shrink-0 overflow-hidden rounded border ${
        dropped
          ? "border-dashed border-amber-500/50 opacity-50 hover:opacity-80"
          : `border-bezel ${onAppend ? "hover:ring-2 hover:ring-accent/60" : "cursor-default"}`
      }`}
    >
      {url ? (
        <img src={url} alt={`Shot ${index + 1}`} className="h-full w-full object-cover" />
      ) : (
        <span
          className={`block h-full w-full ${index % 2 === 0 ? "bg-sky-700/40" : "bg-sky-500/30"}`}
        />
      )}
      <span className="absolute bottom-0 left-0 bg-surface-0/70 px-1 text-[10px] leading-tight text-content-strong">`n        {index + 1}
      </span>
      {dropped && (
        <span
          className="absolute right-0 top-0 rounded-bl bg-amber-600/80 px-0.5 text-[10px] leading-tight text-white"
          aria-hidden="true"
        >
          {shot.drop_reason != null
            ? (DROP_REASON_GLYPH[shot.drop_reason] ?? "✕")
            : "✕"}
        </span>
      )}
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
    return <div className="text-xs text-content-faint">keine Shots</div>;
  }
  return (
    <div className="flex w-full gap-1 overflow-x-auto pb-1">
      {shots.map((s, i) => (
        <ShotThumb key={s.id} client={client} shot={s} index={i} onAppend={onAppend} />
      ))}
    </div>
  );
}


