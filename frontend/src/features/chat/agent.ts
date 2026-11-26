import { HttpAgent, type Message as AgentMessage } from "@ag-ui/client"

import { api, ApiError } from "@/api/client"
import { type EffortName, type Message, unwrap } from "@/api/queries"

/** What the browser knows about the run this thread is streaming. The fetch adapter reads and writes it. */
export type RunRef = {
  runId: string | null
  lastSeq: number
  lastUserMessageId: string | null
  effort: EffortName
  /** Told the id of a run this adapter just created, so the run panel can follow it. */
  onRun?: (id: string) => void
}

export function newRunRef(messages: Message[], effort: EffortName): RunRef {
  const lastUser = messages.filter((m) => m.role === "user").at(-1)
  return {
    runId: null,
    lastSeq: 0,
    lastUserMessageId: lastUser?.id ?? null,
    effort,
  }
}

export function toAgentMessages(messages: Message[]): AgentMessage[] {
  return messages.map((m) =>
    m.role === "user"
      ? { id: m.id, role: "user" as const, content: m.text }
      : { id: m.id, role: "assistant" as const, content: m.text }
  )
}

/** Keep `ref.lastSeq` on the stream's `id:` lines without changing the bytes the parser sees. */
function trackSeq(response: Response, ref: RunRef): Response {
  if (!response.body) return response
  const decoder = new TextDecoder()
  let buffer = ""
  const tap = new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      buffer += decoder.decode(chunk, { stream: true })
      const lines = buffer.split("\n")
      buffer = lines.pop() ?? "" // an id line split across chunks finishes in the next one
      for (const line of lines) {
        if (line.startsWith("id:"))
          ref.lastSeq = Number(line.slice(3).trim()) || ref.lastSeq
      }
      controller.enqueue(chunk)
    },
  })
  return new Response(response.body.pipeThrough(tap), {
    status: response.status,
    headers: response.headers,
  })
}

/**
 * The adapter between AG-UI's RunAgentInput and our REST API (ADR 0012).
 *
 * A last message that is a user message we have not sent starts a run; anything else attaches to the run
 * we already know. Either way the answer is the SSE stream, resumed at the last sequence we saw.
 */
export function makeFetch(threadId: string, ref: RunRef) {
  return async (
    _url: RequestInfo | URL,
    init?: RequestInit
  ): Promise<Response> => {
    const body = JSON.parse(String(init?.body ?? "{}")) as {
      messages?: { id: string; role: string; content?: string }[]
    }
    const last = body.messages?.at(-1)
    if (last?.role === "user" && last.id !== ref.lastUserMessageId) {
      const run = unwrap(
        await api.POST("/api/v1/threads/{thread_id}/runs", {
          params: { path: { thread_id: threadId } },
          body: { message: String(last.content ?? ""), effort: ref.effort },
        })
      )
      ref.runId = run.id
      ref.onRun?.(run.id)
      ref.lastSeq = 0
      ref.lastUserMessageId = last.id
    }
    if (ref.runId === null) throw new ApiError(409)
    const stream = await fetch(
      `${window.location.origin}/api/v1/runs/${ref.runId}/stream?after=${ref.lastSeq}`,
      {
        method: "POST",
        headers: { accept: "text/event-stream" },
        signal: init?.signal ?? null,
      }
    )
    if (!stream.ok) throw new ApiError(stream.status)
    return trackSeq(stream, ref)
  }
}

export function createAgent(
  threadId: string,
  messages: Message[],
  ref: RunRef
): HttpAgent {
  return new HttpAgent({
    threadId,
    initialMessages: toAgentMessages(messages),
    url: `${window.location.origin}/api/v1/runs`, // never requested: the fetch above picks the real URL
    fetch: makeFetch(threadId, ref),
  })
}
