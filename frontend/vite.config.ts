import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Node, not jsdom, and deliberately: what is tested here are the rules a record
  // goes through — what counts as empty, how a value serializes, which columns may
  // be written. None of it renders. A DOM in the config is an invitation to write
  // component tests, and PrimeReact under jsdom is a maintenance cost of its own.
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
  server: {
    port: 5173,
    proxy: {
      // Proxy API calls to the FastAPI backend during development.
      "/api": {
        target: "http://localhost:5000",
        changeOrigin: true,
      },
    },
  },
});
