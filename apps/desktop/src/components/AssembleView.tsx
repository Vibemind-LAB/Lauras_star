import {
  Fragment,
  type DragEvent,
  type ReactElement,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  type Asset,
  type LauraClient,
  type Scene,
  type SequenceTransitionKind,
  type SequenceTranscriptBlock,
  type Timeline,
  type TimelineAudioClip,
  type TimelineClip,
} from "../api";
import { useJobStatus } from "../hooks/useJobStatus";
import { useSequence } from "../hooks/useSequence";
import { log } from "../shared/log";
import { AudioLaneControls } from "./AudioLaneControls";
import { DemoAssistantPanel } from "./DemoAssistantPanel";
import { LipsyncPanel } from "./LipsyncPanel";
import { OverlayControls } from "./OverlayControls";
import { PersonaKitPanel } from "./PersonaKitPanel";
import { ReenactPanel } from "./ReenactPanel";
import { RuntimeSetupPanel } from "./RuntimeSetupPanel";
import { RuntimeStatusPanel } from "./RuntimeStatusPanel";
import { SequencePlayer } from "./SequencePlayer";
import { TimelineBar } from "./TimelineBar";

/** A small source-frame thumbnail, fetched as a token-authed JPEG object URL. */
function Thumb({
  client,
  assetId,
  frame,
  className,
}: {
  client: LauraClient;
  assetId: string | null;
  frame: number | null;
  className?: string;
}): ReactElement {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!assetId) {
      setUrl(null);
      return;
    }
    let active = true;
    let objectUrl: string | null = null;
    client
      .assetFrameUrl(assetId, Math.max(0, frame ?? 0))
      .then((u) => {
        if (!active) {
          URL.revokeObjectURL(u);
          return;
        }
        objectUrl = u;
        setUrl(u);
      })
      .catch(() => {
        /* colour fallback stays */
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [client, assetId, frame]);
  return (
    <span
      className={`block shrink-0 overflow-hidden rounded bg-sky-900/40 ${className ?? "h-9 w-16"}`}
    >
      {url ? <img src={url} alt="" className="h-full w-full object-cover" /> : null}
    </span>
  );
}

function frameLabel(start: number, endExclusive: number): string {
  return `${start}-${Math.max(start, endExclusive - 1)} f`;
}

function transcriptErrorMessage(error: string | null): string | null {
  if (error === null) return null;
  if (error.startsWith("404:")) return "Sequenz-Transkript ist noch nicht verfügbar.";
  if (error.startsWith("422:")) return "Sequenz-Transkript passt noch nicht zur aktuellen Sequenz.";
  return "Sequenz-Transkript konnte nicht geladen werden.";
}

function jobStatusLabel(status: string): string {
  if (status === "succeeded") return "Re-Alignment abgeschlossen.";
  if (status === "failed") return "Re-Alignment fehlgeschlagen.";
  if (status === "cancelled") return "Re-Alignment abgebrochen.";
  return "Re-Alignment läuft.";
}

function voiceoverJobStatusLabel(status: string): string {
  if (status === "succeeded") return "Voiceover erzeugt und auf A2 platziert.";
  if (status === "failed") return "Voiceover fehlgeschlagen.";
  if (status === "cancelled") return "Voiceover abgebrochen.";
  return "Voiceover läuft.";
}

function alignmentStatusLabel(status: string | undefined): string | null {
  if (status === "stale") return "Nicht neu aligned";
  if (status === "aligning") return "Alignment läuft";
  if (status === "failed") return "Alignment fehlgeschlagen";
  return null;
}

function alignmentStatusClass(status: string | undefined): string {
  if (status === "failed") return "border-red-800/80 bg-red-950/40 text-red-200";
  if (status === "stale") return "border-amber-800/80 bg-amber-950/40 text-amber-200";
  if (status === "aligning") return "border-sky-800/80 bg-sky-950/40 text-sky-200";
  return "border-edge bg-panel text-slate-400";
}

