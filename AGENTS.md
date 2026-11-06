# Researchly — agent & contributor guide

Researchly is an open-source, local-first, multi-agent research assistant: chat → agents search keyless scholarly sources → rank → read into verified evidence → graphs, notebooks and books. The only thing that costs the user money is their LLM API key.

- Spec (source of truth): `docs/superpowers/specs/2026-10-28-researchly-design.md`
- Plans: `docs/superpowers/plans/` · ADRs: `docs/adr/` · Research: `docs/research/`
- Feature registry: `docs/features.json` · Session log: `docs/progress.md`

## Principles (Karpathy guidelines)
1. **Think before coding.** Restate the goal, read the code you will touch, name your assumptions. Ask when the spec is ambiguous.
2. **Simplicity first.** The smallest change that meets the spec. No speculative abstractions, flags or config.
3. **Surgical changes.** Touch only what the task needs; no drive-by refactors or reformatting.
4. **Goal-driven execution.** Define "done" as a passing test or check before writing code; stop when it passes.

## Repo map
- `backend/` — Python 3.12 app (FastAPI, LangGraph/Deep Agents, LiteLLM, SQLAlchemy/Alembic). See `backend/AGENTS.md`.
- `frontend/` — React 19 + Vite + TanStack Router + shadcn/ui SPA. See `frontend/AGENTS.md`.
- `cli/` — `researchly` CLI (setup, runtime selection, supervision).
- `e2e/` — Playwright specs, journeys, personas, fixtures (LLM + HTTP replay); its own pnpm project.
- `evals/` — Inspect AI eval tasks.
- `infra/` — compose files, Dockerfiles, native runtime assets.
- `scripts/` — repo checks and maintenance scripts.

## Commands
| Purpose | Command |
|---|---|
| Repo checks | `uv run --no-project --python 3.12 scripts/repo_checks.py` |
| Hook/script tests | `uv run --no-project --python 3.12 --with pytest pytest .claude/hooks/tests scripts/tests -q` |
| Backend tests | `uv run --directory backend pytest -q` |
| Backend platform tests | `uv run --directory backend pytest -m platform -q` (downloads models, Deno and Pyodide; models cached in the gitignored `backend/.models`) |
| Backend lint/format | `uv run --directory backend ruff check` · `uv run --directory backend ruff format` |
| Backend types | `uv run --directory backend pyright` |
| CLI tests | `uv run --directory cli pytest -q` (starts real pg0 Postgres and installs the pinned SearXNG; Docker-mode E2E: prefix `RESEARCHLY_TEST_DOCKER=1`, ~10 min cold) |
| CLI lint/types | `uv run --directory cli ruff check` · `uv run --directory cli ruff format` · `uv run --directory cli pyright` |
| Run locally | `uv run --directory cli researchly start` · `status` · `doctor` · `stop` |
| Frontend tests | `pnpm -C frontend test` |
| Frontend types/lint | `pnpm -C frontend typecheck` · `pnpm -C frontend lint` |
| Sandbox probe | `uv run --no-project --python 3.12 scripts/sandbox_probe.py` (per-OS kernel-sandbox facts, ADR 0006) |
| OpenAPI client | `pnpm -C frontend api:schema` (regenerates `frontend/src/api/`; CI fails if it is stale) |
| E2E smoke | `pnpm -C frontend build` then `pnpm -C e2e exec playwright test --grep @smoke` (isolated data dir under `e2e/.artifacts`; `researchly start` rebuilds a missing or stale UI) |

The default `python` on dev machines may be old; always go through `uv`.

## Binding conventions
- LLM calls go **only** through `backend/src/researchly/llm/` (`llm/registry.py` is the only module importing `litellm`).
- `deepagents` is imported **only** in `backend/src/researchly/agents/factory.py`.
- All outbound HTTP goes through `PoliteClient` (`backend/src/researchly/connectors/http.py`); never call `httpx` directly elsewhere.
- Untrusted text (web, PDFs) never reaches planner/writer prompts directly — use the quarantined extractor (spec §19.1).
- No API keys in state, checkpoints, logs, traces or fixtures. Keys come from the keychain at call time.
- Keyless by default: no feature may require a paid service or a data-source key (spec D14).
- Tests use replay: LLM via `RecordReplay` fixtures, HTTP via vcrpy cassettes. A missing fixture fails the test.
- Migrations: Alembic; never edit an applied revision — add a new one.
- Pin exact versions for `litellm`, `deepagents`, `langgraph`, `langchain*`; upgrades need an ADR (spec §6.6, §19.7).

