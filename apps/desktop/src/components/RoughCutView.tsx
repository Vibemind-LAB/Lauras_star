import { type ReactElement, useCallback, useState } from "react";

import {
  type Asset,
  type BuildFromShotsResult,
  type LauraClient,
  type Segment,
  type Timeline,
} from "../api";
import { useScenes } from "../hooks/useScenes";
import { log } from "../shared/log";
import { BiasSlider, DEFAULT_CUT_BIAS } from "./BiasSlider";
import { Player } from "./Player";
import { QualityPanel } from "./QualityPanel";
import { SceneStrip } from "./SceneStrip";
import { SplitCutList } from "./SplitCutList";
import { TranscriptStatusBanner } from "./TranscriptStatusBanner";

export function RoughCutView({
  client,
  projectId,
  asset,
  roughCut,
  segments,
  transcriptNote = null,
  transcriptBusy = false,
  onGenerateTranscript = () => undefined,
  onRoughCutChange,
  seek,
  currentFrame,
  onSeek,
  onFrame,
}: {
  client: LauraClient;
  projectId: string | null;
  asset: Asset | null;
  roughCut: Timeline | null;
  segments: Segment[];
  transcriptNote?: string | null;
  transcriptBusy?: boolean;
  onGenerateTranscript?: () => void;
  onRoughCutChange: () => Promise<void>;
  seek: { frame: number } | null;
  currentFrame: number;
  onSeek: (frame: number) => void;
  onFrame: (frame: number) => void;
}): ReactElement {
  const scenes = useScenes(client, roughCut?.id ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cutBias, setCutBias] = useState(DEFAULT_CUT_BIAS);
  const [build, setBuild] = useState<BuildFromShotsResult | null>(null);

  const onGenerate = useCallback(async () => {
    if (!asset || !roughCut || !projectId) return;
    setBusy(true);
    setError(null);
    try {
      try {
        await scenes.generate(asset.id);
        return;
      } catch (e) {
        const message = e instanceof Error ? e.message : String(e);
        if (!message.includes("rough cut is empty")) throw e;
        const res = await client.buildRoughCutFromShots(projectId, asset.id, roughCut.id, {
          cutBias,
        });
        setBuild(res);
        await onRoughCutChange();
      }
      await scenes.generate(asset.id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }, [asset, roughCut, projectId, client, cutBias, onRoughCutChange, scenes]);

  // Re-build the rough cut at a new bias. The from-shots endpoint refuses to clobber a populated
  // timeline, so we always create a fresh one (timelineId omitted) and re-point the rough cut at
  // it; the returned quality is what the bias slider exists to surface.
  const onRebuildAtBias = useCallback(
    async (nextBias: number) => {
      setCutBias(nextBias);
      if (!asset || !projectId) return;
      setBusy(true);
      setError(null);
      try {
        const res = await client.buildRoughCutFromShots(projectId, asset.id, undefined, {
          cutBias: nextBias,
        });
        setBuild(res);
        await onRoughCutChange();
      } catch (e) {
        log.error("rebuild rough cut at bias failed", e);
        setError(String(e));
      } finally {
        setBusy(false);
      }
    },
    [asset, projectId, client, onRoughCutChange],
  );

  if (!asset || !roughCut) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-content-faint">
        Pick an asset under Media to create scenes.
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex min-h-0 flex-1 bg-surface-2/20 p-2">
        <Player asset={asset} seekTo={seek} onFrame={onFrame} />
      </div>
      <div className="flex items-start gap-3 border-t border-bezel px-3 py-2">
        <div className="flex flex-col gap-2">
          <button
            type="button"
            onClick={() => void onGenerate()}
            disabled={busy}
            className="rounded bg-accent px-3 py-1 text-xs text-white disabled:opacity-40"
          >
            {busy ? "Erzeuge…" : "Create scenes"}
          </button>
          <div className="w-64">
            <BiasSlider value={cutBias} onChange={(b) => void onRebuildAtBias(b)} disabled={busy} />
          </div>
          <span className="text-[11px] text-content-faint">Frame {currentFrame}</span>
          {(error ?? scenes.error) && (
            <span className="text-[11px] text-status-err">{error ?? scenes.error}</span>
          )}
        </div>
        {build?.quality && (
          <QualityPanel quality={build.quality} splitCuts={build.split_cuts} />
        )}
        {build && build.split_cuts.some((sc) => sc.kind !== "hard") && (
          <SplitCutList
            client={client}
            projectId={projectId}
            timelineId={roughCut.id}
            splitCuts={build.split_cuts}
          />
        )}
      </div>
      {segments.length === 0 && (
        <TranscriptStatusBanner
          note={transcriptNote}
          busy={transcriptBusy}
          onGenerate={onGenerateTranscript}
        />
      )}
      <div className="border-t border-bezel">
        <SceneStrip
          client={client}
          asset={asset}
          scenes={scenes.scenes}
          clips={roughCut.clips}
          segments={segments}
          onSplit={(id, at) => void scenes.split(id, at)}
          onMerge={(id) => void scenes.merge(id)}
          onRename={(id, name) => void scenes.rename(id, name)}
          onSeek={onSeek}
        />
      </div>
    </div>
  );
}