function TranscriptBlockEditor({
  client,
  block,
  active,
  timelineId,
  onSaved,
  onVoiceoverCreated,
}: {
  client: LauraClient;
  block: SequenceTranscriptBlock;
  active: boolean;
  timelineId: string | null;
  onSaved: () => void;
  onVoiceoverCreated: () => void;
}): ReactElement {
  const [text, setText] = useState(block.text);
  const [busy, setBusy] = useState(false);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // One active job at a time: realign job id or voiceover job id.
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  // Tracks which kind of job is active so we know which callback to fire on success.
  const activeJobKindRef = useRef<"realign" | "voiceover" | null>(null);
  // Guard: fire callbacks exactly once per succeeded job.
  const onSuccessFiredRef = useRef<string | null>(null);

  const { jobStatus, error: jobError, isRunning: jobRunning } = useJobStatus(client, activeJobId);

  useEffect(() => {
    setText(block.text);
    setActiveJobId(null);
    activeJobKindRef.current = null;
    onSuccessFiredRef.current = null;
    setError(null);
  }, [block.segment_id, block.text]);

  // Fire the appropriate callback exactly once when the active job succeeds.
  useEffect(() => {
    if (
      jobStatus === null ||
      jobStatus.status !== "succeeded" ||
      onSuccessFiredRef.current === jobStatus.id
    ) {
      return;
    }
    onSuccessFiredRef.current = jobStatus.id;
    if (activeJobKindRef.current === "realign") {
      onSaved();
    } else if (activeJobKindRef.current === "voiceover") {
      onVoiceoverCreated();
    }
  }, [jobStatus, onSaved, onVoiceoverCreated]);

  async function saveAndRealign(): Promise<void> {
    setBusy(true);
    setError(null);
    setActiveJobId(null);
    onSuccessFiredRef.current = null;
    try {
      await client.updateTranscriptSegment(block.segment_id, { text });
      const accepted = await client.realignTranscript(block.asset_id, {
        segmentIds: [block.segment_id],
      });
      activeJobKindRef.current = "realign";
      setActiveJobId(accepted.job_id);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      log.error("transcript edit failed:", msg);
    } finally {
      setBusy(false);
    }
  }

  async function generateVoiceover(): Promise<void> {
    if (timelineId === null) {
      setError("Keine Sequenz ausgewählt.");
      return;
    }
    setVoiceBusy(true);
    setError(null);
    setActiveJobId(null);
    onSuccessFiredRef.current = null;
    try {
      const accepted = await client.createVoiceover(timelineId, {
        segmentId: block.segment_id,
        text,
        seqIn: block.seq_in_frame,
        seqOut: block.seq_out_frame_exclusive,
      });
      activeJobKindRef.current = "voiceover";
      setActiveJobId(accepted.job_id);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      log.error("voiceover generation failed:", msg);
    } finally {
      setVoiceBusy(false);
    }
  }

  const alignmentLabel = alignmentStatusLabel(block.alignment_status);
  const alignmentLanguage = block.alignment_language;

  return (
    <article
      className={`flex flex-col gap-2 border-b border-edge/80 py-3 last:border-b-0 ${
        active ? "bg-sky-950/20 px-2" : ""
      }`}
    >
      <div className="flex items-center justify-between gap-2 text-[10px] uppercase tracking-wide">
        <span className="truncate font-semibold text-slate-400">
          {block.speaker_label ?? "Transcript"}
        </span>
        <span className="flex shrink-0 items-center gap-1">
          {alignmentLabel !== null && (
            <span
              className={`rounded border px-1.5 py-0.5 font-semibold ${alignmentStatusClass(
                block.alignment_status,
              )}`}
            >
              {alignmentLabel}
            </span>
          )}
          <span className="tabular-nums text-slate-600">
            {frameLabel(block.seq_in_frame, block.seq_out_frame_exclusive)}
          </span>
        </span>
      </div>
      {block.alignment_status === "stale" && (
        <div className="rounded border border-amber-900/70 bg-amber-950/20 p-2 text-xs leading-relaxed text-amber-200">
          Text bleibt gespeichert, Timing ist alt.
        </div>
      )}
      {alignmentLanguage !== null && alignmentLanguage !== undefined && (
        <div className="text-[11px] text-slate-500">Sprache: {alignmentLanguage}</div>
      )}
      {block.alignment_status === "failed" && block.alignment_error ? (
        <div className="rounded border border-red-900/70 bg-red-950/20 p-2 text-xs leading-relaxed text-red-200">
          {block.alignment_error}
        </div>
      ) : null}
      <textarea
        aria-label="Sequenz-Transcript-Text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={busy}
        rows={4}
        className="min-h-24 resize-y rounded border border-edge bg-panel px-2 py-2 text-sm leading-relaxed text-slate-100 outline-none focus:border-sky-600 disabled:opacity-60"
      />
      {block.words.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {block.words.map((word) => (
            <span
              key={word.id}
              className="rounded bg-slate-800 px-1.5 py-0.5 text-[11px] text-slate-400"
            >
              {word.text}
            </span>
          ))}
        </div>
      )}
      {error !== null && <div className="text-xs text-red-400">{error}</div>}
      {activeJobId !== null && jobStatus !== null && (
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span
              className={`rounded border px-2 py-0.5 text-[11px] ${
                jobStatus.status === "failed"
                  ? "border-red-800 bg-red-950/40 text-red-200"
                  : jobStatus.status === "succeeded"
                    ? "border-emerald-800 bg-emerald-950/30 text-emerald-200"
                    : "border-sky-800 bg-sky-950/30 text-sky-200"
              }`}
            >
              {activeJobKindRef.current === "realign"
                ? jobStatusLabel(jobStatus.status)
                : voiceoverJobStatusLabel(jobStatus.status)}
            </span>
          </div>
          {jobStatus.status === "failed" && jobError !== null && (
            <div className="rounded border border-red-900/70 bg-red-950/20 p-2 text-xs text-red-200">
              {jobError}
            </div>
          )}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => void saveAndRealign()}
          disabled={busy || voiceBusy || jobRunning || text.trim() === ""}
          className="rounded bg-sky-700 px-3 py-1 text-xs font-medium text-white hover:bg-sky-600 disabled:opacity-40"
        >
          {busy ? "Speichert..." : "Speichern + neu ausrichten"}
        </button>
        <button
          type="button"
          onClick={() => void generateVoiceover()}
          disabled={busy || voiceBusy || jobRunning || text.trim() === "" || timelineId === null}
          className="rounded bg-emerald-700 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-600 disabled:opacity-40"
        >
          {voiceBusy ? "Erzeugt..." : "Stimme erzeugen"}
        </button>
      </div>
    </article>
  );
}

