import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Forward /api/* to the FastAPI backend, dropping the /api prefix.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: process.env.BACKEND_URL || "http://127.0.0.1:8000",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
