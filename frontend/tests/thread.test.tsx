import { act, fireEvent, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, test, vi } from "vitest"

import { healthy, renderAt, serve, version } from "./render"
import { frames, sse, untilInterrupt } from "./sse"

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

const posted = (calls: Request[]) =>
  calls.some((call) => call.url.endsWith("/threads/t1/runs"))

async function askOnDeep() {
  await userEvent.selectOptions(
    await screen.findByRole("combobox", { name: "Effort" }),
    "deep"
  )
  await userEvent.type(
    screen.getByRole("textbox", { name: "Ask a research question" }),
    "Why does RAG help?"
  )
}

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

test("Escape dismisses the confirmation, and the next send asks again rather than starting the run", async () => {
  const calls = serve(routes())
  renderAt("/p/p1/chat/t1")
  await askOnDeep()
  await userEvent.click(screen.getByRole("button", { name: "Send" }))
  const dialog = await screen.findByRole<HTMLDialogElement>("dialog", {
    name: "Confirm the budget",
  })
  // jsdom has no native Escape, so replay what the browser does: `cancel`, the close, then `close`
  fireEvent(dialog, new Event("cancel", { cancelable: true }))
  dialog.close()
  fireEvent(dialog, new Event("close"))
  expect(screen.queryByRole("dialog")).toBeNull()
  await userEvent.click(screen.getByRole("button", { name: "Send" }))
  expect(
    await screen.findByRole("dialog", { name: "Confirm the budget" })
  ).toBeInTheDocument()
  expect(posted(calls)).toBe(false)
})

test("Cancel closes the confirmation without starting the run", async () => {
  const calls = serve(routes())
  renderAt("/p/p1/chat/t1")
  await askOnDeep()
  await userEvent.click(screen.getByRole("button", { name: "Send" }))
  await screen.findByRole("dialog", { name: "Confirm the budget" })
  // an open dialog is not consent: only Confirm lets a submit through. (A browser makes the page
  // inert behind the modal; jsdom does not, which is what lets this second submit be made at all.)
  await userEvent.click(screen.getByRole("button", { name: "Send" }))
  expect(posted(calls)).toBe(false)
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }))
  expect(screen.queryByRole("dialog")).toBeNull()
  expect(posted(calls)).toBe(false)
  await userEvent.click(screen.getByRole("button", { name: "Send" }))
  expect(
    await screen.findByRole("dialog", { name: "Confirm the budget" })
  ).toBeInTheDocument()
  expect(posted(calls)).toBe(false)
})

test("an empty composer on a dear tier cannot send and asks nothing", async () => {
  const calls = serve(routes())
  renderAt("/p/p1/chat/t1")
  await userEvent.selectOptions(
    await screen.findByRole("combobox", { name: "Effort" }),
    "deep"
  )
  expect(screen.getByRole("button", { name: "Send" })).toBeDisabled()
  // Enter still submits the form; the composer then has nothing to send, so neither may the guard
  await userEvent.type(
    screen.getByRole("textbox", { name: "Ask a research question" }),
    "{Enter}"
  )
  expect(screen.queryByRole("dialog")).toBeNull()
  expect(posted(calls)).toBe(false)
})

test("the intercepted submit is prevented, so the form never navigates", async () => {
  // jsdom cannot show the navigation a submit without preventDefault would cause; the flag can
  const submits: Event[] = []
  const record = (event: Event) => submits.push(event)
  window.addEventListener("submit", record, true)
  serve(routes())
  renderAt("/p/p1/chat/t1")
  await askOnDeep()
  await userEvent.click(screen.getByRole("button", { name: "Send" }))
  await screen.findByRole("dialog", { name: "Confirm the budget" })
  window.removeEventListener("submit", record, true)
  expect(submits).toHaveLength(1)
  expect(submits[0]!.defaultPrevented).toBe(true)
})

test("without the effort catalogue nothing can be sent", async () => {
  // no catalogue, no budget to judge: sending anyway would skip the confirmation on any tier
  const calls = serve({
    ...routes(),
    "GET /api/v1/efforts": () => new Response(null, { status: 500 }),
  })
  renderAt("/p/p1/chat/t1")
  const input = await screen.findByRole("textbox", {
    name: "Ask a research question",
  })
  await userEvent.type(input, "Why does RAG help?")
  expect(screen.getByRole("button", { name: "Send" })).toBeDisabled()
  await userEvent.type(input, "{Enter}")
  expect(input).toHaveValue("Why does RAG help?") // a send would have cleared it
  expect(posted(calls)).toBe(false)
})

