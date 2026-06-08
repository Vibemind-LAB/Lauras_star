import { type PointerEvent as ReactPointerEvent, type ReactElement, useEffect, useRef, useState } from "react";

import {
  type ExportFormat,
  type LauraClient,
  type Operation,
  type Timeline,
  type TimelineClip,
} from "../api";

const EXPORT_FORMATS: { fmt: ExportFormat; label: string; ext: string }[] = [
  { fmt: "otio", label: "OTIO", ext: "otio" },
  { fmt: "edl", label: "EDL", ext: "edl" },
  { fmt: "fcp7xml", label: "FCP7-XML", ext: "xml" },
  { fmt: "fcpxml", label: "FCPXML", ext: "fcpxml" },
];

const TRIM_STEP = 5; // frames per trim click
const SNAP_PX = 8; // snap an edge to a neighbour cut within this many pixels
const HANDLE_PX = 6; // width of an edge-trim handle
const AUDIO_SNAP_PX = 10; // snap the audio handle to the picture cut (0) within this many pixels

/** Which edge of the selected clip a pointer-drag is trimming. */
type TrimEdge = "in" | "out";

/**
 * Project a per-clip audio offset (canonical SAMPLES, invariant #3) onto whole frames for the UI.
 * Mirrors the backend `sample_to_frame` (round-half-away-from-zero is close enough at the UI layer,
 * and the backend re-derives the authoritative frame from the column anyway). Returns 0 when the
 * rate is unknown, so a clip with no known audio rate simply draws as a hard cut.
 */
function offsetSamplesToFrames(
  samples: number,
  audioSampleRate: number | null,
  rateNum: number | null,
  rateDen: number | null,
): number {
  if (!audioSampleRate || !rateNum || !rateDen) return 0;
  return Math.round((samples * rateNum) / (audioSampleRate * rateDen));
}

/** Human read-out for an audio offset in frames: hard, J-cut (audio earlier), or L-cut (later). */
function offsetLabel(frames: number): string {
  if (frames === 0) return "Ton 0f → harter Schnitt";
  if (frames < 0) return `Ton ${frames}f → J-Cut`;
  return `Ton +${frames}f → L-Cut`;
}

/** Live state while dragging a clip's audio leading-edge handle (A1 lane). The offset is held in
 *  whole *sequence frames* relative to the picture cut (the unit the set_audio_offset op expects). */
interface AudioDrag {
  clipId: string;
  atSeqFrame: number;
  startX: number;
  /** Sequence frames per CSS pixel for the strip (audio shares the picture timebase 1:1). */
  framesPerPx: number;
  /** The offset the picture cut started at, so the delta is added to the stored value. */
  baseFrames: number;
  /** The live, snapped offset in frames (what gets committed on pointer-up). */
  offsetFrames: number;
}

/** Live state while dragging a clip edge. Frames are *source* frames (the unit the
 *  backend trim op expects); the preview length is derived from them on pointer-up. */
interface EdgeDrag {
  clipId: string;
  edge: TrimEdge;
  startX: number;
  /** Source frames per CSS pixel for this clip (accounts for speed). */
  srcPerPx: number;
  newSrcIn: number;
  newSrcOut: number;
}

