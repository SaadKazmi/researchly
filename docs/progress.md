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
- Final review (opus) fixes: Swagger UI page off (CDN script in the authenticated origin); malformed Host/Origin → 403 not 500; uvicorn `color_message` dropped by the redaction filter; a test keeps every data route under `/api`/`/mcp`; `/` explains a missing build instead of a JSON 404; `start` rebuilds a stale UI (sources newer than the build); the Docker context excludes a local build; 304s keep immutable caching; the CLI reads `WEB_DIR` from `researchly.spa` (no FastAPI import). Accepted: the throwaway E2E token can appear in CI failure traces.
- Found by E2E: on Windows, CPython 3.12.0 `shutil.which("pnpm")` returns npm's extensionless shell shim (WinError 193); the CLI now looks up pnpm.cmd / pnpm.exe.
- User-approved: `trustPolicyExclude: semver@6.3.1` (last 6.x, published without provenance after 7.x had it; Babel 7 needs it). Cooldown pins: shadcn 4.22.1, lucide-react ^1.61.0.
- Accepted: pnpm downloads Node 24.22.2 inside the node:24.22.2 image (~35 s cold, layer-cached); compose builds the app image for both `app` and `migrate` (BuildKit dedupes).
- Deferred: see the plan's Deferred list (Windows/macOS E2E legs and auto-open browser at step 9; WebSocket regression test and LLM fixtures dir at step 3; TS 7 / Node 26 ADR).
- No features.json flips (F10 and onboarding flip at step 9).
- Next: plan 1e spikes (native SearXNG on Windows, native sandbox, Docling-ONNX, ONNX embedder/reranker).

## 2026-11-03 — Step 1e platform spikes (plan docs/superpowers/plans/2026-11-02-step1e-platform-spikes.md, PR #5)
- Done (feat/step1e-platform-spikes): torch-free PDF conversion with docling-slim on pypdfium2 + RapidOCR, with no-torch lock guard (ADR 0005); local int8 embedder (fastembed bge) and bge-reranker-v2-m3 int8 via onnxruntime (ADR 0007); light code cells as Pyodide 314.0.7 in Deno 2.9.8 with an escape suite on five OS (ADR 0006); stdlib per-OS kernel-sandbox probe (`scripts/sandbox_probe.py`, non-blocking CI job); native SearXNG from a pinned commit, sha256-checked, hash-locked deps, Windows `pwd` shim, supervised beside the app (ADR 0004); `platform` CI job on five OS.
- Security: windows-2025 CI showed a planted symlink in a light-cell workdir let a cell read outside it (a junction does the same as non-admin). `run_cell` now refuses workdirs holding symlinks, junctions or hard links.
- Spec updated: §4 web search/PDF rows, §9 Docling/embedder/rerank depth, §19.4 (link refusal, Landlock, Windows AppContainer + Job Object), §26 risks, F6.1 light cells server-side.
- Tests: CLI 114 passed (+ real SearXNG install per OS in CI); backend platform tests on five OS; CI run 41535527204 green on every job.
- Rulings: rerank depth set by a step 4 benchmark (50 int8 pairs take 11–51 s on CI); Windows kernel sandbox is AppContainer + Job Object, not a restricted token.
- Deferred: see the plan's Deferred list (product wiring at step 2, rerank depth at step 4, warm Pyodide and the kernel sandbox at step 7/10, SearXNG install race at step 9).
- No features.json flips.
- Next: step 2 plan.

## 2026-11-03 — Step 2a scholarly connectors (plan docs/superpowers/plans/2026-11-03-step2a-scholarly-connectors.md, PR #6)
- Done (feat/step2a-scholarly-connectors):
  - **`PoliteClient`:** per-host limits from `limits.yaml`, retry with full jitter and `Retry-After`, a circuit breaker per source, a 5 MB body cap, and manual redirects.
  - **SSRF-guarded transport:** checks every resolved address, unwraps mapped, NAT64, 6to4 and Teredo forms, and requires IPv6 to be in 2000::/3.
  - **Cache and paper tables:** an UNLOGGED `http_cache` (allowlisted headers, ETag revalidation) and paper tables in migration 0002.
  - **Eight keyless Tier-1 connectors:** OpenAlex (with a budget guard), arXiv, Crossref, OpenCitations (count-first, 5,000-row cap), DataCite, Europe PMC, PubMed and HF Papers. They are wired by `build_connectors`.
  - **Identity resolution:** exact normalized IDs, then title trigram ≥ 0.92 + year ± 1 + first-author surname. Merging keeps per-field provenance.
  - **Paper store:** `upsert`.
  - **Federated search:** `federated_search` reports a quota or outage per source.
  - **Nightly live checks:** the `nightly.yml` workflow.
  - ADR 0008. The `add-scholarly-connector` skill.
