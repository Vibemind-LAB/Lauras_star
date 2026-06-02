import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Vite config for the React renderer.
export default defineConfig({
  plugins: [react()],
});
