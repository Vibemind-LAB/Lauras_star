import { type ReactElement, useId } from "react";

/**
 * The product default cut_bias: the editorial weight (0.4) of the joint placement's default
 * (0.6 visual / 0.4 editorial). The #5 tuning report (docs/eval/cut-tuning.md) places the
 * picture↔sound crossover between 0.4 and 0.6, so 0.4 sits just on the picture-leaning side of it.
 */
export const DEFAULT_CUT_BIAS = 0.4;

function label(bias: number): string {
  if (bias <= 0.2) return "bild-genau";
  if (bias >= 0.8) return "schnitt-sauber";
  return "ausgewogen";
}

/**
 * Picture-vs-sound bias for the rough-cut build: a labelled range mapping to cut_bias ∈ [0, 1]
 * (0 = pure visual peak, 1 = pure editorial / clean word edge). Moving it re-runs the build with
 * the new bias. Accessible: a real <label>, aria-valuetext, and a live value read-out.
 */
export function BiasSlider({
  value,
  onChange,
  disabled = false,
}: {
  value: number;
  onChange: (next: number) => void;
  disabled?: boolean;
}): ReactElement {
  const id = useId();
  const display = label(value);
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-baseline justify-between gap-2">
        <label htmlFor={id} className="text-[10px] uppercase tracking-wide text-content-faint">
          Schnitt-Bias
        </label>
        <span className="text-[10px] text-content-muted tabular-nums" data-testid="bias-readout">
          {display} · {value.toFixed(2)}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <span className="shrink-0 text-[10px] text-content-faint">Bild-genau</span>
        <input
          id={id}
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(Number(e.target.value))}
          aria-label="Schnitt-Bias: Bild-genau bis Schnitt-sauber"
          aria-valuetext={`${display}, ${value.toFixed(2)}`}
          className="min-w-0 flex-1 accent-accent disabled:opacity-40"
        />
        <span className="shrink-0 text-[10px] text-content-faint">Schnitt-sauber</span>
      </div>
    </div>
  );
}