function SequenceTranscriptPanel({
  client,
  blocks,
  error,
  activeSegmentId,
  timelineId,
  onSaved,
  onVoiceoverCreated,
}: {
  client: LauraClient;
  blocks: SequenceTranscriptBlock[];
  error: string | null;
  activeSegmentId: string | null;
  timelineId: string | null;
  onSaved: () => void;
  onVoiceoverCreated: () => void;
}): ReactElement {
  const friendlyError = transcriptErrorMessage(error);
  if (friendlyError !== null) {
    return (
      <div className="rounded border border-amber-900/60 bg-amber-950/20 p-3 text-xs leading-relaxed text-amber-200">
        {friendlyError}
      </div>
    );
  }
  if (blocks.length === 0) {
    return (
      <div className="rounded border border-edge bg-panel/50 p-3 text-xs leading-relaxed text-slate-500">
        Noch kein Sequenz-Transkript.
      </div>
    );
  }
  return (
    <div className="flex flex-col">
      {blocks.map((block) => (
        <TranscriptBlockEditor
          key={`${block.segment_id}:${block.seq_in_frame}`}
          client={client}
          block={block}
          active={block.segment_id === activeSegmentId}
          timelineId={timelineId}
          onSaved={onSaved}
          onVoiceoverCreated={onVoiceoverCreated}
        />
      ))}
    </div>
  );
}

/**
 * Zusammenfügen (assemble) — transcript-first workspace:
 *   left   = compact scene/asset bin
 *   centre = sequence player, sequence timeline, storyboard order
 *   right  = transcript rail with Tools tab for Replace/Reenact
 */
