import { type ReactElement, useEffect, useRef } from "react";

/** Renders normalised waveform peaks (0..1) as centered bars on a canvas. */
export function Waveform({ peaks }: { peaks: number[] }): ReactElement {
  const ref = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.floor(width * dpr));
    canvas.height = Math.max(1, Math.floor(height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    ctx.clearRect(0, 0, width, height);
    if (peaks.length === 0) return;

    const mid = height / 2;
    const barW = width / peaks.length;
    ctx.fillStyle = "#38bdf8";
    for (let i = 0; i < peaks.length; i++) {
      const amp = Math.min(1, Math.max(0, peaks[i]));
      const barH = Math.max(1, amp * height);
      ctx.fillRect(i * barW, mid - barH / 2, Math.max(1, barW * 0.7), barH);
    }
  }, [peaks]);

  return <canvas ref={ref} className="h-24 w-full rounded-md bg-ink" />;
}
