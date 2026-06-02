import { defineConfig } from "vite";

// Vite config for the preload script.
export default defineConfig({
  build: {
    rollupOptions: {
      external: ["electron"],
    },
  },
});
