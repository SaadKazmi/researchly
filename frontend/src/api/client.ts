import createClient from "openapi-fetch"

import type { paths } from "./schema"

// Same-origin calls carry the HttpOnly login cookie (ADR 0003): the SPA never handles the token.
// fetch is looked up per call so tests can stub it.
export const api = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => fetch(request),
})

export class ApiError extends Error {
  status: number

  constructor(status: number) {
    super(`HTTP ${status}`)
    this.status = status
  }
}
