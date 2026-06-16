/**
 * CaptionPreview — live 9:16 CSS preview of reel caption styling.
 *
 * Approximates the ffmpeg burn-in appearance via CSS overlays so producers
 * can see WYSIWYG styling before committing to a slow render.
 *
 * Canvas reference: 1080 × 1920 px (the internal reel canvas).
 * All pixel values from the caption controls are relative to that canvas
 * and are scaled down to the preview box dimensions on the fly.
 */

import { type ReactElement, useEffect, useRef, useState } from "react";

import type { CaptionMode, CaptionPosition, LauraClient } from "../api";
import { log } from "../shared/log";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Full-canvas height against which captionFontsize and captionSafeMargin
 *  are expressed (same 1080×1920 canvas the backend targets). */
const CANVAS_HEIGHT = 1920;

/** Sample caption text shown regardless of hook (always present so sizing
 *  is representative even without hook text). */
const SAMPLE_CAPTION = "Beispiel-Untertitel";

/** Words used to split karaoke highlight (first word vs the rest). */
const SAMPLE_WORDS = SAMPLE_CAPTION.split(" ");

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface CaptionPreviewProps {
  client: LauraClient;
  /** asset_id of the first flattened clip — used to fetch a poster frame. */
  posterAssetId: string | null;
  /** Source frame index to fetch as poster (defaults to 0). */
  posterFrame?: number;
  /** Hook text — rendered as a top-bar overlay when non-empty. */
  hook: string;
  /** Whether to show the KI-disclosure label (bottom-right burn-in). */
  disclosure: boolean;
  /** Whether captions are enabled; if false the caption box is hidden. */
  captionsOn: boolean;
  mode: CaptionMode;
  position: CaptionPosition;
  /** Font size in pixels on the 1080×1920 canvas. */
  fontsize: number;
  /** Safe-zone margin in pixels on the 1080×1920 canvas. */
  safeMargin: number;
}

// ---------------------------------------------------------------------------
// Helper: scale a canvas-px value to the current preview box height.
// ---------------------------------------------------------------------------

function scale(canvasPx: number, boxHeight: number): number {
  return (canvasPx / CANVAS_HEIGHT) * boxHeight;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function CaptionPreview({
  client,
  posterAssetId,
  posterFrame = 0,
  hook,
  disclosure,
  captionsOn,
  mode,
  position,
  fontsize,
  safeMargin,
}: CaptionPreviewProps): ReactElement {
  const [posterUrl, setPosterUrl] = useState<string | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const [boxHeight, setBoxHeight] = useState<number>(360);

  // ------------------------------------------------------------------
  // Poster frame fetch — mirrors AssembleView's Thumb lifecycle exactly:
  // active-flag guard + URL.revokeObjectURL on cleanup.
  // ------------------------------------------------------------------
  useEffect(() => {
    if (!posterAssetId) {
      setPosterUrl(null);
      return;
    }
    let active = true;
    let objectUrl: string | null = null;
    client
      .assetFrameUrl(posterAssetId, Math.max(0, posterFrame))
      .then((u) => {
        if (!active) {
          URL.revokeObjectURL(u);
          return;
        }
        objectUrl = u;
        setPosterUrl(u);
      })
      .catch((err: unknown) => {
        log.warn("CaptionPreview: poster fetch failed", err);
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [client, posterAssetId, posterFrame]);

  // ------------------------------------------------------------------
  // Measure box height for px scaling (ResizeObserver).
  // ------------------------------------------------------------------
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const obs = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setBoxHeight(entry.contentRect.height);
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  // ------------------------------------------------------------------
  // Derived pixel values (preview-box space).
  // ------------------------------------------------------------------
  const safeInset = scale(safeMargin, boxHeight);
  const captionFontPx = scale(fontsize, boxHeight);
  const hookFontPx = scale(52, boxHeight); // ~52px on canvas for hook bar
  const disclosureFontPx = scale(28, boxHeight);

  // Caption vertical position.
  const captionStyle: React.CSSProperties = (() => {
    const margin = safeInset;
    switch (position) {
      case "top":
        return { top: margin };
      case "middle":
        return { top: "50%", transform: "translateY(-50%)" };
      case "bottom":
      default:
        return { bottom: margin };
    }
  })();

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------
  return (
    <div
      ref={boxRef}
      /* 9:16 aspect ratio, capped to 360px height, dark background */
      className="relative w-full max-w-[203px] self-start overflow-hidden rounded border border-slate-600 bg-slate-950"
      style={{ aspectRatio: "9 / 16", maxHeight: "360px" }}
    >
      {/* ---- Poster frame (or gradient placeholder) ---- */}
      {posterUrl ? (
        <img
          src={posterUrl}
          alt=""
          className="absolute inset-0 h-full w-full object-cover"
          draggable={false}
        />
      ) : (
        <div className="absolute inset-0 bg-gradient-to-b from-slate-800 to-slate-950" />
      )}

      {/* ---- Safe-zone guide (dashed inset rectangle) ---- */}
      <div
        className="pointer-events-none absolute border border-dashed border-white/30"
        style={{
          inset: safeInset,
        }}
        aria-hidden
      />

      {/* ---- Hook bar (top) ---- */}
      {hook.trim() !== "" && (
        <div
          className="absolute left-0 right-0 flex items-center justify-center bg-black/60 px-1 py-0.5"
          style={{ top: safeInset, fontSize: hookFontPx, lineHeight: 1.2 }}
        >
          <span
            className="text-center font-bold text-white"
            style={{
              textShadow: "0 1px 4px rgba(0,0,0,0.9)",
              letterSpacing: "0.01em",
            }}
          >
            {hook.trim()}
          </span>
        </div>
      )}

      {/* ---- Caption box ---- */}
      {captionsOn && (
        <div
          className="pointer-events-none absolute left-0 right-0 flex justify-center px-1"
          style={{ ...captionStyle, paddingLeft: safeInset, paddingRight: safeInset }}
          aria-hidden
        >
          <div
            className="rounded px-1 py-0.5 text-center"
            style={{
              fontSize: captionFontPx,
              lineHeight: 1.25,
              background: "rgba(0,0,0,0.55)",
            }}
          >
            {mode === "karaoke" ? (
              <>
                {/* First word highlighted, rest dimmer */}
                <span
                  className="font-bold"
                  style={{
                    color: "#fff",
                    textShadow: "0 1px 6px rgba(0,0,0,1)",
                  }}
                >
                  {SAMPLE_WORDS[0]}
                </span>
                {SAMPLE_WORDS.length > 1 && (
                  <span
                    style={{
                      color: "rgba(255,255,255,0.55)",
                      textShadow: "0 1px 4px rgba(0,0,0,0.8)",
                    }}
                  >
                    {" "}
                    {SAMPLE_WORDS.slice(1).join(" ")}
                  </span>
                )}
              </>
            ) : (
              <span
                className="font-semibold"
                style={{
                  color: "#fff",
                  textShadow: "0 1px 6px rgba(0,0,0,0.9)",
                }}
              >
                {SAMPLE_CAPTION}
              </span>
            )}
          </div>
        </div>
      )}

      {/* ---- KI disclosure label (bottom-right) ---- */}
      {disclosure && (
        <div
          className="absolute right-1 flex items-center rounded bg-black/50 px-1"
          style={{ bottom: safeInset, fontSize: disclosureFontPx, lineHeight: 1.4 }}
          aria-hidden
        >
          <span className="text-white/70">KI · synthetisch</span>
        </div>
      )}
    </div>
  );
}
