import { type ReactElement, useEffect, useRef, useState } from "react";

import { type LauraClient } from "../api";
import { useJobStatus } from "../hooks/useJobStatus";
import { log } from "../shared/log";
import { framesToTimecode } from "../shared/timecode";

export interface ReenactPanelProps {
  client: LauraClient;
  projectId: string | null;
  timelineId: string | null;
  assets: { id: string; display_name: string }[];
  /** Called after a successful reenact so the parent can reload. */
  onChange: () => void;
  /** Current live playhead position in sequence frames, forwarded from SequencePlayer. */
  currentSeqFrame: number;
  /** Numerator of the project sequence frame rate (e.g. 30000 for 29.97). */
  rateNum: number;
  /** Denominator of the project sequence frame rate (e.g. 1001 for 29.97). */
  rateDen: number;
}

function jobChipClass(status: string): string {
  if (status === "failed") return "border-red-800 bg-red-950/40 text-red-200";
  if (status === "succeeded") return "border-emerald-800 bg-emerald-950/30 text-emerald-200";
  if (status === "running" || status === "leased" || status === "queued")
    return "border-sky-800 bg-sky-950/30 text-sky-200";
  return "border-edge bg-panel text-slate-300";
}

function jobChipLabel(status: string): string {
  if (status === "succeeded") return "Fertig ✓";
  if (status === "failed") return "Fehlgeschlagen";
  if (status === "cancelled") return "Abgebrochen";
  if (status === "running" || status === "leased") return "Läuft…";
  if (status === "queued") return "In Warteschlange";
  return status;
}

/**
 * Two-step panel for reenact (identity-layer / stub or LivePortrait sidecar):
 *
 *  1. Consent step — enter a subject label and confirm consent
 *     → `client.createConsent(projectId, { subjectLabel })`
 *     → stores the returned `consentId` in local state
 *
 *  2. Reenact step — choose seq in/out (integer frames), pick a portrait asset,
 *     then kick off the job via `client.reenact(timelineId, { … })`.
 *     The Reenact button is DISABLED until consent is confirmed.
 *     Live job status is polled every 1.5 s until terminal.
 *     On success `onChange()` fires exactly once; on failure a Retry button appears.
 *
 * No `any`, no console.log — errors routed through `log.error`.
 */
