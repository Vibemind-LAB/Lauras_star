import { defineConfig } from "vite";

// Vite config for the Electron main process.
export default defineConfig({
  resolve: {
    // Prefer Node/CJS resolution for the main process.
    mainFields: ["module", "jsnext:main", "jsnext"],
  },
  build: {
    rollupOptions: {
      external: ["electron"],
    },
  },
});
