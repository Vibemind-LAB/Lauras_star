import { type ReactElement, useEffect, useMemo, useState } from "react";

import type { LauraClient, SceneGateStatus } from "../../api";

/** A candidate tile's thumbnail — same async Blob->ObjectURL pattern as `SceneStrip.tsx`'s
 * `Thumb` helper (`client.assetFrameUrl` is a fetch + `URL.createObjectURL`, not a plain URL
 * string an `<img src>` could consume directly). Falls back to a plain accent tile on failure
 * (offline preview, asset since removed) so a broken thumbnail never blanks the whole tile. */
function Thumb({
  client,
  assetId,
  frame,
}: {
  client: LauraClient;
  assetId: string;
  frame: number;
}): ReactElement {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    client
      .assetFrameUrl(assetId, Math.max(0, frame))
      .then((u) => {
        if (!active) {
          URL.revokeObjectURL(u);
          return;
        }
        objectUrl = u;
        setUrl(u);
      })
      .catch(() => {
        /* colour fallback */
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [client, assetId, frame]);
  return (
    <span className="mb-1 block aspect-video w-full overflow-hidden rounded bg-accent/40">
      {url ? <img src={url} alt="" className="h-full w-full object-cover" /> : null}
    </span>
  );
}

export interface SceneSelectionCardProps {
  gate: SceneGateStatus;
  assetId: string;
  sessionId: string;
  client: LauraClient;
  /** Overridable for tests; defaults to the real confirm call. */
  confirm?: (sessionId: string, sceneNumbers: number[]) => Promise<unknown>;
  /** Fires after a successful confirm — the caller's job is to refresh the session's status so
   * this card's own `gate.pending` goes false and it stops rendering. */
  onConfirmed: () => void;
}

/** Gate S checkpoint: clickable candidate tiles, Laura's recommendation pre-checked (spec
 * 2026-08-06 §4.5). Read-only once submitted — a busy confirm disables both the tiles and the
 * button so a second click cannot fire a second POST while the first is still in flight. */
export function SceneSelectionCard({
  gate,
  assetId,
  sessionId,
  client,
  confirm,
  onConfirmed,
}: SceneSelectionCardProps): ReactElement {
  const candidates = gate.candidates ?? [];
  const [selected, setSelected] = useState<Set<number>>(
    () => new Set(candidates.filter((c) => c.recommended).map((c) => c.scene_number)),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const doConfirm = confirm ?? ((sid: string, nums: number[]) => client.confirmSceneSelection(sid, nums));
  const picked = useMemo(() => [...selected].sort((a, b) => a - b), [selected]);

  const toggle = (n: number): void => {
    if (busy) return;
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(n)) next.delete(n);
      else next.add(n);
      return next;
    });
  };

  const submit = async (): Promise<void> => {
    if (busy || picked.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      await doConfirm(sessionId, picked);
      onConfirmed();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Bestätigung fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-0.5 rounded border border-bezel bg-surface-1 px-1.5 py-1 text-[11px]">
      <div className="mb-1 text-content-strong">
        🎬 Szenen-Auswahl — Lauras Empfehlung ist vorausgewählt
      </div>
      <div className="grid grid-cols-2 gap-1.5">
        {candidates.map((c) => {
          const isOn = selected.has(c.scene_number);
          return (
            <button
              key={c.scene_number}
              type="button"
              data-testid={`scene-tile-${c.scene_number}`}
              data-selected={isOn ? "true" : "false"}
              aria-pressed={isOn}
              disabled={busy}
              onClick={() => toggle(c.scene_number)}
              className={`rounded border p-1 text-left transition disabled:opacity-60 ${
                isOn ? "border-accent bg-accent/15" : "border-bezel opacity-70"
              }`}
            >
              <Thumb client={client} assetId={assetId} frame={c.thumb_frame} />
              <div className="font-semibold text-content-strong">Szene {c.scene_number}</div>
              <div className="text-content-muted">{c.description}</div>
              <div className="italic text-content-faint">„{c.transcript_snippet}"</div>
            </button>
          );
        })}
      </div>
      {error !== null && <div className="mt-1 text-status-err">{error}</div>}
      <button
        type="button"
        disabled={busy || picked.length === 0}
        onClick={() => void submit()}
        className="mt-1 rounded bg-accent px-2 py-1 text-[11px] font-medium text-accent-ink hover:bg-accent-glow disabled:opacity-40"
      >
        Auswahl übernehmen ({picked.length})
      </button>
    </div>
  );
}
