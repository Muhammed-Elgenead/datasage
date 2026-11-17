import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  // .env lives at the repo root; BACKEND_URL is read for the dev proxy only.
  const env = loadEnv(mode, "..", "");
  const backendUrl = env.BACKEND_URL ?? "http://localhost:8000";

  return {
    plugins: [react(), tailwindcss()],
    envDir: "..",
    server: {
      host: true,
      port: 5173,
      proxy: {
        // Production routing is decided in Phase 7.
        "/api": {
          target: backendUrl,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ""),
        },
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
    },
  };
});
