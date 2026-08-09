import { type ReactElement, useEffect, useMemo, useState } from "react";

import type { LauraClient, VisualSelectionGateStatus } from "../../api";

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
        // The metadata remains usable when a local thumbnail is no longer available.
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

function initialSelections(gate: VisualSelectionGateStatus): Record<string, string> {
  const selected: Record<string, string> = {};
  for (const beat of gate.beats) {
    const preferred = beat.selected_candidate_id ?? beat.recommended_candidate_id;
    if (beat.candidates.some((candidate) => candidate.candidate_id === preferred)) {
      selected[beat.beat_id] = preferred;
    }
  }
  return selected;
}

export interface VisualSelectionCardProps {
  gate: VisualSelectionGateStatus;
  assetId: string;
  sessionId: string;
  client: VisualSelectionClient;
  onConfirmed: () => Promise<void> | void;
}

export function VisualSelectionCard({
  gate,
  assetId,
  sessionId,
  client,
  onConfirmed,
}: VisualSelectionCardProps): ReactElement {
  const [selected, setSelected] = useState<Record<string, string>>(() =>
    initialSelections(gate),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSelected(initialSelections(gate));
    setError(null);
  }, [gate]);

  const selectedCandidateIds = useMemo(
    () => gate.beats.map((beat) => selected[beat.beat_id]).filter((id): id is string => id !== undefined),
    [gate.beats, selected],
  );
  const complete =
    gate.proposal_id !== null &&
    gate.beats.length > 0 &&
    gate.beats.every((beat) =>
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
        {gate.beats.map((beat) => (
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
