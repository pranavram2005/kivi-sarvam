import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API runs on 8000; the dev server proxies /api so the frontend never
// needs to know the backend's address and CORS stays out of the way.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  // `vite preview` serves the production build; it needs the proxy declared
  // separately so a reviewer checking `npm run build` output still reaches the
  // API.
  preview: {
    port: 4173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
