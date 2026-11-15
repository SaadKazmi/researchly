import { screen, within } from "@testing-library/react"
import { afterEach, expect, test, vi } from "vitest"

import { healthy, renderAt, serve } from "./render"

afterEach(() => {
  vi.unstubAllGlobals()
})

test("shows the navigation, a connected status and the version", async () => {
  serve({
    "/health": healthy,
    "/api/v1/system": () => Response.json({ version: "1.2.3" }),
  })
  renderAt("/")
  expect(
    await screen.findByRole("heading", { name: "Projects" })
  ).toBeInTheDocument()
  const nav = screen.getByRole("navigation", { name: "Main" })
  for (const name of ["Projects", "Memory", "Usage", "Settings"]) {
    expect(within(nav).getByRole("link", { name })).toBeInTheDocument()
  }
  expect(await screen.findByText("Connected")).toBeInTheDocument()
  expect(await screen.findByText("v1.2.3")).toBeInTheDocument()
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
})

test("asks for the login link when the API answers 401", async () => {
  serve({
    "/health": healthy,
    "/api/v1/system": () =>
      Response.json({ detail: "missing or invalid token" }, { status: 401 }),
  })
  renderAt("/")
  expect(await screen.findByRole("alert")).toHaveTextContent("researchly start")
})

test("shows disconnected when the app is unreachable", async () => {
  serve({})
  renderAt("/")
  expect(await screen.findByText("Disconnected")).toBeInTheDocument()
})

// the chat route is no longer a placeholder heading; thread.test.tsx covers it
test.each([
  ["/p/42/library", "Library"],
  ["/settings/providers", "Settings"],
  ["/onboarding", "Welcome"],
])("renders the %s placeholder", async (path, heading) => {
  serve({
    "/health": healthy,
    "/api/v1/system": () => Response.json({ version: "0.0.0" }),
  })
  renderAt(path)
  expect(
    await screen.findByRole("heading", { level: 1, name: heading })
  ).toBeInTheDocument()
})
