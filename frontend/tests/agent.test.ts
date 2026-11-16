import { afterEach, expect, test, vi } from "vitest"

import { createAgent, makeFetch, newRunRef } from "@/features/chat/agent"

import { serve } from "./render"
import { frames, sse, untilInterrupt } from "./sse"

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

function input(messages: { id: string; role: string; content: string }[]) {
  return {
    method: "POST",
    body: JSON.stringify({ threadId: "t1", runId: "x", messages }),
  }
}

const after = (request: Request) =>
  new URL(request.url).searchParams.get("after")

afterEach(() => vi.unstubAllGlobals())

test("a new user message creates the run, then streams it from the start", async () => {
  const calls = serve({
    "POST /api/v1/threads/t1/runs": () => Response.json(run, { status: 201 }),
    "POST /api/v1/runs/r1/stream": () => sse(untilInterrupt()),
  })
  const ref = newRunRef([], "deep")
  const response = await makeFetch("t1", ref)(
    "https://ignored",
    input([{ id: "u1", role: "user", content: "Why?" }])
  )
  await response.text() // drain, so the sequence tap runs
  expect(await calls[0].clone().json()).toEqual({
    message: "Why?",
    effort: "deep",
  })
  expect(after(calls[1])).toBe("0")
  expect(ref.runId).toBe("r1")
  expect(ref.lastSeq).toBe(untilInterrupt().at(-1)!.seq)
})

test("a freshly started run is reported through onRun", async () => {
  // the run panel follows the run the composer just started; nothing else tells it the id
  serve({
    "POST /api/v1/threads/t1/runs": () => Response.json(run, { status: 201 }),
    "POST /api/v1/runs/r1/stream": () => sse(untilInterrupt()),
  })
  const onRun = vi.fn()
  const ref = { ...newRunRef([], "standard"), onRun }
  await (
    await makeFetch("t1", ref)(
      "https://ignored",
      input([{ id: "u1", role: "user", content: "Why?" }])
    )
  ).text()
  expect(onRun).toHaveBeenCalledWith("r1")
})

test("the same transcript attaches to the known run and resumes after the last sequence", async () => {
  const calls = serve({
    "POST /api/v1/threads/t1/runs": () => Response.json(run, { status: 201 }),
    // like `follow`: the rows after `after`, up to the end of the AG-UI run they belong to
    "POST /api/v1/runs/r1/stream": (request: Request) => {
      const rest = frames().filter((f) => f.seq > Number(after(request)))
      return sse(
        rest.slice(0, rest.findIndex((f) => f.type === "RUN_FINISHED") + 1)
      )
    },
  })
  const ref = newRunRef([], "standard")
  const send = makeFetch("t1", ref)
  const messages = [{ id: "u1", role: "user", content: "Why?" }]
  await (await send("https://ignored", input(messages))).text()
  const seen = ref.lastSeq
  const before = calls.length
  await (await send("https://ignored", input(messages))).text()
  expect(calls.length).toBe(before + 1) // no second createRun
  expect(after(calls.at(-1)!)).toBe(String(seen)) // the reconnect resumes where the first drive stopped
  expect(seen).toBe(untilInterrupt().at(-1)!.seq) // the first drive stopped at the plan interrupt
  expect(ref.lastSeq).toBe(frames().at(-1)!.seq) // the second drive saw only what came after, and ran to the end
})

test("an assistant message last attaches instead of asking again", async () => {
  const calls = serve({
    "POST /api/v1/threads/t1/runs": () => Response.json(run, { status: 201 }),
    "POST /api/v1/runs/r1/stream": () => sse(untilInterrupt()),
  })
  const ref = newRunRef([], "standard")
  const send = makeFetch("t1", ref)
  await (
    await send(
      "https://ignored",
      input([{ id: "u1", role: "user", content: "Why?" }])
    )
  ).text()
  await (
    await send(
      "https://ignored",
      input([
        { id: "u1", role: "user", content: "Why?" },
        { id: "a1", role: "assistant", content: "Because." },
      ])
    )
  ).text()
  expect(calls.map((c) => c.method + " " + new URL(c.url).pathname)).toEqual([
    "POST /api/v1/threads/t1/runs",
    "POST /api/v1/runs/r1/stream",
    "POST /api/v1/runs/r1/stream",
  ])
})

test("a second question starts a second run from sequence zero", async () => {
  let created = 0
  const calls = serve({
    "POST /api/v1/threads/t1/runs": () =>
      Response.json({ ...run, id: `r${++created}` }, { status: 201 }),
    "POST /api/v1/runs/r1/stream": () => sse(untilInterrupt()),
    "POST /api/v1/runs/r2/stream": () => sse(frames().slice(0, 3)),
  })
  const ref = newRunRef([], "standard")
  const send = makeFetch("t1", ref)
  await (
    await send(
      "https://ignored",
      input([{ id: "u1", role: "user", content: "Why?" }])
    )
  ).text()
  expect(ref.lastSeq).toBeGreaterThan(3)
  await (
    await send(
      "https://ignored",
      input([{ id: "u2", role: "user", content: "And how?" }])
    )
  ).text()
  const [, , second, stream] = calls
  expect(await second.clone().json()).toEqual({
    message: "And how?",
    effort: "standard",
  })
  expect(new URL(stream.url).pathname).toBe("/api/v1/runs/r2/stream")
  expect(after(stream)).toBe("0") // not the first run's position
  expect(ref.runId).toBe("r2")
  expect(ref.lastSeq).toBe(3)
})

