# Step 1d research digest: frontend shell, SPA auth, first E2E (2026-11-02)

Verified live on 2026-11-02 against the npm registry JSON (`registry.npmjs.org/<pkg>` for `dist-tags`, `time` and `peerDependencies`), PyPI JSON, the GitHub API, Docker Hub, nodejs.org `dist/index.json`, vendor docs and source. I also ran two things locally in the scratchpad: OpenAPI generation from the real `create_app()`, and `openapi-typescript` against TypeScript 6 and 7. "UNVERIFIED" marks anything I did not confirm.

The 7-day cooldown cutoff (spec §19.7, `minimumReleaseAge`) is **2026-10-26**. Version tables list the latest release and the newest release that clears the cutoff.

## 1. Versions and compatibility

### 1.1 Toolchain choice: TypeScript 6, not 7
- **TypeScript 7.0.2** (2026-07-08) is `latest`. It is the Go-native compiler, and its package root export is now `./lib/version.cjs`. The classic compiler API (`ts.factory` …) is gone, and only `typescript/unstable/*` subpaths remain. Source: `registry.npmjs.org/typescript/7.0.2` (`exports`).
  - **openapi-typescript 7.13.0 crashes on TS 7.** I ran it locally and got `TypeError: Cannot read properties of undefined (reading 'createKeywordTypeNode')`. Its peer range is `typescript ^5.x`. TS 6 support is still an open PR (openapi-ts/openapi-typescript#2723 and #2774). A compiler-free TS 6/7 generator is proposed as a future major in #2872.
  - **typescript-eslint 8.72.1** declares the peer range `typescript >=4.8.4 <6.1.0`, so TS 7 is excluded.
- **TypeScript 6.0.3** (2026-04-16) is the last JS-based compiler. **openapi-typescript 7.13.0 works with it.** I verified this locally: generation succeeds, `tsc --strict` on the output passes, and the only problem is a peer-range warning.
- **The two official starters disagree:**
  - The `create-vite` `template-react-ts` now ships `typescript ~7.0.2` with **oxlint 1.90** instead of ESLint. Source: https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts
  - The **shadcn `vite-app` template** (what `shadcn init -t vite` scaffolds) ships `typescript ~6.0.2`, `eslint ^10`, `typescript-eslint ^8`, `eslint-plugin-react-hooks ^7.1.1`, `eslint-plugin-react-refresh ^0.5.7`, `prettier ^3.9.10` and `prettier-plugin-tailwindcss ^0.8.1`. It was last bumped 2026-10-24. Source: https://github.com/shadcn-ui/ui/tree/main/templates/vite-app
- **Decision:** use the shadcn template's stack (TS 6.0.3 + ESLint 10). It matches spec §22 ("eslint, tsc") and §17 (openapi-typescript), and it is the only combination where every spec'd tool works today. Move to TS 7 (with oxlint or a TS 7-ready typescript-eslint) later, through an ADR, once openapi-typescript ships TS 7 support.
- **"ESLint 9"** in the brief is out of date. ESLint **10** is `latest` (10.0.0 shipped 2026-02-06), and every plugin below accepts `^10`.

### 1.2 Package table

| Package | Latest (date) | Newest that clears the cooldown | License | Peers / engines that matter |
|---|---|---|---|---|
| react, react-dom | 19.3.1 (10-14) | 19.3.1 | MIT | — |
| vite | 8.3.5 (10-21) | 8.3.5 | MIT | node `^20.19 \|\| >=22.12` (Rolldown-based v8) |
| @vitejs/plugin-react | 6.2.1 (11-02) | 6.1.2 | MIT | vite `^8` |
| typescript | 7.0.2 (07-08) | **6.0.3** (chosen, §1.1) | Apache-2.0 | — |
| @tanstack/react-router | 1.170.46 (10-22) | 1.170.46 | MIT | react >=18 |
| @tanstack/router-plugin | 1.168.48 (10-22) | 1.168.48 | MIT | vite >=5 … >=8 |
| @tanstack/react-query | 5.105.1 (11-01) | 5.104.5 | MIT | react ^18 \|\| ^19 |
| tailwindcss, @tailwindcss/vite | 4.3.3 (07-16) | 4.3.3 | MIT | vite `^5.2 … ^8` |
| shadcn (CLI) | 4.23.0 (10-27) | 4.22.1 | MIT | node >=20.18.1 |
| @base-ui/react | 1.9.0 (10-09) | 1.9.0 | MIT | react ^17–19. The old `@base-ui-components/react` name is stuck at 1.0.0-rc.0, so do not use it |
| class-variance-authority / clsx / tailwind-merge / tw-animate-css / lucide-react | 0.7.1 / 2.1.1 / 3.7.0 / 1.4.0 / 1.66.0 | same | Apache-2.0 / MIT / MIT / MIT / ISC | shadcn component deps |
| i18next | 26.7.1 (11-01) | 26.5.11 | MIT | typescript ^5 \|\| ^6 \|\| ^7 |
| react-i18next | 17.1.11 (11-01) | 17.1.9 | MIT | i18next >=26.2.0 |
| vitest | 5.0.5 (10-15) | 5.0.5 | MIT | node `^22.12 \|\| ^24 \|\| >=26`, vite ^6.4–^8 |
| @testing-library/react / dom / jest-dom / user-event | 16.3.3 / 10.4.2 / 7.0.1 / 14.6.8 | same | MIT | jest-dom 7 needs node >=22 |
| jsdom | 30.1.3 (10-15) | 30.1.3 | MIT | **node `^22.22.2 \|\| ^24.15.0 \|\| >=26`** |
| happy-dom | 21.0.4 (10-20) | 21.0.4 | MIT | node >=20 |
| eslint | 10.14.0 (10-30) | 10.13.0 | MIT | node `^20.19 \|\| ^22.13 \|\| >=24` |
| @eslint/js / globals | 10.0.1 / 17.14.0 | same | MIT | — |
| typescript-eslint | 8.72.1 (10-26) | 8.72.0 | MIT | eslint ^8.57–^10, **typescript <6.1.0** |
| eslint-plugin-react-hooks | 7.1.1 (04-17) | 7.1.1 | MIT | eslint up to ^10; flat config `reactHooks.configs.flat.recommended` |
| eslint-plugin-react-refresh | 0.5.7 | 0.5.7 | MIT | eslint ^9 \|\| ^10 |
| prettier / prettier-plugin-tailwindcss | 3.9.10 (10-10) / 0.8.1 | same | MIT | — |
| pnpm | 12.17.0 (10-30); `latest` tag 12.16.1 | **12.15.0** (10-21) | MIT | — |
| openapi-typescript / openapi-fetch | 7.13.0 / 0.17.0 (02-11) | same | MIT | typescript ^5.x (works on 6.0.3, §1.1) |
| @playwright/test | 1.64.1 (10-14) | 1.64.1 | Apache-2.0 | node >=20 |
| @axe-core/playwright | 4.14.0 (10-15) | 4.14.0 | **MPL-2.0** | node >=22.12 |

Sources: `registry.npmjs.org/<pkg>` (`dist-tags`, `time`, `latest`). With `minimumReleaseAge: 10080` set, pnpm resolves to the cooldown column by itself, so the plan only needs ranges plus the lockfile.

### 1.3 Node, pnpm and corepack
- **Node release status** (https://raw.githubusercontent.com/nodejs/Release/main/schedule.json, `nodejs.org/dist/index.json`):
  - **Node 26 "Lithium" became Active LTS on 2026-10-28** (26.13.0) and is supported to 2029-04-30.
  - **Node 24 "Krypton"** moved to Maintenance LTS on 2026-10-20 (24.22.2) and is supported to 2028-04-30.
  - **Recommendation: pin Node 24 (≥24.15)** for now. It has the best ecosystem coverage, and Playwright's Docker images moved to Node 24 in 1.57. Bump to 26 once it has a few weeks in LTS.
  - The dev box has **Node 24.14.0, which is below jsdom 30's `^24.15.0` floor**, so it needs a minor update.
  - Node is a dev/CI-only tool. End users get the prebuilt SPA inside the app (§3), which keeps D14 / zero-cost intact.
- **Corepack:** the Node TSC stopped distributing Corepack from Node 25 on. Node 24 still bundles it as experimental (the dev box has 0.34.6). Source: https://socket.dev/blog/node-js-tsc-votes-to-stop-distributing-corepack. Don't depend on Corepack.
  - pnpm itself honours `packageManager` / `devEngines.packageManager`. `pmOnFail` defaults to `download`, so pnpm switches to the declared version. Source: https://github.com/pnpm/pnpm.io/blob/main/docs/settings/cli.md (`pmOnFail`).
- **pnpm 12** settings live in **`pnpm-workspace.yaml`**. `.npmrc` now holds auth, registry, TLS and proxy settings only. Defaults (source: `docs/settings/dependency-resolution.md`, `build.md`, `peer-dependencies.md` in pnpm/pnpm.io):
  - **`minimumReleaseAge`** defaults to 1440 min, and that built-in default is non-strict. **Setting it explicitly turns `minimumReleaseAgeStrict` on.** Spec §19.7 needs `minimumReleaseAge: 10080`.
  - **`blockExoticSubdeps`** defaults to `true`.
  - **`strictDepBuilds`** defaults to `true`, so unreviewed postinstall scripts fail the install. Allowlist them with `allowBuilds:`.
  - **`trustPolicy`** defaults to `off`. Set `no-downgrade`; it also checks the pinned pnpm version.
  - **`strictPeerDependencies`** defaults to `false`.
  - `peerDependencyRules` is deprecated in favour of `overrides` with `:peer`, though it still works.

### 1.4 shadcn/ui on Base UI
- **Base UI is stable and the shadcn default.** The 2026-07 changelog says: "Base UI is the default component library in shadcn/ui", "Base UI is stable", and "Radix is not being deprecated". Source: https://ui.shadcn.com/docs/changelog/2026-07-base-ui-default
- **CLI** (`create` is an alias of `init`). Source: https://ui.shadcn.com/docs/cli
  - `-t, --template <next|vite|start|react-router|laravel|astro>`
  - `-b, --base <base|radix|aria>`
  - `-p, --preset`, `--rtl`, `--monorepo`
- **Exact command:** `pnpm dlx shadcn@4.22.1 init -t vite -b base`. Base is already the default, but pass `-b base` so the run is deterministic in scripts. Then `pnpm dlx shadcn@4.22.1 add button card badge tooltip …`.
- **`components.json`:** set `"tailwind.config": ""` for Tailwind v4. The docs page does not show how the chosen base is recorded (UNVERIFIED), so commit whatever init writes.
- **API note:** Base UI components use a `render` prop where Radix used `asChild`.

### 1.5 TanStack Router: file-based routing
- The TanStack docs say **"Code-based routing is not recommended for most applications"** and recommend file-based routing.
- **Vite plugin:** `tanstackRouter({ target: 'react', autoCodeSplitting: true })` from `@tanstack/router-plugin/vite`. It must come **before** `react()`.
- **Commit `routeTree.gen.ts`.** The router FAQ says it "is essentially part of your application's runtime, not a build artifact". That also lets `tsc` run in CI without a Vite build first.
- Source: https://github.com/tanstack/router/blob/main/docs/router/faq.md, `routing/code-based-routing.md`
- **§18 route skeleton as files under `src/routes/`:**
  - `__root.tsx` (shell + status indicator) and `index.tsx` (`/` projects)
  - `p/$id/chat/$thread.tsx`, `p/$id/library.tsx`, `p/$id/graph.tsx`, `p/$id/docs/$doc.tsx`, `p/$id/studio.tsx`, `p/$id/review.tsx`
  - `memory.tsx`, `usage.tsx`, `settings/$.tsx` (splat for `/settings/*`), `onboarding.tsx`
  - Eleven placeholders is small, but file-based still wins: typed links, auto code-splitting and the generator cost nothing.

### 1.6 Unit test DOM: jsdom
- Vitest's own docs say happy-dom is "considered to be faster than jsdom, but lacks some API" (https://github.com/vitest-dev/vitest/blob/main/docs/guide/environment.md). I found **no independent benchmark**; the speed claims come from the vendors.
- Pick **jsdom 30** for API completeness with Base UI (focus, pointer and ARIA behaviour). Use `@testing-library/jest-dom/vitest` in a setup file.
- Vitest 5 browser mode (`@vitest/browser-playwright`) is the escape hatch for components that need real layout.

## 2. SPA token delivery

### 2.1 Constraints from the current guard
`LocalhostGuard` (`backend/src/researchly/security/localhost_guard.py`) currently:
- checks `Host ∈ {localhost, 127.0.0.1, ::1}`;
- rejects an `Origin` whose netloc differs from `Host`;
- requires `x-researchly-token` (compared with `hmac.compare_digest`) on **every path except `/health`**.

A browser navigation to `/` cannot add a header, so the SPA's own HTML and assets need a public path. More importantly, **many future requests cannot carry custom headers at all**:
- `<img src=/api/...>` (figures), pdf.js document URLs, `<audio>` (F7 audio overview);
- `<a download>` exports (F6);
- `EventSource` and the WebSocket handshake.

That rules out header-only auth for the browser unless every one of those gets a blob-fetch workaround.

### 2.2 Options compared

| Threat / property | (a) `#token=` → Web Storage, header auth | (b) `?token=` → HttpOnly SameSite=Strict cookie (header still accepted) | (c) token injected into `index.html` |
|---|---|---|---|
| CSRF from websites | Strong: a cross-origin custom header needs a CORS preflight, which we never grant | Strong: SameSite=Strict, the existing Origin check, and `Sec-Fetch-Site: same-origin` required for cookie-auth requests (§2.4) | Strong for the HTML itself, because the SOP stops cross-origin reads |
| DNS rebinding | Host allowlist; storage is per-origin, so the rebinding origin has none | Host allowlist; the cookie is host-only, so it is never sent to `evil.com` | Host allowlist blocks `GET /` under the attacker's Host |
| Other local OS users / local processes | Need the token | Need the token | **Fail:** `curl http://127.0.0.1:PORT/` returns the token, so the token adds nothing beyond the Host/Origin checks |
| Other servers on 127.0.0.1 (other ports) | Storage is per origin, which includes the port | **Weak spot:** cookies are not isolated by port (RFC 6265 §8.5), so a server on another port the user browses receives the cookie | n/a |
| Browser extensions | Content scripts can read storage | HttpOnly hides it from content scripts; extensions with the `cookies` permission can still read it | Readable from the DOM |
| XSS (LLM-rendered content, §19.5) | The permanent token can be exfiltrated | Not readable by JS; XSS can only act while the page is open | Exfiltratable |
| Server logs | The fragment never reaches the server | `?token=` reaches uvicorn's access log, because uvicorn logs the path with the query string (`get_path_with_query_string`, uvicorn 0.54 `h11_impl.py`). **Must redact** | n/a |
| Referer | Fragments are never sent in Referer | The login URL answers with an immediate 303 and renders nothing, so there is no Referer source | n/a |
| Browser history | The first URL is recorded; `replaceState` strips the visible URL (whether the history DB keeps the original is UNVERIFIED) | The first URL is recorded (same exposure as (a), same OS user only) | n/a |
| Bookmark / restart (the token is stable per install) | sessionStorage is lost per tab; localStorage survives while the port is unchanged | The cookie survives restarts and a plain `http://127.0.0.1:8765/` bookmark keeps working, even if the port changes | Works |
| SSE / WebSocket / img / pdf / audio / download | Needs a header everywhere. `EventSource` and `WebSocket` cannot set one, so it falls back to a query token or the subprotocol hack | Automatic: the cookie is sent on same-origin fetch, EventSource and the WS handshake. Browsers always send Origin on the WS handshake, so the existing Origin check covers cross-site WebSocket hijacking | Needs a header |
| Code | SPA bootstrap plus middleware in every client | About 25 lines in the guard; **zero token code in the SPA** | Small, but insecure |

Note that Fetch Metadata headers (`Sec-Fetch-Site`) are sent to "potentially trustworthy" URLs, and loopback qualifies. Source: https://w3c.github.io/webappsec-fetch-metadata/ (§3, step 1).

### 2.3 Prior art (d)
- **Jupyter Server:**
  - Accepts `?token=`, `Authorization: token …` or the login form. "A cookie will be set in your browser and you won't need to use the token again." The token is printed in the terminal URL. Source: https://jupyter-server.readthedocs.io/en/latest/operators/security.html
  - The cookie is signed and `httponly` by default (`set_login_cookie`), and `check_host` enforces `local_hostnames` against DNS rebinding. Source: `jupyter_server/auth/identity.py` and `base/handlers.py` on main.
  - The browser is opened through a local redirect file, "passing the token without putting it in a command-line argument". Source: `jupyter_server/templates/browser-open.html`
- **VS Code `serve-web`:**
  - A `?tkn=` query is turned into a `vscode-tkn` cookie (`sameSite: 'lax'`, 1-week maxAge, refreshed on every page load), then a **302 redirect to the same path with `tkn` stripped**.
  - Requests validate either the query or the cookie.
  - Source: `src/vs/server/node/webClientServer.ts`, `serverConnectionToken.ts` (microsoft/vscode main).
- **Ollama:** binds 127.0.0.1:11434 with no auth; browser access is controlled only by the `OLLAMA_ORIGINS` allowlist. Source: https://docs.ollama.com/faq
- **LM Studio:** auth is off by default. The opt-in "Require Authentication" (0.4.0+) uses Bearer API tokens. Source: https://lmstudio.ai/docs/developer/core/authentication
- **Open WebUI:**
  - A multi-user login issues a JWT; the server reads the Bearer header, then a `token` cookie.
  - A security write-up reports the JWT kept in localStorage, not HttpOnly. That source is secondary (UNVERIFIED in current source).
  - Source: https://docs.openwebui.com/getting-started/advanced-topics/hardening.txt
- **Takeaway:** the two products closest to ours (a local, single-user code-execution server opened from a CLI) both use **query token → cookie → redirect that strips the token**.

### 2.4 Recommendation: (b), the Jupyter/VS Code pattern, hardened
1. **Login:** a `GET` to any non-API path with `?token=<t>`:
   - if `hmac.compare_digest` passes, answer `303 See Other` to the same path without `token`, with:
     - `Set-Cookie: researchly_token=<t>; HttpOnly; SameSite=Strict; Path=/; Max-Age=34560000` (400 days, Chrome's cap; the token never rotates, so a shorter cookie adds little);
     - `Cache-Control: no-store`, `Referrer-Policy: no-referrer`;
   - otherwise answer `403`.
   - No `Domain` attribute, so the cookie is host-only. No `Secure`, because this is plain http on loopback; that also rules out `__Host-`.
2. **Auth on protected paths:**
   - Accept `x-researchly-token` (CLI, MCP, API tests) **or** the cookie. The cookie counts only when `Sec-Fetch-Site` is `same-origin`. This blocks same-*site* pages on other 127.0.0.1 ports, which SameSite does not stop.
   - Keep the Host and Origin checks on every path, including the public ones.
3. **Unauthenticated paths:**
   - `/health`;
   - `GET`/`HEAD` on everything that is **not** under `/api/` or `/mcp`: `index.html`, `/assets/*`, top-level files in `dist/` (favicon …), and every SPA fallback route (`/p/...`, `/settings/...` …).
   - Every other method on a non-API path stays rejected (StaticFiles already answers 405).
   - **Move FastAPI's `openapi_url`/`docs_url` under `/api`** (or disable docs in production). Otherwise `/openapi.json` and `/docs` become public under the "not `/api`" rule.
4. **Logs:** attach the existing `RedactingFilter` to `uvicorn.access` and `uvicorn.error`. `create_app` already calls `register_secret(token)`, so the literal token is scrubbed. Add a test asserting the login request's log line shows `[REDACTED]`.
5. **CLI:**
   - `start` prints `http://127.0.0.1:PORT/?token=…`, as Jupyter and `code serve-web` do. Print it on the "Already running" path too.
   - An optional auto-open should use Jupyter's 0600 redirect-file trick, because browser argv is world-readable via `/proc` on Linux.
   - `status`/`doctor` keep printing the token-free URL.
6. **SPA:** no token code. openapi-fetch's default `credentials: 'same-origin'` sends the cookie. On a 401/403 "missing or invalid token", show "Open Researchly from the link printed by `researchly start`". Consider returning **401** for token failures so they can be told apart from host/origin 403s.
7. **Streaming:**
   - AG-UI is a `POST /api/v1/runs/{id}/stream` SSE over `fetch`, so the cookie is sent automatically and the Origin check applies.
   - A future WebSocket is authenticated by the same cookie on the handshake, plus the existing Origin check (WS close 1008 on failure).
   - Non-browser clients send the header.
8. **Spec impact:** §4.3 currently says "required in a header". Amend it to "header, or an HttpOnly cookie obtained through the one-time `?token=` link", and record that in ADR 0003.
9. **Dev server:**
   - Run Vite on `127.0.0.1:5173` with `server.proxy` for `/api` and `/health` → `http://127.0.0.1:<app port>`.
   - The cookie set by `127.0.0.1:8765` *is* sent to `127.0.0.1:5173` (host-only, not port-scoped) and forwarded by the proxy.
   - The proxy must rewrite or strip `Origin`, because Origin `:5173` ≠ the proxied Host. The exact Vite 8 proxy hook (`configure` / `proxyReq`) is UNVERIFIED.

Rejected alternatives:
- **(c)** turns the token into a no-op against local processes.
- **(a)** cannot authenticate `img`, `audio`, pdf.js, downloads, EventSource or WebSocket without workarounds, and it exposes the token to XSS.
- A fragment + `POST /api/session` hybrid would also keep the token out of access logs. That is not needed once the access log is redacted.

## 3. Serving the SPA from FastAPI

### 3.1 Behaviour
Starlette **1.10.0** (2026-10-24, BSD-3); the repo venv has 1.9.0. `StaticFiles(html=True)` serves `index.html` for directories and **`404.html` with status 404** for misses. It has **no SPA fallback** and sets no `Cache-Control`, only ETag/Last-Modified. Verified in `starlette/staticfiles.py` (`get_response`).

### 3.2 Idiomatic approach
A small `StaticFiles` subclass, mounted at `/` **after** all routes. FastAPI is 0.162.0 (2026-10-23, MIT).

```python
class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or path.split(os.sep, 1)[0] in ("api", "mcp"):
                raise  # unknown API paths stay JSON 404s
            response = await super().get_response("index.html", scope)
        immutable = path.startswith("assets" + os.sep)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if immutable else "no-cache"
        return response

app.mount("/", SPAStaticFiles(directory=WEB_DIR, html=True), name="spa")  # last
```

- `/health`, `/api/...` and `/mcp` routes match first, so they are never shadowed.
- `assets/` filenames are content-hashed by Vite, so they can be cached as immutable. `index.html` gets `no-cache`.
- Add security headers on HTML responses: `Content-Security-Policy` (`default-src 'self'`; `style-src 'self' 'unsafe-inline'` is likely needed for component `style` attributes, UNVERIFIED for Base UI), `frame-ancestors 'none'`, `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.
- If `WEB_DIR` is missing (a source checkout without a build), skip the mount and log `frontend not built: run pnpm -C frontend build`. `doctor` can report it.

### 3.3 Where `dist/` lives: one path for every mode
**`backend/src/researchly/web/`** (gitignored package data):
- Set Vite `build.outDir: '../backend/src/researchly/web'` with `emptyOutDir: true`. Vite refuses to empty an outDir outside its root unless told to.
- The app resolves `Path(__file__).parent / "web"`.

| Mode | How `web/` gets there |
|---|---|
| Native, source checkout | The CLI runs the backend editable from the source tree (`cli/pyproject.toml` → `researchly = { path = "../backend", editable = true }`), so `pnpm -C frontend build` before `researchly start` is enough. |
| Docker | `app.Dockerfile` adds a first stage `FROM node:24-trixie-slim AS web` (tags `24.22.x-trixie-slim` and `26.13.0-trixie-slim` exist on Docker Hub). In compose, add `build.additional_contexts: { frontend: ../frontend }` and keep `context: ../backend`. The stage runs `npm i -g pnpm@12.15.0` (no Corepack from Node 25 on), `pnpm install --frozen-lockfile` and `pnpm build`. Use WORKDIR `/repo/frontend` so the relative outDir lands at `/repo/backend/src/researchly/web`. The final stage copies that into `/src/src/researchly/web` **before** `uv sync --no-editable`. Compose `additional_contexts` syntax: https://docs.docker.com/reference/compose-file/build/. Whether a `.dockerignore` at the frontend context's root is honoured for named contexts is UNVERIFIED, so add `frontend/.dockerignore` (`node_modules`, `dist`) and check the context size in the build log. |
| Release wheel | Hatch drops VCS-ignored files: it uses "the first `.gitignore` … found in your project's root directory or parent directories". So add `artifacts = ["src/researchly/web/**"]` to `[tool.hatch.build.targets.wheel]`. Source: https://hatch.pypa.io/latest/config/build/ |

Users never need Node.

## 4. OpenAPI client generation

| Tool | Version (date) | License | Output | Fit |
|---|---|---|---|---|
| **openapi-typescript + openapi-fetch** | 7.13.0 / 0.17.0 (2026-02-11) | MIT | Types only, plus a thin fetch wrapper | Mandated by spec §17. Smallest output |
| @hey-api/openapi-ts | 0.99.0 (2026-06-22) | MIT | SDK + plugins (TanStack Query, zod) | Fallback if openapi-typescript stalls. Peer ts `>=5.5.3 \|\| >=6.0.0`, node >=22.18 |
| orval | 8.46.0 (2026-10-30) | MIT | Hooks/clients | Most generated code, so heavier than we need |

openapi-react-query 0.5.4 (MIT) exists as the official TanStack glue. Skip it for 1d; a plain `queryFn` around openapi-fetch is enough.

**Generation without a server** (verified locally):
1. Dump the schema: `uv run --directory backend python -c "import json,sys; from researchly.app import create_app; json.dump(create_app(token='x').openapi(), sys.stdout, indent=2, sort_keys=True)"`.
   - Passing `token=` avoids the keychain. Constructing the app touches no DB.
   - The output is OpenAPI 3.1.0 with `/health`.
   - Put this in a tiny `python -m researchly.openapi` module rather than quoting it in package.json, because shell quoting differs on Windows.
2. Generate: `openapi-typescript openapi.json -o src/api/schema.d.ts`.

**Drift check:**
- `openapi-typescript openapi.json -o src/api/schema.d.ts --check` exits **0** when current and **1** ("Generated types are not up-to-date!") when stale. I verified both with 7.13.0 on TS 6.0.3.
- The CI frontend job: dump the JSON with the backend venv → `pnpm exec openapi-typescript … --check`.
- Commit only `schema.d.ts`. The JSON is a temp file (or commit it too if a reviewable API diff is wanted).
- Silence the TS 6 peer warning with `peerDependencyRules.allowedVersions: { "openapi-typescript>typescript": "6" }` in `pnpm-workspace.yaml`. `strictPeerDependencies` is false by default, so it is only noise.

**Client:** `createClient<paths>({ baseUrl: '/' })` needs no auth middleware with the cookie (§2.4). For non-browser use, `client.use({ onRequest({ request }) { request.headers.set('x-researchly-token', t) } })`. Source: https://github.com/openapi-ts/openapi-typescript/blob/main/docs/openapi-fetch/middleware-auth.md

## 5. Playwright

### 5.1 Version, browsers and assertions
- **Version:** @playwright/test **1.64.1** (2026-10-14, Apache-2.0), with Chromium 156.0.8078.4, Firefox 157.0 and WebKit 27.2 (`playwright-core/browsers.json`).
- **Browsers for smoke:** Chromium only. Install with `pnpm exec playwright install --with-deps --only-shell chromium`. `--only-shell` exists in 1.64.1's CLI and installs just `chromium-headless-shell`, which headless mode uses since 1.57.
- **CI guidance** (https://playwright.dev/docs/ci): "Caching browser binaries is not recommended"; `workers: 1` on CI.
- **Aria snapshots:** `toMatchAriaSnapshot` is current. 1.60 added page-level `expect(page).toMatchAriaSnapshot()`, 1.61 added `ariaSnapshotJSON()`, and 1.64 added `page.getByRef()`. Nothing is deprecated. Source: https://playwright.dev/docs/release-notes
- **axe:** `@axe-core/playwright` 4.14.0, `new AxeBuilder({ page }).analyze()`. It is MPL-2.0 (§7).

### 5.2 Starting the stack: globalSetup, not webServer
- `webServer` expects a **long-running** command. Playwright waits on `url` and at the end SIGKILLs the process group by default. `gracefulShutdown` signals are ignored on Windows. Source: https://playwright.dev/docs/test-webserver
- `researchly start` returns after detaching, so `webServer` would need a custom blocking wrapper that traps signals and calls `stop`. That wrapper would also not run `stop` on Windows.
- **Use `globalSetup` that returns a teardown function.** The docs say "let `globalSetup` return a function that will be used as a global teardown", and the runner (`runner/tasks.ts`) calls a returned function at teardown.
  - **Setup:** run `researchly config set runtime native`, then `researchly start`. Parse the login URL from stdout (`/http:\/\/127\.0\.0\.1:\d+\/\?token=\w+/`) and put it in `process.env`.
  - **Teardown:** `researchly stop`.
  - Invoke the CLI via `uv run --directory ../cli researchly …`.
- **Pin native explicitly.** GitHub's ubuntu runners have Docker running, so `auto` would pick Docker mode. The existing `docker` CI job proves an engine is present.
- **Auth:** the documented Playwright auth pattern. A `setup` project (`auth.setup.ts`) visits the login URL and saves `storageState` (the HttpOnly cookie is captured). The `chromium` project depends on it and uses that state.
- **Env timing:** "environment variables which are set in globalSetup are only available inside test()". So provide `baseURL` through a fixture override (`base.extend({ baseURL: async ({}, use) => use(process.env.RESEARCHLY_E2E_URL) })`), not from the config object.

### 5.3 Isolation
Set these env vars for both `start` and `stop`:
- **`RESEARCHLY_DATA_DIR` and `RESEARCHLY_LOG_DIR`** under the gitignored `e2e/.artifacts/`.
  - Use a **fixed** path, not a fresh tmpdir. The pg0 instance name is a hash of the pgdata path, so a fixed dir reuses one instance: warm start is 0.5 s vs 6.6 s cold (step 1c measurements).
  - It also avoids piling up `~/.pg0/instances/*`, each of which holds a superuser password (ADR 0002).
- **`PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring`.** keyring honours this env var (`keyring/core.py` reads it), and `keychain._backend()` then switches to `EncryptedFileKeyring` inside the data dir. Secrets become per-data-dir on every OS, and the developer's real keychain is untouched locally.
  - Without it, macOS and Windows runners use the real login Keychain / Credential Locker.
  - On ubuntu runners I expect no D-Bus session bus, so keyring already falls back (UNVERIFIED). The env var makes it deterministic either way.

### 5.4 Package layout
- `e2e/` gets its **own `package.json` + `pnpm-lock.yaml` + `pnpm-workspace.yaml`** (settings only, `packages: []`, as in the shadcn template). Do the same for `frontend/`.
- This matches spec §5 (`frontend/pnpm-lock.yaml`) and the AGENTS.md commands (`pnpm -C frontend …`, `pnpm -C e2e exec playwright test`).
- A root workspace would save one install but moves the lockfile to the root and adds a root `package.json`. Not worth it for two packages that share no code.

## 6. CI

### 6.1 Actions (latest release → commit SHA, via the GitHub API)
- `actions/setup-node` **v7.1.0** → `949feb2413d6458794dcd2491c4babbbce0c15c1`.
  - v7 breaking change: the dummy `NODE_AUTH_TOKEN` was removed (irrelevant here).
  - `node-version-file: frontend/package.json` reads `devEngines.runtime`, then `engines.node`.
  - `cache: pnpm` needs pnpm installed first.
- `pnpm/action-setup` **v6.1.0** → `ea17c68df8912ef543352723c149a84f56e3d413`. It supports pnpm ≤12. With no root `package.json`, pass `package_json_file: frontend/package.json` so it reads `packageManager`.
  - The newer all-in-one `pnpm/setup` v3.1.2 (`fa36a5ef7dcdefc3a7cb25655c122e00cf6e636e`, MIT, created 2026-05) also installs Node. It is younger, so keep the well-trodden pair.
- `actions/upload-artifact` **v7.0.2** → `cf430e030ddbb5b0abf93d22962f4752f3646cd9`.
- Existing pins stay: `actions/checkout@3d3c42e…` v7.0.1 and `astral-sh/setup-uv@07a047e…` v10.3.2 with uv 0.12.10.

### 6.2 Jobs
- **`frontend` (ubuntu-24.04):**
  1. checkout → setup-uv → pnpm/action-setup → setup-node (`cache: pnpm`, `cache-dependency-path: frontend/pnpm-lock.yaml`)
  2. `pnpm -C frontend install --frozen-lockfile`
  3. `lint`, `exec tsc --noEmit`, `test`
  4. OpenAPI drift check (§4)
  5. `build`
- **`e2e` (ubuntu-24.04, native, gating):**
  1. The same setup, plus `cache-dependency-path` covering both lockfiles and uv caching on `cli/uv.lock`.
  2. `pnpm -C frontend install --frozen-lockfile && pnpm -C frontend build` (into `backend/src/researchly/web`).
  3. `uv sync --locked` in `cli/`.
  4. `pnpm -C e2e install --frozen-lockfile` and `pnpm -C e2e exec playwright install --with-deps --only-shell chromium`.
  5. `pnpm -C e2e exec playwright test --grep @smoke`.
  6. On failure, upload `e2e/playwright-report`, `e2e/test-results` and `e2e/.artifacts/logs`.
  - About 25 min timeout.
- **Windows/macOS E2E: later.** The CLI job already exercises native start on all five targets. What E2E adds (SPA serving, cookie auth, browser) is OS-independent apart from `os.sep` in the static-file code. Add `windows-2025` and `macos-15` as non-gating legs of the same job when step 9 (native parity) lands; spec §22 wants them eventually. Add the Docker-mode E2E leg when the app image builds the SPA (§3.3).

## 7. Licenses (Apache-2.0 compatibility)
- **Permissive:** everything recommended is MIT, ISC (lucide-react) or Apache-2.0 (TypeScript, Playwright, class-variance-authority), and all of these are compatible.
- **The one exception is `@axe-core/playwright` / `axe-core` (MPL-2.0).** That is a weak, file-level copyleft and OSI-approved. It is used unmodified as a **dev-only** test dependency and is never shipped in the wheel or image, so it creates no obligation for our Apache-2.0 code. Add a note to the license check allowlist.
- The browser binaries Playwright downloads are test-only and never redistributed.

## 8. Later steps: version and compatibility risks only
- **assistant-ui:** `@assistant-ui/react` 0.15.32 (pre-1.0, roughly weekly releases) **depends on `radix-ui ^1.8.1`**. The chat UI will pull in Radix next to our Base UI shadcn. That is acceptable, but expect two primitive libraries and a mixed styling idiom. `@assistant-ui/react-ag-ui` 0.1.1 is very new (depends on `@ag-ui/client ^1.0.3`).
- **Graphs:** sigma 4.0.4 + graphology 0.26.0 (MIT); `@cosmos.gl/graph` 3.5.1 (node >=22, peer `@luma.gl/* ~9.3`); `@xyflow/react` 12.12.1.
- **Editor:** platejs is at 53.5.2, so frequent majors. Pin it exactly.
- **TypeScript 7:** every package that imports the TS compiler API (openapi-typescript, typescript-eslint, and possibly ts-morph-based tools) must support it before we move. Track openapi-typescript #2872.

## Recommendations for the plan
1. **Stack (exact pins via lockfile + `minimumReleaseAge: 10080`, `trustPolicy: no-downgrade`):**
   - Node 24 (≥24.15) via `devEngines.runtime`; `packageManager: pnpm@12.15.0`;
   - React 19.3.1, Vite 8.3.5, @vitejs/plugin-react 6.1.x;
   - **TypeScript 6.0.3**;
   - TanStack Router 1.170.x (file-based, `routeTree.gen.ts` committed) and Query 5.104.x;
   - Tailwind 4.3.3 + `@tailwindcss/vite`;
   - shadcn via `pnpm dlx shadcn@4.22.1 init -t vite -b base`;
   - i18next 26.5 + react-i18next 17.1;
   - Vitest 5.0.5 + jsdom 30 + Testing Library;
   - ESLint 10 + typescript-eslint 8.72.0 + react-hooks 7.1.1 + react-refresh 0.5.7;
   - Prettier 3.9.10 + prettier-plugin-tailwindcss.
   - Scaffold from the shadcn `vite-app` template rather than hand-rolling configs.
2. **Auth: option (b).**
   - The guard accepts the header **or** an HttpOnly SameSite=Strict host-only cookie set by a `GET <non-api path>?token=` → 303 that strips the token. The cookie counts only with `Sec-Fetch-Site: same-origin`.
   - Public: `/health`, plus GET/HEAD outside `/api/` and `/mcp`. Move OpenAPI/docs under `/api`.
   - Redact the uvicorn access log.
   - `start` prints the login URL.
   - ADR 0003 amends spec §4.3.
3. **Serving:** a `SPAStaticFiles` subclass mounted last at `/`, with immutable caching for `assets/`, `no-cache` for `index.html`, JSON 404s kept for unknown `/api`/`/mcp` paths, and CSP/nosniff/no-referrer headers. Build into `backend/src/researchly/web/` (gitignored, hatch `artifacts`). Docker gets a Node build stage via compose `additional_contexts`.
4. **OpenAPI:** `python -m researchly.openapi` dumps `create_app(token=…).openapi()` → `openapi-typescript … -o src/api/schema.d.ts`. CI runs the same with `--check`. Use openapi-fetch with `baseUrl: '/'` and no auth middleware.
5. **E2E:**
   - `e2e/` is its own pnpm project with Playwright 1.64.1, Chromium headless shell only, and `@axe-core/playwright` 4.14.0.
   - `globalSetup` runs `config set runtime native` + `start` and returns a teardown that runs `stop`.
   - A `setup` project saves `storageState` from the login URL, and `baseURL` comes from a fixture.
   - The fixed data/log dirs live under `e2e/.artifacts/`, with `PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring`.
   - The smoke spec checks: the shell renders, the `/health` indicator is green, one authenticated `/api/v1/system` call succeeds (it proves the cookie path, so add this one tiny route), an aria snapshot of the shell, and an axe scan with zero violations.
6. **CI:** new `frontend` and `e2e` jobs on ubuntu-24.04 with SHA-pinned `pnpm/action-setup` v6.1.0 + `actions/setup-node` v7.1.0 (`cache: pnpm`) + `upload-artifact` v7.0.2. Windows/macOS E2E legs come later, non-gating at first.

## Open risks
- **openapi-typescript maintenance lag.** The last release was 2026-02-11, TS 6 support is unmerged (it works with a peer warning, verified), and TS 7 breaks it (verified). If it stays stalled, it blocks a TS 7 move. The fallback is `@hey-api/openapi-ts` (TS 7 compatibility UNVERIFIED).
- **Cookies are not isolated by port** (RFC 6265 §8.5). Any other server the user visits on 127.0.0.1 receives the cookie. `Sec-Fetch-Site: same-origin` stops other-port pages from *using* it, but not a hostile local server from *harvesting* it. That is accepted for single-user localhost, and Jupyter and VS Code serve-web carry the same exposure.
- **The token appears in terminal output, browser history and (until redacted) uvicorn access logs.** The redaction test is mandatory.
- **Unverified mechanics:**
  - whether Chrome's history DB keeps a `replaceState`-stripped or redirected login URL;
  - the exact Vite 8 proxy hook for rewriting `Origin` in dev;
  - `.dockerignore` handling for compose `additional_contexts`;
  - how shadcn records the chosen base in `components.json`;
  - whether a CSP without `'unsafe-inline'` styles works with Base UI;
  - D-Bus/Secret Service absence on GitHub ubuntu runners (made moot by `PYTHON_KEYRING_BACKEND`).
- **Node 26 became LTS only on 2026-10-28.** Re-evaluate pinning 24 vs 26 in about a month. The dev box (24.14.0) is below jsdom 30's floor.
- **assistant-ui (step 3) brings Radix** alongside Base UI, and its AG-UI adapter is 0.1.x.
- **No independent benchmarks** were found for happy-dom vs jsdom, Vite 8/Rolldown or TS 7. The choices above rest on compatibility, not speed claims.
