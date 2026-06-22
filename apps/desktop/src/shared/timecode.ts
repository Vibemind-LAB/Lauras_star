/**
 * Shared timecode / display-time helpers.
 *
 * All arithmetic is integer-only (no floating-point state). Drop-frame
 * display is deliberately NOT supported here — this helper is for the
 * mm:ss human-readable label shown next to frame-number inputs, not for
 * professional timecode output (that lives in ExportView / backend).
 */

/**
 * Convert a frame count to a `mm:ss` string using integer arithmetic.
 *
 * @param frames   Non-negative integer frame index / count.
 * @param rateNum  Numerator of the frame-rate fraction (e.g. 30000 for 29.97).
 * @param rateDen  Denominator of the frame-rate fraction (e.g. 1001 for 29.97).
 * @returns        A string like "1:23" or "12:04". Falls back to "0:00" on
 *                 invalid rate.
 */
export function framesToTimecode(frames: number, rateNum: number, rateDen: number): string {
  if (rateNum <= 0 || rateDen <= 0 || frames < 0) return "0:00";
  const totalSeconds = Math.floor((frames * rateDen) / rateNum);
  const mm = Math.floor(totalSeconds / 60);
  const ss = totalSeconds % 60;
  return `${mm}:${ss.toString().padStart(2, "0")}`;
}
