import { readFileSync } from "node:fs"

import AxeBuilder from "@axe-core/playwright"

import { expect, test } from "../fixtures"
import { appLog } from "../paths"

test.describe("shell", { tag: "@smoke" }, () => {
  test("renders, reports health and reads the API through the cookie", async ({ page }) => {
    const errors: string[] = []
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text())
    })
    await page.goto("/")
    await expect(page.getByRole("heading", { level: 1, name: "Projects" })).toBeVisible()
    await expect(page.getByRole("status")).toHaveText("Connected")
    await expect(page.getByText(/^v\d+\.\d+\.\d+/)).toBeVisible() // GET /api/v1/system with the cookie
    await expect(page.getByRole("navigation", { name: "Main" })).toMatchAriaSnapshot(`
      - navigation "Main":
        - list:
          - listitem:
            - link "Projects"
          - listitem:
            - link "Memory"
          - listitem:
            - link "Usage"
          - listitem:
            - link "Settings"
    `)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    expect(errors).toEqual([]) // includes CSP violations
  })

  test("deep links load through the SPA fallback", async ({ page }) => {
    await page.goto("/p/demo/library")
    await expect(page.getByRole("heading", { level: 1, name: "Library" })).toBeVisible()
    await page.getByRole("link", { name: "Memory" }).click()
    await expect(page).toHaveURL(/\/memory$/)
  })

  test("a browser without the cookie is told how to sign in", async ({ browser, baseURL }) => {
    const context = await browser.newContext({
      baseURL,
      storageState: { cookies: [], origins: [] },
    })
    const page = await context.newPage()
    await page.goto("/")
    await expect(page.getByRole("alert")).toContainText("researchly start")
    await context.close()
  })

  test("the API rejects requests without the token", async ({ playwright, baseURL }) => {
    const api = await playwright.request.newContext({ baseURL })
    expect((await api.get("/api/v1/system")).status()).toBe(401)
    await api.dispose()
  })

  test("the login token never reaches the app log", async () => {
    const token = new URL(process.env.RESEARCHLY_E2E_LOGIN_URL!).searchParams.get("token")!
    const log = readFileSync(appLog, "utf-8")
    // litellm filters uvicorn.access too, ahead of ours, and drops the whole query rather than the
    // token's value alone - so the login line reads "/?REDACTED". Assert the query was redacted, not
    // which of the two filters shaped it.
    expect(log).toMatch(/"GET \/\?\S*REDACTED\S* HTTP/)
    expect(log).not.toContain(token)
  })
})
