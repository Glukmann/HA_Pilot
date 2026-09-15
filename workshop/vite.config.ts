import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Relative base so the built bundle works when served from any sub-path
// (the add-on serves it through the Supervisor ingress, task 10).
export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Dev: the add-on already listens on localhost:8899; proxy REST + WS.
      "/ws": {
        target: "http://localhost:8899",
        ws: true,
      },
    },
  },
});
