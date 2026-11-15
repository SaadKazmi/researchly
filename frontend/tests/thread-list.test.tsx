import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, test, vi } from "vitest"

import { healthy, renderAt, serve, version } from "./render"

const base = { project_id: "p1", created_at: "2026-11-13T10:00:00Z" }
const threads = [
  { ...base, id: "t1", title: "Retrieval augmentation" },
  { ...base, id: "t2", title: "Diffusion" },
]

// The POST is what makes the new thread appear, so the list changes behind it: a stub that
// always returned the same array could not tell a refresh from no refresh.
function routes() {
  let created = false
  return {
    "/health": healthy,
    "/api/v1/system": version,
    "GET /api/v1/projects/p1/threads": (request: Request) => {
      const q = new URL(request.url).searchParams.get("q")
      const all = created
        ? [...threads, { ...base, id: "t3", title: null }]
        : threads
      return Response.json(
        q ? all.filter((t) => t.title?.toLowerCase().includes(q)) : all
      )
    },
    "POST /api/v1/projects/p1/threads": () => {
      created = true
      return Response.json({ ...base, id: "t3", title: null }, { status: 201 })
    },
  }
}

const listRequests = (calls: Request[]) =>
  calls.filter(
    (call) =>
      call.method === "GET" &&
      new URL(call.url).pathname === "/api/v1/projects/p1/threads"
  )

const hasQ = (call: Request) => new URL(call.url).searchParams.has("q")

afterEach(() => vi.unstubAllGlobals())

test("lists the project's threads and filters them by the search text", async () => {
  const calls = serve(routes())
  const { router } = renderAt("/p/p1/chat/t1")
  const nav = await screen.findByRole("navigation", { name: "Threads" })
  expect(router.state.location.pathname).toBe("/p/p1/chat/t1")
  expect(await within(nav).findAllByRole("link")).toHaveLength(2)
  expect(
    within(nav)
      .getAllByRole("link")
      .map((link) => link.textContent)
  ).toEqual(["Retrieval augmentation", "Diffusion"])
  // no search text: the request carries no q at all, not an empty one
  expect(listRequests(calls).some(hasQ)).toBe(false)

  await userEvent.type(
    screen.getByRole("searchbox", { name: "Search chat history" }),
    "diff"
  )
  await waitFor(() =>
    expect(
      within(nav).queryByRole("link", { name: "Retrieval augmentation" })
    ).toBeNull()
  )
  expect(
    within(nav).getByRole("link", { name: "Diffusion" })
  ).toBeInTheDocument()
  // the typed text reached the server
  expect(
    listRequests(calls).some(
      (call) => new URL(call.url).searchParams.get("q") === "diff"
    )
  ).toBe(true)
})

test("whitespace-only search text is not sent as a query", async () => {
  const calls = serve(routes())
  renderAt("/p/p1/chat/t1")
  const nav = await screen.findByRole("navigation", { name: "Threads" })
  await within(nav).findAllByRole("link")
  const box = screen.getByRole("searchbox", { name: "Search chat history" })
  await userEvent.type(box, "   ")
  await waitFor(() => expect(box).toHaveValue("   "))
  expect(listRequests(calls).some(hasQ)).toBe(false)
})

test("creates a thread and opens that one, not the one already open", async () => {
  const calls = serve(routes())
  const { router } = renderAt("/p/p1/chat/t1")
  await userEvent.click(
    await screen.findByRole("button", { name: "New thread" })
  )
  await waitFor(() =>
    expect(router.state.location.pathname).toBe("/p/p1/chat/t3")
  )
  expect(
    calls.filter(
      (call) =>
        call.method === "POST" &&
        new URL(call.url).pathname === "/api/v1/projects/p1/threads"
    )
  ).toHaveLength(1)
  const nav = await screen.findByRole("navigation", { name: "Threads" })
  expect(
    await within(nav).findByRole("link", { name: "Untitled thread" })
  ).toBeInTheDocument() // the list refreshed
})

test("an empty list says so", async () => {
  serve({
    ...routes(),
    "GET /api/v1/projects/p1/threads": () => Response.json([]),
  })
  renderAt("/p/p1/chat/t1")
  expect(await screen.findByText("No threads match.")).toBeInTheDocument()
})

test("a failed thread list does not claim there are none", async () => {
  serve({
    ...routes(),
    "GET /api/v1/projects/p1/threads": () =>
      Response.json({ detail: "nope" }, { status: 500 }),
  })
  renderAt("/p/p1/chat/t1")
  expect(
    await screen.findByText(/could not load your threads/i)
  ).toBeInTheDocument()
  expect(screen.queryByText("No threads match.")).not.toBeInTheDocument()
})

test("a pending thread list does not claim there are none", async () => {
  serve({
    ...routes(),
    "GET /api/v1/projects/p1/threads": () => new Promise<Response>(() => {}),
  })
  renderAt("/p/p1/chat/t1")
  expect(await screen.findByText(/loading threads/i)).toBeInTheDocument()
  expect(screen.queryByText("No threads match.")).not.toBeInTheDocument()
})
