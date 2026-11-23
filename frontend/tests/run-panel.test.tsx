import type { HttpAgent } from "@ag-ui/client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, test, vi } from "vitest"

import "@/i18n"
import { RunStateProvider } from "@/features/chat/run-state"
import { RunPanel } from "@/features/chat/run-panel"

import { serve } from "./render"
import { frames, untilInterrupt } from "./sse"

const run = {
  id: "r1",
  project_id: "p1",
  thread_id: "t1",
  parent_run_id: null,
  effort: "standard",
  status: "running",
  plan: null,
  budget_usd: "1.00",
  spent_usd: "0.84",
  branch_from_checkpoint: null,
  started_at: "2026-11-13T10:00:00Z",
  ended_at: null,
}

const checkpoints = [
  { checkpoint_id: "ck-2", created_at: "2026-11-13T10:02:00Z", step: 2 },
  { checkpoint_id: "ck-1", created_at: "2026-11-13T10:01:00Z", step: 1 },
]

/** A stand-in for the agent: the panel only ever reads what the subscriber was handed. */
function fakeAgent() {
  const subscribers: { onEvent?: (payload: { event: unknown }) => void }[] = []
  const agent = {
    subscribe(subscriber: { onEvent?: (payload: { event: unknown }) => void }) {
      subscribers.push(subscriber)
      return {
        unsubscribe: () =>
          subscribers.splice(subscribers.indexOf(subscriber), 1),
      }
    },
  } as unknown as HttpAgent
  const play = async (list: { data: string }[]) => {
    await act(async () => {
      for (const frame of list)
        for (const s of subscribers)
          s.onEvent?.({ event: JSON.parse(frame.data) })
    })
  }
  return { agent, play }
}

function mount(agent: HttpAgent, onReattach = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <RunStateProvider agent={agent}>
        <RunPanel runId="r1" onReattach={onReattach} />
      </RunStateProvider>
    </QueryClientProvider>
  )
  return onReattach
}

afterEach(() => vi.unstubAllGlobals())

test("the tree shows the lead, its subagent and their tool calls, and the todos", async () => {
  serve({ "GET /api/v1/runs/r1": () => Response.json(run) })
  const { agent, play } = fakeAgent()
  mount(agent)
  await play(frames())
  const tree = screen.getByRole("list", { name: "Run activity" })
  expect(within(tree).getByText("lead")).toBeInTheDocument()
  expect(within(tree).getByText("scout")).toBeInTheDocument()
  expect(within(tree).getByText("submit_plan")).toBeInTheDocument()
  expect(within(tree).getByText("search_papers")).toBeInTheDocument()
  const todos = screen.getByRole("list", { name: "Plan" })
  expect(within(todos).getAllByRole("listitem")).toHaveLength(2)
})

test("the meter tracks the spend and warns at 80% of the budget", async () => {
  serve({ "GET /api/v1/runs/r1": () => Response.json(run) })
  const { agent, play } = fakeAgent()
  mount(agent)
  await play(untilInterrupt())
  expect(screen.getByRole("progressbar", { name: "Budget" })).toHaveValue(0.012)
  // the shell owns the one role="status": the notice is a plain polite live region, found by text
  expect(screen.queryByText(/wrapping up/)).toBeNull()
  // found by what it carries, not by its position: the transcript gains frames as the run
  // grows, and a sequence number that silently points at a different frame still passes
  await play(
    frames().filter((frame) => frame.data.includes('"spent_usd": 0.84'))
  )
  expect(screen.getByText(/wrapping up/)).toBeInTheDocument()
  expect(screen.getByText("$0.84 of $1.00")).toBeInTheDocument()
})

test("the end-of-run summary reports the status, the spend and the coverage", async () => {
  serve({
    "GET /api/v1/runs/r1": () =>
      Response.json({ ...run, status: "completed", spent_usd: "0.86" }),
  })
  const { agent, play } = fakeAgent()
  mount(agent)
  await play(frames())
  expect(
    await screen.findByText("completed · $0.86 spent · 73% coverage")
  ).toBeInTheDocument()
  // the meter ends on the same total as the summary, and a finished run is no longer wrapping up
  expect(screen.getByText("$0.86 of $1.00")).toBeInTheDocument()
  expect(screen.getByText("43,940 tokens")).toBeInTheDocument()
  expect(screen.queryByText(/wrapping up/)).toBeNull()
})

