import { defineConfig, devices } from "@playwright/test"

import { authFile } from "./paths"

export default defineConfig({
  testDir: "./specs",
  outputDir: "./test-results",
  globalSetup: "./global-setup.ts",
  forbidOnly: !!process.env.CI,
  workers: 1, // one shared app instance
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: { trace: "retain-on-failure" },
  projects: [
    { name: "setup", testMatch: /.*\.setup\.ts/ },
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], storageState: authFile },
      dependencies: ["setup"],
    },
  ],
})
