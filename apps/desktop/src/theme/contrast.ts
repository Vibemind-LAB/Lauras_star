/**
 * WCAG 2.1 contrast-ratio helpers.
 *
 * Used exclusively by contrast.test.ts — no runtime/production import.
 * Pure arithmetic, no DOM, no side effects.
 */

/** Parse a six-digit CSS hex colour (#RRGGBB) into [r, g, b] in [0, 255]. */
function parseHex(hex: string): [number, number, number] {
  const clean = hex.startsWith("#") ? hex.slice(1) : hex;
  if (clean.length !== 6) throw new Error(`Invalid hex colour: ${hex}`);
  return [
    parseInt(clean.slice(0, 2), 16),
    parseInt(clean.slice(2, 4), 16),
    parseInt(clean.slice(4, 6), 16),
  ];
}

/** Convert an 8-bit sRGB channel value to linear light. */
function toLinear(channel8: number): number {
  const c = channel8 / 255;
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

/** Relative luminance of a hex colour per WCAG 2.1 § 1.4.3. */
export function relativeLuminance(hex: string): number {
  const [r8, g8, b8] = parseHex(hex);
  const r = toLinear(r8);
  const g = toLinear(g8);
  const b = toLinear(b8);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/**
 * WCAG contrast ratio between two hex colours.
 * Returns a value ≥ 1 (1 = identical, 21 = black on white).
 */
export function contrastRatio(hex1: string, hex2: string): number {
  const l1 = relativeLuminance(hex1);
  const l2 = relativeLuminance(hex2);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}