test("a run that found nothing to estimate from reports no coverage", async () => {
  serve({ "GET /api/v1/runs/r1": () => Response.json(run) })
  const { agent, play } = fakeAgent()
  mount(agent)
  const summary = {
    status: "completed",
    spent_usd: 0,
    budget_usd: 0.1,
    coverage: { observed: 0, estimated: 0, lists: 0 },
  }
  await play([
    {
      data: JSON.stringify({
        type: "CUSTOM",
        name: "run_summary",
        value: summary,
      }),
    },
  ])
  expect(screen.getByText("completed · $0.00 spent")).toBeInTheDocument()
  expect(screen.queryByText(/coverage/)).toBeNull()
})

test("pausing a running run calls the API", async () => {
  const calls = serve({
    "GET /api/v1/runs/r1": () => Response.json(run),
    "POST /api/v1/runs/r1/pause": () =>
      Response.json({ ...run, status: "paused" }),
  })
  const { agent } = fakeAgent()
  mount(agent)
  await userEvent.click(await screen.findByRole("button", { name: "Pause" }))
  expect(calls.some((call) => call.url.endsWith("/runs/r1/pause"))).toBe(true)
})

test("branching posts the chosen checkpoint and re-attaches to the child run", async () => {
  const calls = serve({
    "GET /api/v1/runs/r1": () => Response.json({ ...run, status: "paused" }),
    "GET /api/v1/runs/r1/checkpoints": () => Response.json(checkpoints),
    "POST /api/v1/runs/r1/branch": () =>
      Response.json({ ...run, id: "r2", parent_run_id: "r1" }, { status: 201 }),
  })
  const { agent } = fakeAgent()
  const onReattach = mount(agent)
  await userEvent.click(await screen.findByRole("button", { name: "Branch" }))
  await userEvent.selectOptions(
    await screen.findByRole("combobox", { name: "Checkpoint" }),
    "ck-1"
  )
  await userEvent.click(
    screen.getByRole("button", { name: "Branch from here" })
  )
  const branch = await vi.waitUntil(() =>
    calls.find((call) => call.url.endsWith("/runs/r1/branch"))
  )
  expect(await branch.clone().json()).toEqual({ checkpoint_id: "ck-1" })
  expect(onReattach).toHaveBeenCalledWith("r2")
})

test("resuming a paused run re-attaches the transcript to the same run", async () => {
  serve({
    "GET /api/v1/runs/r1": () => Response.json({ ...run, status: "paused" }),
    "POST /api/v1/runs/r1/resume": () => Response.json(run),
  })
  const { agent } = fakeAgent()
  const onReattach = mount(agent)
  await userEvent.click(await screen.findByRole("button", { name: "Resume" }))
  await vi.waitUntil(() => onReattach.mock.calls.length > 0)
  expect(onReattach).toHaveBeenCalledWith() // no id: the same run, on a fresh stream
})

test("cancelling a live run calls the API", async () => {
  const calls = serve({
    "GET /api/v1/runs/r1": () => Response.json(run),
    "POST /api/v1/runs/r1/cancel": () =>
      Response.json({ ...run, status: "cancelled" }),
  })
  const { agent } = fakeAgent()
  mount(agent)
  await userEvent.click(
    await screen.findByRole("button", { name: "Cancel the run" })
  )
  await vi.waitUntil(() =>
    calls.find((call) => call.url.endsWith("/runs/r1/cancel"))
  )
})

test("a spent budget says the run stopped early, not that it is wrapping up", async () => {
  serve({ "GET /api/v1/runs/r1": () => Response.json(run) })
  const { agent, play } = fakeAgent()
  mount(agent)
  const cost = { spent_usd: 1, budget_usd: 1, tokens: 9000 }
  await play([
    { data: JSON.stringify({ type: "CUSTOM", name: "cost", value: cost }) },
  ])
  expect(screen.getByText(/stopped early/)).toBeInTheDocument()
  expect(screen.queryByText(/wrapping up/)).toBeNull()
})

