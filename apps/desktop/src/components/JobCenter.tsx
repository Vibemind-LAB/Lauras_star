import { type ReactElement, useCallback, useEffect, useMemo, useState } from "react";

import { type JobStatus, type LauraClient } from "../api";
import { log } from "../shared/log";

const RUNNING = new Set(["queued", "leased", "running"]);

function statusLabel(status: string): string {
  if (status === "succeeded") return "Fertig";
  if (status === "failed") return "Fehler";
  if (status === "cancelled") return "Abgebrochen";
  if (status === "running" || status === "leased") return "Läuft";
  if (status === "queued") return "Wartet";
  return status;
}

function statusClass(status: string): string {
  if (status === "failed") return "border-red-800 bg-red-950/40 text-red-200";
  if (status === "succeeded") return "border-emerald-800 bg-emerald-950/30 text-emerald-200";
  if (RUNNING.has(status)) return "border-sky-800 bg-sky-950/30 text-sky-200";
  return "border-edge bg-panel text-slate-300";
}

function errorText(job: JobStatus): string | null {
  if (!job.error_json) return null;
  try {
    const parsed = JSON.parse(job.error_json) as unknown;
    if (typeof parsed === "object" && parsed !== null) {
      const error = (parsed as { error?: unknown }).error;
      if (typeof error === "string") return error;
    }
  } catch {
    return job.error_json;
  }
  return job.error_json;
}

export function JobCenter({
  client,
}: {
  client: LauraClient;
}): ReactElement {
  const [open, setOpen] = useState(false);
  const [jobs, setJobs] = useState<JobStatus[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    try {
      setJobs(await client.listJobs(30));
      setError(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      log.error("listJobs failed:", msg);
    }
  }, [client]);

  useEffect(() => {
    if (!open) return;
    void load();
  }, [load, open]);

  useEffect(() => {
    if (!open || !jobs.some((job) => RUNNING.has(job.status))) return;
    const interval = window.setInterval(() => void load(), 1500);
    return () => window.clearInterval(interval);
  }, [jobs, load, open]);

  const counts = useMemo(() => {
    const running = jobs.filter((job) => RUNNING.has(job.status)).length;
    const failed = jobs.filter((job) => job.status === "failed").length;
    return { running, failed };
  }, [jobs]);

  async function retry(jobId: string): Promise<void> {
    setBusyId(jobId);
    try {
      await client.retryJob(jobId);
      await load();
    } finally {
      setBusyId(null);
    }
  }

  async function cancel(jobId: string): Promise<void> {
    setBusyId(jobId);
    try {
      await client.cancelJob(jobId);
      await load();
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="rounded border border-edge bg-ink px-3 py-1 text-xs text-slate-200 hover:bg-slate-800"
      >
        Jobs {counts.running > 0 ? `· ${counts.running}` : ""}{counts.failed > 0 ? ` · !${counts.failed}` : ""}
      </button>
      {open && (
        <section className="absolute right-0 z-20 mt-2 flex max-h-[70vh] w-[28rem] flex-col overflow-hidden rounded border border-edge bg-ink shadow-2xl">
          <div className="flex items-center justify-between border-b border-edge px-3 py-2">
            <div className="text-xs font-semibold text-slate-200">Job-Zentrale</div>
            <button
              type="button"
              onClick={() => void load()}
              className="rounded bg-panel px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800"
            >
              aktualisieren
            </button>
          </div>
          {error !== null && <div className="border-b border-red-900/70 p-2 text-xs text-red-300">{error}</div>}
          <div className="min-h-0 overflow-y-auto">
            {jobs.length === 0 ? (
              <div className="p-4 text-xs text-slate-500">Keine Jobs.</div>
            ) : (
              jobs.map((job) => {
                const err = errorText(job);
                const canCancel = RUNNING.has(job.status);
                const canRetry = job.status === "failed";
                return (
                  <article key={job.id} className="border-b border-edge p-3 last:border-b-0">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate text-xs font-medium text-slate-200">{job.kind}</div>
                        <div className="truncate text-[11px] text-slate-500">{job.queue} · {job.id}</div>
                      </div>
                      <span className={`shrink-0 rounded border px-2 py-0.5 text-[11px] ${statusClass(job.status)}`}>
                        {statusLabel(job.status)}
                      </span>
                    </div>
                    {err !== null && (
                      <div className="mt-2 rounded border border-red-900/70 bg-red-950/20 p-2 text-xs text-red-200">
                        {err}
                      </div>
                    )}
                    {(canCancel || canRetry) && (
                      <div className="mt-2 flex gap-2">
                        {canCancel && (
                          <button
                            type="button"
                            onClick={() => void cancel(job.id)}
                            disabled={busyId === job.id}
                            className="rounded bg-slate-700 px-2 py-1 text-[11px] text-slate-200 hover:bg-slate-600 disabled:opacity-40"
                          >
                            Cancel
                          </button>
                        )}
                        {canRetry && (
                          <button
                            type="button"
                            onClick={() => void retry(job.id)}
                            disabled={busyId === job.id}
                            className="rounded bg-sky-700 px-2 py-1 text-[11px] text-white hover:bg-sky-600 disabled:opacity-40"
                          >
                            Retry
                          </button>
                        )}
                      </div>
                    )}
                  </article>
                );
              })
            )}
          </div>
        </section>
      )}
    </div>
  );
}
