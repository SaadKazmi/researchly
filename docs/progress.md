# Progress log

Newest entries at the bottom. Each session appends: date, plan + task, what changed, test status, next step.

## 2026-10-28
- Design spec approved: docs/superpowers/specs/2026-10-28-researchly-design.md
- Research digest: docs/research/2026-10-28-research-digest.md
- Started plan: docs/superpowers/plans/2026-10-28-step1a-dev-harness.md

## 2026-10-28 (Task 7: settings + status line)
- Wired hooks (protect/format/typecheck/session_context), deny rules, and status line in .claude/settings.json; added .claude/statusline.py.
- Claude Code version: 2.1.77 (not updated; the installed copy hosts the dispatching session).
- autoCompactWindow: documented in settings-reference (number of tokens 100000-1000000, any file); set to 300000. Installed 2.1.77 may predate it and ignore it.
- Hook/statusLine commands verified by scripted run through Git Bash, not interactive /hooks.
## Step 1a — tool kit and CI
- Step 1a tool kit installed: plugins (pyright-lsp, typescript-lsp, security-guidance, pr-review-toolkit, claude-md-management, frontend-design), LangChain skills (7, MIT, langchain-skills@126c384), MCP docs-langchain; Claude Code 2.1.77. `data` plugin still active (not found by CLI 2.1.77 config) — disable manually via /plugin.
- Step 1a complete: harness, hooks (fail-closed, UTF-8, control-plane guard), agents, plugins, CI repo-checks green on PR #1. Next: docs/superpowers/plans/2026-10-28-step1b-backend-core.md
- Final review fixes (c4963ad, 7da4386): per-agent models now effective (CLAUDE_CODE_SUBAGENT_MODEL removed — it overrides frontmatter); hook subprocess output decoded UTF-8; protect guards scripts/repo_checks.py and imports hooks dir first; LangChain MIT license added; .claude/worktrees/ ignored. 56 tests passing; CI green.

### Carried into step 1b
- All resolved in step 1b (Task 0 hooks, conventions, pyrightconfig, CI pins/cache, data plugin) except: Bash-matcher guard for control-plane paths, due when Bash allow rules are added.

### User actions outstanding
- Done 2026-10-28: `claude update` → 2.1.312; context7 authenticated (`claude mcp list` → Connected).
- Optional: approve project MCP `docs-langchain` in an interactive `claude` session (CLI shows "Pending approval"; the VS Code session already loads it).
- Done 2026-10-28: `data` plugin (synced from claude.ai, `data@knowledge-work-plugins`) disabled for this project via gitignored `.claude/settings.local.json`; global pyright 1.1.414 restored to a complete install after a second EBUSY (a running pyright-langserver locks `dist`).
- Note: run `claude update` (and any `npm i -g` of a tool that is currently running) only with all Claude Code sessions closed — npm cannot replace locked files on Windows and leaves a broken install.

## 2026-11-02 — Step 1b backend core (plan docs/superpowers/plans/2026-10-28-step1b-backend-core.md)
- Done (feat/step1b-backend-core, PR below): worktree-aware hooks; FastAPI app + Settings; Postgres/pgvector schema via Alembic baseline (testcontainers locally, service container in CI); OS keychain secrets, local token, secret redaction (longest-first, tracebacks); localhost guard (host allowlist, same-origin, token, WS 1008); capability table + role routing + LiteLLM registry (keys excluded from model dumps); LLM ledger (one llm_call row per call, error rows); RecordReplay fixtures (canonical key, leaf-level scrubbing, diff on miss); dev-only claude-code provider (tool emulation, gated to env=development, excluded from the wheel); CI backend job (ruff, pyright, pytest on pgvector:pg18), uv 0.12.10 and pytest 9.1.1 pinned.
- Tests: 48 backend passed (+1 live deselected), 64 hook/script passed; ruff/pyright clean.
- Live smoke (`RESEARCHLY_LIVE_DEV_PROVIDER=1`, -m live): first hit `error_max_turns` — structured output spends a CLI turn, and "you can call these tools" made the model try native tool_use. Fixed with max_turns=2 + a catalogue that routes tools through the structured reply (5/5 probes, 3/3 live); spec §7.7 updated.
- Deferred: keychain encrypted-file fallback (1c); SPA token delivery (1d); dev-provider streaming; fixture secret-scan CI check (with the first committed fixtures); ledger raise_error policy (decide retry vs. original-error-wins when the ledger is wired into agents); WS 1008 / host-case / `/health/` regression test (first WebSocket route, step 3); tool_choice in the fixture key (if a collision appears).
- Next: plan 1c (CLI, runtime selection, spikes).

