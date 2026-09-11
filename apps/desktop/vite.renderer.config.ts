import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Vite config for the React renderer.
export default defineConfig({
  base: "./",
  plugins: [react()],
  // 5173 is frequently squatted by Docker Desktop (com.docker.backend) on this machine,
  // which makes Vite fail to bind with EACCES; use the next port, incrementing if busy.
  server: { port: 5174, strictPort: false },
});
