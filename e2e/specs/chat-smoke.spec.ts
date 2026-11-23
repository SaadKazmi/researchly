import { expect, test } from "../fixtures"

// The Quick Lead is told to search first; the app has no HTTP replay, so the question asks it not to,
// and the committed fixture holds model calls only (see AGENTS.md, E2E chat layers)
const question = "Without searching, define retrieval-augmented generation in one sentence."

test.describe("chat end to end", { tag: "@smoke" }, () => {
  test("a Quick run answers in the thread and survives a reload", async ({ page }) => {
    test.setTimeout(240_000) // replay answers in seconds; a recording waits on the live model
    const name = `Smoke ${Date.now()}` // the E2E data dir outlives a run
    await page.goto("/")
    await page.getByRole("textbox", { name: "Project name" }).fill(name)
    await page.getByRole("button", { name: "Create project" }).click()
    await page.getByRole("link", { name }).click()

    await page.getByRole("combobox", { name: "Effort" }).selectOption("quick")
    await page.getByRole("textbox", { name: "Ask a research question" }).fill(question)
    await page.getByRole("button", { name: "Send", exact: true }).click()

    // the first run in a fresh data dir downloads the embedding models before it starts
    await expect(page.getByText(/^completed · /)).toBeVisible({ timeout: 180_000 })
    // the answer streamed in live; scoped to the conversation, where the question is the only other text
    const conversation = page.getByRole("region", { name: "Conversation" })
    const answer = conversation.getByText(/retriev/i).filter({ hasNotText: question })
    await expect(answer.first()).toBeVisible()

    await page.reload() // the question and the stored answer come back from the thread
    await expect(page.getByText(question)).toBeVisible()
    await expect(answer.first()).toBeVisible()
  })
})