export function ReenactPanel({
  client,
  projectId,
  timelineId,
  assets,
  onChange,
  currentSeqFrame,
  rateNum,
  rateDen,
}: ReenactPanelProps): ReactElement {
  // --- Consent step state ---
  const [subjectLabel, setSubjectLabel] = useState<string>("");
  const [consentId, setConsentId] = useState<string | null>(null);
  const [confirmedLabel, setConfirmedLabel] = useState<string | null>(null);
  const [consentBusy, setConsentBusy] = useState(false);
  const [consentError, setConsentError] = useState<string | null>(null);

  // --- Reenact step state ---
  const [portraitAssetId, setPortraitAssetId] = useState<string>(assets[0]?.id ?? "");
  const [seqIn, setSeqIn] = useState<number>(0);
  const [seqOut, setSeqOut] = useState<number>(0);
  const [backend, setBackend] = useState<"stub" | "liveportrait">("stub");
  const [reenactBusy, setReenactBusy] = useState(false);
  const [reenactError, setReenactError] = useState<string | null>(null);
  const [lastJobId, setLastJobId] = useState<string | null>(null);

  // Guard: fire onChange exactly once per succeeded job.
  const onChangeFiredRef = useRef<string | null>(null);

  // Default portrait picker to first asset once assets load.
  useEffect(() => {
    if (!portraitAssetId && assets.length > 0) {
      setPortraitAssetId(assets[0].id);
    }
  }, [assets, portraitAssetId]);

  // Reset consent (and the subject label) when projectId changes — a confirmed
  // consent must never carry over to a different project's form.
  useEffect(() => {
    setConsentId(null);
    setConfirmedLabel(null);
    setConsentError(null);
    setSubjectLabel("");
  }, [projectId]);

  // Poll the active job until terminal.
  const { jobStatus, error: jobError, isRunning } = useJobStatus(client, lastJobId);

  // Fire onChange exactly once when the job succeeds.
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

  async function confirmConsent(): Promise<void> {
    if (!projectId || !subjectLabel.trim()) return;
    setConsentBusy(true);
    setConsentError(null);
    try {
      const record = await client.createConsent(projectId, { subjectLabel: subjectLabel.trim() });
      setConsentId(record.id);
      setConfirmedLabel(record.subject_label);
      log.info("consent created", record.id, "for subject", record.subject_label);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      log.error("createConsent failed:", msg);
      setConsentError(msg);
    } finally {
      setConsentBusy(false);
    }
  }

  async function submitReenact(): Promise<void> {
    if (!timelineId || !consentId || !portraitAssetId || seqOut <= seqIn) return;
    setReenactBusy(true);
    setReenactError(null);
    setLastJobId(null);
    onChangeFiredRef.current = null;
    try {
      const result = await client.reenact(timelineId, {
        seqIn,
        seqOut,
        portraitAssetId,
        consentId,
        backend,
      });
      setLastJobId(result.job_id);
      log.info(
        "reenact job started",
        result.job_id,
        "on timeline",
        timelineId,
        "frames",
        seqIn,
        "–",
        seqOut,
      );
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      log.error("reenact failed:", msg);
      setReenactError(msg);
    } finally {
      setReenactBusy(false);
    }
  }

  const reenactDisabled =
    reenactBusy ||
    isRunning ||
    !consentId ||
    !timelineId ||
    seqOut <= seqIn ||
    !portraitAssetId;

  return (
    <div className="flex flex-col gap-3 rounded-md border border-edge bg-ink/60 p-3">
      {/* Heading */}
      <span className="text-[10px] font-semibold uppercase tracking-wide text-amber-300">
        Reenact (Identitäts-Ebene)
      </span>

      {/* ── Consent step ── */}
      <div className="flex flex-col gap-1.5">
        <span className="text-[10px] font-medium uppercase tracking-wide text-slate-500">
          Schritt 1 — Consent
        </span>

        {consentError && <div className="text-xs text-red-400">{consentError}</div>}

        {confirmedLabel !== null ? (
          <div className="text-xs text-emerald-400">
            ✓ Consent für <span className="font-semibold">{confirmedLabel}</span>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <input
              type="text"
              placeholder={'Subjekt-Label (z. B. „Person A")'}
              value={subjectLabel}
              onChange={(e) => setSubjectLabel(e.target.value)}
              disabled={consentBusy}
              aria-label="Subjekt-Label für Consent"
              className="min-w-0 flex-1 rounded border border-edge bg-panel px-2 py-1 text-xs text-slate-200 placeholder:text-slate-600 disabled:opacity-50"
            />
            <button
              type="button"
              onClick={() => void confirmConsent()}
              disabled={consentBusy || !projectId || !subjectLabel.trim()}
              className="shrink-0 rounded bg-amber-700 px-3 py-1 text-xs font-medium text-white hover:bg-amber-600 disabled:opacity-40"
            >
              {consentBusy ? "…" : "Consent bestätigen"}
            </button>
          </div>
        )}
      </div>

      {/* ── Reenact step ── */}
      <div className="flex flex-col gap-1.5">
        <span className="text-[10px] font-medium uppercase tracking-wide text-slate-500">
          Schritt 2 — Reenact
        </span>

        {reenactError !== null && <div className="text-xs text-red-400">{reenactError}</div>}

        {/* Live job status chip */}
        {lastJobId !== null && jobStatus !== null && (
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center gap-2">
              <span
                className={`rounded border px-2 py-0.5 text-[11px] ${jobChipClass(jobStatus.status)}`}
              >
                {jobChipLabel(jobStatus.status)}
              </span>
              <span className="truncate text-[10px] text-slate-600">{lastJobId}</span>
            </div>
            {jobStatus.status === "failed" && (
              <>
                {jobError !== null && (
                  <div className="rounded border border-red-900/70 bg-red-950/20 p-2 text-xs text-red-200">
                    {jobError}
                  </div>
                )}
                <button
                  type="button"
                  onClick={() => void submitReenact()}
                  disabled={reenactBusy || !consentId || !timelineId || seqOut <= seqIn || !portraitAssetId}
                  className="self-start rounded bg-sky-700 px-3 py-1 text-xs font-medium text-white hover:bg-sky-600 disabled:opacity-40"
                >
                  Erneut versuchen
                </button>
              </>
            )}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          {/* Portrait asset picker */}
          <select
            value={portraitAssetId}
            onChange={(e) => setPortraitAssetId(e.target.value)}
            disabled={reenactBusy || isRunning || assets.length === 0}
            aria-label="Portrait-Asset auswählen"
            className="min-w-0 flex-1 truncate rounded border border-edge bg-panel px-2 py-1 text-xs text-slate-200 disabled:opacity-50"
          >
            {assets.length === 0 ? (
              <option value="">— keine Assets —</option>
            ) : (
              assets.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.display_name}
                </option>
              ))
            )}
          </select>

          <select
            value={backend}
            onChange={(e) => setBackend(e.target.value === "liveportrait" ? "liveportrait" : "stub")}
            disabled={reenactBusy || isRunning}
            aria-label="Reenact-Backend auswählen"
            className="rounded border border-edge bg-panel px-2 py-1 text-xs text-slate-200 disabled:opacity-50"
          >
            <option value="stub">Stub</option>
            <option value="liveportrait">LivePortrait Sidecar</option>
          </select>

          {/* seq in */}
          <div className="flex flex-col gap-0.5">
            <label className="flex items-center gap-1 text-xs text-slate-400">
              <span>seq in</span>
              <input
                type="number"
                min={0}
                step={1}
                value={seqIn}
                onChange={(e) => setSeqIn(Math.max(0, Math.trunc(Number(e.target.value)) || 0))}
                disabled={reenactBusy || isRunning}
                aria-label="Sequenz-Einpunkt (Frames)"
                className="w-20 rounded border border-edge bg-panel px-1.5 py-0.5 text-xs tabular-nums text-slate-200 disabled:opacity-50"
              />
              <span className="text-[10px] text-slate-600 tabular-nums">
                {framesToTimecode(seqIn, rateNum, rateDen)}
              </span>
            </label>
            <button
              type="button"
              onClick={() => setSeqIn(Math.max(0, Math.trunc(currentSeqFrame)))}
              disabled={reenactBusy || isRunning || !Number.isFinite(currentSeqFrame)}
              className="self-start rounded border border-edge bg-panel px-1.5 py-0.5 text-[10px] text-slate-400 hover:bg-slate-700 hover:text-slate-200 disabled:opacity-40"
            >
              In = Playhead
            </button>
          </div>

          {/* seq out */}
          <div className="flex flex-col gap-0.5">
            <label className="flex items-center gap-1 text-xs text-slate-400">
              <span>seq out</span>
              <input
                type="number"
                min={0}
                step={1}
                value={seqOut}
                onChange={(e) => setSeqOut(Math.max(0, Math.trunc(Number(e.target.value)) || 0))}
                disabled={reenactBusy || isRunning}
                aria-label="Sequenz-Auspunkt exklusiv (Frames)"
                className="w-20 rounded border border-edge bg-panel px-1.5 py-0.5 text-xs tabular-nums text-slate-200 disabled:opacity-50"
              />
              <span className="text-[10px] text-slate-600 tabular-nums">
                {framesToTimecode(seqOut, rateNum, rateDen)}
              </span>
            </label>
            <button
              type="button"
              onClick={() => setSeqOut(Math.max(0, Math.trunc(currentSeqFrame)))}
              disabled={reenactBusy || isRunning || !Number.isFinite(currentSeqFrame)}
              className="self-start rounded border border-edge bg-panel px-1.5 py-0.5 text-[10px] text-slate-400 hover:bg-slate-700 hover:text-slate-200 disabled:opacity-40"
            >
              Out = Playhead
            </button>
          </div>

          {/* Submit */}
          <button
            type="button"
            onClick={() => void submitReenact()}
            disabled={reenactDisabled}
            title={!consentId ? "Zuerst Consent bestätigen (Schritt 1)" : undefined}
            className="rounded bg-sky-700 px-3 py-1 text-xs font-medium text-white hover:bg-sky-600 disabled:opacity-40"
          >
            {reenactBusy ? "…" : isRunning ? "Läuft…" : backend === "liveportrait" ? "Reenact (LivePortrait)" : "Reenact (stub)"}
          </button>
        </div>
      </div>

      {/* Muted hint */}
      <p className="text-[10px] leading-relaxed text-slate-600">
        Stub erzeugt eine sichtbar markierte Platzhalter-Ausgabe. LivePortrait nutzt den lokalen Sidecar unter LAURA_LIVEPORTRAIT_URL.
        Die Ausgabe wird als <span className="italic">synthetic</span> gekennzeichnet.
      </p>
    </div>
  );
}
