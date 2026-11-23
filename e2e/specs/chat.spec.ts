import AxeBuilder from "@axe-core/playwright"
import type { Page } from "@playwright/test"

import { mockRun } from "../agui"
import { expect, test } from "../fixtures"

async function ask(page: Page) {
  await page.goto("/p/p1/chat/t1")
  await page.getByRole("textbox", { name: "Ask a research question" }).fill("Why does RAG help?")
  await page.getByRole("button", { name: "Send", exact: true }).click()
}

test.describe("chat", () => {
  test.beforeEach(async ({ page }) => {
    await mockRun(page)
  })

  test("searches the chat history", { tag: "@F1.1" }, async ({ page }) => {
    await page.goto("/p/p1/chat/t1")
    const threads = page.getByRole("navigation", { name: "Threads" })
    await expect(threads.getByRole("link", { name: "Retrieval augmentation" })).toBeVisible()
    await page.getByRole("searchbox", { name: "Search chat history" }).fill("diffusion")
    await expect(threads.getByText("No threads match your search.")).toBeVisible()
    await expect(threads.getByRole("link")).toHaveCount(0)
  })

  test("streams the run tree while the agent works", { tag: "@F1.5" }, async ({ page }) => {
    await ask(page)
    const tree = page.getByRole("list", { name: "Run activity" })
    await expect(tree.getByText("lead", { exact: true })).toBeVisible()
    await expect(tree.getByText("submit_plan")).toBeVisible()
    await expect(page.getByRole("list", { name: "Plan" }).getByRole("listitem")).toHaveCount(2)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    // each node carries its status (read out to screen readers) and its elapsed time
    const row = (name: string) => tree.getByText(name, { exact: true }).locator("..")
    await expect(row("lead")).toContainText(/running \d+ s/)
    await expect(row("submit_plan")).toContainText(/running \d+ s/) // the plan waits on the review
    await page.getByRole("button", { name: "Approve", exact: true }).click()
    await expect(row("search_papers")).toContainText(/done \d+ s/) // its result arrived
    await expect(row("scout")).toContainText(/done \d+ s/)
    await expect(row("lead")).toContainText(/done \d+ s/) // the run finished
  })

  test(
    "the plan waits for approval, then the run continues",
    { tag: "@F1.3" },
    async ({ page }) => {
      await ask(page)
      const plan = page.getByRole("textbox", { name: "Plan" })
      await expect(plan).toHaveValue(/retrieval augmentation/i)
      const tree = page.getByRole("list", { name: "Run activity" })
      await expect(tree.getByText("lead", { exact: true })).toBeVisible() // the tree is drawn
      await expect(tree.getByText("scout", { exact: true })).toHaveCount(0)
      await page.getByRole("button", { name: "Approve", exact: true }).click()
      await expect(tree.getByText("scout", { exact: true })).toBeVisible()
      await expect(page.getByText(/lowers hallucination rates/)).toBeVisible()
      await expect(plan).toHaveCount(0) // the answered review closes
    }
  )

  test("the effort selector shows the tier's estimate", { tag: "@F1.4" }, async ({ page }) => {
    const runs: string[] = []
    page.on("request", (request) => {
      if (request.method() === "POST" && request.url().endsWith("/threads/t1/runs"))
        runs.push(request.url())
    })
    await page.goto("/p/p1/chat/t1")
    await page.getByRole("combobox", { name: "Effort" }).selectOption("deep")
    await expect(page.getByText("Estimated $1.25–$5.00 · budget $5.00")).toBeVisible()
    await page.getByRole("textbox", { name: "Ask a research question" }).fill("Why does RAG help?")
    await page.getByRole("button", { name: "Send", exact: true }).click()
    await expect(page.getByRole("dialog", { name: "Confirm the budget" })).toBeVisible()
    expect(runs).toEqual([]) // nothing starts before the budget is confirmed
  })

  test(
    "the budget meter warns near the cap and reports the summary",
    { tag: "@F1.7" },
    async ({ page }) => {
      await ask(page)
      await page.getByRole("button", { name: "Approve", exact: true }).click()
      // a polite live region, not role="status": the shell owns the page's one status
      await expect(page.getByText(/wrapping up/)).toBeVisible()
      await expect(page.getByText("$0.84 of $1.00")).toBeVisible()
      await expect(page.getByText(/73% coverage/)).toBeVisible()
    }
  )

  test("a run can be paused and resumed", { tag: "@F1.6" }, async ({ page }) => {
    await ask(page)
    await expect(page.getByRole("textbox", { name: "Plan" })).toBeVisible()
    await page.getByRole("button", { name: "Pause", exact: true }).click()
    await expect(page.getByRole("button", { name: "Resume", exact: true })).toBeVisible()
    await page.getByRole("button", { name: "Resume", exact: true }).click()
    // a plain resume drives up to the same review again; only its answer moves the run on
    await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible()
    await expect(page.getByRole("textbox", { name: "Plan" })).toBeVisible()
    await page.getByRole("button", { name: "Approve", exact: true }).click()
    await expect(page.getByText(/lowers hallucination rates/)).toBeVisible()
  })

  test("a reload re-attaches to a live run", { tag: "@F1.5" }, async ({ page }) => {
    await ask(page)
    await expect(page.getByRole("textbox", { name: "Plan" })).toBeVisible()
    await page.reload() // the mock stored the question on the thread, as the backend does
    await expect(page.getByText("Why does RAG help?")).toBeVisible()
    await expect(page.getByRole("textbox", { name: "Plan" })).toBeVisible() // rebuilt from the replay
  })
})

test.describe("chat with a clarifying question", () => {
  test.beforeEach(async ({ page }) => {
    await mockRun(page, "ask-user.sse")
  })

  test("the agent's question takes a typed answer", { tag: "@F1.2" }, async ({ page }) => {
    await ask(page)
    await expect(page.getByText("Which years should I cover?")).toBeVisible()
    const answer = page.getByRole("textbox", { name: "Answer" })
    await answer.fill("2020 onwards.")
    const resumed = page.waitForRequest("**/api/v1/runs/r1/resume")
    await page.getByRole("button", { name: "Send answer" }).click()
    expect((await resumed).postDataJSON()).toEqual({
      decisions: [{ type: "respond", message: "2020 onwards." }],
    })
    await expect(page.getByText(/lowered hallucination rates/)).toBeVisible()
    await expect(answer).toHaveCount(0) // the answered question closes
  })
})
