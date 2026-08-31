// PLT-04 — Vite build config (skeleton).
//
// `npm run build` here produces `ui/dist/`, which is NOT yet what
// app/main.py serves — the `/ui` StaticFiles mount still points at this
// same `ui/` directory and serves VAIDYAAI.html (the CDN + in-browser
// Babel version) directly. Swapping the mount to serve `ui/dist/` instead
// is the last step of PLT-04, deliberately not done here: the components
// under src/ are stubs (see src/main.jsx), not a working port of the real
// dashboard/report/image/claim/job screens yet.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    // ui/index.html is the existing marketing landing page (untouched by
    // this build) — the Vite entry lives at index.dev.html instead so the
    // two don't collide.
    rollupOptions: {
      input: "index.dev.html",
    },
  },
  server: {
    proxy: {
      // Same-origin API calls during `npm run dev`, matching how the
      // built app will talk to FastAPI once served together.
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
