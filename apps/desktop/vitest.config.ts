import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Renderer unit tests (Portion 16.5). jsdom + Testing Library; bundler-side logic
// only — Electron/IPC paths are integration-tested manually.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // Headroom above the 5s waitFor deadline (test-setup) so the per-test timeout never fires
    // first and masks the real waitFor diagnostic; passing tests finish well under this.
    testTimeout: 15000,
  },
});
