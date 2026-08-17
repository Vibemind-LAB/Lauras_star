import { type ReactElement, useEffect, useMemo, useState } from "react";

import type {
  LauraClient,
  VisualSceneCandidate,
  VisualSceneSelection,
  VisualSelectionGateStatus,
} from "../../api";

type VisualSelectionClient = Pick<
  LauraClient,
  "assetFrameUrl" | "confirmVisualSelection"
>;

function CandidateThumb({
  client,
  assetId,
  frame,
}: {
  client: Pick<LauraClient, "assetFrameUrl">;
  assetId: string;
  frame: number;
}): ReactElement {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    client
      .assetFrameUrl(assetId, Math.max(0, frame))
      .then((nextUrl) => {
        if (!active) {
          URL.revokeObjectURL(nextUrl);
          return;
        }
        objectUrl = nextUrl;
        setUrl(nextUrl);
      })
      .catch(() => {
        // Decision metadata remains usable when a local thumbnail is unavailable.
      });
    return () => {
      active = false;
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
    };
  }, [assetId, client, frame]);

  return (
    <span className="mb-1 block aspect-video w-full overflow-hidden rounded bg-accent/20">
      {url !== null ? <img src={url} alt="" className="h-full w-full object-contain" /> : null}
    </span>
  );
}

export interface VisualSelectionCardProps {
  gate: VisualSelectionGateStatus;
  assetId: string;
  sessionId: string;
  client: VisualSelectionClient;
  onConfirmed: () => Promise<void> | void;
}

function initialBeatSelections(gate: VisualSelectionGateStatus): Record<string, string> {
  const selected: Record<string, string> = {};
  for (const beat of gate.beats ?? []) {
    const preferred = beat.selected_candidate_id ?? beat.recommended_candidate_id;
    if (beat.candidates.some((candidate) => candidate.candidate_id === preferred)) {
      selected[beat.beat_id] = preferred;
    }
  }
  return selected;
}

function LegacyVisualSelectionCard({
  gate,
  assetId,
  sessionId,
  client,
  onConfirmed,
}: VisualSelectionCardProps): ReactElement {
  const beats = gate.beats ?? [];
  const [selected, setSelected] = useState<Record<string, string>>(() =>
    initialBeatSelections(gate),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSelected(initialBeatSelections(gate));
    setError(null);
  }, [gate]);

  const selectedCandidateIds = useMemo(
    () => beats.map((beat) => selected[beat.beat_id]).filter((id): id is string => id !== undefined),
    [beats, selected],
  );
  const complete =
    gate.proposal_id !== null &&
    beats.length > 0 &&
    beats.every((beat) =>
      beat.candidates.some(
        (candidate) => candidate.candidate_id === selected[beat.beat_id],
      ),
    );

  const submit = async (): Promise<void> => {
    if (!complete || busy || gate.proposal_id === null) return;
    setBusy(true);
    setError(null);
    try {
      await client.confirmVisualSelection(sessionId, gate.proposal_id, selectedCandidateIds);
      await onConfirmed();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Bildauswahl konnte nicht bestätigt werden");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-0.5 rounded border border-bezel bg-surface-1 px-1.5 py-1 text-[11px]">
      <div className="mb-1 text-content-strong">Bildauswahl prüfen</div>
      <div className="space-y-2">
        {beats.map((beat) => (
          <fieldset key={beat.beat_id} className="rounded border border-bezel p-1">
            <legend className="px-1 text-content-strong">{beat.narration_text}</legend>
            <div className="grid grid-cols-2 gap-1.5">
              {beat.candidates.map((candidate) => (
                <label
                  key={candidate.candidate_id}
                  className={`cursor-pointer rounded border p-1 text-left ${
                    selected[beat.beat_id] === candidate.candidate_id
                      ? "border-accent bg-accent/15"
                      : "border-bezel opacity-75"
                  }`}
                >
                  <CandidateThumb
                    client={client}
                    assetId={assetId}
                    frame={candidate.thumb_frame}
                  />
                  <input
                    type="radio"
                    name={`visual-beat-${beat.beat_id}`}
                    value={candidate.candidate_id}
                    data-testid={`visual-candidate-${candidate.candidate_id}`}
                    checked={selected[beat.beat_id] === candidate.candidate_id}
                    disabled={busy}
                    onChange={() =>
                      setSelected((current) => ({
                        ...current,
                        [beat.beat_id]: candidate.candidate_id,
                      }))
                    }
                    className="mr-1"
                  />
                  <span className="font-semibold text-content-strong">
                    Szene {candidate.scene_number}
                  </span>
                  <div className="text-content-muted">{candidate.description}</div>
                  <div className="text-content-faint">{candidate.rationale}</div>
                </label>
              ))}
            </div>
          </fieldset>
        ))}
      </div>
      {error !== null ? (
        <div className="mt-1 text-status-err" role="alert">
          {error}
        </div>
      ) : null}
      <button
        type="button"
        disabled={!complete || busy}
        onClick={() => void submit()}
        className="mt-1 rounded bg-accent px-2 py-1 font-medium text-accent-ink disabled:opacity-40"
      >
        Bildauswahl übernehmen
      </button>
    </div>
  );
}

