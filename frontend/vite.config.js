import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The backend serves every API route at the root (no /api prefix), so the dev
// server forwards those paths unchanged.
const target = process.env.BACKEND_URL || "http://127.0.0.1:8000";
const paths = ["/auth", "/health", "/stats", "/chats", "/messages", "/ocr", "/rules", "/notifications", "/members", "/groups", "/events", "/categories", "/dashboard", "/absences", "/evidence", "/exports"];

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: Object.fromEntries(paths.map((p) => [p, { target }])),
  },
});
