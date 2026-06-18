import { type ReactElement, useCallback, useEffect, useMemo, useState } from "react";

import { type AiRuntime, type AiRuntimeEvent, type LauraClient } from "../api";
import { log } from "../shared/log";

function readStatusState(runtime: AiRuntime): string {
  const state = runtime.status["state"];
  return typeof state === "string" ? state : "unknown";
}

function readStatusReady(runtime: AiRuntime): boolean | null {
  const ready = runtime.status["ready"];
  return typeof ready === "boolean" ? ready : null;
}

function formatStatusPayload(value: Record<string, unknown>): string {
  return JSON.stringify(value, null, 2);
}

function runtimeTone(runtime: AiRuntime): string {
  const ready = readStatusReady(runtime);
  const state = readStatusState(runtime);
  if (ready === true || state === "ready" || state === "running") {
    return "border-emerald-800/80 bg-emerald-950/30 text-emerald-200";
  }
  if (state === "stopped" || state === "disabled") {
    return "border-amber-800/80 bg-amber-950/30 text-amber-200";
  }
  if (state === "error" || state === "failed" || runtime.enabled === false) {
    return "border-red-800/80 bg-red-950/30 text-red-200";
  }
  return "border-edge bg-panel text-slate-300";
}

function runtimeMeta(runtime: AiRuntime): string {
  const parts: string[] = [runtime.effect, runtime.kind];
  if (runtime.requires_gpu) parts.push("GPU");
  return parts.join(" · ");
}

export function RuntimeStatusPanel({
  client,
  reloadKey = 0,
}: {
  client: LauraClient;
  reloadKey?: number;
}): ReactElement {
  const [runtimes, setRuntimes] = useState<AiRuntime[]>([]);
  const [eventsByRuntime, setEventsByRuntime] = useState<Record<string, AiRuntimeEvent[]>>({});
  const [expandedStatusId, setExpandedStatusId] = useState<string | null>(null);
  const [expandedEventsId, setExpandedEventsId] = useState<string | null>(null);
  const [busyActionId, setBusyActionId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      setRuntimes(await client.listAiRuntimes());
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      log.error("listAiRuntimes failed:", message);
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [client]);

  useEffect(() => {
    void load();
  }, [load, reloadKey]);

  const sortedRuntimes = useMemo(
    () =>
      [...runtimes].sort((left, right) =>
        left.display_name.localeCompare(right.display_name, "de", { sensitivity: "base" }),
      ),
    [runtimes],
  );

  async function runAction(
    runtimeId: string,
    action: (id: string) => Promise<unknown>,
  ): Promise<void> {
    setBusyActionId(runtimeId);
    setError(null);
    try {
      await action(runtimeId);
      await load();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      log.error("runtime action failed:", message);
      setError(message);
    } finally {
      setBusyActionId(null);
    }
  }

  async function toggleEvents(runtimeId: string): Promise<void> {
    if (expandedEventsId === runtimeId) {
      setExpandedEventsId(null);
      return;
    }
    if (eventsByRuntime[runtimeId] === undefined) {
      try {
        const events = await client.listAiRuntimeEvents(runtimeId);
        setEventsByRuntime((current) => ({ ...current, [runtimeId]: events }));
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        log.error("listAiRuntimeEvents failed:", message);
        setError(message);
        return;
      }
    }
    setExpandedEventsId(runtimeId);
  }

  return (
    <section className="flex flex-col gap-3 rounded border border-edge bg-panel/50 p-3">
      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="text-xs font-semibold text-slate-200">AI Runtimes</div>
          <div className="text-[11px] text-slate-600">Status, Refresh, Start/Stop und letzte Events</div>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="rounded border border-edge bg-ink px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800 disabled:opacity-40"
        >
          {loading ? "Lädt..." : "Neu laden"}
        </button>
      </div>

      {error !== null && (
        <div className="rounded border border-red-900/70 bg-red-950/20 p-2 text-xs text-red-200">
          {error}
        </div>
      )}

      {sortedRuntimes.length === 0 ? (
        <div className="text-xs text-slate-500">Noch keine Runtime registriert.</div>
      ) : (
        <div className="flex flex-col gap-2">
          {sortedRuntimes.map((runtime) => {
            const runtimeEvents = eventsByRuntime[runtime.id] ?? [];
            const busy = busyActionId === runtime.id;

            return (
              <article key={runtime.id} className="rounded border border-edge bg-ink/60 p-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-xs font-medium text-slate-100">
                      {runtime.display_name}
                    </div>
                    <div className="text-[11px] text-slate-500">{runtimeMeta(runtime)}</div>
                  </div>
                  <span
                    className={`shrink-0 rounded border px-2 py-0.5 text-[11px] font-medium ${runtimeTone(runtime)}`}
                  >
                    {readStatusState(runtime)}
                  </span>
                </div>

                <div className="mt-2 flex flex-wrap gap-1">
                  <button
                    type="button"
                    aria-label={`Refresh ${runtime.display_name}`}
                    onClick={() => void runAction(runtime.id, client.refreshAiRuntime.bind(client))}
                    disabled={busy}
                    className="rounded bg-slate-700 px-2 py-1 text-[11px] text-slate-100 hover:bg-slate-600 disabled:opacity-40"
                  >
                    Refresh
                  </button>
                  {runtime.kind === "container" && (
                    <>
                      <button
                        type="button"
                        onClick={() => void runAction(runtime.id, client.startAiRuntime.bind(client))}
                        disabled={busy}
                        className="rounded bg-emerald-700 px-2 py-1 text-[11px] text-white hover:bg-emerald-600 disabled:opacity-40"
                      >
                        Start
                      </button>
                      <button
                        type="button"
                        onClick={() => void runAction(runtime.id, client.stopAiRuntime.bind(client))}
                        disabled={busy}
                        className="rounded bg-red-800 px-2 py-1 text-[11px] text-white hover:bg-red-700 disabled:opacity-40"
                      >
                        Stop
                      </button>
                    </>
                  )}
                  <button
                    type="button"
                    onClick={() =>
                      setExpandedStatusId((current) => (current === runtime.id ? null : runtime.id))
                    }
                    className="rounded border border-edge bg-panel px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800"
                  >
                    Status
                  </button>
                  <button
                    type="button"
                    onClick={() => void toggleEvents(runtime.id)}
                    className="rounded border border-edge bg-panel px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800"
                  >
                    Events
                  </button>
                </div>

                {expandedStatusId === runtime.id && (
                  <pre className="mt-2 overflow-x-auto rounded border border-edge bg-panel p-2 text-[10px] leading-relaxed text-slate-300">
                    {formatStatusPayload(runtime.status)}
                  </pre>
                )}

                {expandedEventsId === runtime.id && (
                  <div className="mt-2 flex flex-col gap-1 rounded border border-edge bg-panel p-2">
                    {runtimeEvents.length === 0 ? (
                      <div className="text-[11px] text-slate-500">Keine Events.</div>
                    ) : (
                      runtimeEvents.map((event) => (
                        <div key={event.id} className="rounded bg-ink/70 px-2 py-1 text-[11px]">
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-medium text-slate-200">{event.message}</span>
                            <span className="shrink-0 uppercase text-slate-500">
                              {event.level}
                            </span>
                          </div>
                          <div className="text-slate-500">
                            {event.event_type} · {event.created_at}
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