test("a revisited thread shows the answer of a run that finished meanwhile", async () => {
  const question = {
    id: "m1",
    role: "user",
    text: "Why does RAG help?",
    run_id: "r1",
    created_at: "2026-11-13T10:00:00Z",
  }
  const answer = {
    id: "m2",
    role: "assistant",
    text: "Because it grounds the answer.",
    run_id: "r1",
    created_at: "2026-11-13T10:01:00Z",
  }
  const thread = (id: string, messages: unknown[]) =>
    Response.json({
      id,
      project_id: "p1",
      title: null,
      created_at: "2026-11-13T10:00:00Z",
      messages,
    })
  // the run's answer lands between the first visit and the second. The first visit reads the thread
  // twice: the run had already ended when it was read, so its answer may have landed in between
  let visits = 0
  serve({
    ...routes(),
    "GET /api/v1/threads/t1": () =>
      thread("t1", ++visits <= 2 ? [question] : [question, answer]),
    "GET /api/v1/threads/t2": () => thread("t2", []),
    "GET /api/v1/runs/r1": () => Response.json({ ...run, status: "completed" }),
  })
  const { router } = renderAt("/p/p1/chat/t1")
  expect(await screen.findByText("Why does RAG help?")).toBeInTheDocument()
  expect(screen.queryByText("Because it grounds the answer.")).toBeNull()
  const open = (id: string) =>
    act(() =>
      router.navigate({
        to: "/p/$id/chat/$thread",
        params: { id: "p1", thread: id },
      })
    )
  await open("t2")
  await waitFor(() =>
    expect(screen.queryByText("Why does RAG help?")).toBeNull()
  )
  await open("t1")
  expect(
    await screen.findByText("Because it grounds the answer.")
  ).toBeInTheDocument()
})

test("a thread that fails to load says so instead of loading forever", async () => {
  serve({
    ...routes(),
    "GET /api/v1/threads/t1": () => new Response(null, { status: 500 }),
  })
  renderAt("/p/p1/chat/t1")
  expect(
    await screen.findByText(
      "Could not load this thread. Check that Researchly is still running."
    )
  ).toBeInTheDocument()
})

test("a run that finishes refetches the thread, so the stored copy catches up with its answer", async () => {
  // the transcript was hydrated before the run's answer was stored; the run's end is the moment
  // the stored thread catches up
  let visits = 0
  const base = routes()
  serve({
    ...base,
    "GET /api/v1/threads/t1": () => {
      visits += 1
      return base["GET /api/v1/threads/t1"]()
    },
  })
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
  // the summary is the stream's last event: once the panel shows it, the run is over
  expect(
    await screen.findByText("completed · $0.86 spent · 73% coverage")
  ).toBeInTheDocument()
  await waitFor(() => expect(visits).toBe(2))
})

test("branching from the panel re-attaches the transcript to the child run's stream", async () => {
  const calls = serve({
    ...routes([
      {
        id: "m1",
        role: "user",
        text: "Why does RAG help?",
        run_id: "r1",
        created_at: "2026-11-13T10:00:00Z",
      },
    ]),
    "GET /api/v1/runs/r1": () => Response.json({ ...run, status: "paused" }),
    "POST /api/v1/runs/r1/stream": () => sse(untilInterrupt()),
    "GET /api/v1/runs/r1/checkpoints": () =>
      Response.json([
        { checkpoint_id: "ck-1", created_at: "2026-11-13T10:01:00Z", step: 1 },
      ]),
    "POST /api/v1/runs/r1/branch": () =>
      Response.json({ ...run, id: "r2", parent_run_id: "r1" }, { status: 201 }),
    "GET /api/v1/runs/r2": () => Response.json({ ...run, id: "r2" }),
    "POST /api/v1/runs/r2/stream": () => sse(frames()),
  })
  renderAt("/p/p1/chat/t1")
  await userEvent.click(await screen.findByRole("button", { name: "Branch" }))
  await userEvent.click(
    await screen.findByRole("button", { name: "Branch from here" })
  )
  // only the child's stream carries this text: it reaches the transcript through the remount
  expect(
    await screen.findByText(/lowers hallucination rates/)
  ).toBeInTheDocument()
  expect(calls.some((call) => call.url.includes("/runs/r2/stream"))).toBe(true)
})

