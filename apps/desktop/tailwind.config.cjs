/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        /* ── Existing dark tokens — do NOT remove until P5-T2 ── */
        ink: "#0b0f14",
        panel: "#121822",
        edge: "#1f2a37",

        /* ── P5-T1: Light/Green token system (additive) ── */
        surface: {
          0:   "var(--surface-0)",
          1:   "var(--surface-1)",
          "1.5": "var(--surface-1-5)",
          2:   "var(--surface-2)",
        },
        accent: {
          DEFAULT: "var(--accent)",
          glow:    "var(--accent-glow)",
          press:   "var(--accent-press)",
          ink:     "var(--accent-ink)",
        },
        status: {
          ok:   "var(--status-ok)",
          warn: "var(--status-warn)",
          err:  "var(--status-err)",
        },
        content: {
          strong: "var(--text-strong)",
          muted:  "var(--text-muted)",
          faint:  "var(--text-faint)",
        },
        bezel:    "var(--bezel)",
        playhead: "var(--playhead)",
      },

      boxShadow: {
        glow: "0 0 0 1px var(--accent), 0 0 16px -2px var(--accent-glow)",
      },

      keyframes: {
        /* Computing/loading border sweep */
        shimmer: {
          "0%":   { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        /* 180 ms opacity fill — signals "ready" */
        "fill-in": {
          "0%":   { opacity: "0" },
          "100%": { opacity: "1" },
        },
        /* 1.6 s exhale glow loop — idle/listening state */
        breathe: {
          "0%, 100%": { boxShadow: "0 0 0 1px var(--accent)" },
          "50%":      { boxShadow: "0 0 0 1px var(--accent), 0 0 16px -2px var(--accent-glow)" },
        },
        /* Horizontal sweep — progress or reveal */
        sweep: {
          "0%":   { transform: "scaleX(0)", transformOrigin: "left" },
          "100%": { transform: "scaleX(1)", transformOrigin: "left" },
        },
        /* Snappy latch — confirm / lock-in action */
        "latch-snap": {
          "0%":   { transform: "scale(1)" },
          "40%":  { transform: "scale(0.92)" },
          "70%":  { transform: "scale(1.06)" },
          "100%": { transform: "scale(1)" },
        },
      },

      animation: {
        shimmer:     "shimmer 2s linear infinite",
        "fill-in":   "fill-in 180ms ease-out both",
        breathe:     "breathe 1.6s ease-in-out infinite",
        sweep:       "sweep 400ms ease-out both",
        "latch-snap": "latch-snap 300ms cubic-bezier(0.34,1.56,0.64,1) both",
      },
    },
  },
  plugins: [],
};
