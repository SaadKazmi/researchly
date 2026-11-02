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
- `e2e/` — Playwright specs, journeys, personas, fixtures (LLM + HTTP replay).
- `evals/` — Inspect AI eval tasks.
- `infra/` — compose files, Dockerfiles, native runtime assets.
- `scripts/` — repo checks and maintenance scripts.

## Commands
| Purpose | Command |
|---|---|
| Repo checks | `uv run --no-project --python 3.12 scripts/repo_checks.py` |
| Hook/script tests | `uv run --no-project --python 3.12 --with pytest pytest .claude/hooks/tests scripts/tests -q` |
| Backend tests | `uv run --directory backend pytest -q` |
| Backend lint/format | `uv run --directory backend ruff check` · `uv run --directory backend ruff format` |
| Backend types | `uv run --directory backend pyright` |
| Frontend tests | `pnpm -C frontend test` |
| Frontend types/lint | `pnpm -C frontend exec tsc --noEmit` · `pnpm -C frontend lint` |
| E2E smoke | `pnpm -C e2e exec playwright test --grep @smoke` |

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

## Compact instructions
When compacting, keep: current plan path and task number, failing test names and errors, files changed this session, decisions made and why. Drop: full file dumps, passing test output, exploration dead ends.
