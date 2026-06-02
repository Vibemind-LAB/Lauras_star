import { type ReactElement, useCallback, useEffect, useState } from "react";

import { type Asset, type LauraClient, type Segment, type Shot, type Timeline } from "../api";

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

type Status = "idle" | "running" | "done" | "error";

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

function ShotStrip({
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

function asrNote(diagnostics: Record<string, unknown>): string | null {
  const asr = diagnostics["asr"];
  if (asr && typeof asr === "object") {
    const rec = asr as Record<string, unknown>;
    if (rec["status"] === "skipped" && typeof rec["reason"] === "string") {
      return `Transkript übersprungen: ${rec["reason"]}`;
    }
  }
  return null;
}

function SegmentText({
  segment,
  currentFrame,
  onSeek,
}: {
  segment: Segment;
  currentFrame: number;
  onSeek?: (frame: number) => void;
}): ReactElement {
  if (segment.words.length === 0) {
    const active = currentFrame >= segment.start_frame && currentFrame < segment.end_frame;
    return (
      <button
        type="button"
        onClick={() => onSeek?.(segment.start_frame)}
        className={`text-left ${active ? "text-sky-300" : ""} ${onSeek ? "hover:underline" : ""}`}
      >
        {segment.text}
      </button>
    );
  }
  return (
    <span className="leading-relaxed">
      {segment.words.map((w) => {
        const active = currentFrame >= w.start_frame && currentFrame < w.end_frame;
        return (
          <span key={w.id}>
            <button
              type="button"
              onClick={() => onSeek?.(w.start_frame)}
              className={`rounded px-0.5 ${active ? "bg-sky-600/50 text-white" : ""} ${
                onSeek ? "hover:bg-edge" : ""
              }`}
            >
              {w.text}
            </button>{" "}
          </span>
        );
      })}
    </span>
  );
}

export function AnalysisPanel({
  client,
  asset,
  roughCut,
  onTimelineChange,
  currentFrame,
  onSeek,
}: {
  client: LauraClient;
  asset: Asset;
  roughCut: Timeline | null;
  onTimelineChange: () => void;
  currentFrame?: number;
  onSeek?: (frame: number) => void;
}): ReactElement {
  const [status, setStatus] = useState<Status>("idle");
  const [shots, setShots] = useState<Shot[]>([]);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadResults = useCallback(async () => {
    const [sh, tr] = await Promise.all([client.getShots(asset.id), client.getTranscript(asset.id)]);
    setShots(sh);
    setSegments(tr);
  }, [client, asset.id]);

  useEffect(() => {
    let cancelled = false;
    setStatus("idle");
    setShots([]);
    setSegments([]);
    setNote(null);
    setError(null);
    void (async () => {
      const run = await client.getLatestAnalysis(asset.id);
      if (cancelled || !run) return;
      setNote(asrNote(run.diagnostics));
      if (run.status === "succeeded") {
        setStatus("done");
        await loadResults();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, asset.id, loadResults]);

  async function runAnalysis(): Promise<void> {
    setStatus("running");
    setError(null);
    try {
      await client.startAnalysis(asset.id, { scene: true, asr: true });
      for (let i = 0; i < 180; i++) {
        const run = await client.getLatestAnalysis(asset.id);
        if (run && (run.status === "succeeded" || run.status === "failed")) {
          setNote(asrNote(run.diagnostics));
          break;
        }
        await sleep(700);
      }
      await loadResults();
      setStatus("done");
    } catch (e) {
      setError(String(e));
      setStatus("error");
    }
  }

  async function exportCaptions(fmt: "srt" | "vtt"): Promise<void> {
    try {
      const text = await client.getCaptions(asset.id, fmt);
      await window.laura.saveTextFile(`${asset.display_name}.${fmt}`, text);
    } catch (e) {
      setError(String(e));
    }
  }

  async function appendShot(shot: Shot): Promise<void> {
    if (!roughCut) return;
    try {
      await client.applyOperation(roughCut.id, {
        op: "append_clip",
        asset_id: asset.id,
        src_in_frame: shot.src_in_frame,
        src_out_frame_exclusive: shot.src_out_frame_exclusive,
      });
      onTimelineChange();
    } catch (e) {
      setError(String(e));
    }
  }

  async function appendSegment(seg: Segment): Promise<void> {
    if (!roughCut || seg.words.length === 0) return;
    try {
      await client.applyOperation(roughCut.id, {
        op: "append_from_words",
        word_start_id: seg.words[0].id,
        word_end_id: seg.words[seg.words.length - 1].id,
      });
      onTimelineChange();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-slate-500">Analyse</span>
        <button
          type="button"
          onClick={() => void runAnalysis()}
          disabled={status === "running"}
          className="rounded-md bg-panel px-2 py-1 text-xs text-slate-200 transition hover:bg-edge disabled:opacity-40"
        >
          {status === "running" ? "Analysiere…" : "Analyse starten"}
        </button>
      </div>

      {error && <div className="text-xs text-red-400">{error}</div>}

      <div>
        <div className="mb-1 text-xs text-slate-500">Shots ({shots.length})</div>
        <ShotStrip client={client} shots={shots} onAppend={roughCut ? appendShot : undefined} />
      </div>

      <div>
        <div className="mb-1 flex items-center justify-between">
          <span className="text-xs text-slate-500">Transkript</span>
          {segments.length > 0 && (
            <span className="flex gap-1">
              <button
                type="button"
                onClick={() => void exportCaptions("srt")}
                className="rounded bg-panel px-2 py-0.5 text-xs text-slate-300 hover:bg-edge"
              >
                SRT
              </button>
              <button
                type="button"
                onClick={() => void exportCaptions("vtt")}
                className="rounded bg-panel px-2 py-0.5 text-xs text-slate-300 hover:bg-edge"
              >
                VTT
              </button>
            </span>
          )}
        </div>
        {segments.length === 0 ? (
          <div className="text-xs text-slate-600">{note ?? "noch kein Transkript"}</div>
        ) : (
          <ul className="max-h-48 space-y-1 overflow-auto">
            {segments.map((seg) => (
              <li
                key={seg.id}
                className={`flex items-start justify-between gap-2 rounded-md px-3 py-2 text-sm text-slate-200 ${
                  currentFrame != null &&
                  currentFrame >= seg.start_frame &&
                  currentFrame < seg.end_frame
                    ? "bg-sky-900/40 ring-1 ring-sky-700"
                    : "bg-panel"
                }`}
              >
                <span>
                  {seg.speaker_label && (
                    <span className="mr-2 rounded bg-ink px-1.5 py-0.5 text-xs text-sky-300">
                      {seg.speaker_label}
                    </span>
                  )}
                  <SegmentText segment={seg} currentFrame={currentFrame ?? -1} onSeek={onSeek} />
                </span>
                {roughCut && seg.words.length > 0 && (
                  <button
                    type="button"
                    onClick={() => void appendSegment(seg)}
                    title="an Rough Cut anhängen"
                    className="shrink-0 rounded bg-ink px-2 text-xs text-emerald-300 hover:bg-edge"
                  >
                    →
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
