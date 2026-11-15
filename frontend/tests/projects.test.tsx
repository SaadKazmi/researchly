import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, test, vi } from "vitest"

import { healthy, renderAt, serve, version } from "./render"

const project = {
  id: "p1",
  name: "Retrieval",
  description: null,
  created_at: "2026-11-13T10:00:00Z",
}
const thread = {
  id: "t1",
  project_id: "p1",
  title: null,
  created_at: "2026-11-13T10:00:00Z",
}

afterEach(() => vi.unstubAllGlobals())

test("lists the projects and creates one", async () => {
  const created = { ...project, id: "p2", name: "Diffusion" }
  const calls = serve({
    "/health": healthy,
    "/api/v1/system": version,
    "GET /api/v1/projects": () => Response.json([project]),
    "POST /api/v1/projects": () => Response.json(created, { status: 201 }),
  })
  renderAt("/")
  expect(
    await screen.findByRole("link", { name: "Retrieval" })
  ).toBeInTheDocument()
  await userEvent.type(screen.getByLabelText("Project name"), "Diffusion")
  await userEvent.click(screen.getByRole("button", { name: "Create project" }))
  const posted = calls.find((call) => call.method === "POST")
  expect(await posted!.clone().json()).toEqual({ name: "Diffusion" })
})

test("opening a project without threads creates one and goes to its chat", async () => {
  const calls = serve({
    "/health": healthy,
    "/api/v1/system": version,
    "GET /api/v1/projects": () => Response.json([project]),
    "GET /api/v1/projects/p1/threads": () => Response.json([]),
    "POST /api/v1/projects/p1/threads": () =>
      Response.json(thread, { status: 201 }),
    "GET /api/v1/threads/t1": () => Response.json({ ...thread, messages: [] }),
    "GET /api/v1/efforts": () => Response.json([]),
  })
  const { router } = renderAt("/")
  await userEvent.click(await screen.findByRole("link", { name: "Retrieval" }))
  await waitFor(() =>
    expect(router.state.location.pathname).toBe("/p/p1/chat/t1")
  )
  expect(
    calls.some(
      (call) =>
        call.url.endsWith("/api/v1/projects/p1/threads") &&
        call.method === "POST"
    )
  ).toBe(true)
})

// The API lists threads newest first (threads.py orders by created_at desc), so opening a
// project must take the first one and create nothing. The chat view arrives in Task 8, so both
// tests assert the route the navigation landed on rather than what that route renders.
test("opening a project with threads reuses the newest one", async () => {
  const newest = { ...thread, id: "t2", created_at: "2026-11-13T12:00:00Z" }
  const calls = serve({
    "/health": healthy,
    "/api/v1/system": version,
    "GET /api/v1/projects": () => Response.json([project]),
    "GET /api/v1/projects/p1/threads": () => Response.json([newest, thread]),
    "GET /api/v1/threads/t2": () => Response.json({ ...newest, messages: [] }),
    "GET /api/v1/efforts": () => Response.json([]),
  })
  const { router } = renderAt("/")
  await userEvent.click(await screen.findByRole("link", { name: "Retrieval" }))
  await waitFor(() =>
    expect(router.state.location.pathname).toBe("/p/p1/chat/t2")
  )
  expect(calls.some((call) => call.method === "POST")).toBe(false)
})
