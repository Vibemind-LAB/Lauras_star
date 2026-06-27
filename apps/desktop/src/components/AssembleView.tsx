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
import { useQuery, useQueryClient } from "@tanstack/react-query";

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
import { qk } from "../cache/queryKeys";
import { useJobStatus } from "../hooks/useJobStatus";
import { useSequence } from "../hooks/useSequence";
import { log } from "../shared/log";
import { AudioLaneControls } from "./AudioLaneControls";
import { DemoAssistantPanel } from "./DemoAssistantPanel";
import { OverlayControls } from "./OverlayControls";
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
      className={`block shrink-0 overflow-hidden rounded bg-accent/40 ${className ?? "h-9 w-16"}`}
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

function alignmentStatusLabel(status: string | undefined): string | null {
  if (status === "stale") return "Nicht neu aligned";
  if (status === "aligning") return "Alignment läuft";
  if (status === "failed") return "Alignment fehlgeschlagen";
  return null;
}

function alignmentStatusClass(status: string | undefined): string {
  if (status === "failed") return "border-status-err/80 bg-status-err/15 text-status-err";
  if (status === "stale") return "border-status-warn/80 bg-status-warn/15 text-status-warn";
  if (status === "aligning") return "border-accent/80 bg-accent/40 text-accent";
  return "border-bezel bg-surface-1 text-content-muted";
}

