/// <reference types="vitest/config" />
import { resolve } from "node:path"
import tailwindcss from "@tailwindcss/vite"
import { tanstackRouter } from "@tanstack/router-plugin/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// Dev: `researchly start`, open its login link once (the cookie is host-wide), then use http://127.0.0.1:5173
const app = `http://127.0.0.1:${process.env.RESEARCHLY_PORT ?? "8765"}`
const api = { target: app, changeOrigin: true, headers: { origin: app } } // the guard rejects a foreign Origin

export default defineConfig({
  plugins: [
    tanstackRouter({ target: "react", autoCodeSplitting: true }), // must come before react()
    react(),
    tailwindcss(),
  ],
  resolve: { alias: { "@": resolve(import.meta.dirname, "./src") } },
  build: { outDir: "../backend/src/researchly/web", emptyOutDir: true }, // served by FastAPI
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: { "/api": api, "/health": api },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
    // Must stay well above the 10s `asyncUtilTimeout` that `tests/setup.ts` sets for assistant-ui's
    // slow first render. At vitest's 5s default a single `findBy` that used its whole window left
    // under a second for the rest of the test, so chat tests failed on the clock under load rather
    // than on the DOM. A ceiling, not a delay: passing tests are no slower for it.
    testTimeout: 20_000,
  },
})
