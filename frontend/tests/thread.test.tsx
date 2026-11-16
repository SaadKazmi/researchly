import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, test, vi } from "vitest"

import { healthy, renderAt, serve, version } from "./render"
import { frames, sse } from "./sse"

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

const run = {
  id: "r1",
  project_id: "p1",
  thread_id: "t1",
  parent_run_id: null,
  effort: "standard",
  status: "running",
  plan: null,
  budget_usd: "1.00",
  spent_usd: "0.00",
  branch_from_checkpoint: null,
  started_at: null,
  ended_at: null,
}

function routes(messages: unknown[] = []) {
  return {
    "/health": healthy,
    "/api/v1/system": version,
    "GET /api/v1/efforts": () => Response.json(efforts),
    "GET /api/v1/projects/p1/threads": () =>
      Response.json([
        {
          id: "t1",
          project_id: "p1",
          title: "Retrieval",
          created_at: "2026-11-13T10:00:00Z",
        },
      ]),
    "GET /api/v1/threads/t1": () =>
      Response.json({
        id: "t1",
        project_id: "p1",
        title: "Retrieval",
        created_at: "2026-11-13T10:00:00Z",
        messages,
      }),
    "GET /api/v1/runs/r1": () => Response.json(run),
    "POST /api/v1/threads/t1/runs": () => Response.json(run, { status: 201 }),
    "POST /api/v1/runs/r1/stream": () => sse(frames()),
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

test("the stored messages hydrate the thread", async () => {
  serve(
    routes([
      {
        id: "m1",
        role: "user",
        text: "Why does RAG help?",
        run_id: "r0",
        created_at: "2026-11-13T10:00:00Z",
      },
      {
        id: "m2",
        role: "assistant",
        text: "Because it grounds the answer.",
        run_id: "r0",
        created_at: "2026-11-13T10:01:00Z",
      },
    ])
  )
  renderAt("/p/p1/chat/t1")
  expect(await screen.findByText("Why does RAG help?")).toBeInTheDocument()
  expect(
    await screen.findByText("Because it grounds the answer.")
  ).toBeInTheDocument()
})

test("a quick run starts without confirmation and streams its answer", async () => {
  const calls = serve(routes())
  renderAt("/p/p1/chat/t1")
  await userEvent.selectOptions(
    await screen.findByRole("combobox", { name: "Effort" }),
    "quick"
  )
  await userEvent.type(
    screen.getByRole("textbox", { name: "Ask a research question" }),
    "Why does RAG help?"
  )
  await userEvent.click(screen.getByRole("button", { name: "Send" }))
  expect(
    await screen.findByText(/lowers hallucination rates/)
  ).toBeInTheDocument()
  const created = calls.find((call) => call.url.endsWith("/threads/t1/runs"))!
  expect(await created.clone().json()).toEqual({
    message: "Why does RAG help?",
    effort: "quick",
  })
})

test("a tier over the budget threshold asks for confirmation first", async () => {
  const calls = serve(routes())
  renderAt("/p/p1/chat/t1")
  await userEvent.selectOptions(
    await screen.findByRole("combobox", { name: "Effort" }),
    "deep"
  )
  expect(
    screen.getByText("Estimated $1.25–$5.00 · budget $5.00")
  ).toBeInTheDocument()
  await userEvent.type(
    screen.getByRole("textbox", { name: "Ask a research question" }),
    "Why does RAG help?"
  )
  await userEvent.click(screen.getByRole("button", { name: "Send" }))
  expect(
    await screen.findByRole("dialog", { name: "Confirm the budget" })
  ).toBeInTheDocument()
  expect(calls.some((call) => call.url.endsWith("/threads/t1/runs"))).toBe(
    false
  )
  await userEvent.click(screen.getByRole("button", { name: "Start the run" }))
  const created = await vi.waitUntil(() =>
    calls.find((call) => call.url.endsWith("/threads/t1/runs"))
  )
  expect(await created.clone().json()).toEqual({
    message: "Why does RAG help?",
    effort: "deep",
  })
})

test("a thread whose last run is still live re-attaches to its stream", async () => {
  const calls = serve(
    routes([
      {
        id: "m1",
        role: "user",
        text: "Why does RAG help?",
        run_id: "r1",
        created_at: "2026-11-13T10:00:00Z",
      },
    ])
  )
  renderAt("/p/p1/chat/t1")
  expect(
    await screen.findByText(/lowers hallucination rates/)
  ).toBeInTheDocument()
  expect(calls.some((call) => call.url.endsWith("/threads/t1/runs"))).toBe(
    false
  )
  expect(calls.some((call) => call.url.includes("/runs/r1/stream"))).toBe(true)
})

test("the default tier sits exactly on the threshold and still sends without asking", async () => {
  // `standard` has a budget of $1.00 and the default threshold is $1, so this is the boundary the
  // comparison turns on. Relaxing `>` to `>=` would make the default tier - the one almost every run
  // uses - demand a confirmation before it would spend a cent, and no other test touches the case.
  const calls = serve(routes())
  renderAt("/p/p1/chat/t1")
  await screen.findByRole("combobox", { name: "Effort" })
  await userEvent.type(
    screen.getByRole("textbox", { name: "Ask a research question" }),
    "Why does RAG help?"
  )
  await userEvent.click(screen.getByRole("button", { name: "Send" }))
  const created = await vi.waitUntil(() =>
    calls.find((call) => call.url.endsWith("/threads/t1/runs"))
  )
  expect(await created.clone().json()).toEqual({
    message: "Why does RAG help?",
    effort: "standard",
  })
  expect(screen.queryByRole("dialog")).toBeNull()
})

test("a threshold raised in this browser lets a dearer tier through without asking", async () => {
  // the stored threshold is the only thing that makes the confirmation configurable, and nothing
  // else reads it: with the localStorage lookup removed, every run would be judged against $1
  localStorage.setItem("researchly.budget-threshold", "10")
  const calls = serve(routes())
  renderAt("/p/p1/chat/t1")
  await userEvent.selectOptions(
    await screen.findByRole("combobox", { name: "Effort" }),
    "deep"
  )
  await userEvent.type(
    screen.getByRole("textbox", { name: "Ask a research question" }),
    "Why does RAG help?"
  )
  await userEvent.click(screen.getByRole("button", { name: "Send" }))
  const created = await vi.waitUntil(() =>
    calls.find((call) => call.url.endsWith("/threads/t1/runs"))
  )
  expect(await created.clone().json()).toEqual({
    message: "Why does RAG help?",
    effort: "deep",
  })
  expect(screen.queryByRole("dialog")).toBeNull()
})
