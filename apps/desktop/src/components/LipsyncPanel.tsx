import { type ReactElement, useEffect, useMemo, useRef, useState } from "react";

import { type Asset, type LauraClient } from "../api";
import { useJobStatus } from "../hooks/useJobStatus";
import { log } from "../shared/log";

function jobChipClass(status: string): string {
  if (status === "failed") return "border-status-err bg-status-err/15 text-status-err";
  if (status === "succeeded") return "border-status-ok bg-status-ok/20 text-status-ok";
  if (status === "running" || status === "leased" || status === "queued")
    return "border-sky-800 bg-sky-950/30 text-sky-200";
  return "border-bezel bg-surface-1 text-content-muted";
}

function jobChipLabel(status: string): string {
  if (status === "succeeded") return "Fertig ✓";
  if (status === "failed") return "Fehlgeschlagen";
  if (status === "cancelled") return "Abgebrochen";
  if (status === "queued") return "In Warteschlange";
  if (status === "running" || status === "leased") return "Läuft…";
  return "Läuft…";
}

export function LipsyncPanel({
  client,
  projectId,
  timelineId,
  assets,
  onChange,
}: {
  client: LauraClient;
  projectId: string | null;
  timelineId: string | null;
  assets: Asset[];
  onChange: () => void;
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
  const [licenseAccepted, setLicenseAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);

  const { jobStatus, error: jobError, isRunning } = useJobStatus(client, jobId);

  // Guard: fire onChange exactly once per succeeded job.
  const onChangeFiredRef = useRef<string | null>(null);

  useEffect(() => {
    if (
      jobStatus !== null &&
      jobStatus.status === "succeeded" &&
      onChangeFiredRef.current !== jobStatus.id
    ) {
      onChangeFiredRef.current = jobStatus.id;
      onChange();
    }
  }, [jobStatus, onChange]);

  useEffect(() => {
    if (!audioAssetId && audioAssets.length > 0) setAudioAssetId(audioAssets[0].id);
  }, [audioAssetId, audioAssets]);

  useEffect(() => {
    setConsentId(null);
    setConfirmedLabel(null);
    setSubjectLabel("");
    setLicenseAccepted(false);
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
    onChangeFiredRef.current = null;
    try {
      const accepted = await client.lipsync(timelineId, {
        seqIn,
        seqOut,
        audioAssetId,
        consentId,
        licenseAccepted,
        backend,
        qualityThreshold: 0.6,
      });
      setJobId(accepted.job_id);
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
    isRunning ||
    timelineId === null ||
    consentId === null ||
    !licenseAccepted ||
    !audioAssetId ||
    seqOut <= seqIn;

  return (
    <section className="flex flex-col gap-3 rounded border border-bezel bg-surface-0/60 p-3">
      <span className="text-[10px] font-semibold uppercase tracking-wide text-cyan-300">
        Lipsync (Deepfake)
      </span>

      {error !== null && <div className="text-xs text-status-err">{error}</div>}

      {/* Live job status chip */}
      {jobId !== null && jobStatus !== null && (
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-2">
            <span
              className={`rounded border px-2 py-0.5 text-[11px] ${jobChipClass(jobStatus.status)}`}
            >
              {jobChipLabel(jobStatus.status)}
            </span>
            <span className="truncate text-[10px] text-content-faint">{jobId}</span>
          </div>
          {jobStatus.status === "failed" && jobError !== null && (
            <div className="rounded border border-status-err/40 bg-status-err/10 p-2 text-xs text-status-err">
              {jobError}
            </div>
          )}
        </div>
      )}

      <div className="flex flex-col gap-1.5">
        <span className="text-[10px] font-medium uppercase tracking-wide text-content-faint">
          Schritt 1 — Consent
        </span>
        {confirmedLabel !== null ? (
          <div className="text-xs text-status-ok">Consent für {confirmedLabel}</div>
        ) : (
          <div className="flex gap-2">
            <input
              aria-label="Subjekt-Label für Lipsync-Consent"
              value={subjectLabel}
              onChange={(e) => setSubjectLabel(e.target.value)}
              disabled={busy}
              placeholder="Subjekt-Label"
              className="min-w-0 flex-1 rounded border border-bezel bg-surface-1 px-2 py-1 text-xs text-content-strong placeholder:text-content-faint disabled:opacity-50"
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

      <label className="flex items-center gap-2 text-xs text-content-muted">
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
        <label className="col-span-2 flex flex-col gap-1 text-content-muted">
          Audio
          <select
            aria-label="Lipsync-Audio auswählen"
            value={audioAssetId}
            onChange={(e) => setAudioAssetId(e.target.value)}
            disabled={busy || audioAssets.length === 0}
            className="rounded border border-bezel bg-surface-1 px-2 py-1 text-content-strong disabled:opacity-50"
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
        <label className="flex flex-col gap-1 text-content-muted">
          seq in
          <input
            aria-label="Lipsync seq in"
            type="number"
            min={0}
            step={1}
            value={seqIn}
            onChange={(e) => setSeqIn(Math.max(0, Math.trunc(Number(e.target.value)) || 0))}
            disabled={busy}
            className="rounded border border-bezel bg-surface-1 px-2 py-1 tabular-nums text-content-strong disabled:opacity-50"
          />
        </label>
        <label className="flex flex-col gap-1 text-content-muted">
          seq out
          <input
            aria-label="Lipsync seq out"
            type="number"
            min={0}
            step={1}
            value={seqOut}
            onChange={(e) => setSeqOut(Math.max(0, Math.trunc(Number(e.target.value)) || 0))}
            disabled={busy}
            className="rounded border border-bezel bg-surface-1 px-2 py-1 tabular-nums text-content-strong disabled:opacity-50"
          />
        </label>
        <label className="col-span-2 flex flex-col gap-1 text-content-muted">
          Backend
          <select
            aria-label="Lipsync-Backend auswählen"
            value={backend}
            onChange={(e) => setBackend(e.target.value === "vibevideo" ? "vibevideo" : "stub")}
            disabled={busy}
            className="rounded border border-bezel bg-surface-1 px-2 py-1 text-content-strong disabled:opacity-50"
          >
            <option value="stub">Stub</option>
            <option value="vibevideo">VibeVideo Sidecar</option>
          </select>
        </label>
      </div>

      <button
        type="button"
        onClick={() => void submit()}
        disabled={disabled}
        className="self-start rounded bg-cyan-700 px-3 py-1 text-xs font-medium text-white hover:bg-cyan-600 disabled:opacity-40"
      >
        {backend === "vibevideo" ? "Lipsync (VibeVideo)" : "Lipsync (stub)"}
      </button>
      <p className="text-[10px] leading-relaxed text-content-faint">
        Erzeugt ein synthetisch markiertes Replace-Overlay. Der echte VibeVideo-Pfad bleibt ein
        lokaler Sidecar; Laura lädt keine Deepfake-Modelle in den Kern.
      </p>
    </section>
  );
}
