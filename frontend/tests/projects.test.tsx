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
  const created = { ...project, id: "p2", name: "Diffusion UI" }
  // the POST is what makes the new project appear, so the list has to change behind it:
  // a stub that always returns the same array cannot tell a refresh from no refresh
  let listed = [project]
  const calls = serve({
    "/health": healthy,
    "/api/v1/system": version,
    "GET /api/v1/projects": () => Response.json(listed),
    "POST /api/v1/projects": () => {
      listed = [project, created]
      return Response.json(created, { status: 201 })
    },
  })
  renderAt("/")
  expect(
    await screen.findByRole("link", { name: "Retrieval" })
  ).toBeInTheDocument()
  const input = screen.getByLabelText("Project name")
  await userEvent.type(input, "  Diffusion UI  ") // padded: the name is sent trimmed
  await userEvent.click(screen.getByRole("button", { name: "Create project" }))
  const posted = calls.find((call) => call.method === "POST")
  expect(await posted!.clone().json()).toEqual({ name: "Diffusion UI" })
  expect(
    await screen.findByRole("link", { name: "Diffusion UI" })
  ).toBeInTheDocument() // the list refreshed
  expect(input).toHaveValue("") // and the form is ready for the next one
})

test("a failed project list does not claim there are none", async () => {
  serve({
    "/health": healthy,
    "/api/v1/system": version,
    "GET /api/v1/projects": () =>
      Response.json({ detail: "nope" }, { status: 500 }),
  })
  renderAt("/")
  expect(
    await screen.findByText(/could not load your projects/i)
  ).toBeInTheDocument()
  expect(screen.queryByText(/no projects yet/i)).not.toBeInTheDocument()
})

test("opening a project without threads creates one and goes to its chat", async () => {
  const calls = serve({
    "/health": healthy,
    "/api/v1/system": version,
    "GET /api/v1/projects": () => Response.json([project]),
    "GET /api/v1/projects/p1/threads": () => Response.json([]),
    "POST /api/v1/projects/p1/threads": () =>
      Response.json(thread, { status: 201 }),
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
  })
  const { router } = renderAt("/")
  await userEvent.click(await screen.findByRole("link", { name: "Retrieval" }))
  await waitFor(() =>
    expect(router.state.location.pathname).toBe("/p/p1/chat/t2")
  )
  expect(calls.some((call) => call.method === "POST")).toBe(false)
})