- Tests: backend 447 passed, 2 skipped; db 28 passed; nightly live run 10 passed (all eight sources, keyless). Connector tests replay vcrpy cassettes: 200s or deliberate 4xx only, with no cookie, key or email.
- Review fixes worth remembering:
  - Identifiers are quoted in paths and filters.
  - Unreadable 2xx bodies raise `SourceUnavailable`.
  - `set-cookie` is stripped from cassettes.
  - Upsert ranks each field by its real source, so merged records keep their provenance.
  - Non-Latin titles keep their letters, and weak titles never fuzzy-match. Postgres trigram candidates are re-checked in Python, because pg_trgm depends on the DB locale.
  - A source may refresh its own fields.
- CI: GitHub Actions stopped starting jobs on the private repo because of billing. The repo was made public and the reruns went green.
- Rulings: arXiv search stays an AND of `all:` words, because a phrase clause hurt ranking and known titles come from OpenAlex and Crossref. OpenCitations citer lists are cached 7 days and reference lists 60 days. The `Connector.get` parameter is positional-only.
- Deferred: see the plan's Deferred list (cache prune at step 9; author index, batching and upgrades at step 5; per-hop redirect pacing at step 2b; arXiv query tuning).
- No features.json flips: search and the graph flip when the agents use them (step 3+).
- Next: plan 2b (full-text ingestion: robots.txt, chunking, embeddings, hybrid search and rerank).

## 2026-11-06 — Step 2b full-text ingestion and hybrid retrieval (plan docs/superpowers/plans/2026-11-04-step2b-ingestion-retrieval.md)
- Done (feat/step2b-ingestion-retrieval):
  - **PoliteClient:** content-type gate with `max_bytes`, per-hop host pacing, and `Crawl-delay` applied to the limiter; `arxiv.org` limited to 1 per 15 s.
  - **robots.txt:** Protego 0.7.0 under RFC 9309 (4xx allow, 5xx disallow, 24 h cache, over 60 s skipped).
  - **Full text:** arXiv HTML, then Europe PMC JATS, then PDF through Docling (`%PDF-` sniff, 50 MB cap). LaTeX tier dropped.
  - **Chunking:** semchunk at 512 with 15% overlap, using the Qwen3 tokenizer.
  - **Migration 0003:** sections, chunks, `halfvec(1024)` embeddings with HNSW, and BM25 by engine (pg_textsearch or tsvector).
  - **Services:** `ingest_paper` and hybrid retrieval (BM25 + dense, one-statement RRF, then rerank).
  - **CI:** the `backend-bm25` job runs the DB tests on the `infra/postgres` image.
  - ADR 0009.
- Tests: backend 512 passed, 2 skipped; `-m db` 45 passed on tsvector (pgvector pg18) and 45 on pg_textsearch; CI run 42223460180 green on every job. The platform check measured a mean of 392 and a max of 420 Qwen3 tokens per chunk on synthetic text (research: about 350 on real papers).
- Rulings:
  - Hold one host permit at a time across redirects, with pacing outside the total timeout (the planned held-set design could deadlock).
  - A blocked hop resets the breaker's failure count like any non-outage answer.
  - Stricter-than-RFC robots limits kept (over 5 redirects, over 500 KB: disallow).
  - Tests, not a DDL string assert, guard BM25 index-expression drift.
  - Namespaced JATS roots and UTF-16 HTML are unhandled; those papers fall back to PDF.
  - `tokenizers` stays transitive via fastembed.
- Final review (opus) fixes: the tsvector query cast its OR-ed lexemes with `to_tsquery`, which stemmed them twice ("disease" never matched); a failed re-fetch deleted the stored full text; `expire_all` left the caller's objects unusable in async; the PDF failure log carried the full URL.
- Deferred: Docling page cap and timeout (step 3); BM25 objects on a runtime switch (step 9); robots.txt on redirect hops; the ingest transaction split (step 3 job wrapper); scoped lexical search on pg_textsearch (step 4); the rest are in the plan's Deferred list (SPECTER2 at step 5, GROBID at 2c/4, rich tables at step 4, re-embedding at step 9).
- No features.json flips.
- Next: plan 2c (GROBID and local sidecars), then step 3 (run manager).

