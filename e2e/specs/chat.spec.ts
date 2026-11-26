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
    // each node carries its status (read out to screen readers) and its elapsed time; an agent's row
    // also holds its tools' rows, so the text is anchored at the node's own name
    const node = (name: string, status: string) =>
      expect(tree.getByRole("listitem").filter({ hasText: new RegExp(`^${name} `) })).toContainText(
        new RegExp(`^${name} ${status} \\d+ s`)
      )
    await node("lead", "running")
    await node("submit_plan", "running") // the plan waits on the review
    await page.getByRole("button", { name: "Approve", exact: true }).click()
    await node("search_papers", "done") // its result arrived
    await node("scout", "done")
    await node("lead", "done") // the run finished
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

  test("a reload re-attaches to a live run", { tag: "@F1.5" }, async ({ page }) => {
    await ask(page)
    await expect(page.getByRole("textbox", { name: "Plan" })).toBeVisible()
    await page.reload() // the mock stored the question on the thread, as the backend does
    await expect(page.getByText("Why does RAG help?")).toBeVisible()
    await expect(page.getByRole("textbox", { name: "Plan" })).toBeVisible() // rebuilt from the replay
  })
})

test.describe("chat while the agent is still working", () => {
  test("a run can be paused and resumed", { tag: "@F1.6" }, async ({ page }) => {
    await mockRun(page, "standard-run.sse", { holdAfter: "SUBAGENT_STARTED" })
    await ask(page)
    await page.getByRole("button", { name: "Approve", exact: true }).click()
    const tree = page.getByRole("list", { name: "Run activity" })
    const scout = tree.getByRole("listitem").filter({ hasText: /^scout / })
    await expect(scout).toContainText(/^scout running/) // the drive is mid-segment, past the review
    await expect(page.getByText("The run failed.")).toHaveCount(0)
    await page.getByRole("button", { name: "Pause", exact: true }).click()
    await expect(page.getByRole("button", { name: "Resume", exact: true })).toBeVisible()
    await expect(page.getByText(/lowers hallucination rates/)).toHaveCount(0)
    await page.getByRole("button", { name: "Resume", exact: true }).click()
    await expect(page.getByText(/lowers hallucination rates/)).toBeVisible()
    await expect(scout).toContainText(/^scout done/)
    await expect(page.getByRole("button", { name: "Pause", exact: true })).toHaveCount(0) // it finished
  })

  test(
    "the budget meter warns near the cap and reports the summary",
    { tag: "@F1.7" },
    async ({ page }) => {
      await mockRun(page, "standard-run.sse", { holdAfter: '"spent_usd": 0.84' })
      await ask(page)
      await page.getByRole("button", { name: "Approve", exact: true }).click()
      // a polite live region, not role="status": the shell owns the page's one status
      await expect(page.getByText(/wrapping up/)).toBeVisible()
      await expect(page.getByText("$0.84 of $1.00")).toBeVisible()
      await page.getByRole("button", { name: "Pause", exact: true }).click()
      await page.getByRole("button", { name: "Resume", exact: true }).click()
      // the summary carries the run's final figures, and a finished run has nothing left to wrap up
      await expect(page.getByText(/73% coverage/)).toBeVisible()
      await expect(page.getByText("$0.86 of $1.00")).toBeVisible()
      await expect(page.getByText("43,940 tokens")).toBeVisible()
      await expect(page.getByText(/wrapping up/)).toHaveCount(0)
    }
  )
})

test.describe("chat controls", () => {
  test.beforeEach(async ({ page }) => {
    await mockRun(page)
  })

  test("a run can be cancelled at its review", { tag: "@F1.6" }, async ({ page }) => {
    await ask(page)
    await expect(page.getByRole("textbox", { name: "Plan" })).toBeVisible()
    await expect(page.getByRole("button", { name: "Pause", exact: true })).toHaveCount(0) // it waits on us
    const cancelled = page.waitForRequest("**/api/v1/runs/r1/cancel")
    await page.getByRole("button", { name: "Cancel the run" }).click()
    await cancelled
    await expect(page.getByRole("textbox", { name: "Plan" })).toHaveCount(0) // a cancelled run closes it
    await expect(page.getByRole("button", { name: "Cancel the run" })).toHaveCount(0)
    await expect(page.getByRole("button", { name: "Branch", exact: true })).toBeVisible()
  })

  test("a finished run branches from a checkpoint", { tag: "@F1.6" }, async ({ page }) => {
    await ask(page)
    await page.getByRole("button", { name: "Approve", exact: true }).click()
    await expect(page.getByText(/73% coverage/)).toBeVisible()
    await page.getByRole("button", { name: "Branch", exact: true }).click()
    await page.getByRole("combobox", { name: "Checkpoint" }).selectOption("cp-start")
    const branched = page.waitForRequest("**/api/v1/runs/r1/branch")
    const followed = page.waitForRequest("**/api/v1/runs/r2/stream*")
    await page.getByRole("button", { name: "Branch from here" }).click()
    expect((await branched).postDataJSON()).toEqual({ checkpoint_id: "cp-start" })
    await followed // the panel follows the child run
    await expect(
      page.getByRole("list", { name: "Run activity" }).getByText("scout", { exact: true })
    ).toBeVisible()
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
