import { type ReactElement, useEffect, useState } from "react";

import type { LauraClient } from "../../api";

/**
 * What the preview pane currently shows. `contact_sheet.version` is the cache-buster the
 * production board bumps on every cutlist rebuild (see api.ts's `contactSheetUrl` doc and
 * ChatPanel.tsx's `ContactSheetViewer`, whose object-URL lifecycle this mirrors) — the pane
 * re-fetches whenever it changes even if `sessionId` stays the same.
 */
export type PreviewTarget =
  | { kind: "none" }
  | { kind: "contact_sheet"; sessionId: string; version: number }
  | { kind: "export"; exportId: string };

export interface ChatPreviewProps {
  target: PreviewTarget;
  client: LauraClient;
}

/** Shared pane chrome: centers its child in the available height. */
const PANE_CLS = "flex h-full items-center justify-center overflow-auto p-2";

/**
 * The chat panel's preview pane: nothing, a production contact sheet, or a finished export
 * video. The sheet branch mirrors `ChatPanel.tsx`'s `ContactSheetViewer` object-URL lifecycle
 * exactly — fetch on mount/change via `client.contactSheetUrl`, revoke the previous object URL
 * before replacing it and on unmount, re-fetch when `version` bumps. The export branch needs no
 * such lifecycle: it just points a `<video>` at the Task-8 `laura-media://` export lane
 * (`laura-media://media/export/<exportId>` — the protocol handler in `main.ts`'s
 * `resolveExportPath` triggers on the fixed `media` host with assetId `"export"` and kind the
 * export id; NOT `laura-media://export/<exportId>`), so the browser streams it directly.
 */
export function ChatPreview({ target, client }: ChatPreviewProps): ReactElement {
  const sessionId = target.kind === "contact_sheet" ? target.sessionId : null;
  const version = target.kind === "contact_sheet" ? target.version : null;
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (sessionId === null) {
      setUrl(null);
      setError(null);
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    setUrl(null);
    setError(null);
    void client
      .contactSheetUrl(sessionId)
      .then((u) => {
        if (cancelled) {
          URL.revokeObjectURL(u);
          return;
        }
        objectUrl = u;
        setUrl(u);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
    };
    // `version` is intentionally in the dependency list though unused in the body — it is the
    // cache-buster that forces a re-fetch when the board rewrites the sheet under the same id.
  }, [client, sessionId, version]);

  if (target.kind === "none") {
    return (
      <div className={`${PANE_CLS} text-[11px] text-content-faint`}>
        Nothing to show yet — build something.
      </div>
    );
  }

  if (target.kind === "export") {
    return (
      <div className={PANE_CLS}>
        <video
          controls
          src={`laura-media://media/export/${target.exportId}`}
          className="max-h-full max-w-full rounded border border-bezel"
        />
      </div>
    );
  }

  if (error !== null) {
    return (
      <div className={`${PANE_CLS} text-[11px] text-status-err`} role="alert">
        Could not load the sheet
      </div>
    );
  }

  if (url === null) {
    return (
      <div className={`${PANE_CLS} text-[11px] text-content-faint`} role="status">
        loading …
      </div>
    );
  }

  return (
    <div className={PANE_CLS}>
      <img src={url} alt="Kontaktbogen" className="max-h-full max-w-full rounded border border-bezel" />
    </div>
  );
}