## 2026-11-02 — Step 1c CLI and runtime (plan docs/superpowers/plans/2026-11-02-step1c-cli-runtime.md, PR #3)
- Done (feat/step1c-cli-runtime): `researchly` CLI (`cli/`, own uv project + pyright config) with setup/start/stop/status/doctor/config; container engine detection + auto-start (Docker Desktop, Podman, Colima, Rancher Desktop, OrbStack) with automatic native fallback and a runtime-switch warning; native mode on pg0 PG 18 + detached supervisor (backoff, log rotation, Windows kill-on-close Job Object, PID-reuse- and reboot-safe tree termination); Docker mode (pgvector + pg_textsearch image, app image, SearXNG, optional GROBID; compose run from the repo root); DB provisioning (passwords kept out of errors); AES-GCM encrypted-file keychain fallback; hex secrets; failed/interrupted starts clean up after themselves; state/config validated on read. ADR 0002 (native Postgres via pg0).
- Tests: cli 84 passed (+1 Docker E2E, gated), backend 69 passed; CI green on ubuntu x64/arm64, windows-2025, macos-15 arm64/Intel + Docker E2E. pg0 cold start 1.8–4.2 s on CI runners (7.8 s on the Windows dev box).
- CI lessons: pyright needs `sys.platform` guards inside Windows-only bodies; CPython ≥3.12.4 `mkdir(0o700)` on Windows sets an owner-only ACL that breaks initdb on elevated runners (tests use default-mode temp dirs); compose bake only reads inside its cwd.
- Accepted limitation: pg0 stores the superuser password in `~/.pg0/instances/<name>/instance.json` (ADR 0002; POSIX 0700).
- Deferred: start lock (step 9); corrupt state.json can orphan a run (step 9); Podman socket readiness on Linux (step 9); Windows graceful stop, native PG health restart, pre-migration backup, doctor migration state (step 9); uvicorn Selector loop on Windows native before the first DB route (step 2).
- No features.json flips (F10.1 flips at step 9).
- Next: plan 1d (frontend shell + first E2E; LLM fixtures dir in the harness; SPA token delivery), then plan 1e spikes (native SearXNG on Windows, native sandbox, Docling-ONNX, ONNX embedder/reranker).

## 2026-11-02 — Step 1d frontend shell and first E2E (plan docs/superpowers/plans/2026-11-02-step1d-frontend-shell.md)
- Done (feat/step1d-frontend-shell): SPA auth via one-time login link → HttpOnly SameSite=Strict cookie + Fetch Metadata check, token failures 401, uvicorn log redaction (ADR 0003, spec §4.3); FastAPI serves the built SPA (index fallback, CSP/nosniff/no-referrer, immutable hashed assets; shipped in the wheel); `python -m researchly.openapi`; `/api/v1/system`; frontend scaffold (Vite 8, React 19, shadcn/Base UI, Tailwind 4, TanStack Router/Query, i18next, openapi-fetch, Vitest; pnpm 12 with Node 24.22.2 via devEngines; 7-day release cooldown + trust no-downgrade); app shell with §18 routes, health status, version, sign-in notice; `researchly start` prints the login link and builds the web UI on a source checkout's first native start; `doctor` reports the build; Docker app image builds the SPA; Playwright E2E project (@smoke: login, shell, a11y via axe, deep links, 401, log redaction) on an isolated data dir and fail keyring; CI frontend + e2e jobs (e2e starts from a fresh checkout, no pre-build).
- Tests: backend 107 passed; cli 95 passed (+ Docker E2E 1 passed, 262 s); frontend 7 passed; E2E smoke 6 passed (cold first start incl. UI build ~36 s, warm ~12 s).
- Found by E2E: on Windows, CPython 3.12.0 `shutil.which("pnpm")` returns npm's extensionless shell shim (WinError 193); the CLI now looks up pnpm.cmd / pnpm.exe.
- User-approved: `trustPolicyExclude: semver@6.3.1` (last 6.x, published without provenance after 7.x had it; Babel 7 needs it). Cooldown pins: shadcn 4.22.1, lucide-react ^1.61.0.
- Accepted: pnpm downloads Node 24.22.2 inside the node:24.22.2 image (~35 s cold, layer-cached); compose builds the app image for both `app` and `migrate` (BuildKit dedupes).
- Deferred: see the plan's Deferred list (Windows/macOS E2E legs and auto-open browser at step 9; WebSocket regression test and LLM fixtures dir at step 3; TS 7 / Node 26 ADR).
- No features.json flips (F10 and onboarding flip at step 9).
- Next: plan 1e spikes (native SearXNG on Windows, native sandbox, Docling-ONNX, ONNX embedder/reranker).
