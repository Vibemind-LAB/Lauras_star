/**
 * lint-tokens.mjs — grep-lint for raw dark/emerald Tailwind classes.
 *
 * Scans src/**\/*.tsx for token violations that should have been migrated to
 * the P5-T1 semantic token system. Exits 1 with file:line detail on any match.
 *
 * Allow-listed files (where raw values legitimately live):
 *   - src/theme/**        (contrast test uses raw hex values)
 *   - CaptionPreview.tsx  (deliberate dark video-preview simulation — kept dark)
 *
 * Usage: node scripts/lint-tokens.mjs
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const __dir = fileURLToPath(new URL(".", import.meta.url));
const srcRoot = join(__dir, "..", "src");

/** Patterns that are forbidden in component code after the P5-T2 sweep. */
const BANNED_PATTERNS = [
  // Legacy dark background tokens
  /\bbg-ink\b/,
  /\bbg-panel\b/,
  // Legacy border token
  /\bborder-edge\b/,
  // Raw slate backgrounds
  /\bbg-slate-[0-9]+\b/,
  // Raw slate text
  /\btext-slate-[0-9]+\b/,
  // Raw slate borders
  /\bborder-slate-[0-9]+\b/,
  // Raw emerald fills (should be accent or status-ok)
  /\bbg-emerald-[0-9]+\b/,
  // Raw emerald text (should be text-accent or text-status-ok)
  /\btext-emerald-[0-9]+\b/,
  // Raw emerald borders (should be border-accent)
  /\bborder-emerald-[0-9]+\b/,
  // Literal hex greens from the old dark palette
  /#15803[dD]/,
  /#16[aA]34[aA]/,
  /#16653[4]/,
];

/** Files or directories to skip (relative to srcRoot). */
const ALLOW_LIST = new Set([
  // Theme test uses raw color values
  "theme",
]);

/** Specific filenames to skip (basename match). */
const ALLOW_LIST_FILES = new Set([
  // Intentionally dark — simulates a phone screen with video content
  "CaptionPreview.tsx",
]);

/**
 * Recursively collect all .tsx files under `dir`.
 * @param {string} dir
 * @returns {string[]}
 */
function collectTsx(dir) {
  /** @type {string[]} */
  const results = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const rel = relative(srcRoot, full);
    const stat = statSync(full);
    if (stat.isDirectory()) {
      if (!ALLOW_LIST.has(entry)) {
        results.push(...collectTsx(full));
      }
    } else if (
      entry.endsWith(".tsx") &&
      !ALLOW_LIST_FILES.has(entry)
    ) {
      results.push(full);
    }
  }
  return results;
}

const files = collectTsx(srcRoot);

/** @type {{ file: string; line: number; col: number; text: string; pattern: string }[]} */
const violations = [];

for (const file of files) {
  const content = readFileSync(file, "utf8");
  const lines = content.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    for (const pattern of BANNED_PATTERNS) {
      if (pattern.test(line)) {
        violations.push({
          file: relative(srcRoot, file).replace(/\\/g, "/"),
          line: i + 1,
          col: line.search(pattern) + 1,
          text: line.trim(),
          pattern: pattern.toString(),
        });
        break; // one violation per line is enough
      }
    }
  }
}

if (violations.length === 0) {
  process.stdout.write("lint-tokens: OK — no raw dark/emerald classes found.\n");
  process.exit(0);
} else {
  process.stderr.write(
    `lint-tokens: FAIL — ${violations.length} violation(s) found:\n\n`,
  );
  for (const v of violations) {
    process.stderr.write(`  ${v.file}:${v.line}:${v.col}  ${v.text}\n`);
  }
  process.stderr.write(
    "\nMigrate these to semantic tokens (surface-*, accent, status-*, content-*, bezel).\n",
  );
  process.exit(1);
}
