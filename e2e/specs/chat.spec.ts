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
    await expect(tree.getByText("lead")).toBeVisible()
    await expect(tree.getByText("submit_plan")).toBeVisible()
    await expect(page.getByRole("list", { name: "Plan" }).getByRole("listitem")).toHaveCount(2)
    // TODO: the chat route has no level-one heading (every other page has one); drop this disable once it does
    const axe = new AxeBuilder({ page }).disableRules(["page-has-heading-one"])
    expect((await axe.analyze()).violations).toEqual([])
  })

  test(
    "the plan waits for approval, then the run continues",
    { tag: "@F1.3" },
    async ({ page }) => {
      await ask(page)
      const plan = page.getByRole("textbox", { name: "Plan" })
      await expect(plan).toHaveValue(/retrieval augmentation/i)
      const tree = page.getByRole("list", { name: "Run activity" })
      await expect(tree.getByText("scout")).toHaveCount(0)
      await page.getByRole("button", { name: "Approve", exact: true }).click()
      await expect(tree.getByText("scout")).toBeVisible()
      await expect(page.getByText(/lowers hallucination rates/)).toBeVisible()
      await expect(plan).toHaveCount(0) // the answered review closes
    }
  )

  test("the effort selector shows the tier's estimate", { tag: "@F1.4" }, async ({ page }) => {
    await page.goto("/p/p1/chat/t1")
    await page.getByRole("combobox", { name: "Effort" }).selectOption("deep")
    await expect(page.getByText("Estimated $1.25–$5.00 · budget $5.00")).toBeVisible()
    await page.getByRole("textbox", { name: "Ask a research question" }).fill("Why does RAG help?")
    await page.getByRole("button", { name: "Send", exact: true }).click()
    await expect(page.getByRole("dialog", { name: "Confirm the budget" })).toBeVisible()
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
    await page.getByRole("button", { name: "Pause" }).click()
    await expect(page.getByRole("button", { name: "Resume" })).toBeVisible()
    await page.getByRole("button", { name: "Resume" }).click()
    // a plain resume drives up to the same review again; only its answer moves the run on
    await expect(page.getByRole("button", { name: "Pause" })).toBeVisible()
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