interface SceneDecision {
  candidateId: string;
  included: boolean;
  durationS: number;
}

function initialSceneDecisions(gate: VisualSelectionGateStatus): Record<number, SceneDecision> {
  const decisions: Record<number, SceneDecision> = {};
  for (const choice of gate.scene_choices ?? []) {
    const preferredCandidateId =
      choice.selected_candidate_id ?? choice.recommended_candidate_id;
    const candidate = choice.candidates.find(
      (entry) => entry.candidate_id === preferredCandidateId,
    );
    if (candidate === undefined) continue;
    const preferredDuration =
      choice.requested_duration_s ?? choice.recommended_duration_s;
    decisions[choice.rough_cut_order] = {
      candidateId: preferredCandidateId,
      included: choice.included ?? choice.recommended_included,
      durationS: Math.max(1, Math.min(preferredDuration, candidate.max_duration_s)),
    };
  }
  return decisions;
}

function selectedCandidate(
  candidates: VisualSceneCandidate[],
  decision: SceneDecision | undefined,
): VisualSceneCandidate | undefined {
  return candidates.find((candidate) => candidate.candidate_id === decision?.candidateId);
}

function secondsLabel(frames: number, fps: number): string {
  return (frames / fps).toFixed(1).replace(".", ",");
}

function RoughCutVisualSelectionCard({
  gate,
  assetId,
  sessionId,
  client,
  onConfirmed,
}: VisualSelectionCardProps): ReactElement {
  const choices = gate.scene_choices ?? [];
  const [decisions, setDecisions] = useState<Record<number, SceneDecision>>(() =>
    initialSceneDecisions(gate),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDecisions(initialSceneDecisions(gate));
    setError(null);
  }, [gate]);

  const selections = useMemo<VisualSceneSelection[]>(
    () =>
      choices.flatMap((choice) => {
        const decision = decisions[choice.rough_cut_order];
        const candidate = selectedCandidate(choice.candidates, decision);
        if (decision === undefined || candidate === undefined) return [];
        return [
          {
            rough_cut_order: choice.rough_cut_order,
            candidate_id: candidate.candidate_id,
            included: decision.included,
            requested_duration_s: decision.durationS,
          },
        ];
      }),
    [choices, decisions],
  );

  const fps = typeof gate.fps === "number" && gate.fps > 0 ? gate.fps : null;
  const voiceFrames =
    typeof gate.voice_total_frames === "number" && gate.voice_total_frames > 0
      ? gate.voice_total_frames
      : null;
  const included = selections.filter((selection) => selection.included);
  const requestedFrames =
    fps === null
      ? 0
      : included.reduce(
          (total, selection) => total + Math.round(selection.requested_duration_s * fps),
          0,
        );
  const lastIncluded = included.at(-1);
  const lastRequestedFrames =
    lastIncluded === undefined || fps === null
      ? 0
      : Math.round(lastIncluded.requested_duration_s * fps);
  const finalLastFrames =
    voiceFrames === null ? 0 : voiceFrames - (requestedFrames - lastRequestedFrames);
  const validFinalTrim =
    fps !== null &&
    finalLastFrames >= Math.round(fps) &&
    finalLastFrames <= lastRequestedFrames;
  const complete =
    gate.proposal_id !== null &&
    selections.length === choices.length &&
    included.length >= 3 &&
    fps !== null &&
    voiceFrames !== null &&
    requestedFrames >= voiceFrames &&
    validFinalTrim;

  const chooseCandidate = (
    roughCutOrder: number,
    candidate: VisualSceneCandidate,
  ): void => {
    setDecisions((current) => {
      const previous = current[roughCutOrder];
      if (previous === undefined) return current;
      return {
        ...current,
        [roughCutOrder]: {
          ...previous,
          candidateId: candidate.candidate_id,
          durationS: Math.min(previous.durationS, candidate.max_duration_s),
        },
      };
    });
  };

  const submit = async (): Promise<void> => {
    if (!complete || busy || gate.proposal_id === null) return;
    setBusy(true);
    setError(null);
    try {
      await client.confirmVisualSelection(sessionId, gate.proposal_id, selections);
      await onConfirmed();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Bildauswahl konnte nicht bestätigt werden");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-0.5 rounded border border-bezel bg-surface-1 px-1.5 py-1 text-[11px]">
      <div className="mb-1 text-content-strong">Bildauswahl prüfen</div>
      {fps !== null && voiceFrames !== null ? (
        <div className="mb-1 rounded bg-surface-2 px-1.5 py-1 text-content-muted">
          <div>
            Gewählt: {secondsLabel(requestedFrames, fps)} s · Voice: {secondsLabel(voiceFrames, fps)} s
          </div>
          {requestedFrames >= voiceFrames && included.length > 0 ? (
            validFinalTrim ? (
              <div>Letzte Szene final: {secondsLabel(finalLastFrames, fps)} s</div>
            ) : (
              <div className="text-status-err">
                Die früheren Szenen lassen weniger als 1,0 s für die letzte Szene.
              </div>
            )
          ) : (
            <div>Die gewählte Dauer muss die Voice vollständig abdecken.</div>
          )}
        </div>
      ) : (
        <div className="mb-1 text-status-err">Voice-Länge oder Framerate fehlt.</div>
      )}
      <div className="space-y-2">
        {choices.map((choice) => {
          const decision = decisions[choice.rough_cut_order];
          const candidate = selectedCandidate(choice.candidates, decision);
          const label = `Szene ${choice.scene_number} · Rough Cut ${choice.rough_cut_order + 1}`;
          return (
            <div
              key={choice.rough_cut_order}
              role="group"
              aria-label={label}
              className={`rounded border border-bezel p-1 ${decision?.included === false ? "opacity-70" : ""}`}
            >
              <div className="flex items-center gap-1">
                <label className="flex items-center gap-1 font-semibold text-content-strong">
                  <input
                    type="checkbox"
                    data-testid={`visual-scene-use-${choice.rough_cut_order}`}
                    checked={decision?.included ?? false}
                    disabled={busy || decision === undefined}
                    onChange={(event) =>
                      setDecisions((current) => {
                        const previous = current[choice.rough_cut_order];
                        if (previous === undefined) return current;
                        return {
                          ...current,
                          [choice.rough_cut_order]: {
                            ...previous,
                            included: event.target.checked,
                          },
                        };
                      })
                    }
                  />
                  Verwenden
                </label>
                <span className="text-content-strong">{label}</span>
              </div>
              <p className="text-content-muted">{choice.description}</p>
              <div className="mt-1 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
                {choice.candidates.map((entry) => (
                  <label
                    key={entry.candidate_id}
                    className={`cursor-pointer rounded border p-1 ${
                      decision?.candidateId === entry.candidate_id
                        ? "border-accent bg-accent/15"
                        : "border-bezel opacity-75"
                    }`}
                  >
                    <CandidateThumb client={client} assetId={assetId} frame={entry.thumb_frame} />
                    <input
                      type="radio"
                      name={`visual-scene-${choice.rough_cut_order}`}
                      data-testid={`visual-scene-candidate-${entry.candidate_id}`}
                      checked={decision?.candidateId === entry.candidate_id}
                      disabled={busy}
                      onChange={() => chooseCandidate(choice.rough_cut_order, entry)}
                      className="mr-1"
                    />
                    <span>{entry.description}</span>
                    <div className="text-content-faint">
                      Frames {entry.src_start_frame}–{entry.src_end_frame_exclusive}
                    </div>
                  </label>
                ))}
              </div>
              {candidate !== undefined ? (
                <div className="mt-1 text-content-faint">
                  <p>{candidate.transcript_snippet || choice.transcript}</p>
                  <p>{candidate.rationale || choice.rationale}</p>
                </div>
              ) : null}
              {candidate !== undefined ? (
                <div className="mt-1 flex flex-wrap gap-1">
                  {Array.from({ length: candidate.max_duration_s }, (_, index) => index + 1).map(
                    (duration) => (
                      <button
                        key={duration}
                        type="button"
                        aria-label={`Szene ${choice.scene_number}: ${duration} Sekunden`}
                        disabled={busy || decision?.included === false}
                        onClick={() =>
                          setDecisions((current) => {
                            const previous = current[choice.rough_cut_order];
                            if (previous === undefined) return current;
                            return {
                              ...current,
                              [choice.rough_cut_order]: { ...previous, durationS: duration },
                            };
                          })
                        }
                        className={`rounded border px-1 py-0.5 ${
                          decision?.durationS === duration
                            ? "border-accent bg-accent/15 text-content-strong"
                            : "border-bezel text-content-muted"
                        } disabled:opacity-40`}
                      >
                        {duration}s
                      </button>
                    ),
                  )}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
      {error !== null ? (
        <div className="mt-1 text-status-err" role="alert">
          {error}
        </div>
      ) : null}
      <button
        type="button"
        disabled={!complete || busy}
        onClick={() => void submit()}
        className="mt-1 rounded bg-accent px-2 py-1 font-medium text-accent-ink disabled:opacity-40"
      >
        Bildauswahl übernehmen
      </button>
    </div>
  );
}

export function VisualSelectionCard(props: VisualSelectionCardProps): ReactElement {
  if ((props.gate.scene_choices?.length ?? 0) > 0) {
    return <RoughCutVisualSelectionCard {...props} />;
  }
  return <LegacyVisualSelectionCard {...props} />;
}
