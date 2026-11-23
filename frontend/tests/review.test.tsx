import type { HttpAgent } from "@ag-ui/client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, test, vi } from "vitest"

import "@/i18n"
import { Review } from "@/features/chat/review"
import { RunStateProvider } from "@/features/chat/run-state"

import { serve } from "./render"
import { untilInterrupt } from "./sse"

const question = {
  type: "RUN_FINISHED",
  threadId: "thread-1",
  runId: "agui-1",
  outcome: {
    type: "interrupt",
    interrupts: [
      {
        id: "int-2",
        reason: "researchly:ask_user",
        // no `toolCallId`: the adapter never puts one on an interrupt (grep `runs/agui.py`, and
        // the committed transcript's plan interrupt has none). A fixture that invents one lets a
        // component key off a field that is always undefined in the real app.
        message: "Which years should I cover?",
        responseSchema: {
          type: "object",
          properties: {
            decisions: {
              type: "array",
              prefixItems: [{ properties: { type: { enum: ["respond"] } } }],
            },
          },
        },
        metadata: {
          langgraph: {
            raw: {
              action_requests: [
                {
                  name: "ask_user",
                  args: { question: "Which years should I cover?" },
                },
              ],
            },
          },
        },
      },
    ],
  },
}

// the adapter joins the names of an interrupt with several actions with `+`; the backend wants one
// decision per action, in order, in one request
const planAndQuestion = {
  ...question,
  outcome: {
    type: "interrupt",
    interrupts: [
      {
        id: "int-3",
        reason: "researchly:submit_plan+ask_user",
        message: "",
        responseSchema: {
          type: "object",
          properties: {
            decisions: {
              type: "array",
              prefixItems: [
                {
                  properties: { type: { enum: ["approve", "edit", "reject"] } },
                },
                { properties: { type: { enum: ["respond"] } } },
              ],
            },
          },
        },
        metadata: {
          langgraph: {
            raw: {
              action_requests: [
                { name: "submit_plan", args: { plan: "1. Scope it." } },
                {
                  name: "ask_user",
                  args: { question: "Which years should I cover?" },
                },
              ],
            },
          },
        },
      },
    ],
  },
}

function mount(events: unknown[]) {
  const subscribers: { onEvent?: (payload: { event: unknown }) => void }[] = []
  const agent = {
    subscribe(subscriber: { onEvent?: (payload: { event: unknown }) => void }) {
      subscribers.push(subscriber)
      return { unsubscribe: () => {} }
    },
  } as unknown as HttpAgent
  const onAnswered = vi.fn()
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <RunStateProvider agent={agent}>
        <Review runId="r1" onAnswered={onAnswered} />
      </RunStateProvider>
    </QueryClientProvider>
  )
  act(() => {
    for (const event of events)
      for (const s of subscribers) s.onEvent?.({ event })
  })
  return onAnswered
}

const planEvents = untilInterrupt().map((frame) => JSON.parse(frame.data))
const started = { type: "RUN_STARTED", threadId: "thread-1", runId: "agui-1" }
const resumed = () => Response.json({ id: "r1", status: "running" })
const resumes = (calls: Request[]) =>
  calls.filter((call) => call.url.endsWith("/runs/r1/resume"))

afterEach(() => vi.unstubAllGlobals())

test("approving the plan resumes the run and re-attaches", async () => {
  const calls = serve({ "POST /api/v1/runs/r1/resume": resumed })
  const onAnswered = mount(planEvents)
  expect(screen.getByRole("textbox", { name: "Plan" })).toHaveValue(
    "1. How does retrieval augmentation change hallucination rates?"
  )
  await userEvent.click(screen.getByRole("button", { name: "Approve" }))
  const resume = await vi.waitUntil(() => resumes(calls)[0])
  expect(await resume.clone().json()).toEqual({
    decisions: [{ type: "approve" }],
  })
  await vi.waitUntil(() => onAnswered.mock.calls.length > 0)
})

test("an edited plan resumes with the edited action", async () => {
  const calls = serve({ "POST /api/v1/runs/r1/resume": resumed })
  mount(planEvents)
  const plan = screen.getByRole("textbox", { name: "Plan" })
  await userEvent.clear(plan)
  await userEvent.type(plan, "1. Narrow it to medicine.")
  await userEvent.click(
    screen.getByRole("button", { name: "Approve with edits" })
  )
  const resume = await vi.waitUntil(() => resumes(calls)[0])
  expect(await resume.clone().json()).toEqual({
    decisions: [
      {
        type: "edit",
        edited_action: {
          name: "submit_plan",
          args: { plan: "1. Narrow it to medicine." },
        },
      },
    ],
  })
})

