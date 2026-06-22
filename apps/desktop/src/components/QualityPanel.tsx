import { type ReactElement } from "react";

import { type RoughCutQuality, type SplitCut } from "../api";

/** Green at/above .8, amber at/above .55, else red — a quick at-a-glance grade of a score. */
function grade(score: number): { bar: string; text: string } {
  if (score >= 0.8) return { bar: "bg-status-ok", text: "text-status-ok" };
  if (score >= 0.55) return { bar: "bg-status-warn", text: "text-status-warn" };
  return { bar: "bg-status-err", text: "text-status-err" };
}

function pct(score: number): string {
  return `${Math.round(score * 100)}%`;
}

function Bar({ label, score }: { label: string; score: number }): ReactElement {
  const g = grade(score);
  return (
    <div className="flex items-center gap-2">
      <span className="w-24 shrink-0 text-[10px] text-content-muted">{label}</span>
      <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-surface-2">
        <div
          className={`h-full rounded-full ${g.bar}`}
          style={{ width: `${Math.round(Math.min(1, Math.max(0, score)) * 100)}%` }}
        />
      </div>
      <span className={`w-9 shrink-0 text-right text-[10px] font-medium tabular-nums ${g.text}`}>
        {pct(score)}
      </span>
    </div>
  );
}

/** Count L (audio after video) and J (audio before video) edits among the split-cut recs. */
function countLJ(splitCuts: SplitCut[]): { l: number; j: number } {
  let l = 0;
  let j = 0;
  for (const sc of splitCuts) {
    if (sc.kind === "L") l += 1;
    else if (sc.kind === "J") j += 1;
  }
  return { l, j };
}

/**
 * Compact rough-cut quality read-out: overall + visual-exactness + editorial-cleanliness as
 * color-graded bars, plus the count of recommended L/J split cuts. Shown after a build so the user
 * can SEE how exact/clean the cut is and how it shifts with the bias slider. `splitCuts` feeds the
 * L/J tooltip; `quality.n_split_cuts` is the headline count.
 */
export function QualityPanel({
  quality,
  splitCuts,
}: {
  quality: RoughCutQuality;
  splitCuts: SplitCut[];
}): ReactElement {
  const g = grade(quality.overall);
  const { l, j } = countLJ(splitCuts);
  return (
    <div
      className="w-64 rounded-md border border-bezel bg-surface-0 p-2"
      data-testid="quality-panel"
    >
      <div className="mb-1.5 flex items-baseline justify-between">
        <span className="text-[10px] uppercase tracking-wide text-content-faint">Schnittqualität</span>
        <span className={`text-sm font-semibold tabular-nums ${g.text}`}>{pct(quality.overall)}</span>
      </div>
      <div className="space-y-1">
        <Bar label="Bild-genau" score={quality.visual_exactness} />
        <Bar label="Schnitt-sauber" score={quality.editorial_cleanliness} />
      </div>
      <div
        className="mt-1.5 flex items-center justify-between border-t border-bezel pt-1.5 text-[10px] text-content-muted"
        title={`${j} J-Cuts, ${l} L-Cuts`}
      >
        <span>{quality.n_cuts} Schnitte</span>
        <span>
          {quality.n_split_cuts} Split-Cuts empfohlen
        </span>
      </div>
    </div>
  );
}


