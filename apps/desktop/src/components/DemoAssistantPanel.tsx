import { type ReactElement, useEffect, useMemo, useRef, useState } from "react";

import { type Asset, type DemoDraft, type DemoDraftItem, type LauraClient } from "../api";
import { useJobStatus } from "../hooks/useJobStatus";
import { log } from "../shared/log";

function jobStatusText(status: string): string {
  if (status === "succeeded") return "Draft bereit.";
  if (status === "failed") return "Draft-Analyse fehlgeschlagen.";
  if (status === "cancelled") return "Draft-Analyse abgebrochen.";
  return "Draft analysis running.";
}

function itemDuration(item: DemoDraftItem): string {
  return `${item.src_in_frame}-${Math.max(item.src_in_frame, item.src_out_frame_exclusive - 1)} f`;
}

export function DemoAssistantPanel({
  client,
  assets,
  onApplied,
}: {
  client: LauraClient;
  assets: Asset[];
  onApplied: () => void;
}): ReactElement {
  const videoAssets = useMemo(() => assets.filter((asset) => asset.type === "video"), [assets]);
  const [assetId, setAssetId] = useState(videoAssets[0]?.id ?? "");
  const [draft, setDraft] = useState<DemoDraft | null>(null);
  const [items, setItems] = useState<DemoDraftItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [draftJobId, setDraftJobId] = useState<string | null>(null);
  const pendingDraftIdRef = useRef<string | null>(null);
  const loadedJobRef = useRef<string | null>(null);
  const { jobStatus, error: jobError } = useJobStatus(client, draftJobId);

  useEffect(() => {
    if (!assetId && videoAssets.length > 0) setAssetId(videoAssets[0].id);
  }, [assetId, videoAssets]);

  useEffect(() => {
    if (jobStatus === null || draftJobId === null || loadedJobRef.current === jobStatus.id) return;
    if (jobStatus.status === "succeeded") {
      loadedJobRef.current = jobStatus.id;
      const draftId = pendingDraftIdRef.current;
      if (draftId === null) { setBusy(false); return; }
      client
        .getDemoDraft(draftId)
        .then((loaded) => { setDraft(loaded); setItems(loaded.items); setStatus(jobStatusText("succeeded")); })
        .catch((e: unknown) => { setError(e instanceof Error ? e.message : String(e)); })
        .finally(() => setBusy(false));
    } else if (jobStatus.status === "failed" || jobStatus.status === "cancelled") {
      loadedJobRef.current = jobStatus.id;
      setStatus(jobStatusText(jobStatus.status));
      if (jobError !== null) setError(jobError);
      setBusy(false);
    }
  }, [jobStatus, jobError, draftJobId, client]);

  function updateItem(index: number, patch: Partial<DemoDraftItem>): void {
    setItems((current) =>
      current.map((item, i) => (i === index ? { ...item, ...patch } : item)),
    );
  }

  async function createDraft(): Promise<void> {
    if (!assetId) { setError("No video asset available."); return; }
    setBusy(true);
    setError(null);
    setStatus(null);
    setDraftJobId(null);
    loadedJobRef.current = null;
    try {
      const accepted = await client.createDemoDraft(assetId);
      pendingDraftIdRef.current = accepted.draft_id;
      setDraftJobId(accepted.job_id);
      setStatus(jobStatusText("running"));
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      log.error("createDemoDraft failed:", msg);
      setError(msg);
      setBusy(false);
    }
  }

  async function saveDraft(): Promise<DemoDraft | null> {
    if (draft === null) return null;
    const updated = await client.updateDemoDraft(draft.id, items);
    setDraft(updated);
    setItems(updated.items);
    return updated;
  }

  async function applyDraft(): Promise<void> {
    if (draft === null) return;
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const saved = await saveDraft();
      if (saved === null) return;
      const applied = await client.applyDemoDraft(saved.id);
      setDraft(applied.draft);
      setItems(applied.draft.items);
      setStatus("Sequence taken from the draft.");
      onApplied();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      log.error("applyDemoDraft failed:", msg);
      setError(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex flex-col gap-3 rounded border border-bezel bg-surface-1/50 p-3">
      <div>
        <div className="text-xs font-semibold text-content-strong">Demo-Draft</div>
        <div className="text-[11px] text-content-faint">Product story from shots and transcript</div>
      </div>

      {error !== null && (
        <div className="rounded border border-status-err/40 bg-status-err/10 p-2 text-xs text-status-err">
          {error}
        </div>
      )}
      {status !== null && <div className="text-xs text-content-muted">{status}</div>}

      <label className="flex flex-col gap-1 text-xs text-content-muted">
        Video
        <select
          value={assetId}
          onChange={(e) => {
            setAssetId(e.target.value);
            setDraft(null);
            setItems([]);
            setStatus(null);
          }}
          disabled={busy || videoAssets.length === 0}
          className="rounded border border-bezel bg-surface-0 px-2 py-1 text-content-strong disabled:opacity-50"
        >
          {videoAssets.length === 0 ? (
            <option value="">No video assets</option>
          ) : (
            videoAssets.map((asset) => (
              <option key={asset.id} value={asset.id}>
                {asset.display_name}
              </option>
            ))
          )}
        </select>
      </label>

      <button
        type="button"
        onClick={() => void createDraft()}
        disabled={busy || videoAssets.length === 0}
        className="self-start rounded bg-accent px-3 py-1 text-xs font-medium text-white hover:bg-accent-glow disabled:opacity-40"
      >
        {busy && draft === null ? "Analysiert..." : "Demo-Draft erzeugen"}
      </button>

      {items.length > 0 && (
        <div className="flex flex-col gap-2">
          {items.map((item, index) => (
            <article
              key={`${item.src_in_frame}:${item.src_out_frame_exclusive}:${index}`}
              className="rounded border border-bezel bg-surface-0 p-2 text-xs"
            >
              <div className="mb-2 flex items-center justify-between gap-2">
                <label className="flex items-center gap-2 font-medium text-content-strong">
                  <input
                    aria-label={`Demo-Item ${index + 1} aktiv`}
                    type="checkbox"
                    checked={item.enabled}
                    onChange={(e) => updateItem(index, { enabled: e.target.checked })}
                    disabled={busy}
                    className="h-3.5 w-3.5"
                  />
                  Shot {index + 1}
                </label>
                <span className="tabular-nums text-content-faint">{itemDuration(item)}</span>
              </div>
              <label className="mb-2 flex flex-col gap-1 text-content-muted">
                Label
                <input
                  aria-label={`Demo-Label ${index + 1}`}
                  value={item.label}
                  onChange={(e) => updateItem(index, { label: e.target.value })}
                  disabled={busy}
                  className="rounded border border-bezel bg-surface-1 px-2 py-1 text-content-strong disabled:opacity-50"
                />
              </label>
              <label className="flex flex-col gap-1 text-content-muted">
                Voiceover
                <textarea
                  aria-label={`Demo-Voiceovertext ${index + 1}`}
                  value={item.voiceover_text}
                  onChange={(e) => updateItem(index, { voiceover_text: e.target.value })}
                  disabled={busy}
                  rows={3}
                  className="resize-y rounded border border-bezel bg-surface-1 px-2 py-1 text-content-strong disabled:opacity-50"
                />
              </label>
            </article>
          ))}
          <button
            type="button"
            onClick={() => void applyDraft()}
            disabled={busy || draft === null || items.every((item) => !item.enabled)}
            className="self-start rounded bg-accent px-3 py-1 text-xs font-medium text-white hover:bg-accent-glow disabled:opacity-40"
          >
            Apply to sequence
          </button>
        </div>
      )}
    </section>
  );
}
