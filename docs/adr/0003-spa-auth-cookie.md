# ADR 0003: SPA authentication through a one-time login link and an HttpOnly cookie

- Status: accepted
- Date: 2026-11-02
- Spec sections: §4.3 (amended), §16.1, §17, §18, §19.5

## Context
Spec §4.3 required the per-install token "in a header". The browser SPA cannot meet that for every request it makes:
- the first navigation to `/` cannot carry a header;
- neither can `<img>` figures, pdf.js documents, `<audio>` overviews, `<a download>` exports, `EventSource` and the WebSocket handshake.

A header-only design would need a blob-fetch workaround for each of these. It would also keep the permanent token in JavaScript-readable storage, where an XSS in LLM-rendered content (§19.5) could exfiltrate it. Research: `docs/research/2026-11-02-step1d-frontend-shell.md` §2.

The options compared were:
- (a) `#token=` → Web Storage → header;
- (b) `?token=` → HttpOnly cookie;
- (c) the token injected into `index.html`.

The two closest products, local single-user servers opened from a CLI, both use (b): Jupyter Server (`?token=` → cookie) and VS Code `serve-web` (`?tkn=` → cookie, then a redirect that strips it).

## Decision
Option (b), hardened:
- **Login link.** `researchly start` prints `http://127.0.0.1:<port>/?token=<t>`.
  - A `GET` to any path outside `/api` and `/mcp` that carries `?token=` is checked with `hmac.compare_digest`.
  - On success it answers `303` to the same path without `token`, setting `researchly_token=<t>; HttpOnly; SameSite=Strict; Path=/; Max-Age=34560000`. There is no `Domain` (host-only) and no `Secure` (plain http on loopback). The response also carries `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.
  - The redirect target is always same-origin: leading slashes are collapsed and the location is percent-quoted.
- **Protected requests** (everything under `/api` and `/mcp`, and every non-GET/HEAD request) accept either:
  - the `x-researchly-token` header (CLI, MCP clients, tests), or
  - the cookie, only when `Sec-Fetch-Site` is `same-origin` or `none` (a typed or bookmarked URL).
- **Public:** GET/HEAD outside `/api` and `/mcp`, which covers the SPA's HTML, its hashed assets and client-side routes. The OpenAPI schema and docs move to `/api/openapi.json` and `/api/docs`, so they stay protected.
- **Kept on every request, public or not:** the Host allowlist and the same-origin `Origin` check.
- **Status codes:** token failures answer `401`; Host/Origin failures stay `403`; a WebSocket closes with `1008`.
- **Logs:** uvicorn logs the query string, so the `uvicorn.access` and `uvicorn.error` loggers get redaction filters. A test pins `token=[REDACTED]` in the access log, and the E2E smoke reads the real `app.log`.
- **The SPA holds no token code.** openapi-fetch's same-origin requests carry the cookie. On `401` the shell tells the user to open the link printed by `researchly start`.

## Consequences
- Every browser feature (images, pdf.js, audio, downloads, SSE, WebSocket) authenticates with no extra code. XSS can act while the page is open, but cannot read or exfiltrate the token.
- A bookmark of `http://127.0.0.1:<port>/` keeps working across restarts, because the token is stable per install.
- **Port isolation (accepted risk).** Cookies are not isolated by port (RFC 6265 §8.5), so another server on 127.0.0.1 that the user visits receives the cookie.
  - Requiring `Sec-Fetch-Site: same-origin | none` stops pages on other ports from *using* it.
  - A hostile local server could still *harvest* it. That needs code already running as the user, which the token cannot defend against anyway.
  - It could also plant a decoy `researchly_token` cookie (for example with `Path=/api`, which browsers send first). The guard accepts a request when *any* `researchly_token` value is valid, so a decoy cannot lock the user out.
  - Jupyter and VS Code serve-web carry the same exposure.
- **Token exposure.** The token appears in terminal output and in the browser's history (the first URL), both readable by the same OS user only. It never appears in logs.
- **Browser support.** Browsers without Fetch Metadata (Safari < 16.4) fall back to the 401 notice. Non-browser clients use the header.
- **Dev server.** The Vite dev server on `:5173` proxies `/api` and `/health`. The host-only cookie set by `:8765` is also sent to `:5173`, and the proxy rewrites `Origin` to the app's.

## Evidence
- Research digest §2.2 (threat table), §2.3 (prior art: Jupyter Server `auth/identity.py`, VS Code `webClientServer.ts`), §2.4.
- Fetch Metadata applies to potentially trustworthy URLs, and loopback qualifies: https://w3c.github.io/webappsec-fetch-metadata/
- Tests: `backend/tests/security/test_localhost_guard.py`; E2E `e2e/specs/auth.setup.ts` and `smoke.spec.ts`.
