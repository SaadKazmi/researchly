import { readFileSync } from "node:fs"
import { resolve } from "node:path"

import type { Page, Route } from "@playwright/test"

import { repo } from "./paths"

type Frame = { seq: number; data: string }

function frames(transcript: string): Frame[] {
  const text = readFileSync(resolve(repo, "e2e/fixtures/agui", transcript), "utf-8")
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

const TERMINAL = ["completed", "partial", "cancelled", "failed"]
const isLive = (status: string) => !TERMINAL.includes(status)

/**
 * Serves the project, thread, efforts and run routes from the frozen transcript: no LLM key, no network.
 *
 * `holdAfter` names a frame of the segment after the review: an answered review's drive stops there,
 * still running, until a pause and a plain resume release it (a held stream simply ends early).
 */
export async function mockRun(
  page: Page,
  transcript = "standard-run.sse",
  { holdAfter }: { holdAfter?: string } = {}
): Promise<void> {
  const all = frames(transcript)
  // the run stops at the transcript's interrupt until a resume answers it, exactly as the backend does
  const interrupt = all.findIndex((frame) => frame.data.includes('"interrupt"')) + 1
  const held = holdAfter
    ? all.findIndex((frame, i) => i >= interrupt && frame.data.includes(holdAfter)) + 1
    : all.length
  let until = interrupt
  let status = "running"
  // a branch is the child run r2, which replays the segment after the review from its checkpoint
  let branch: { status: string; checkpoint: string } | null = null
  // the question the composer sends is stored on the thread, so a reload re-attaches to its run
  const messages: { id: string; role: string; text: string; run_id: string; created_at: string }[] =
    []
  const run = (id = "r1") => ({
    id,
    project_id: "p1",
    thread_id: "t1",
    parent_run_id: id === "r1" ? null : "r1",
    effort: "standard",
    status: id === "r1" ? status : branch!.status,
    plan: null,
    budget_usd: "1.00",
    // nothing spent on the row: the meter must read the spend from the stream's cost events
    spent_usd: "0.00",
    branch_from_checkpoint: id === "r1" ? null : branch!.checkpoint,
    started_at: "2026-11-13T10:00:00Z",
    ended_at: null,
  })
  const sse = (route: Route, served: Frame[]) => {
    const after = Number(new URL(route.request().url()).searchParams.get("after") ?? 0)
    const body = served
      .filter((frame) => frame.seq > after)
      .map((frame) => `id: ${frame.seq}\ndata: ${frame.data}\n\n`)
      .join("")
    return route.fulfill({ status: 200, contentType: "text/event-stream", body })
  }

  await page.route("**/api/v1/projects", (route) => json(route, [project]))
  await page.route("**/api/v1/projects/p1/threads*", (route) => {
    const q = new URL(route.request().url()).searchParams.get("q")
    const match = !q || thread.title.toLowerCase().includes(q.toLowerCase())
    return route.request().method() === "POST"
      ? json(route, thread, 201)
      : json(route, match ? [thread] : [])
  })
  // the backend names the thread's newest run while it is live: a reload follows it
  const liveRun = () =>
    branch
      ? isLive(branch.status)
        ? "r2"
        : null
      : messages.length > 0 && isLive(status)
        ? "r1"
        : null
  await page.route("**/api/v1/threads/t1", (route) =>
    json(route, { ...thread, messages, live_run_id: liveRun() })
  )
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
    // only an answer moves past the review; a plain resume after a pause releases a held drive
    if (route.request().postDataJSON()?.decisions) until = held
    else if (until > interrupt) until = all.length
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
  await page.route("**/api/v1/runs/r1/checkpoints", (route) =>
    json(route, [
      { checkpoint_id: "cp-plan", created_at: "2026-11-13T10:00:05Z", step: 4 },
      { checkpoint_id: "cp-start", created_at: "2026-11-13T10:00:00Z", step: 1 },
    ])
  )
  await page.route("**/api/v1/runs/r1/branch", (route) => {
    const { checkpoint_id } = route.request().postDataJSON() as { checkpoint_id: string }
    branch = { status: "running", checkpoint: checkpoint_id }
    return json(route, run("r2"), 201)
  })
  await page.route("**/api/v1/runs/r2", (route) => json(route, run("r2")))
  // every re-attach streams from after=0, so each call serves every frame up to `until`
  await page.route("**/api/v1/runs/r1/stream*", (route) => {
    const served = all.slice(0, until)
    // the run's status follows where its stream stands, unless a pause or a cancel stopped it
    if (status === "running" || status === "awaiting_user")
      status =
        until === interrupt ? "awaiting_user" : until === all.length ? "completed" : "running"
    return sse(route, served)
  })
  await page.route("**/api/v1/runs/r2/stream*", (route) => {
    branch!.status = "completed"
    return sse(route, all.slice(interrupt))
  })
}
