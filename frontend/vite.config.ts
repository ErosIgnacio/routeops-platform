import { loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const backend = env.BACKEND_PROXY_TARGET || "http://localhost:8000";
  return {
    plugins: [react()],
    optimizeDeps: {
      exclude: ["maplibre-gl"],
    },
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        "/api": {
          target: backend,
          changeOrigin: true,
        },
        "/health": {
          target: backend,
          changeOrigin: true,
        },
      },
    },
    test: {
      environment: "jsdom",
    },
  };
});
