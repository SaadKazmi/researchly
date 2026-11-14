import { afterEach, expect, test, vi } from "vitest"

import {
  checkpointsQuery,
  effortsQuery,
  LIVE_STATUSES,
  runQuery,
  threadQuery,
  threadsQuery,
} from "@/api/queries"

function stub(body: unknown, status = 200) {
  const calls: string[] = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      calls.push(
        `${request.method} ${new URL(request.url).pathname}${new URL(request.url).search}`
      )
      return Response.json(body, { status })
    })
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

test("the thread list sends the search text only when there is some", async () => {
  const calls = stub([])
  await threadsQuery("p1").queryFn!({} as never)
  await threadsQuery("p1", "rag").queryFn!({} as never)
  expect(calls).toEqual([
    "GET /api/v1/projects/p1/threads",
    "GET /api/v1/projects/p1/threads?q=rag",
  ])
})

test("a failing response becomes an ApiError with its status", async () => {
  stub({ detail: "nope" }, 404)
  await expect(threadQuery("t1").queryFn!({} as never)).rejects.toThrow(
    "HTTP 404"
  )
})

test("the run query is disabled without a run and polls while the run is live", () => {
  expect(runQuery(null).enabled).toBe(false)
  const live = runQuery("r1").refetchInterval as (q: {
    state: { data?: { status: string } }
  }) => number | false
  expect(live({ state: { data: { status: "running" } } })).toBe(2000)
  expect(live({ state: { data: { status: "completed" } } })).toBe(false)
  // the predicate must read LIVE_STATUSES itself: a lone `!== "completed"` passes the two lines above
  expect(live({ state: { data: { status: "awaiting_user" } } })).toBe(2000)
  expect(live({ state: { data: { status: "planning" } } })).toBe(2000)
  for (const terminal of ["partial", "cancelled", "failed"]) {
    expect(live({ state: { data: { status: terminal } } })).toBe(false)
  }
  expect(LIVE_STATUSES).toContain("awaiting_user")
})

test("efforts and checkpoints hit their endpoints", async () => {
  const calls = stub([])
  await effortsQuery.queryFn!({} as never)
  await checkpointsQuery("r1").queryFn!({} as never)
  expect(calls).toEqual([
    "GET /api/v1/efforts",
    "GET /api/v1/runs/r1/checkpoints",
  ])
})
