import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router"
import { render } from "@testing-library/react"
import { vi } from "vitest"

import "@/i18n"
import { createAppRouter } from "@/router"

export type Handler = (request: Request) => Response | Promise<Response>

/**
 * Route stubbed fetch by "METHOD /path"; anything unrouted fails like an unreachable app.
 * Both call shapes are normalized to a Request: openapi-fetch passes one, our agent passes (url, init).
 */
export function serve(routes: Record<string, Handler>) {
  const calls: Request[] = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string | URL, init?: RequestInit) => {
      const request =
        input instanceof Request ? input : new Request(String(input), init)
      calls.push(request)
      const { pathname } = new URL(request.url)
      const handler =
        routes[`${request.method} ${pathname}`] ?? routes[pathname]
      if (!handler) throw new TypeError("Failed to fetch")
      return handler(request)
    })
  )
  return calls
}

export function renderAt(path: string) {
  const router = createAppRouter(
    createMemoryHistory({ initialEntries: [path] })
  )
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
}

export const healthy = () => Response.json({ status: "ok" })
export const version = () => Response.json({ version: "1.2.3" })
