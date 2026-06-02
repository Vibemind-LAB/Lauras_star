import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Renderer unit tests (Portion 16.5). jsdom + Testing Library; bundler-side logic
// only — Electron/IPC paths are integration-tested manually.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