function ClipThumb({
  client,
  clip,
  index,
  total,
  selected,
  assetDuration,
  dragOver,
  onSelect,
  onDragStart,
  onDragEnter,
  onDragEnd,
  onDrop,
  onEdgeDown,
}: {
  client: LauraClient;
  clip: TimelineClip;
  index: number;
  total: number;
  selected: boolean;
  /** Asset duration in source frames, if known — used only for a UI affordance hint. */
  assetDuration: number | null;
  /** Show the left-border insertion hint because a drag is hovering this clip. */
  dragOver: boolean;
  onSelect: () => void;
  onDragStart: () => void;
  onDragEnter: () => void;
  onDragEnd: () => void;
  onDrop: () => void;
  onEdgeDown: (edge: TrimEdge, e: ReactPointerEvent) => void;
}): ReactElement {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    client
      .assetFrameUrl(clip.asset_id, clip.src_in_frame)
      .then((u) => {
        if (!active) {
          URL.revokeObjectURL(u);
          return;
        }
        objectUrl = u;
        setUrl(u);
      })
      .catch(() => {
        /* fall back to the colour block */
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [client, clip.asset_id, clip.src_in_frame]);

  const pct = total > 0 ? ((clip.seq_out_frame_exclusive - clip.seq_in_frame) / total) * 100 : 0;
  const retimed = clip.speed_num !== clip.speed_den;
  // The right edge can only extend while source frames remain; surface that as a hint.
  const canExtendOut = assetDuration === null || clip.src_out_frame_exclusive < assetDuration;
  return (
    <button
      type="button"
      draggable
      onClick={onSelect}
      onDragStart={(e) => {
        // dataTransfer is always present in a real browser but absent under jsdom.
        if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
        onDragStart();
      }}
      onDragOver={(e) => {
        e.preventDefault();
        if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
      }}
      onDragEnter={onDragEnter}
      onDragEnd={onDragEnd}
      onDrop={(e) => {
        e.preventDefault();
        onDrop();
      }}
      title={`Clip ${index + 1} · src ${clip.src_in_frame}–${clip.src_out_frame_exclusive}${
        retimed ? ` · ${clip.speed_num}/${clip.speed_den}×` : ""
      } (Klick = auswählen, ziehen = umsortieren)`}
      style={{ width: `${pct}%` }}
      className={`relative flex items-center justify-center overflow-hidden ${
        url ? "" : index % 2 === 0 ? "bg-sky-700/50" : "bg-sky-500/40"
      } ${selected ? "z-10 ring-2 ring-inset ring-amber-400" : "hover:brightness-125"} ${
        dragOver ? "border-l-2 border-amber-300" : ""
      }`}
    >
      {url && <img src={url} alt="" className="absolute inset-0 h-full w-full object-cover" />}
      <span className="relative rounded bg-ink/70 px-1 text-[10px] leading-tight text-slate-100">
        {index + 1}
        {retimed ? "⏩" : ""}
      </span>
      {selected && (
        <>
          {/* Left edge handle: trims the source IN point. */}
          <span
            role="separator"
            aria-label="Trim clip start"
            title="Ziehen = Anfang trimmen"
            onPointerDown={(e) => {
              e.stopPropagation();
              onEdgeDown("in", e);
            }}
            // Stop the click that would otherwise fire on the button after a handle press.
            onClick={(e) => e.stopPropagation()}
            style={{ width: `${HANDLE_PX}px` }}
            className="absolute inset-y-0 left-0 z-20 cursor-ew-resize bg-amber-400/80 hover:bg-amber-300"
          />
          {/* Right edge handle: trims the source OUT point. */}
          <span
            role="separator"
            aria-label="Trim clip end"
            title="Ziehen = Ende trimmen"
            onPointerDown={(e) => {
              e.stopPropagation();
              onEdgeDown("out", e);
            }}
            onClick={(e) => e.stopPropagation()}
            style={{ width: `${HANDLE_PX}px` }}
            className={`absolute inset-y-0 right-0 z-20 cursor-ew-resize ${
              canExtendOut ? "bg-amber-400/80 hover:bg-amber-300" : "bg-amber-400/40"
            }`}
          />
        </>
      )}
    </button>
  );
}

/**
 * One clip's block on the A1 (audio) lane. Its leading edge is drawn shifted from the picture cut by
 * `offsetFrames` (the projection of the clip's `audio_offset_samples`): negative = J-cut (audio
 * reaches left, before the picture cut), positive = L-cut (audio starts after). A non-first clip
 * carries a draggable handle on that leading edge; the first clip's head is a hard cut (0) and is
 * non-draggable (no predecessor — invariant first-clip-0).
 */
function AudioBlock({
  clip,
  index,
  total,
  offsetFrames,
  isFirst,
  isDragging,
  onHandleDown,
}: {
  clip: TimelineClip;
  index: number;
  total: number;
  /** The live (possibly mid-drag) offset in frames relative to the picture leading edge. */
  offsetFrames: number;
  isFirst: boolean;
  isDragging: boolean;
  onHandleDown: (e: ReactPointerEvent) => void;
}): ReactElement {
  const seqLen = clip.seq_out_frame_exclusive - clip.seq_in_frame;
  // The audio block keeps the picture's duration but its leading edge shifts by the offset, so a
  // J-cut (offset < 0) widens/leads left and an L-cut (offset > 0) starts later.
  const leadFrames = clip.seq_in_frame + offsetFrames;
  const leftPct = total > 0 ? (leadFrames / total) * 100 : 0;
  const widthPct = total > 0 ? (seqLen / total) * 100 : 0;
  const isSplit = offsetFrames !== 0;
  return (
    <div
      role="group"
      aria-label={`Audio Clip ${index + 1} · ${offsetLabel(offsetFrames)}`}
      title={`Audio Clip ${index + 1} · ${offsetLabel(offsetFrames)}${
        isFirst ? " (erster Clip: harter Schnitt)" : " (Ton-Kante ziehen = J/L-Cut)"
      }`}
      style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
      className={`absolute inset-y-0 flex items-center overflow-hidden rounded-sm ${
        isSplit ? "bg-emerald-700/50 ring-1 ring-inset ring-emerald-400/60" : "bg-teal-800/40"
      }`}
    >
      <span className="pointer-events-none truncate px-1 text-[9px] leading-none text-teal-100/80">
        A{index + 1}
        {isSplit ? ` ${offsetFrames > 0 ? "+" : ""}${offsetFrames}f` : ""}
      </span>
      {!isFirst && (
        <span
          role="slider"
          aria-label={`Ton-Versatz Clip ${index + 1}`}
          aria-valuenow={offsetFrames}
          aria-valuetext={offsetLabel(offsetFrames)}
          tabIndex={0}
          title="Ziehen = Ton-Schnitt gegen Bild-Schnitt versetzen (J/L-Cut)"
          onPointerDown={(e) => {
            e.stopPropagation();
            onHandleDown(e);
          }}
          style={{ width: `${HANDLE_PX}px` }}
          className={`absolute inset-y-0 left-0 z-20 cursor-ew-resize ${
            isDragging ? "bg-amber-300" : "bg-emerald-400/80 hover:bg-emerald-300"
          }`}
        />
      )}
    </div>
  );
}

export function TimelineBar({
  client,
  timeline,
  onChange,
  onScrub,
  onSelect,
}: {
  client: LauraClient;
  timeline: Timeline | null;
  onChange: () => void;
  /** Jump the player to a clip's source IN frame when its thumbnail is clicked. */
  onScrub?: (assetId: string, frame: number) => void;
  /** Notify the parent which clip is selected (null = none) so it can open the
   *  SceneInspector. TimelineBar keeps its own `selected` state as the source of truth. */
  onSelect?: (clipId: string | null) => void;
}): ReactElement {
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [history, setHistory] = useState<TimelineClip[][]>([]);
  const [future, setFuture] = useState<TimelineClip[][]>([]);
  // HTML5 drag-and-drop reorder state.
  const [dragId, setDragId] = useState<string | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);
  const [dragOverEnd, setDragOverEnd] = useState(false);
  // Source duration of the selected clip's asset (for the right-edge trim clamp).
  const [assetDuration, setAssetDuration] = useState<number | null>(null);
  // Live pointer-drag of a clip edge (Priority 2 trim); null when not trimming.
  const [edgeDrag, setEdgeDrag] = useState<EdgeDrag | null>(null);
  // Live pointer-drag of a clip's audio leading edge on the A1 lane (L/J split); null otherwise.
  const [audioDrag, setAudioDrag] = useState<AudioDrag | null>(null);
  // The timeline's audio rate (single-asset rough cuts), to project the stored sample offset onto
  // frames for the A1 lane. Read from the first clip's asset; null leaves clips drawn as hard cuts.
  const [audioRate, setAudioRate] = useState<{
    sampleRate: number | null;
    rateNum: number | null;
    rateDen: number | null;
  }>({ sampleRate: null, rateNum: null, rateDen: null });
  // Measures the clip strip's pixel width so a pointer delta maps to a frame delta.
  const stripRef = useRef<HTMLDivElement | null>(null);

  // Reset edit history only when switching to a different timeline.
  const tlId = timeline?.id ?? null;
  useEffect(() => {
    setHistory([]);
    setFuture([]);
    setSelected(null);
    onSelect?.(null);
  }, [tlId, onSelect]);

  // Load the selected clip's asset duration so edge-trim can clamp the OUT point to it.
  const selAssetId = timeline?.clips.find((c) => c.id === selected)?.asset_id ?? null;
  useEffect(() => {
    if (!selAssetId) {
      setAssetDuration(null);
      return;
    }
    let active = true;
    client
      .getAsset(selAssetId)
      .then((a) => {
        if (active) setAssetDuration(a.duration_frames);
      })
      .catch(() => {
        if (active) setAssetDuration(null);
      });
    return () => {
      active = false;
    };
  }, [client, selAssetId]);

  // Load the timeline's audio rate (from its first clip's asset) so the A1 lane can project the
  // per-clip sample offset onto frames. Rough cuts are single-asset; if clips span assets this is
  // a reasonable display approximation (the backend re-derives the authoritative frame anyway).
  const firstAssetId = timeline?.clips[0]?.asset_id ?? null;
  useEffect(() => {
    if (!firstAssetId) {
      setAudioRate({ sampleRate: null, rateNum: null, rateDen: null });
      return;
    }
    let active = true;
    client
      .getAsset(firstAssetId)
      .then((a) => {
        if (active) {
          setAudioRate({
            sampleRate: a.audio_sample_rate,
            rateNum: a.rate_num,
            rateDen: a.rate_den,
          });
        }
      })
      .catch(() => {
        if (active) setAudioRate({ sampleRate: null, rateNum: null, rateDen: null });
      });
    return () => {
      active = false;
    };
  }, [client, firstAssetId]);

  if (!timeline) {
    return (
      <div className="flex h-20 items-center border-t border-edge bg-panel px-5 text-xs text-slate-600">
        Rough Cut — wähle ein Projekt.
      </div>
    );
  }

  const tl = timeline;
  const total = tl.clips.reduce((m, c) => Math.max(m, c.seq_out_frame_exclusive), 0);
  const sel = tl.clips.find((c) => c.id === selected) ?? null;

  async function runOp(op: Operation): Promise<void> {
    const snapshot = tl.clips;
    setError(null);
    try {
      await client.applyOperation(tl.id, op);
      setHistory((h) => [...h, snapshot]);
      setFuture([]);
      onChange();
    } catch (e) {
      setError(String(e));
    }
  }

  async function restore(clips: TimelineClip[], pushTo: "history" | "future"): Promise<void> {
    const cur = tl.clips;
    setError(null);
    try {
      await client.setClips(tl.id, clips);
      if (pushTo === "future") setFuture((f) => [...f, cur]);
      else setHistory((h) => [...h, cur]);
      onChange();
    } catch (e) {
      setError(String(e));
    }
  }

  async function undo(): Promise<void> {
    if (history.length === 0) return;
    const prev = history[history.length - 1];
    setHistory((h) => h.slice(0, -1));
    await restore(prev, "future");
  }

  async function redo(): Promise<void> {
    if (future.length === 0) return;
    const next = future[future.length - 1];
    setFuture((f) => f.slice(0, -1));
    await restore(next, "history");
  }

  /** Drop the dragged clip before the clip whose start is `toSeqFrame` (backend
   *  move semantics: it inserts before the first clip with seq_in ≥ toSeqFrame). */
  async function reorderTo(toSeqFrame: number): Promise<void> {
    const d = tl.clips.find((c) => c.id === dragId);
    setDragId(null);
    setDragOverId(null);
    setDragOverEnd(false);
    if (!d || d.seq_in_frame === toSeqFrame) return; // dropped on itself / its own slot
    await runOp({ op: "move", at_seq_frame: d.seq_in_frame, to_seq_frame: toSeqFrame });
  }

  async function splitSelected(): Promise<void> {
    if (!sel) return;
    const mid = Math.floor((sel.seq_in_frame + sel.seq_out_frame_exclusive) / 2);
    await runOp({ op: "split", at_seq_frame: mid });
  }

  async function trimSelected(delta: number): Promise<void> {
    if (!sel) return;
    const newOut = Math.max(sel.src_in_frame + 1, sel.src_out_frame_exclusive + delta);
    await runOp({
      op: "trim",
      at_seq_frame: sel.seq_in_frame,
      new_src_in_frame: sel.src_in_frame,
      new_src_out_frame_exclusive: newOut,
    });
  }

  async function duplicateSelected(): Promise<void> {
    if (!sel) return;
    await runOp({
      op: "insert_clip",
      asset_id: sel.asset_id,
      src_in_frame: sel.src_in_frame,
      src_out_frame_exclusive: sel.src_out_frame_exclusive,
      at_seq_frame: sel.seq_out_frame_exclusive,
      lane: sel.lane,
    });
  }

  async function deleteSelected(): Promise<void> {
    if (!sel) return;
    await runOp({
      op: "delete",
      seq_in_frame: sel.seq_in_frame,
      seq_out_frame_exclusive: sel.seq_out_frame_exclusive,
    });
    setSelected(null);
    onSelect?.(null);
  }

  /** Snap a candidate source-frame boundary to the nearest neighbour cut when the
   *  resulting on-strip pixel position is within SNAP_PX. Boundaries are expressed in
   *  *source* frames of the dragged clip; neighbour cuts are sequence frames, so we map
   *  through the clip's own seq↔src scale (1:1 at native speed). */
  function snapEdge(clip: TimelineClip, edge: TrimEdge, candidateSrc: number): number {
    const stripW = stripRef.current?.getBoundingClientRect().width ?? 0;
    if (stripW <= 0 || total <= 0) return candidateSrc;
    const pxPerSeq = stripW / total;
    const srcLen = clip.src_out_frame_exclusive - clip.src_in_frame;
    const seqLen = clip.seq_out_frame_exclusive - clip.seq_in_frame;
    const srcPerSeq = seqLen > 0 ? srcLen / seqLen : 1;
    // The fixed end of the clip stays anchored; the moving edge's sequence position is
    // the anchor ± the source delta converted back to sequence frames.
    const anchorSeq = edge === "out" ? clip.seq_in_frame : clip.seq_out_frame_exclusive;
    const movedSrcFromAnchor =
      edge === "out" ? candidateSrc - clip.src_in_frame : candidateSrc - clip.src_out_frame_exclusive;
    const candidateSeq = anchorSeq + (srcPerSeq > 0 ? movedSrcFromAnchor / srcPerSeq : movedSrcFromAnchor);
    // Neighbour cut sequence positions (every clip boundary except the moving edge itself).
    const cuts = new Set<number>();
    cuts.add(0);
    cuts.add(total);
    for (const c of tl.clips) {
      cuts.add(c.seq_in_frame);
      cuts.add(c.seq_out_frame_exclusive);
    }
    let best: number | null = null;
    let bestPx = SNAP_PX;
    for (const cut of cuts) {
      const dpx = Math.abs((cut - candidateSeq) * pxPerSeq);
      if (dpx <= bestPx) {
        best = cut;
        bestPx = dpx;
      }
    }
    if (best === null) return candidateSrc;
    // Convert the snapped sequence position back to a source frame for the moving edge.
    const snappedSrc = edge === "out"
      ? clip.src_in_frame + Math.round((best - clip.seq_in_frame) * srcPerSeq)
      : clip.src_out_frame_exclusive + Math.round((best - clip.seq_out_frame_exclusive) * srcPerSeq);
    return snappedSrc;
  }

  function clampEdge(edge: TrimEdge, srcIn: number, srcOut: number): [number, number] {
    if (edge === "out") {
      let out = Math.max(srcIn + 1, srcOut);
      if (assetDuration !== null) out = Math.min(out, assetDuration);
      out = Math.max(srcIn + 1, out); // re-assert non-empty after the duration clamp
      return [srcIn, out];
    }
    const inFrame = Math.min(srcOut - 1, Math.max(0, srcIn));
    return [inFrame, srcOut];
  }

  function onEdgeDown(clip: TimelineClip, edge: TrimEdge, e: ReactPointerEvent): void {
    const stripW = stripRef.current?.getBoundingClientRect().width ?? 0;
    if (stripW <= 0 || total <= 0) return;
    const seqLen = clip.seq_out_frame_exclusive - clip.seq_in_frame;
    const srcLen = clip.src_out_frame_exclusive - clip.src_in_frame;
    const framesPerPx = total / stripW; // sequence frames per pixel
    const srcPerSeq = seqLen > 0 ? srcLen / seqLen : 1;
    const srcPerPx = framesPerPx * srcPerSeq; // source frames per pixel
    (e.target as Element).setPointerCapture?.(e.pointerId);
    setEdgeDrag({
      clipId: clip.id,
      edge,
      startX: e.clientX,
      srcPerPx,
      newSrcIn: clip.src_in_frame,
      newSrcOut: clip.src_out_frame_exclusive,
    });
  }

  function onEdgeMove(e: ReactPointerEvent): void {
    if (!edgeDrag) return;
    const clip = tl.clips.find((c) => c.id === edgeDrag.clipId);
    if (!clip) return;
    const dxFrames = Math.round((e.clientX - edgeDrag.startX) * edgeDrag.srcPerPx);
    let srcIn = clip.src_in_frame;
    let srcOut = clip.src_out_frame_exclusive;
    if (edgeDrag.edge === "out") srcOut = clip.src_out_frame_exclusive + dxFrames;
    else srcIn = clip.src_in_frame + dxFrames;
    const snapped = edgeDrag.edge === "out"
      ? snapEdge(clip, "out", srcOut)
      : snapEdge(clip, "in", srcIn);
    if (edgeDrag.edge === "out") srcOut = snapped;
    else srcIn = snapped;
    const [ci, co] = clampEdge(edgeDrag.edge, srcIn, srcOut);
    setEdgeDrag({ ...edgeDrag, newSrcIn: ci, newSrcOut: co });
  }

  async function onEdgeUp(): Promise<void> {
    const drag = edgeDrag;
    setEdgeDrag(null);
    if (!drag) return;
    const clip = tl.clips.find((c) => c.id === drag.clipId);
    if (!clip) return;
    // Only apply if something actually changed and the range is valid.
    const changed =
      drag.newSrcIn !== clip.src_in_frame || drag.newSrcOut !== clip.src_out_frame_exclusive;
    if (!changed || drag.newSrcOut <= drag.newSrcIn) return;
    await runOp({
      op: "trim",
      at_seq_frame: clip.seq_in_frame,
      new_src_in_frame: drag.newSrcIn,
      new_src_out_frame_exclusive: drag.newSrcOut,
    });
  }

  /** Begin dragging a clip's audio leading-edge handle on the A1 lane. `clip` must not be the
   *  sequence-first clip (its head has no predecessor cut → always a hard cut, non-draggable). */
  function onAudioDown(clip: TimelineClip, e: ReactPointerEvent): void {
    const stripW = stripRef.current?.getBoundingClientRect().width ?? 0;
    if (stripW <= 0 || total <= 0) return;
    const baseFrames = offsetSamplesToFrames(
      clip.audio_offset_samples,
      audioRate.sampleRate,
      audioRate.rateNum,
      audioRate.rateDen,
    );
    (e.target as Element).setPointerCapture?.(e.pointerId);
    setAudioDrag({
      clipId: clip.id,
      atSeqFrame: clip.seq_in_frame,
      startX: e.clientX,
      framesPerPx: total / stripW, // audio shares the picture sequence timebase 1:1
      baseFrames,
      offsetFrames: baseFrames,
    });
  }

  /** Snap a candidate audio offset (frames, relative to the picture cut) to the hard cut (0) when it
   *  is within AUDIO_SNAP_PX of the picture leading edge — the most useful magnet for L/J trims. */
  function snapAudioOffset(offsetFrames: number, framesPerPx: number): number {
    const px = Math.abs(offsetFrames / (framesPerPx || 1));
    return px <= AUDIO_SNAP_PX ? 0 : offsetFrames;
  }

  function onAudioMove(e: ReactPointerEvent): void {
    if (!audioDrag) return;
    const dxFrames = Math.round((e.clientX - audioDrag.startX) * audioDrag.framesPerPx);
    const raw = audioDrag.baseFrames + dxFrames;
    setAudioDrag({ ...audioDrag, offsetFrames: snapAudioOffset(raw, audioDrag.framesPerPx) });
  }

  async function onAudioUp(): Promise<void> {
    const drag = audioDrag;
    setAudioDrag(null);
    if (!drag) return;
    // Only commit when the offset actually changed; the backend hard-clamps |offset| < 1 frame → 0.
    if (drag.offsetFrames === drag.baseFrames) return;
    await runOp({
      op: "set_audio_offset",
      at_seq_frame: drag.atSeqFrame,
      audio_offset_frames: drag.offsetFrames,
    });
  }

  async function exportAs(fmt: ExportFormat, ext: string): Promise<void> {
    setError(null);
    try {
      const result = await client.exportTimeline(tl.id, fmt);
      if (result.content) {
        await window.laura.saveTextFile(`${tl.name}.${ext}`, result.content);
      }
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="border-t border-edge bg-panel px-5 py-3">
      <div className="mb-1 flex items-center justify-between">
        <span className="flex items-center gap-2">
          <span className="text-xs uppercase tracking-wide text-slate-500">
            Rough Cut · {tl.name}
          </span>
          <button
            type="button"
            onClick={() => void undo()}
            disabled={history.length === 0}
            title="Rückgängig"
            className="rounded bg-ink px-2 py-0.5 text-xs text-slate-300 hover:bg-edge disabled:opacity-30"
          >
            ↶ Undo
          </button>
          <button
            type="button"
            onClick={() => void redo()}
            disabled={future.length === 0}
            title="Wiederholen"
            className="rounded bg-ink px-2 py-0.5 text-xs text-slate-300 hover:bg-edge disabled:opacity-30"
          >
            ↷ Redo
          </button>
        </span>
        <span className="flex items-center gap-2">
          {tl.clips.length > 0 &&
            EXPORT_FORMATS.map((f) => (
              <button
                key={f.fmt}
                type="button"
                onClick={() => void exportAs(f.fmt, f.ext)}
                className="rounded bg-ink px-2 py-0.5 text-xs text-slate-300 hover:bg-edge"
              >
                {f.label}
              </button>
            ))}
          <span className="text-xs text-slate-500">
            {tl.clips.length} Clips · {total} frames
          </span>
        </span>
      </div>
      {error && <div className="mb-1 text-xs text-red-400">{error}</div>}
      {tl.clips.length === 0 ? (
        <div className="flex h-12 items-center justify-center rounded-md border border-dashed border-edge text-xs text-slate-600">
          Klicke einen Shot oder Transkript-Satz an, um ihn anzuhängen.
        </div>
      ) : (
        <div className="flex flex-col gap-1">
          {/* V1 — picture lane (reorder + edge-trim). */}
          <div className="flex items-center gap-2">
            <span className="w-6 shrink-0 text-[9px] font-medium uppercase text-slate-500">V1</span>
            <div
              ref={stripRef}
              className="flex h-12 min-w-0 flex-1 gap-px overflow-hidden rounded-md"
              onPointerMove={onEdgeMove}
              onPointerUp={() => void onEdgeUp()}
              onPointerCancel={() => void onEdgeUp()}
            >
              {tl.clips.map((c, i) => (
                <ClipThumb
                  key={c.id}
                  client={client}
                  clip={c}
                  index={i}
                  total={total}
                  selected={c.id === selected}
                  assetDuration={c.id === selected ? assetDuration : null}
                  dragOver={dragId !== null && dragOverId === c.id && dragId !== c.id}
                  onSelect={() => {
                    const next = c.id === selected ? null : c.id;
                    setSelected(next);
                    onSelect?.(next);
                    onScrub?.(c.asset_id, c.src_in_frame);
                  }}
                  onDragStart={() => setDragId(c.id)}
                  onDragEnter={() => setDragOverId(c.id)}
                  onDragEnd={() => {
                    setDragId(null);
                    setDragOverId(null);
                    setDragOverEnd(false);
                  }}
                  onDrop={() => void reorderTo(c.seq_in_frame)}
                  onEdgeDown={(edge, e) => onEdgeDown(c, edge, e)}
                />
              ))}
              {/* Drop-at-end affordance: a thin zone after the last clip. */}
              <div
                aria-label="Move clip to end"
                title="Hierher ziehen = ans Ende"
                onDragOver={(e) => {
                  e.preventDefault();
                  if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
                  setDragOverEnd(true);
                }}
                onDragLeave={() => setDragOverEnd(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  void reorderTo(total);
                }}
                className={`h-full w-2 shrink-0 ${
                  dragId !== null && dragOverEnd ? "bg-amber-300/80" : "bg-transparent"
                }`}
              />
            </div>
          </div>
          {/* A1 — audio lane. Each clip's audio leading edge is drawn offset from its picture cut by
              `audio_offset_samples` (projected to frames); the handle drags a J/L split. The lane
              shares the V1 timebase (same `total`), so the geometry lines up under the picture. */}
          <div className="flex items-center gap-2">
            <span className="w-6 shrink-0 text-[9px] font-medium uppercase text-slate-500">A1</span>
            <div
              aria-label="Audio-Spur (A1)"
              className="relative h-7 min-w-0 flex-1 overflow-hidden rounded-md bg-ink/40"
              onPointerMove={onAudioMove}
              onPointerUp={() => void onAudioUp()}
              onPointerCancel={() => void onAudioUp()}
            >
              {tl.clips.map((c, i) => {
                const dragging = audioDrag?.clipId === c.id;
                const offsetFrames = dragging
                  ? audioDrag.offsetFrames
                  : offsetSamplesToFrames(
                      c.audio_offset_samples,
                      audioRate.sampleRate,
                      audioRate.rateNum,
                      audioRate.rateDen,
                    );
                return (
                  <AudioBlock
                    key={c.id}
                    clip={c}
                    index={i}
                    total={total}
                    offsetFrames={offsetFrames}
                    isFirst={i === 0}
                    isDragging={dragging}
                    onHandleDown={(e) => onAudioDown(c, e)}
                  />
                );
              })}
            </div>
          </div>
          {audioDrag && (
            <div className="pl-8 text-[10px] text-emerald-300" data-testid="audio-offset-readout">
              {offsetLabel(audioDrag.offsetFrames)}
            </div>
          )}
        </div>
      )}
      {sel && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
          <span className="text-slate-500">
            Clip src{" "}
            {edgeDrag && edgeDrag.clipId === sel.id ? edgeDrag.newSrcIn : sel.src_in_frame}–
            {edgeDrag && edgeDrag.clipId === sel.id ? edgeDrag.newSrcOut : sel.src_out_frame_exclusive}:
          </span>
          <button
            type="button"
            onClick={() => void splitSelected()}
            className="rounded bg-ink px-2 py-0.5 text-slate-200 hover:bg-edge"
          >
            Split (Mitte)
          </button>
          <button
            type="button"
            onClick={() => void trimSelected(-TRIM_STEP)}
            className="rounded bg-ink px-2 py-0.5 text-slate-200 hover:bg-edge"
          >
            Trim −{TRIM_STEP}
          </button>
          <button
            type="button"
            onClick={() => void trimSelected(TRIM_STEP)}
            className="rounded bg-ink px-2 py-0.5 text-slate-200 hover:bg-edge"
          >
            Trim +{TRIM_STEP}
          </button>
          <button
            type="button"
            onClick={() => void duplicateSelected()}
            className="rounded bg-ink px-2 py-0.5 text-slate-200 hover:bg-edge"
          >
            Duplizieren
          </button>
          <button
            type="button"
            onClick={() => void deleteSelected()}
            className="rounded bg-ink px-2 py-0.5 text-red-300 hover:bg-red-600/40"
          >
            Löschen
          </button>
        </div>
      )}
    </div>
  );
}