test("rejecting the plan closes the review and re-attaches nothing", async () => {
  // a reject cancels the run: re-attaching would replay the plan interrupt and reopen the review
  const calls = serve({
    "POST /api/v1/runs/r1/resume": () =>
      Response.json({ id: "r1", status: "cancelled" }),
  })
  const onAnswered = mount(planEvents)
  await userEvent.click(screen.getByRole("button", { name: "Reject" }))
  const resume = await vi.waitUntil(() => resumes(calls)[0])
  expect(await resume.clone().json()).toEqual({
    decisions: [{ type: "reject" }],
  })
  await waitFor(() =>
    expect(screen.queryByRole("textbox", { name: "Plan" })).toBeNull()
  )
  expect(onAnswered).not.toHaveBeenCalled()
})

test("a clarifying question takes a typed answer", async () => {
  const calls = serve({ "POST /api/v1/runs/r1/resume": resumed })
  mount([started, question])
  expect(screen.getByText("Which years should I cover?")).toBeInTheDocument()
  await userEvent.type(
    screen.getByRole("textbox", { name: "Answer" }),
    "2020 onwards."
  )
  await userEvent.click(screen.getByRole("button", { name: "Send answer" }))
  const resume = await vi.waitUntil(() => resumes(calls)[0])
  expect(await resume.clone().json()).toEqual({
    decisions: [{ type: "respond", message: "2020 onwards." }],
  })
})

test("an interrupt with two actions sends both decisions together, in action order", async () => {
  const calls = serve({ "POST /api/v1/runs/r1/resume": resumed })
  mount([started, planAndQuestion])
  const plan = screen.getByRole("textbox", { name: "Plan" })
  await userEvent.type(plan, " Medicine.")
  const edits = screen.getByRole("button", { name: "Approve with edits" })
  await userEvent.click(edits)
  expect(resumes(calls)).toHaveLength(0) // the question has no answer yet
  expect(edits).toHaveAttribute("aria-pressed", "true") // the decision waits, visibly
  // a recorded decision sends the draft as it stands when the request goes out
  await userEvent.type(plan, " Since 2020.")
  await userEvent.type(
    screen.getByRole("textbox", { name: "Answer" }),
    "2020 onwards."
  )
  await userEvent.click(screen.getByRole("button", { name: "Send answer" }))
  const resume = await vi.waitUntil(() => resumes(calls)[0])
  expect(await resume.clone().json()).toEqual({
    decisions: [
      {
        type: "edit",
        edited_action: {
          name: "submit_plan",
          args: { plan: "1. Scope it. Medicine. Since 2020." },
        },
      },
      { type: "respond", message: "2020 onwards." },
    ],
  })
  expect(resumes(calls)).toHaveLength(1)
})

test("an answer cleared after it was recorded holds the request until it is given again", async () => {
  const calls = serve({ "POST /api/v1/runs/r1/resume": resumed })
  mount([started, planAndQuestion])
  const answer = screen.getByRole("textbox", { name: "Answer" })
  await userEvent.type(answer, "2020 onwards.")
  await userEvent.click(screen.getByRole("button", { name: "Send answer" }))
  await userEvent.clear(answer)
  await userEvent.click(screen.getByRole("button", { name: "Approve" }))
  expect(resumes(calls)).toHaveLength(0) // an empty answer never reaches the agent
  await userEvent.type(answer, "Since 2018.")
  await userEvent.click(screen.getByRole("button", { name: "Send answer" }))
  const resume = await vi.waitUntil(() => resumes(calls)[0])
  expect(await resume.clone().json()).toEqual({
    decisions: [
      { type: "approve" },
      { type: "respond", message: "Since 2018." },
    ],
  })
})

test("a resume that fails says so and does not re-attach", async () => {
  serve({
    "POST /api/v1/runs/r1/resume": () => new Response(null, { status: 409 }),
  })
  const onAnswered = mount(planEvents)
  await userEvent.click(screen.getByRole("button", { name: "Approve" }))
  expect(
    await screen.findByText(
      "That answer did not go through. Check that Researchly is still running, then try again."
    )
  ).toBeInTheDocument()
  expect(onAnswered).not.toHaveBeenCalled()
  expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled()
})
