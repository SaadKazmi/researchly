import { readFileSync } from "node:fs"
import { resolve } from "node:path"

import type { Page, Route } from "@playwright/test"

import { repo } from "./paths"

type Frame = { seq: number; data: string }

function frames(): Frame[] {
  const text = readFileSync(resolve(repo, "e2e/fixtures/agui/standard-run.sse"), "utf-8")
  return text
    .split("\n\n")
    .filter((block) => block.trim())
    .map((block) => ({
      seq: Number(/^id: (\d+)$/m.exec(block)![1]),
      data: /^data: (.*)$/m.exec(block)![1]!,
    }))
}

const project = { id: "p1", name: "Hallucination", created_at: "2026-11-13T10:00:00Z" }
const thread = {
  id: "t1",
  project_id: "p1",
  title: "Retrieval augmentation",
  created_at: "2026-11-13T10:00:00Z",
}
const efforts = [
  {
    name: "quick",
    scouts: 0,
    readers: 0,
    tool_calls: 10,
    budget_usd: "0.10",
    estimate_low_usd: "0.03",
    estimate_high_usd: "0.10",
  },
  {
    name: "standard",
    scouts: 4,
    readers: 4,
    tool_calls: 25,
    budget_usd: "1.00",
    estimate_low_usd: "0.25",
    estimate_high_usd: "1.00",
  },
  {
    name: "deep",
    scouts: 8,
    readers: 12,
    tool_calls: 40,
    budget_usd: "5.00",
    estimate_low_usd: "1.25",
    estimate_high_usd: "5.00",
  },
]

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) })

/** Serves the project, thread, efforts and run routes from the frozen transcript: no LLM key, no network. */
export async function mockRun(page: Page): Promise<void> {
  const all = frames()
  // the run stops at the plan interrupt until a resume answers it, exactly as the backend does
  const interrupt = all.findIndex((frame) => frame.data.includes('"interrupt"')) + 1
  let until = interrupt
  let status = "running"
  // the question the composer sends is stored on the thread, so a reload re-attaches to its run
  const messages: { id: string; role: string; text: string; run_id: string; created_at: string }[] =
    []
  const run = () => ({
    id: "r1",
    project_id: "p1",
    thread_id: "t1",
    parent_run_id: null,
    effort: "standard",
    status,
    plan: null,
    budget_usd: "1.00",
    spent_usd: "0.84",
    branch_from_checkpoint: null,
    started_at: "2026-11-13T10:00:00Z",
    ended_at: null,
  })

  await page.route("**/api/v1/projects", (route) => json(route, [project]))
  await page.route("**/api/v1/projects/p1/threads*", (route) => {
    const q = new URL(route.request().url()).searchParams.get("q")
    const match = !q || thread.title.toLowerCase().includes(q.toLowerCase())
    return route.request().method() === "POST"
      ? json(route, thread, 201)
      : json(route, match ? [thread] : [])
  })
  await page.route("**/api/v1/threads/t1", (route) => json(route, { ...thread, messages }))
  await page.route("**/api/v1/efforts", (route) => json(route, efforts))
  await page.route("**/api/v1/threads/t1/runs", (route) => {
    const { message } = route.request().postDataJSON() as { message: string }
    messages.push({
      id: "m1",
      role: "user",
      text: message,
      run_id: "r1",
      created_at: "2026-11-13T10:00:00Z",
    })
    return json(route, run(), 201)
  })
  await page.route("**/api/v1/runs/r1", (route) => json(route, run()))
  await page.route("**/api/v1/runs/r1/resume", (route) => {
    // only an answer to the review moves past it; a plain resume after a pause drives up to it again
    if (route.request().postDataJSON()?.decisions) until = all.length
    status = "running"
    return json(route, run())
  })
  await page.route("**/api/v1/runs/r1/pause", (route) => {
    status = "paused"
    return json(route, run())
  })
  await page.route("**/api/v1/runs/r1/cancel", (route) => {
    status = "cancelled"
    return json(route, run())
  })
  // every re-attach streams from after=0, so each call serves every frame up to `until`
  await page.route("**/api/v1/runs/r1/stream*", (route) => {
    const after = Number(new URL(route.request().url()).searchParams.get("after") ?? 0)
    const body = all
      .slice(0, until)
      .filter((frame) => frame.seq > after)
      .map((frame) => `id: ${frame.seq}\ndata: ${frame.data}\n\n`)
      .join("")
    return route.fulfill({ status: 200, contentType: "text/event-stream", body })
  })
}