test("the ref starts from the thread's last user message, so a reload attaches instead of re-asking", () => {
  const ref = newRunRef(
    [
      {
        id: "m1",
        role: "user",
        text: "Why?",
        run_id: "r1",
        created_at: "2026-11-13T10:00:00Z",
      },
      {
        id: "m2",
        role: "assistant",
        text: "Because.",
        run_id: "r1",
        created_at: "2026-11-13T10:01:00Z",
      },
    ],
    "standard"
  )
  expect(ref.lastUserMessageId).toBe("m1")
  expect(ref.runId).toBeNull()
})

test("the real client accepts the committed transcript: every event arrives, in order, with no protocol error", async () => {
  // the transcript stands in for the backend in every later test, so the client's own schema and
  // verifier (run in order, two AG-UI runs on one stream) must take it
  serve({
    "POST /api/v1/threads/t1/runs": () => Response.json(run, { status: 201 }),
    "POST /api/v1/runs/r1/stream": () => sse(frames()),
  })
  const ref = newRunRef([], "standard")
  const agent = createAgent("t1", [], ref)
  agent.addMessage({ id: "u1", role: "user", content: "Why?" })
  const seen: string[] = []
  await agent.runAgent(
    {},
    { onEvent: ({ event }) => void seen.push(event.type as string) }
  )
  expect(seen).toEqual(frames().map((f) => f.type))
  expect(ref.lastSeq).toBe(frames().at(-1)!.seq)
})

test("a tool result in the committed transcript never reuses an assistant message's id", () => {
  // the real adapter gives a tool result the ToolMessage's own id, never the parent assistant
  // message's (`ag_ui_langgraph/agent.py`). A fixture that reuses one hands assistant-ui two
  // messages that collide on their React key, and the "client accepts the transcript" test above
  // cannot see it: that one compares event types only.
  const events = frames().map(
    (frame) =>
      JSON.parse(frame.data) as {
        type: string
        messageId?: string
        parentMessageId?: string
      }
  )
  // an assistant message is created by a TEXT_MESSAGE_* run and also, implicitly, by the tool call
  // that hangs off it - `parentMessageId` names that one, and it is the id the collision was on
  const assistant = new Set(
    events.flatMap((event) =>
      [
        event.type.startsWith("TEXT_MESSAGE_") ? event.messageId : undefined,
        event.parentMessageId,
      ].filter((id): id is string => Boolean(id))
    )
  )
  const tool = new Set(
    events
      .filter((event) => event.type === "TOOL_CALL_RESULT" && event.messageId)
      .map((event) => event.messageId!)
  )
  expect(assistant.size).toBeGreaterThan(0) // a transcript that stopped carrying ids proves nothing
  expect(tool.size).toBeGreaterThan(0)
  expect([...tool].filter((id) => assistant.has(id))).toEqual([])
})

test("the thread's history is handed to the client as its initial messages", async () => {
  const ref = newRunRef([], "standard")
  const agent = createAgent(
    "t1",
    [
      {
        id: "m1",
        role: "user",
        text: "Why?",
        run_id: "r1",
        created_at: "2026-11-13T10:00:00Z",
      },
      {
        id: "m2",
        role: "assistant",
        text: "Because.",
        run_id: "r1",
        created_at: "2026-11-13T10:01:00Z",
      },
    ],
    ref
  )
  // without this the client starts every reload with an empty transcript, and the user's own
  // question disappears from the thread they just reopened
  expect(agent.messages).toEqual([
    { id: "m1", role: "user", content: "Why?" },
    { id: "m2", role: "assistant", content: "Because." },
  ])
})

test("an id line split across two network chunks still advances the sequence", async () => {
  const body = frames()
    .map((frame) => `data: ${frame.data}\nid: ${frame.seq}\n\n`)
    .join("")
  const cut = body.lastIndexOf("\nid: ") + 3 // leaves a bare "id" at the end of the first chunk
  serve({
    "POST /api/v1/runs/r1/stream": () =>
      new Response(
        new ReadableStream({
          start(controller) {
            const encode = new TextEncoder()
            controller.enqueue(encode.encode(body.slice(0, cut)))
            controller.enqueue(encode.encode(body.slice(cut)))
            controller.close()
          },
        }),
        { headers: { "content-type": "text/event-stream" } }
      ),
  })
  const ref = {
    runId: "r1",
    lastSeq: 0,
    lastUserMessageId: "u1",
    effort: "standard" as const,
  }
  const response = await makeFetch("t1", ref)(
    "/",
    input([{ id: "u1", role: "user", content: "Why?" }])
  )
  await response.text() // drain it: the tap only sees what the consumer pulls
  expect(ref.lastSeq).toBe(frames().at(-1)!.seq)
})

test("attaching with no run known fails instead of silently streaming nothing", async () => {
  serve({})
  const ref = newRunRef([], "standard")
  // the trailing message is the one the ref already saw, so no run is created and none is known
  await expect(
    makeFetch("t1", { ...ref, lastUserMessageId: "u1" })(
      "/",
      input([{ id: "u1", role: "user", content: "Why?" }])
    )
  ).rejects.toMatchObject({ status: 409 })
})

test("a refused stream surfaces its status rather than being treated as a run", async () => {
  serve({
    "POST /api/v1/runs/r1/stream": () => new Response("no", { status: 503 }),
  })
  const ref = {
    runId: "r1",
    lastSeq: 4,
    lastUserMessageId: "u1",
    effort: "standard" as const,
  }
  await expect(
    makeFetch("t1", ref)(
      "/",
      input([{ id: "u1", role: "user", content: "Why?" }])
    )
  ).rejects.toMatchObject({ status: 503 })
})
