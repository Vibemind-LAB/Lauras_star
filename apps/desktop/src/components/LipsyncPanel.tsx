import { type ReactElement, useEffect, useMemo, useState } from "react";

import { type Asset, type LauraClient } from "../api";
import { log } from "../shared/log";
import { RuntimeSelect } from "./RuntimeSelect";

export function LipsyncPanel({
  client,
  projectId,
  timelineId,
  assets,
  onChange,
  runtimeReloadKey = 0,
}: {
  client: LauraClient;
  projectId: string | null;
  timelineId: string | null;
  assets: Asset[];
  onChange: () => void;
  runtimeReloadKey?: number;
}): ReactElement {
  const audioAssets = useMemo(
    () => assets.filter((asset) => asset.type === "audio" || asset.codec_audio !== null),
    [assets],
  );
  const [subjectLabel, setSubjectLabel] = useState("");
  const [consentId, setConsentId] = useState<string | null>(null);
  const [confirmedLabel, setConfirmedLabel] = useState<string | null>(null);
  const [audioAssetId, setAudioAssetId] = useState(audioAssets[0]?.id ?? "");
  const [seqIn, setSeqIn] = useState(0);
  const [seqOut, setSeqOut] = useState(0);
  const [backend, setBackend] = useState<"stub" | "vibevideo">("stub");
  const [runtimeId, setRuntimeId] = useState("");
  const [licenseAccepted, setLicenseAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);

  useEffect(() => {
    if (!audioAssetId && audioAssets.length > 0) setAudioAssetId(audioAssets[0].id);
  }, [audioAssetId, audioAssets]);

  useEffect(() => {
    setConsentId(null);
    setConfirmedLabel(null);
    setSubjectLabel("");
    setLicenseAccepted(false);
    setRuntimeId("");
    setError(null);
    setJobId(null);
  }, [projectId]);

  async function confirmConsent(): Promise<void> {
    if (projectId === null || subjectLabel.trim() === "") return;
    setBusy(true);
    setError(null);
    try {
      const record = await client.createConsent(projectId, { subjectLabel: subjectLabel.trim() });
      setConsentId(record.id);
      setConfirmedLabel(record.subject_label);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      log.error("create lipsync consent failed:", msg);
      setError(msg);
    } finally {
      setBusy(false);
    }
  }

  async function submit(): Promise<void> {
    if (
      timelineId === null ||
      consentId === null ||
      !licenseAccepted ||
      !audioAssetId ||
      seqOut <= seqIn
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    setJobId(null);
    try {
      const request: Parameters<LauraClient["lipsync"]>[1] = {
        seqIn,
        seqOut,
        audioAssetId,
        consentId,
        licenseAccepted,
        backend,
        qualityThreshold: 0.6,
      };
      if (runtimeId !== "") {
        request.runtimeId = runtimeId;
      }
      const accepted = await client.lipsync(timelineId, request);
      setJobId(accepted.job_id);
      onChange();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      log.error("lipsync failed:", msg);
      setError(msg);
    } finally {
      setBusy(false);
    }
  }

  const disabled =
    busy ||
    timelineId === null ||
    consentId === null ||
    !licenseAccepted ||
    !audioAssetId ||
    seqOut <= seqIn;

  return (
    <section className="flex flex-col gap-3 rounded border border-edge bg-ink/60 p-3">
      <span className="text-[10px] font-semibold uppercase tracking-wide text-cyan-300">
        Lipsync (Deepfake)
      </span>

      {error !== null && <div className="text-xs text-red-400">{error}</div>}
      {jobId !== null && <div className="text-xs text-sky-400">Job gestartet: {jobId}</div>}

      <div className="flex flex-col gap-1.5">
        <span className="text-[10px] font-medium uppercase tracking-wide text-slate-500">
          Schritt 1 — Consent
        </span>
        {confirmedLabel !== null ? (
          <div className="text-xs text-emerald-400">Consent für {confirmedLabel}</div>
        ) : (
          <div className="flex gap-2">
            <input
              aria-label="Subjekt-Label für Lipsync-Consent"
              value={subjectLabel}
              onChange={(e) => setSubjectLabel(e.target.value)}
              disabled={busy}
              placeholder="Subjekt-Label"
              className="min-w-0 flex-1 rounded border border-edge bg-panel px-2 py-1 text-xs text-slate-200 placeholder:text-slate-600 disabled:opacity-50"
            />
            <button
              type="button"
              onClick={() => void confirmConsent()}
              disabled={busy || projectId === null || subjectLabel.trim() === ""}
              className="shrink-0 rounded bg-amber-700 px-3 py-1 text-xs font-medium text-white hover:bg-amber-600 disabled:opacity-40"
            >
              Consent bestätigen
            </button>
          </div>
        )}
      </div>

      <label className="flex items-center gap-2 text-xs text-slate-300">
        <input
          aria-label="Lizenz und Nutzung bestätigt"
          type="checkbox"
          checked={licenseAccepted}
          onChange={(e) => setLicenseAccepted(e.target.checked)}
          disabled={busy}
          className="h-3.5 w-3.5"
        />
        Lizenz/Sidecar-Nutzung bestätigt
      </label>

      <div className="grid grid-cols-2 gap-2 text-xs">
        <label className="col-span-2 flex flex-col gap-1 text-slate-400">
          Audio
          <select
            aria-label="Lipsync-Audio auswählen"
            value={audioAssetId}
            onChange={(e) => setAudioAssetId(e.target.value)}
            disabled={busy || audioAssets.length === 0}
            className="rounded border border-edge bg-panel px-2 py-1 text-slate-200 disabled:opacity-50"
          >
            {audioAssets.length === 0 ? (
              <option value="">Keine Audio-Assets</option>
            ) : (
              audioAssets.map((asset) => (
                <option key={asset.id} value={asset.id}>
                  {asset.display_name}
                </option>
              ))
            )}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-slate-400">
          seq in
          <input
            aria-label="Lipsync seq in"
            type="number"
            min={0}
            step={1}
            value={seqIn}
            onChange={(e) => setSeqIn(Math.max(0, Math.trunc(Number(e.target.value)) || 0))}
            disabled={busy}
            className="rounded border border-edge bg-panel px-2 py-1 tabular-nums text-slate-200 disabled:opacity-50"
          />
        </label>
        <label className="flex flex-col gap-1 text-slate-400">
          seq out
          <input
            aria-label="Lipsync seq out"
            type="number"
            min={0}
            step={1}
            value={seqOut}
            onChange={(e) => setSeqOut(Math.max(0, Math.trunc(Number(e.target.value)) || 0))}
            disabled={busy}
            className="rounded border border-edge bg-panel px-2 py-1 tabular-nums text-slate-200 disabled:opacity-50"
          />
        </label>
        <label className="col-span-2 flex flex-col gap-1 text-slate-400">
          Backend
          <select
            aria-label="Lipsync-Backend auswählen"
            value={backend}
            onChange={(e) => setBackend(e.target.value === "vibevideo" ? "vibevideo" : "stub")}
            disabled={busy}
            className="rounded border border-edge bg-panel px-2 py-1 text-slate-200 disabled:opacity-50"
          >
            <option value="stub">Stub</option>
            <option value="vibevideo">VibeVideo Sidecar</option>
          </select>
        </label>
        <RuntimeSelect
          client={client}
          effect="lipsync"
          label="Lipsync-Runtime auswählen"
          value={runtimeId}
          onChange={setRuntimeId}
          disabled={busy}
          reloadKey={runtimeReloadKey}
          labelClassName="col-span-2 flex flex-col gap-1 text-slate-400"
          selectClassName="rounded border border-edge bg-panel px-2 py-1 text-slate-200 disabled:opacity-50"
        />
      </div>

      <button
        type="button"
        onClick={() => void submit()}
        disabled={disabled}
        className="self-start rounded bg-cyan-700 px-3 py-1 text-xs font-medium text-white hover:bg-cyan-600 disabled:opacity-40"
      >
        {backend === "vibevideo" ? "Lipsync (VibeVideo)" : "Lipsync (stub)"}
      </button>
      <p className="text-[10px] leading-relaxed text-slate-600">
        Erzeugt ein synthetisch markiertes Replace-Overlay. Der echte VibeVideo-Pfad bleibt ein
        lokaler Sidecar; Laura lädt keine Deepfake-Modelle in den Kern.
      </p>
    </section>
  );
}