function TranscriptBlockEditor({
  client,
  block,
  active,
  onSaved,
}: {
  client: LauraClient;
  block: SequenceTranscriptBlock;
  active: boolean;
  onSaved: () => void;
}): ReactElement {
  const [text, setText] = useState(block.text);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // One active job at a time: realign job id.
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  // Guard: fire callbacks exactly once per succeeded job.
  const onSuccessFiredRef = useRef<string | null>(null);

  const { jobStatus, error: jobError, isRunning: jobRunning } = useJobStatus(client, activeJobId);

  useEffect(() => {
    setText(block.text);
    setActiveJobId(null);
    onSuccessFiredRef.current = null;
    setError(null);
  }, [block.segment_id, block.text]);

  // Fire the onSaved callback exactly once when the realign job succeeds.
  useEffect(() => {
    if (
      jobStatus === null ||
      jobStatus.status !== "succeeded" ||
      onSuccessFiredRef.current === jobStatus.id
    ) {
      return;
    }
    onSuccessFiredRef.current = jobStatus.id;
    onSaved();
  }, [jobStatus, onSaved]);

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
      setActiveJobId(accepted.job_id);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      log.error("transcript edit failed:", msg);
    } finally {
      setBusy(false);
    }
  }

  const alignmentLabel = alignmentStatusLabel(block.alignment_status);
  const alignmentLanguage = block.alignment_language;

  return (
    <article
      className={`flex flex-col gap-2 border-b border-bezel/80 py-3 last:border-b-0 ${
        active ? "bg-accent/10 px-2" : ""
      }`}
    >
      <div className="flex items-center justify-between gap-2 text-[10px] uppercase tracking-wide">
        <span className="truncate font-semibold text-content-muted">
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
          <span className="tabular-nums text-content-faint">
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
        <div className="text-[11px] text-content-faint">Sprache: {alignmentLanguage}</div>
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
        className="min-h-24 resize-y rounded border border-bezel bg-surface-1 px-2 py-2 text-sm leading-relaxed text-content-strong outline-none focus:border-accent disabled:opacity-60"
      />
      {block.words.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {block.words.map((word) => (
            <span
              key={word.id}
              className="rounded bg-surface-2 px-1.5 py-0.5 text-[11px] text-content-muted"
            >
              {word.text}
            </span>
          ))}
        </div>
      )}
      {error !== null && <div className="text-xs text-status-err">{error}</div>}
      {activeJobId !== null && jobStatus !== null && (
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span
              className={`rounded border px-2 py-0.5 text-[11px] ${
                jobStatus.status === "failed"
                  ? "border-status-err bg-status-err/15 text-status-err"
                  : jobStatus.status === "succeeded"
                    ? "border-status-ok bg-status-ok/20 text-status-ok"
                    : "border-accent bg-accent/30 text-accent"
              }`}
            >
              {jobStatusLabel(jobStatus.status)}
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
          disabled={busy || jobRunning || text.trim() === ""}
          className="rounded bg-accent px-3 py-1 text-xs font-medium text-white hover:bg-accent-glow disabled:opacity-40"
        >
          {busy ? "Speichert..." : "Speichern + neu ausrichten"}
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
  onSaved,
}: {
  client: LauraClient;
  blocks: SequenceTranscriptBlock[];
  error: string | null;
  activeSegmentId: string | null;
  onSaved: () => void;
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
      <div className="rounded border border-bezel bg-surface-1/50 p-3 text-xs leading-relaxed text-content-faint">
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
          onSaved={onSaved}
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
  const [reloadKey, setReloadKey] = useState(0);
  const [railTab, setRailTab] = useState<"transcript" | "tools">("transcript");
  const [seqFrame, setSeqFrame] = useState(0);
  const [captionPreview, setCaptionPreview] = useState(true);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);
  const [activeSceneId, setActiveSceneId] = useState<string | null>(null);

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

  const queryClient = useQueryClient();
  const sequenceId = sequence?.timeline_id ?? null;
  const reloadSeqClips = useCallback(() => {
    setReloadKey((k) => k + 1);
  }, []);
  const reloadTranscript = useCallback(() => {
    if (!sequenceId) return;
    void queryClient.invalidateQueries({ queryKey: qk.sequenceTranscript(sequenceId) });
  }, [queryClient, sequenceId]);
  const reloadAudioClips = useCallback(() => {
    if (!sequenceId) return;
    void queryClient.invalidateQueries({ queryKey: qk.audioClips(sequenceId) });
  }, [queryClient, sequenceId]);

  // Audio clips for the sequence — cached under qk.audioClips(sequenceId).
  // Invalidated by any operation that can change the A2 lane (overlay add/remove/edit,
  // DemoAssistant apply, AudioLaneControls onChange, TimelineBar onChange).
  const audioClipsQuery = useQuery<TimelineAudioClip[]>({
    queryKey: qk.audioClips(sequenceId ?? "none"),
    queryFn: () => client.listTimelineAudioClips(sequenceId!),
    enabled: sequenceId !== null,
  });
  const audioClips = audioClipsQuery.data ?? [];

  // Sequence transcript — cached under qk.sequenceTranscript(sequenceId).
  // Invalidated on realignment job success (via onSaved → reloadTranscript) and on scene changes.
  const transcriptQuery = useQuery<SequenceTranscriptBlock[], Error>({
    queryKey: qk.sequenceTranscript(sequenceId ?? "none"),
    queryFn: () => client.getSequenceTranscript(sequenceId!),
    enabled: sequenceId !== null,
  });
  const transcript = transcriptQuery.data ?? [];
  const transcriptError = transcriptQuery.error !== null
    ? (transcriptQuery.error?.message ?? "Sequenz-Transkript konnte nicht geladen werden.")
    : null;

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
  const items = useMemo(() => sequence?.items ?? [], [sequence]);
  const ids = useMemo(() => items.map((i) => i.scene_id), [items]);
  // Scene ids that still exist — regenerated scenes get new ids, so the sequence can hold stale refs.
  const validSceneIds = useMemo(() => new Set(scenes.map((s) => s.id)), [scenes]);

  // Default activeSceneId to the first sequence item whenever the sequence loads or changes,
  // but only if no scene is already active (or the active scene is no longer in the sequence).
  useEffect(() => {
    const firstId = items[0]?.scene_id ?? null;
    setActiveSceneId((prev) => {
      // Keep existing selection if it is still a valid sequence item.
      if (prev !== null && items.some((it) => it.scene_id === prev)) return prev;
      return firstId;
    });
  }, [items]);

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
      // Reconcile before sending: drop refs to scenes that no longer exist, so one stale id can't
      // 422 the whole request and the storyboard stays in sync. The backend drops unknown ids too.
      const reconciled =
        scenes.length > 0 ? sceneIds.filter((id) => validSceneIds.has(id)) : sceneIds;
      await setScenes(reconciled);
      reloadSeqClips();
      reloadTranscript();
    },
    [reloadSeqClips, reloadTranscript, scenes.length, setScenes, validSceneIds],
  );

  // Self-heal stale sequence references on load: if the sequence holds scene ids that no longer
  // exist (regenerated scenes), drop them so the storyboard stops showing "?" without a manual op.
  const healingRef = useRef(false);
  useEffect(() => {
    if (scenes.length === 0 || ids.length === 0 || healingRef.current) return;
    if (ids.some((id) => !validSceneIds.has(id))) {
      healingRef.current = true;
      void applySceneIds(ids).finally(() => {
        healingRef.current = false;
      });
    }
  }, [ids, validSceneIds, scenes.length, applySceneIds]);

  const activeCaption = transcript.find(
    (block) => seqFrame >= block.seq_in_frame && seqFrame < block.seq_out_frame_exclusive,
  ) ?? transcript[0] ?? null;
  const totalSequenceFrames = seqClips.reduce(
    (max, clip) => Math.max(max, clip.seq_out_frame_exclusive),
    0,
  );

  // Transcript blocks belonging to the active scene only.
  // Filter by seq-frame range: Scene.seq_in_frame <= block.seq_in_frame < Scene.seq_out_frame_exclusive.
  const activeScene = activeSceneId !== null ? scenes.find((s) => s.id === activeSceneId) ?? null : null;
  const activeSceneTranscript = useMemo(() => {
    if (activeScene === null) return transcript;
    return transcript.filter(
      (block) =>
        block.seq_in_frame >= activeScene.seq_in_frame &&
        block.seq_in_frame < activeScene.seq_out_frame_exclusive,
    );
  }, [transcript, activeScene]);

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
    <div className="grid min-h-0 flex-1 grid-cols-[250px_minmax(0,1fr)_360px] gap-px bg-surface-2">
      <aside
        aria-label="Szenen-Bin"
        className="flex min-h-0 flex-col gap-3 overflow-y-auto bg-surface-0 p-3"
      >
        <div>
          <div className="text-xs font-semibold text-content-strong">Szenen-Bin</div>
          <div className="text-[11px] text-content-faint">Klick hängt an die Sequenz an</div>
        </div>
        <input
          value={sceneQuery}
          onChange={(e) => setSceneQuery(e.target.value)}
          placeholder="Szenen suchen"
          className="rounded border border-bezel bg-surface-1 px-2 py-1 text-xs text-content-strong placeholder:text-content-faint outline-none focus:border-accent"
        />
        {binError !== null && <div className="text-xs text-status-err">{binError}</div>}
        {sequenceError !== null && <div className="text-xs text-status-err">{sequenceError}</div>}
        {scenes.length === 0 ? (
          <div className="text-xs text-content-faint">
            Noch keine Szenen — erst im Rough Cut Szenen erzeugen.
          </div>
        ) : groups.length === 0 ? (
          <div className="text-xs text-content-faint">Keine Szene passt zum Filter.</div>
        ) : (
          groups.map((g) => (
            <div key={g.assetId} className="flex flex-col gap-1">
              <div className="flex items-center justify-between gap-1">
                <div className="min-w-0 truncate text-[10px] font-semibold uppercase tracking-wide text-content-faint">
                  {assetName(g.assetId)} · {g.scenes.length}
                </div>
                {g.scenes.length > 1 && (
                  <button
                    type="button"
                    title={`Alle ${g.scenes.length} Szenen von „${assetName(g.assetId)}" anhängen`}
                    onClick={() => void applySceneIds([...ids, ...g.scenes.map((s) => s.id)])}
                    className="shrink-0 rounded bg-accent px-1.5 py-0.5 text-[10px] font-medium text-white hover:bg-accent-glow"
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
                  className="flex items-center gap-2 rounded border border-bezel bg-surface-2/50 p-1 text-left text-xs hover:bg-surface-2"
                >
                  <Thumb client={client} assetId={s.asset_id ?? g.assetId} frame={s.thumb_frame ?? 0} />
                  <span className="min-w-0 flex-1 truncate text-content-strong">{s.name}</span>
                  <span className="shrink-0 rounded bg-accent px-1.5 py-0.5 font-medium text-white">
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
        className="flex min-h-0 flex-col gap-3 overflow-y-auto bg-surface-0 p-3"
      >
        <div className="relative w-full max-w-3xl">
          <SequencePlayer
            client={client}
            projectId={projectId}
            sequenceId={sequence?.timeline_id ?? null}
            reloadKey={reloadKey}
            onFrame={setSeqFrame}
            audioClips={audioClips}
            rateNum={rateNum}
            rateDen={rateDen}
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
            <div className="text-xs font-semibold text-content-strong">Storyboard</div>
            <div className="text-[11px] text-content-faint">Ziehen zum Umordnen, Klick springt zur Szene</div>
          </div>
          <div className="flex items-center gap-3 text-xs tabular-nums text-content-faint">
            <button
              type="button"
              onClick={() => setCaptionPreview((v) => !v)}
              className="rounded border border-bezel bg-surface-1 px-2 py-1 text-content-muted hover:bg-surface-2"
            >
              {captionPreview ? "Caption-Preview aus" : "Caption-Preview ein"}
            </button>
            <span>Gesamtdauer {totalSequenceFrames} f</span>
            <span>{items.length} Clips</span>
          </div>
        </div>
        <div className="flex gap-2 overflow-x-auto pb-2">
          {items.length === 0 ? (
            <div className="py-6 text-xs text-content-faint">
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
                    className={`flex w-28 shrink-0 cursor-grab flex-col gap-1 rounded border bg-surface-2 p-1 text-xs ${
                      dragOverIndex === i
                        ? "border-accent shadow-[inset_3px_0_0_#38bdf8]"
                        : activeSceneId === it.scene_id
                          ? "border-accent ring-1 ring-accent"
                          : "border-bezel"
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
                      onClick={() => {
                        onSeekScene(it.scene_id);
                        setActiveSceneId(it.scene_id);
                      }}
                      className="truncate text-left text-content-strong hover:text-white"
                    >
                      {i + 1}. {it.scene_name}
                    </button>
                    <button
                      type="button"
                      onClick={() => void applySceneIds(ids.filter((_, j) => j !== i))}
                      className="rounded bg-surface-2 px-1 py-0.5 text-[11px] text-content-muted hover:bg-surface-2"
                    >
                      entfernen
                    </button>
                  </div>
                  {i < items.length - 1 && (
                    <label className="flex w-20 shrink-0 flex-col justify-center gap-1 text-[10px] text-content-faint">
                      <span>Übergang</span>
                      <select
                        aria-label={`Transition nach Szene ${i + 1}`}
                        value={it.transition_after_kind}
                        onChange={(e) =>
                          updateTransition(it.id, e.target.value as SequenceTransitionKind)}
                        className="rounded border border-bezel bg-surface-1 px-1 py-1 text-[11px] text-content-strong"
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
        className="flex min-h-0 flex-col overflow-y-auto bg-surface-0 p-3"
      >
        <div className="mb-3 flex rounded border border-bezel bg-surface-1 p-0.5">
          <button
            type="button"
            onClick={() => setRailTab("transcript")}
            className={`flex-1 rounded px-3 py-1.5 text-xs font-medium ${
              railTab === "transcript" ? "bg-accent text-white" : "text-content-muted hover:text-content-strong"
            }`}
          >
            Transkript
          </button>
          <button
            type="button"
            onClick={() => setRailTab("tools")}
            className={`flex-1 rounded px-3 py-1.5 text-xs font-medium ${
              railTab === "tools" ? "bg-accent text-white" : "text-content-muted hover:text-content-strong"
            }`}
          >
            Tools
          </button>
        </div>
        {railTab === "transcript" ? (
          <SequenceTranscriptPanel
            client={client}
            blocks={activeSceneTranscript}
            error={transcriptError}
            activeSegmentId={activeCaption?.segment_id ?? null}
            onSaved={reloadTranscript}
          />
        ) : (
          <div className="flex flex-col gap-3">
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
          </div>
        )}
      </aside>
    </div>
  );
}



