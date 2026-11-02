import { test as base } from "@playwright/test"

// globalSetup sets the URL after the config is loaded, so baseURL comes from a fixture
export const test = base.extend({
  baseURL: async ({}, use) => {
    await use(process.env.RESEARCHLY_E2E_URL)
  },
})

export { expect } from "@playwright/test"