test("a control the API refuses says so", async () => {
  serve({
    "GET /api/v1/runs/r1": () => Response.json(run),
    "POST /api/v1/runs/r1/pause": () => new Response(null, { status: 409 }),
  })
  const { agent } = fakeAgent()
  mount(agent)
  await userEvent.click(await screen.findByRole("button", { name: "Pause" }))
  expect(await screen.findByText(/did not go through/)).toBeInTheDocument()
})

test("each node in the tree shows how long it ran", async () => {
  serve({ "GET /api/v1/runs/r1": () => Response.json(run) })
  const { agent, play } = fakeAgent()
  mount(agent)
  const clock = vi.spyOn(Date, "now")
  const at = (ms: number, event: Record<string, unknown>) => {
    clock.mockReturnValue(ms)
    return play([{ data: JSON.stringify(event) }])
  }
  await at(1_000, { type: "RUN_STARTED" })
  await at(2_000, {
    type: "SUBAGENT_STARTED",
    subagentRunId: "s1",
    name: "scout",
  })
  await at(4_000, {
    type: "TOOL_CALL_START",
    subagentRunId: "s1",
    toolCallId: "c1",
    toolCallName: "search_papers",
  })
  await at(9_000, { type: "TOOL_CALL_RESULT", toolCallId: "c1", content: "ok" })
  await at(14_000, { type: "SUBAGENT_FINISHED", subagentRunId: "s1" })
  clock.mockRestore()
  const tree = screen.getByRole("list", { name: "Run activity" })
  const row = (name: string) => within(tree).getByText(name).parentElement!
  expect(row("search_papers")).toHaveTextContent("5 s") // its result came 5 s after it started
  expect(row("scout")).toHaveTextContent("12 s")
  expect(row("lead")).toHaveTextContent("13 s") // still running: its time as of the last event
})

test("a single token is not counted as tokens", async () => {
  serve({ "GET /api/v1/runs/r1": () => Response.json(run) })
  const { agent, play } = fakeAgent()
  mount(agent)
  const cost = { spent_usd: 0, budget_usd: 1, tokens: 1 }
  await play([
    { data: JSON.stringify({ type: "CUSTOM", name: "cost", value: cost }) },
  ])
  expect(screen.getByText("1 token")).toBeInTheDocument()
})

test("the live regions are rendered before they have anything to say", async () => {
  // a screen reader announces a live region's change only if the region was in the accessibility
  // tree before it: `display: none` while empty (Tailwind's `empty:hidden`) silences the notices
  serve({
    "GET /api/v1/runs/r1": () => Response.json({ ...run, spent_usd: "0.00" }),
  })
  const { agent } = fakeAgent()
  mount(agent)
  await screen.findByRole("button", { name: "Pause" })
  const regions = document.querySelectorAll('[aria-live="polite"]')
  expect(regions).toHaveLength(3) // the budget notice, the failure line and the action error
  for (const region of regions) {
    expect(region).toBeEmptyDOMElement()
    expect(region.className).not.toMatch(/(^|\s)(empty:)?hidden(\s|$)/)
  }
})

test("a resume whose drive died at once still re-attaches, so its failure is read", async () => {
  // the stream carries the RUN_ERROR; without a re-attach the user sees only the buttons go away
  serve({
    "GET /api/v1/runs/r1": () => Response.json({ ...run, status: "paused" }),
    "POST /api/v1/runs/r1/resume": () =>
      Response.json({ ...run, status: "failed" }),
  })
  const { agent } = fakeAgent()
  const onReattach = mount(agent)
  await userEvent.click(await screen.findByRole("button", { name: "Resume" }))
  await vi.waitUntil(() => onReattach.mock.calls.length > 0)
  expect(onReattach).toHaveBeenCalledWith()
})

test.each([
  ["running", false],
  ["awaiting_user", false],
  ["paused", true],
  ["completed", true],
  ["failed", true],
])("a %s run offers Branch: %s", async (status, offered) => {
  // the backend branches only a paused or finished run
  serve({ "GET /api/v1/runs/r1": () => Response.json({ ...run, status }) })
  const { agent } = fakeAgent()
  mount(agent)
  await screen.findByText("$0.84 of $1.00") // the run has been read
  expect(screen.queryByRole("button", { name: "Branch" }) !== null).toBe(
    offered
  )
})