export function AssembleView({
  client,
  projectId,
  // roughCutId accepted for API compatibility; the bin is project-wide.
  roughCutId: _roughCutId,
  onSeekScene,
  rateNum = 30,
  rateDen = 1,
}: {
  client: LauraClient;
  projectId: string | null;
  roughCutId?: string | null;
  onSeekScene: (sceneId: string) => void;
  /** Numerator of the project sequence frame rate. Defaults to 30. */
  rateNum?: number;
  /** Denominator of the project sequence frame rate. Defaults to 1. */
  rateDen?: number;
}): ReactElement {
  const [scenes, setScenesList] = useState<Scene[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [binError, setBinError] = useState<string | null>(null);
  const [sceneQuery, setSceneQuery] = useState("");
  const [seqClips, setSeqClips] = useState<TimelineClip[]>([]);
  const [audioClips, setAudioClips] = useState<TimelineAudioClip[]>([]);
  const [reloadKey, setReloadKey] = useState(0);
  const [runtimeReloadKey, setRuntimeReloadKey] = useState(0);
  const [transcriptReloadKey, setTranscriptReloadKey] = useState(0);
  const [transcript, setTranscript] = useState<SequenceTranscriptBlock[]>([]);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);
  const [railTab, setRailTab] = useState<"transcript" | "tools">("transcript");
  const [seqFrame, setSeqFrame] = useState(0);
  const [captionPreview, setCaptionPreview] = useState(true);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);

  useEffect(() => {
    if (projectId === null) {
      setScenesList([]);
      setAssets([]);
      setBinError(null);
      return;
    }
    let cancelled = false;
    Promise.all([client.listProjectScenes(projectId), client.listAssets(projectId)])
      .then(([s, a]) => {
        if (!cancelled) {
          setScenesList(s);
          setAssets(a);
          setBinError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setBinError(err instanceof Error ? err.message : "Fehler beim Laden der Szenen");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [client, projectId]);

  const { sequence, error: sequenceError, setScenes, reload: reloadSequence } = useSequence(
    client,
    projectId,
  );

  const sequenceId = sequence?.timeline_id ?? null;
  const reloadSeqClips = useCallback(() => {
    setReloadKey((k) => k + 1);
  }, []);
  const reloadTranscript = useCallback(() => {
    setTranscriptReloadKey((k) => k + 1);
  }, []);
  const reloadAudioClips = useCallback(() => {
    if (!sequenceId) {
      setAudioClips([]);
      return;
    }
    client
      .listTimelineAudioClips(sequenceId)
      .then(setAudioClips)
      .catch((e: unknown) => {
        log.error("listTimelineAudioClips failed:", e instanceof Error ? e.message : String(e));
        setAudioClips([]);
      });
  }, [client, sequenceId]);

  useEffect(() => {
    if (!sequenceId) {
      setSeqClips([]);
      return;
    }
    let cancelled = false;
    client
      .getSequenceFlattened(sequenceId)
      .then((clips) => {
        if (!cancelled) setSeqClips(clips);
      })
      .catch(() => {
        if (!cancelled) setSeqClips([]);
      });
    return () => {
      cancelled = true;
    };
  }, [client, sequenceId, reloadKey]);

  useEffect(() => {
    reloadAudioClips();
  }, [reloadAudioClips]);

  useEffect(() => {
    if (!sequenceId) {
      setTranscript([]);
      setTranscriptError(null);
      return;
    }
    let cancelled = false;
    client
      .getSequenceTranscript(sequenceId)
      .then((blocks) => {
        if (!cancelled) {
          setTranscript(blocks);
          setTranscriptError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setTranscript([]);
          setTranscriptError(
            err instanceof Error ? err.message : "Sequenz-Transkript konnte nicht geladen werden.",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [client, sequenceId, transcriptReloadKey]);

  const seqTimeline: Timeline | null =
    sequence !== null
      ? {
          id: sequence.timeline_id,
          project_id: sequence.project_id,
          name: "Sequenz",
          kind: "sequence",
          created_at: "",
          clips: seqClips,
        }
      : null;
  const items = sequence?.items ?? [];
  const ids = items.map((i) => i.scene_id);

  const sceneById = (id: string): Scene | undefined => scenes.find((s) => s.id === id);
  const assetName = (id: string | null | undefined): string =>
    assets.find((a) => a.id === id)?.display_name ?? "Video";

  const filteredScenes = useMemo(() => {
    const needle = sceneQuery.trim().toLocaleLowerCase();
    if (!needle) return scenes;
    return scenes.filter((s) => {
      const hay = `${s.name} ${assetName(s.asset_id)}`.toLocaleLowerCase();
      return hay.includes(needle);
    });
  }, [assets, sceneQuery, scenes]);

  const groups = useMemo(() => {
    const out: { assetId: string; scenes: Scene[] }[] = [];
    for (const s of filteredScenes) {
      const key = s.asset_id ?? "?";
      const existing = out.find((g) => g.assetId === key);
      if (existing) existing.scenes.push(s);
      else out.push({ assetId: key, scenes: [s] });
    }
    return out;
  }, [filteredScenes]);

  const applySceneIds = useCallback(
    async (sceneIds: string[]): Promise<void> => {
      await setScenes(sceneIds);
      reloadSeqClips();
      reloadTranscript();
    },
    [reloadSeqClips, reloadTranscript, setScenes],
  );

  const activeCaption = transcript.find(
    (block) => seqFrame >= block.seq_in_frame && seqFrame < block.seq_out_frame_exclusive,
  ) ?? transcript[0] ?? null;
  const totalSequenceFrames = seqClips.reduce(
    (max, clip) => Math.max(max, clip.seq_out_frame_exclusive),
    0,
  );

  const dragIndex = useRef<number | null>(null);
  const reorder = (from: number, to: number): void => {
    const next = [...ids];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    void applySceneIds(next);
  };
  const onDragStart = (e: DragEvent<HTMLDivElement>, i: number): void => {
    dragIndex.current = i;
    e.dataTransfer.setData("text/plain", String(i));
  };
  const onDrop = (e: DragEvent<HTMLDivElement>, i: number): void => {
    e.preventDefault();
    const from = dragIndex.current ?? Number(e.dataTransfer.getData("text/plain"));
    if (from !== i) reorder(from, i);
    dragIndex.current = null;
    setDragOverIndex(null);
  };
  const onDragOver = (e: DragEvent<HTMLDivElement>): void => e.preventDefault();

  const updateTransition = (itemId: string, kind: SequenceTransitionKind): void => {
    if (!sequence) return;
    const current = items.find((it) => it.id === itemId);
    const durationFrames = kind === "hard"
      ? 0
      : Math.max(1, current?.transition_after_frames || 12);
    client
      .updateSequenceTransition(sequence.timeline_id, itemId, { kind, durationFrames })
      .then(() => {
        void reloadSequence();
        reloadSeqClips();
      })
      .catch((e: unknown) => {
        log.error("updateSequenceTransition failed:", e instanceof Error ? e.message : String(e));
      });
  };

  const assetOptions = assets.map((a) => ({ id: a.id, display_name: a.display_name }));

  return (
    <div className="grid min-h-0 flex-1 grid-cols-[250px_minmax(0,1fr)_360px] gap-px bg-edge">
      <aside
        aria-label="Szenen-Bin"
        className="flex min-h-0 flex-col gap-3 overflow-y-auto bg-ink p-3"
      >
        <div>
          <div className="text-xs font-semibold text-slate-200">Szenen-Bin</div>
          <div className="text-[11px] text-slate-600">Klick hängt an die Sequenz an</div>
        </div>
        <input
          value={sceneQuery}
          onChange={(e) => setSceneQuery(e.target.value)}
          placeholder="Szenen suchen"
          className="rounded border border-edge bg-panel px-2 py-1 text-xs text-slate-200 placeholder:text-slate-600 outline-none focus:border-sky-600"
        />
        {binError !== null && <div className="text-xs text-red-400">{binError}</div>}
        {sequenceError !== null && <div className="text-xs text-red-400">{sequenceError}</div>}
        {scenes.length === 0 ? (
          <div className="text-xs text-slate-600">
            Noch keine Szenen — erst im Rough Cut Szenen erzeugen.
          </div>
        ) : groups.length === 0 ? (
          <div className="text-xs text-slate-600">Keine Szene passt zum Filter.</div>
        ) : (
          groups.map((g) => (
            <div key={g.assetId} className="flex flex-col gap-1">
              <div className="flex items-center justify-between gap-1">
                <div className="min-w-0 truncate text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                  {assetName(g.assetId)} · {g.scenes.length}
                </div>
                {g.scenes.length > 1 && (
                  <button
                    type="button"
                    title={`Alle ${g.scenes.length} Szenen von „${assetName(g.assetId)}" anhängen`}
                    onClick={() => void applySceneIds([...ids, ...g.scenes.map((s) => s.id)])}
                    className="shrink-0 rounded bg-sky-700 px-1.5 py-0.5 text-[10px] font-medium text-white hover:bg-sky-600"
                  >
                    + alle
                  </button>
                )}
              </div>
              {g.scenes.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  title={`„${s.name}" an die Reihenfolge anhängen`}
                  onClick={() => void applySceneIds([...ids, s.id])}
                  className="flex items-center gap-2 rounded border border-edge bg-slate-800/50 p-1 text-left text-xs hover:bg-slate-700"
                >
                  <Thumb client={client} assetId={s.asset_id ?? g.assetId} frame={s.thumb_frame ?? 0} />
                  <span className="min-w-0 flex-1 truncate text-slate-200">{s.name}</span>
                  <span className="shrink-0 rounded bg-sky-600 px-1.5 py-0.5 font-medium text-white">
                    +
                  </span>
                </button>
              ))}
            </div>
          ))
        )}
      </aside>

      <section
        aria-label="Sequenz-Arbeitsfläche"
        className="flex min-h-0 flex-col gap-3 overflow-y-auto bg-ink p-3"
      >
        <div className="relative w-full max-w-3xl">
          <SequencePlayer
            client={client}
            projectId={projectId}
            sequenceId={sequence?.timeline_id ?? null}
            reloadKey={reloadKey}
            onFrame={setSeqFrame}
          />
          {captionPreview && activeCaption !== null && (
            <div
              aria-label="Caption-Preview"
              className="pointer-events-none absolute inset-x-8 bottom-14 flex justify-center"
            >
              <div className="max-w-full rounded bg-black/75 px-3 py-1.5 text-center text-sm font-medium leading-snug text-white shadow">
                {activeCaption.text}
              </div>
            </div>
          )}
        </div>

        {seqTimeline !== null && (
          <div className="w-full max-w-3xl">
            <TimelineBar
              client={client}
              timeline={seqTimeline}
              audioClips={audioClips}
              onChange={() => {
                reloadSeqClips();
                reloadAudioClips();
                void reloadSequence();
                reloadTranscript();
              }}
              onRemoveOverlay={(clipId) => {
                if (!sequence) return;
                client
                  .removeOverlay(sequence.timeline_id, clipId)
                  .then(() => {
                    reloadSeqClips();
                    void reloadSequence();
                    reloadTranscript();
                  })
                  .catch((e: unknown) => {
                    log.error("removeOverlay failed:", e instanceof Error ? e.message : String(e));
                  });
              }}
            />
          </div>
        )}

        <div className="flex items-center justify-between gap-2">
          <div>
            <div className="text-xs font-semibold text-slate-200">Storyboard</div>
            <div className="text-[11px] text-slate-600">Ziehen zum Umordnen, Klick springt zur Szene</div>
          </div>
          <div className="flex items-center gap-3 text-xs tabular-nums text-slate-500">
            <button
              type="button"
              onClick={() => setCaptionPreview((v) => !v)}
              className="rounded border border-edge bg-panel px-2 py-1 text-slate-300 hover:bg-slate-800"
            >
              {captionPreview ? "Caption-Preview aus" : "Caption-Preview ein"}
            </button>
            <span>Gesamtdauer {totalSequenceFrames} f</span>
            <span>{items.length} Clips</span>
          </div>
        </div>
        <div className="flex gap-2 overflow-x-auto pb-2">
          {items.length === 0 ? (
            <div className="py-6 text-xs text-slate-600">
              Links eine Szene anklicken, um sie hier anzuhängen.
            </div>
          ) : (
            items.map((it, i) => {
              const sc = sceneById(it.scene_id);
              return (
                <Fragment key={it.id}>
                  <div
                    draggable
                    onDragStart={(e) => onDragStart(e, i)}
                    onDragEnter={() => setDragOverIndex(i)}
                    onDragOver={onDragOver}
                    onDrop={(e) => onDrop(e, i)}
                    onDragLeave={() => setDragOverIndex(null)}
                    className={`flex w-28 shrink-0 cursor-grab flex-col gap-1 rounded border bg-slate-800 p-1 text-xs ${
                      dragOverIndex === i ? "border-sky-400 shadow-[inset_3px_0_0_#38bdf8]" : "border-edge"
                    }`}
                  >
                    {dragOverIndex === i && (
                      <span className="sr-only">Einfügemarke vor {i + 1}</span>
                    )}
                    <Thumb
                      client={client}
                      assetId={sc?.asset_id ?? null}
                      frame={sc?.thumb_frame ?? 0}
                      className="h-14 w-full"
                    />
                    <button
                      type="button"
                      onClick={() => onSeekScene(it.scene_id)}
                      className="truncate text-left text-slate-200 hover:text-white"
                    >
                      {i + 1}. {it.scene_name}
                    </button>
                    <button
                      type="button"
                      onClick={() => void applySceneIds(ids.filter((_, j) => j !== i))}
                      className="rounded bg-slate-700 px-1 py-0.5 text-[11px] text-slate-300 hover:bg-slate-600"
                    >
                      entfernen
                    </button>
                  </div>
                  {i < items.length - 1 && (
                    <label className="flex w-20 shrink-0 flex-col justify-center gap-1 text-[10px] text-slate-500">
                      <span>Übergang</span>
                      <select
                        aria-label={`Transition nach Szene ${i + 1}`}
                        value={it.transition_after_kind}
                        onChange={(e) =>
                          updateTransition(it.id, e.target.value as SequenceTransitionKind)}
                        className="rounded border border-edge bg-panel px-1 py-1 text-[11px] text-slate-200"
                      >
                        <option value="hard">Hard</option>
                        <option value="dip_black">Dip</option>
                        <option value="fade_black">Fade</option>
                        <option value="crossfade">Cross</option>
                      </select>
                    </label>
                  )}
                </Fragment>
              );
            })
          )}
        </div>
      </section>

      <aside
        aria-label="Transkript und Werkzeuge"
        className="flex min-h-0 flex-col overflow-y-auto bg-ink p-3"
      >
        <div className="mb-3 flex rounded border border-edge bg-panel p-0.5">
          <button
            type="button"
            onClick={() => setRailTab("transcript")}
            className={`flex-1 rounded px-3 py-1.5 text-xs font-medium ${
              railTab === "transcript" ? "bg-sky-700 text-white" : "text-slate-400 hover:text-slate-100"
            }`}
          >
            Transkript
          </button>
          <button
            type="button"
            onClick={() => setRailTab("tools")}
            className={`flex-1 rounded px-3 py-1.5 text-xs font-medium ${
              railTab === "tools" ? "bg-sky-700 text-white" : "text-slate-400 hover:text-slate-100"
            }`}
          >
            Tools
          </button>
        </div>
        {railTab === "transcript" ? (
          <SequenceTranscriptPanel
            client={client}
            blocks={transcript}
            error={transcriptError}
            activeSegmentId={activeCaption?.segment_id ?? null}
            timelineId={sequence?.timeline_id ?? null}
            onSaved={reloadTranscript}
            onVoiceoverCreated={() => {
              reloadAudioClips();
              reloadSeqClips();
            }}
          />
        ) : (
          <div className="flex flex-col gap-3">
            <RuntimeStatusPanel client={client} reloadKey={runtimeReloadKey} />
            <PersonaKitPanel
              key={`persona-kit:${projectId ?? "none"}:${runtimeReloadKey}`}
              client={client}
              projectId={projectId}
            />
            <RuntimeSetupPanel
              client={client}
              onCreated={() => setRuntimeReloadKey((key) => key + 1)}
            />
            <AudioLaneControls
              client={client}
              timelineId={sequence?.timeline_id ?? null}
              assets={assets}
              onChange={() => {
                reloadSeqClips();
                reloadAudioClips();
                void reloadSequence();
                reloadTranscript();
              }}
            />
            <DemoAssistantPanel
              client={client}
              assets={assets}
              onApplied={() => {
                void reloadSequence();
                reloadSeqClips();
                reloadAudioClips();
                reloadTranscript();
              }}
            />
            <OverlayControls
              client={client}
              timelineId={sequence?.timeline_id ?? null}
              assets={assetOptions}
              onChange={() => {
                reloadSeqClips();
                void reloadSequence();
                reloadTranscript();
              }}
              currentSeqFrame={seqFrame}
              rateNum={rateNum}
              rateDen={rateDen}
            />
            <ReenactPanel
              client={client}
              projectId={projectId}
              timelineId={sequence?.timeline_id ?? null}
              assets={assetOptions}
              onChange={() => {
                reloadSeqClips();
                void reloadSequence();
                reloadTranscript();
              }}
              currentSeqFrame={seqFrame}
              rateNum={rateNum}
              rateDen={rateDen}
            />
            <LipsyncPanel
              client={client}
              projectId={projectId}
              timelineId={sequence?.timeline_id ?? null}
              assets={assets}
              onChange={() => {
                reloadSeqClips();
                reloadAudioClips();
                void reloadSequence();
                reloadTranscript();
              }}
            />
          </div>
        )}
      </aside>
    </div>
  );
}