## 2026-11-07 — Step 2c Tier-2 sources and web search (plan docs/superpowers/plans/2026-11-06-step2c-tier2-and-web-search.md)
- Done (feat/step2c-tier2-and-web-search):
  - **SearXNG:** engines cut to brave, mojeek, yahoo and github, JSON only; the CLI passes `RESEARCHLY_SEARXNG_URL` (native and Docker); `RESEARCHLY_WEB_SEARCH=off` turns it off (17b0870).
  - **Sidecar client:** origin-pinned `connectors/sidecar.py`, plus a test that only the http modules import httpx (c57bff6).
  - **Web search:** `web_search` service and `ids.from_url` (da46ab4); a background probe records `capability.web_search` (1d8f916).
  - **Tier-2 connectors:** PwC archive table with migration 0004 (108a125), bioRxiv/medRxiv (d104f7b), OpenReview decisions (0112fa9), DBLP over SPARQL (9919ec3), OpenAIRE (aca8863); routing as a Deep-run extra and as enrichment (280df8c).
  - ADR 0010; ADRs 0004, 0005 and 0008, the spec and AGENTS.md amended.
- Tests: backend 654 passed, 2 skipped; `-m db` 51 passed (tsvector); CI run 42389334866 green on every job; CLI: the searxng settings and URL-plumbing tests, including `test_real_settings_enable_exactly_the_four_engines` against the pinned SearXNG; nightly: live checks for the connectors, the PwC snapshot and web search.
- Rulings: see the plan (pre-flight rulings), among them that the origin-pinned sidecar client is the one exception to "all HTTP via PoliteClient".
- Final review (opus) fixes: OpenReview picks the best decision among same-title notes (a spotlight could be recorded as rejected); DBLP keys moved from `ids` to `extra` (a preprint and its published version shared an ID, so identity merged them); a failed SearXNG start or a malformed SearXNG URL now only turns web search off; the capability reads `starting` until the probe answers; `ids.doi` rejects control characters; bioRxiv routing skips the CSH journals' `10.1101/` DOIs.
- Deferred: see the plan's Deferred list (GROBID and `paper_refs` at step 4, keyed web search at step 9, web result caching and calling enrichment at step 3, CORE and ACL Anthology later).
- No features.json flips.
- Next: step 3 (run manager).

## 2026-11-08 — Step 3a agent runtime (plan docs/superpowers/plans/2026-11-07-step3a-agent-runtime.md)
- Done (feat/step3a-agent-runtime):
  - **Pins:** deepagents 0.7.32, langchain 1.4.7, langchain-core 1.7.3, langgraph 1.2.19, langgraph-checkpoint-postgres 3.2.0.
  - **Selector loop** for uvicorn (native and Docker); LangGraph's tables from `setup_agent_tables`.
  - **Migration 0005:** project, thread, message, run, run_event, evidence, tool_cache.
  - **Ledger:** agent names from `lc_agent_name`, `attach_ledger`, `spent_usd`; registry models carry `profile`.
  - **Ranking (§10):** gate, percentiles, renormalised weights in `ranking.yaml`; Chao2 as a pure function. OpenAlex `counts_by_year` (cache key v2).
  - **Reader:** RCS evidence extraction with verified verbatim quotes (`services/evidence.py`); `tool_cache`.
  - **Agents:** tools, guards (budget, loop, effort), prompts, and the factory (Quick Lead; Lead with scouts and readers, plan review, `ask_user`).
  - **Run manager:** start, pause, resume, cancel, branch, resume on startup; `LiveRuntime`; the app's pool, checkpointer and store.
  - ADR 0011; spec §6.2–6.6, §16 and §24 amended.
- Tests: backend 726 passed, 2 skipped; `-m db` 88 passed; CLI (`-k "not docker"`) 95 passed; pyright 0 errors.
- CI: pending (PR).
- Rulings: see the plan (rulings 12–32); pre-flight rulings: Quick Lead tool cap uses `exit_behavior="continue"` and the run ends `partial`; `evidence.gather` releases its DB connection before the parallel model calls; `gather_evidence` takes a per-paper advisory lock before ingesting; tool-cache expiry uses the DB clock; a branch resumed before its first checkpoint restarts from its fork.
- Final review (opus) fixes:
  - `gather_evidence` held a DB connection through the full-text fetch, so parallel readers could exhaust the pool. The fetch now runs with no session open (`store_fulltext` split out of `ingest_paper`), under a per-paper lock.
  - Subagent results reached the Lead untagged; they now arrive inside `<untrusted>` (§19.1).
  - Evidence extraction ignored the budget; `gather_evidence` refuses once the run's budget is spent.
  - A pause or cancel could race a resume and leave a cancelled run running; control calls now take a per-run lock.
  - Decisions sent for a paused run were dropped, and a reject sent to a running run cancelled it.
  - One failed extractor call failed the whole paper; failed chunks are now dropped, and the error is re-raised only if every call fails.
- Deferred: see the plan's Deferred list (3b API and streaming, 3c UI and E2E, step 4 calibration, and the evidence `chunk_id` migration before any re-ingest path).
- No features.json flips: F1 and F2 flip with the 3c E2E specs.
- Next: plan 3b (projects, threads and runs API; AG-UI streaming).
