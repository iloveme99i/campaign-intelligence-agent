import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const sourceDirectory = decodeURIComponent(new URL("./src", import.meta.url).pathname);

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": sourceDirectory,
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8101",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "node",
    globals: true,
  },
});