test("a paused and resumed run keeps the question the composer sent", async () => {
  // the view opened on an empty thread; the question exists only in the stored thread from here on,
  // so the remount that re-attaches must hydrate from a fresh copy
  let asked = false
  let status = "running"
  const question = {
    id: "m1",
    role: "user",
    text: "Why does RAG help?",
    run_id: "r1",
    created_at: "2026-11-13T10:00:00Z",
  }
  const calls = serve({
    ...routes(),
    "GET /api/v1/threads/t1": () =>
      Response.json({
        id: "t1",
        project_id: "p1",
        title: null,
        created_at: "2026-11-13T10:00:00Z",
        messages: asked ? [question] : [],
      }),
    "POST /api/v1/threads/t1/runs": () => {
      asked = true
      return Response.json(run, { status: 201 })
    },
    "GET /api/v1/runs/r1": () => Response.json({ ...run, status }),
    "POST /api/v1/runs/r1/pause": () => {
      status = "paused"
      return Response.json({ ...run, status })
    },
    "POST /api/v1/runs/r1/resume": () => {
      status = "running"
      return Response.json({ ...run, status })
    },
    "POST /api/v1/runs/r1/stream": () => sse(untilInterrupt()),
  })
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
  await userEvent.click(await screen.findByRole("button", { name: "Pause" }))
  await userEvent.click(await screen.findByRole("button", { name: "Resume" }))
  const streams = () =>
    calls.filter((call) => call.url.includes("/runs/r1/stream")).length
  // only the remounted runtime opens a second stream
  await vi.waitUntil(() => streams() === 2, { timeout: 10_000 })
  expect(await screen.findByText("Why does RAG help?")).toBeInTheDocument()
})

test("a run that ended while the thread loaded still shows its answer on the first visit", async () => {
  // the thread is read before the run's answer is stored, the run after it has ended: no stream
  // will carry the answer, so the thread is read again
  let reads = 0
  serve({
    ...routes(),
    "GET /api/v1/threads/t1": () =>
      Response.json({
        id: "t1",
        project_id: "p1",
        title: null,
        created_at: "2026-11-13T10:00:00Z",
        messages: [
          {
            id: "m1",
            role: "user",
            text: "Why does RAG help?",
            run_id: "r1",
            created_at: "2026-11-13T10:00:00Z",
          },
          ...(++reads === 1
            ? []
            : [
                {
                  id: "m2",
                  role: "assistant",
                  text: "Because it grounds the answer.",
                  run_id: "r1",
                  created_at: "2026-11-13T10:01:00Z",
                },
              ]),
        ],
      }),
    "GET /api/v1/runs/r1": () => Response.json({ ...run, status: "completed" }),
  })
  renderAt("/p/p1/chat/t1")
  expect(
    await screen.findByText("Because it grounds the answer.")
  ).toBeInTheDocument()
})

test("a conversation stays open when its run ends under it", async () => {
  // the run turns terminal while the view is open, which is exactly what the opening gates wait out;
  // re-applying them would unmount the streamed transcript and reopen on a thread without it
  let runReads = 0
  let threadReads = 0
  const base = routes([
    {
      id: "m1",
      role: "user",
      text: "Why does RAG help?",
      run_id: "r1",
      created_at: "2026-11-13T10:00:00Z",
    },
  ])
  serve({
    ...base,
    "GET /api/v1/threads/t1": async () => {
      // the panel's reread at the run's end is slow, as a real one can be: the view renders the
      // ended run before the newer thread lands
      if (++threadReads > 1) await new Promise((done) => setTimeout(done, 300))
      return base["GET /api/v1/threads/t1"]()
    },
    "GET /api/v1/runs/r1": () =>
      Response.json({
        ...run,
        status: ++runReads === 1 ? "running" : "completed",
      }),
  })
  renderAt("/p/p1/chat/t1")
  expect(
    await screen.findByText(/lowers hallucination rates/)
  ).toBeInTheDocument()
  await waitFor(() => expect(threadReads).toBe(2))
  await act(() => new Promise((done) => setTimeout(done, 500))) // the slow reread lands
  expect(screen.getByText(/lowers hallucination rates/)).toBeInTheDocument()
  expect(
    screen.getByText("completed · $0.86 spent · 73% coverage")
  ).toBeInTheDocument()
})