## Workflow contract (every session)
1. Read `docs/progress.md`, `docs/features.json`, `git log --oneline -15`, and the current plan.
2. Run the smoke checks for what exists (repo checks; later `@smoke`).
3. Take **one** plan task. TDD: failing test → minimal code → passing test.
4. Verify (tests + relevant E2E), then commit with a Conventional Commit message.
5. Flip `passes` in `features.json` only after its E2E spec passes; append to `docs/progress.md`.

## Gotchas
- Windows dev box: PowerShell 5.1 has no `&&`; use `;` with `if ($?)`. Hooks are Python scripts launched via `uv run` (Claude Code runs hook commands through Git Bash on Windows).
- `docs/features.json` edits other than `passes` require user approval (hook asks).
- Docker may be installed but stopped; the CLI starts it — don't assume either runtime in tests; both modes are in CI.
- Windows asyncio: psycopg async needs `SelectorEventLoop` (tests set it via `anyio_backend`); the dev `claude-code` provider runs the Agent SDK in its own thread on a Proactor loop.
- Pyright picks the repo-root `pyrightconfig.json` over a project's `pyproject.toml`, so the root config excludes `cli/` and the CLI has its own `cli/pyrightconfig.json`. A new Python project needs the same.
- Frontend: pnpm 12 provides Node 24.22.2 via `devEngines.runtime` (system Node may differ). TypeScript stays on 6.0.3 until openapi-typescript supports TS 7. Supply chain: `minimumReleaseAge` 7 days + `trustPolicy: no-downgrade`; `trustPolicyExclude` entries need user approval.
- The SPA builds into the gitignored `backend/src/researchly/web/` (shipped in the wheel). Native `researchly start` (re)builds it when it is missing or older than the frontend sources; an already-running app keeps serving the old build until restarted. Frontend dev: `researchly start`, open its login link once, then `pnpm -C frontend dev` on :5173 (proxies `/api`).
- No torch: never add Docling model extras (they pull torch or `onnxruntime-gpu`); `backend/tests/test_lock.py` guards both locks. `docling-slim` is pinned exactly because `ingest/pdf.py` patches private behaviour (ADR 0005). onnxruntime is held at 1.23.2 on Intel Macs, the last x86_64 wheel (ADR 0007).
- Light cells (`sandbox/light.py`): pass Deno paths as `Path.resolve().as_posix()` (Deno denies Windows 8.3 short names); Deno checks a link's own path, so `run_cell` refuses workdirs holding links. Deno/Pyodide bumps re-run the escape tests on all five OS (ADR 0006).
- Native SearXNG: bumping the pin means a new commit SHA, tarball sha256 and regenerated `searxng-requirements.txt` (command in its header), plus the compose image tag (ADR 0004).
- Connectors (`backend/src/researchly/connectors/`, ADR 0008; checklist in the `add-scholarly-connector` skill):
  - Per-host limits live in `connectors/limits.yaml`.
  - Record cassettes live, keyless and with no email, using `pytest tests/connectors/test_<name>.py --record-mode=once`. Before committing, grep them for `mailto`, `cookie`, `authorization` and email addresses, and never commit a 429 or 5xx.
  - The live checks run nightly (`-m nightly`, `.github/workflows/nightly.yml`), not in CI.
  - Local sidecars (SearXNG, GROBID) will be the only HTTP outside `PoliteClient`, through a loopback-only client, from step 2b/2c.
- A backend dependency change needs `uv lock --directory cli` too: the CLI locks the editable backend, and CI fails on a stale `cli/uv.lock`.
- Native mode: `pg0.Pg0.drop()` deletes the data dir; only test teardown may call it. In a Windows venv, `sys.executable` is a launcher whose child is the real interpreter (the supervisor's PID is the launcher's; kill-on-close jobs chain).
- BM25 objects are engine-specific (pg_textsearch index or a tsvector column plus GIN), have `bm25` in their names, live only in migrations and are skipped by `test_models_match_migrations`. The `backend-bm25` CI job runs the DB tests with pg_textsearch (ADR 0009).
- Full-text fetches pass the host as the breaker `source` (`arxiv.org`, the PDF host), so one blocked publisher cannot open a shared breaker.

## Compact instructions
When compacting, keep: current plan path and task number, failing test names and errors, files changed this session, decisions made and why. Drop: full file dumps, passing test output, exploration dead ends.
