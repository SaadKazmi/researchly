# Step 3b: Projects, Threads and Runs API with AG-UI Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose Researchly's runs over HTTP. REST covers projects, threads (with their messages) and runs (create, read, pause, resume with decisions, cancel, branch). Each run streams its AG-UI events over SSE, persisted to `run_event` so that a reconnecting client replays from its last sequence number. The run manager drives every run through `ag-ui-langgraph`. There is no UI in this step (3c).

**Architecture:**
- **Pins:** `ag-ui-langgraph==0.0.47` and `ag-ui-protocol==1.0.0`, exact (ADR 0012).
- **Adapter subclass** (`runs/agui.py`, `ResearchlyAgent`): one instance per run drive. It passes our `RunContext` instance and continues a pending checkpoint with `input=None`. It sends a branch's `checkpoint_id` to the stream only, keeps only `todos` in `STATE_SNAPSHOT`, and turns HITL interrupts into typed reviews (`reason`, `message`, `responseSchema`).
- **Event log** (`runs/events.py`):
  - The drive task is the only writer of its run's `run_event` rows. It persists first, then publishes to an in-process `RunHub`, so live and replayed rows are identical and clients dedupe by `seq`.
  - `RAW` and `MESSAGES_SNAPSHOT` are never stored. Deltas are coalesced, and error text is scrubbed.
  - A run's terminal AG-UI event commits in the same transaction as its new status.
- **Run manager** (`runs/manager.py`): `_drive` iterates `ResearchlyAgent.run(RunAgentInput)` and keeps every 3a behaviour.
  - Input: a new run sends its one user message (our stable `message.id`); a resume with decisions sends `resume=[...]` for the open interrupt; a paused, restarted or branched run continues.
  - Decisions are checked against the persisted open review.
  - Every run ends with a `CUSTOM run_summary` event (status, cost, Chao2 coverage). `BudgetGuard` emits `CUSTOM cost` before each model call.
- **API** (`api/projects.py`, `api/threads.py`, `api/runs.py`): FastAPI routers under `/api/v1` with Pydantic models and `operation_id`s.
  - `POST /api/v1/runs/{id}/stream` uses FastAPI's native SSE.
  - `RunStateError` maps to 409, unknown ids to 404, and a missing run manager to 503.
- **Client:** the OpenAPI TypeScript client is regenerated.

**Tech Stack:**
- Python 3.12, FastAPI 0.157 (native SSE in `fastapi.sse`), Starlette 1.9, SQLAlchemy 2 async, httpx 0.28.1 (`ASGITransport` in tests).
- ag-ui-langgraph 0.0.47 and ag-ui-protocol 1.0.0 (`ag-ui-a2ui-toolkit` 0.0.6 comes transitively).
- Unchanged pins: langgraph 1.2.19, langchain 1.4.7, langchain-core 1.7.3, deepagents 0.7.32.

**Spec:** `docs/superpowers/specs/2026-10-28-researchly-design.md` (§3 F1, §4.2–4.3, §6.3–6.5, §16, §17, §19.1, §19.7, §21, §24).
- Research: `docs/research/2026-11-07-step3-streaming-ui.md` (**R-ui**, Q1, Q2, Q4, Q5) and the adapter spike `docs/research/2026-11-10-step3b-adapter-spike.md` (**Spike §N**). The spike wins where the two differ.
- ADR 0011 (agent runtime). This plan adds ADR 0012 (AG-UI streaming).

## Global Constraints

- **Binding conventions** (AGENTS.md, verbatim):
  - LLM calls go **only** through `backend/src/researchly/llm/` (`llm/registry.py` is the only module importing `litellm`).
  - `deepagents` is imported **only** in `backend/src/researchly/agents/factory.py`.
  - All outbound HTTP goes through `PoliteClient` (`backend/src/researchly/connectors/http.py`); never call `httpx` directly elsewhere.
  - Untrusted text (web, PDFs) never reaches planner/writer prompts directly — use the quarantined extractor (spec §19.1).
  - No API keys in state, checkpoints, logs, traces or fixtures. Keys come from the keychain at call time.
  - Keyless by default: no feature may require a paid service or a data-source key (spec D14).
  - Tests use replay: LLM via `RecordReplay` fixtures, HTTP via vcrpy cassettes. A missing fixture fails the test.
  - Migrations: Alembic; never edit an applied revision — add a new one.
  - Pin exact versions for `litellm`, `deepagents`, `langgraph`, `langchain*`; upgrades need an ADR (spec §6.6, §19.7).
- **Pins** (Task 1, ADR 0012):
  - New: `ag-ui-langgraph==0.0.47` and `ag-ui-protocol==1.0.0`.
  - Every 3a pin stays as it is.
  - After the change, run `uv lock --directory backend` and `uv lock --directory cli`.
  - `backend/tests/test_lock.py` asserts both new pins in both locks and keeps its torch guard.
- **Imports:**
  - `ag_ui_langgraph` is imported only in `backend/src/researchly/runs/agui.py`.
  - `ag_ui.core` models may be used in `runs/`.
  - `api/` uses `runs.events.follow` and the manager, never the adapter.
  - `runs/agui.py` must not import `deepagents` (Spike §8: the adapter needs only the compiled graph).
- **No migration:** `run_event` (migration 0005) already has `run_id`, `seq` (unique per run), `type`, `agent`, `payload` and `ts`.
- **Secrets in events:**
  - `RUN_ERROR` and `SUBAGENT_ERROR` messages are replaced with `The run failed.` before they are stored. The adapter puts `str(exc)` there, which can carry a provider response.
  - The adapter's logger `ag_ui_langgraph.agent` gets the `RedactingFilter`.
- **Tests:**
  - DB tests are marked `pytest.mark.db` and use the `db_url` fixture (a fresh database per test). Async tests use `pytest.mark.anyio`.
  - API tests drive `create_app(Settings(env="test"), token=TOKEN)` through `httpx.ASGITransport` with the `x-researchly-token` header.
    - `ASGITransport` runs no lifespan, so tests set `app.state.runs` to a `RunManager` built on fakes.
    - `ASGITransport` buffers the whole response, so an SSE request returns only when the stream ends.
  - Chat models are `ToolFake` (`tests/agents/fakes.py`), which streams after Task 2. No real LLM call and no fixture recording in 3b.
  - Test helpers are imported relatively (`from ..agents.fakes import ...`).
- **Never** edit `docs/features.json`. No flips happen in 3b: F1 and F2 flip with the 3c E2E specs.
- **Verify:**
  - Backend: `uv run --directory backend pytest -q`, `uv run --directory backend pytest -m db -q`, `uv run --directory backend ruff check`, `uv run --directory backend ruff format --check`, `uv run --directory backend pyright` (0 errors).
  - CLI, after the lock change: `uv run --directory cli pytest -q -k "not docker"`.
  - Repo: `uv run --no-project --python 3.12 scripts/repo_checks.py`.
  - Frontend, after the client regeneration: `pnpm -C frontend typecheck`.
- **Baseline** (main after step 3a): backend 728 collected, 726 passed and 2 skipped; `-m db` 88 passed.
- **Formatting:** run `uv run --directory backend ruff check --fix` and then `uv run --directory backend ruff format` before every lint check step. A few code lines in this plan exceed 120 characters, and the formatter wraps them. Keep comments under 120 characters, since ruff cannot wrap them.
- **Pyright:**
  - `ag_ui_langgraph` ships loose types. Where pyright flags an override of a `LangGraphAgent` method or an `ag_ui.core` union, prefer a narrow `# pyright: ignore[<rule>]` on that line over widening our own signatures.
  - Never silence a whole file.
- **Commits:** Conventional Commits, each ending with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (every commit command below carries it).

## Rulings made while planning

Controller rulings (given):

1. **Scope: 3b is backend only.**
   - REST for projects, threads (with their messages) and runs.
   - The AG-UI SSE stream endpoint.
   - `run_event` persistence and an in-process fan-out.
   - `RunManager` drives runs through the adapter.
   - The OpenAPI TS client is regenerated (`pnpm -C frontend api:schema`) so CI stays green.
   - No UI, no E2E spec and no `features.json` flip (3c).
2. **Adapter pins:**
   - `ag-ui-langgraph==0.0.47` and `ag-ui-protocol==1.0.0`, exact, in `backend/pyproject.toml`;
   - both locks regenerated;
   - `tests/test_lock.py` PINS extended;
   - ADR 0012 records the pin, the overrides and the event-log design;
   - AGENTS.md gets a short gotcha block.
3. **The adapter subclass lives in `runs/agui.py`.**
   - One instance per run drive.
   - `get_stream_kwargs` passes our `RunContext` instance, and sets `input=None` when continuing a pending checkpoint.
   - `get_state_snapshot` returns only `todos`.
   - `_interrupts_to_agui` adds `reason`, `message` and `response_schema`.
   - Flags: `emit_raw_events=False`, `emit_interrupt_outcome=True`, `subagent_visibility="attributed"`.
4. **`RunManager._drive` iterates `agent.run(RunAgentInput)`** and keeps every 3a behaviour and test.
   - Inputs:
     - start: the one new user message with our stable `message.id`;
     - resume with decisions: `resume=[{interrupt_id: <read server-side from the graph state>, status: "resolved", payload: {"decisions": [...]}}]`;
     - continue: `messages=[]`, `input=None`.
   - Endings: `RUN_ERROR` → `failed`; `RUN_FINISHED` with an interrupt outcome → `awaiting_user`.
5. **Event log with a single writer.**
   - `RunEventLog`: per-run `seq` continuing from `max(seq)`, plus `replay`. `RunHub` publishes after persist.
   - Never stored: `RAW` and `MESSAGES_SNAPSHOT`.
   - Coalescing: adjacent `TEXT_MESSAGE_CONTENT` and `TOOL_CALL_ARGS` deltas with the same id, flushed on any non-delta event, at 2 KB, or after 250 ms.
   - `type` is the AG-UI type, `agent` is the subagent name or `lead`, and `payload` is the event JSON (by alias, without nulls).
6. **Stream endpoint** `POST /api/v1/runs/{id}/stream`.
   - Resume point: `Last-Event-ID` or `?after=`, default 0.
   - Order: subscribe, replay, then tail, skipping `seq <= last`.
   - Frames: `id: <seq>` and `data: <json>`, with a `: ping` every 15 s.
   - Headers: `Cache-Control: no-cache` and `X-Accel-Buffering: no`.
   - A disconnect ends only the generator.
7. **REST under `/api/v1`** with routers, Pydantic models and `operation_id`s.
   - `RunStateError` → 409; an unknown id → 404.
   - The manager comes from `app.state.runs`; tests inject one.
8. **Run summary and Chao2:**
   - One `CUSTOM run_summary` at run end with `spent_usd`, `budget_usd`, `status` and Chao2 coverage over the per-(sub-question × source) incidence lists.
   - `CUSTOM cost` from `BudgetGuard` before each model call, via `adispatch_custom_event`.
   - No `CITATION` event in 3b.
9. **Tests:**
   - Make `ToolFake` stream.
   - API integration tests go through `ASGITransport`. They cover:
     - an SSE run end to end;
     - a reconnect with no gaps and no duplicates;
     - a plan interrupt, then `POST /resume` approve, then completion;
     - pause, then resume continuing the pending tool with no error ToolMessage;
     - the coalescing rules;
     - a normalised event-type snapshot.
   - DB tests are marked `db`.
10. **Deferred:** see the Deferred table.

Rulings added while planning (verified against the installed `ag_ui_langgraph` 0.0.47, `ag_ui.core` 1.0.0, FastAPI 0.157.0 and httpx 0.28.1 sources and by probes run during planning):

11. **Subagent attribution.**
    - Events inside a subagent carry `subagentRunId`, not the subagent's name. The name arrives once, on `SUBAGENT_STARTED` (Spike §2).
    - `RunEventLog` therefore keeps a `subagentRunId → name` map. A row's `agent` is:
      - `lead` when the event has no `subagentRunId`;
      - else the recorded name;
      - else `subagent`.
12. **A branch goes through the adapter.** No 3a-style first step is needed.
    - `ResearchlyAgent(checkpoint_id=...)` puts the id in the adapter's `config`.
    - `get_stream_kwargs` copies the configurable into the stream's own config, then deletes `checkpoint_id` from the adapter's dict. The adapter reuses that one config object after the stream (`aget_state` for the closing snapshots), so the later reads see the new head instead of the fork.
    - Probe C: a branch from the `("model",)` checkpoint re-ran the model, and the thread became `["Q?", "second answer"]`.
    - Probe D: a branch from before a pending plan review answered "after branch".
    - The 3a pre-flight S6 rule (restart from `branch_from_checkpoint`) is kept unchanged.
13. **The branch body is `{"checkpoint_id": str}` (required); the brief's `{checkpoint_id?, message?}` is wrong.**
    - The brief itself says to match `RunManager.branch(run_id, checkpoint_id: str)`, which has no message and a required id.
    - A branch with a new user message is a new turn. That belongs with multi-turn threads (3c), as does an endpoint listing a run's checkpoints.
14. **The legacy interrupt event is off** (`enable_legacy_on_interrupt_event=False`).
    - The `CUSTOM on_interrupt` event repeats the raw interrupt that `RUN_FINISHED.outcome` already carries (Spike §3).
    - Clients resume through `RunAgentInput.resume`, not `forwardedProps.command`.
15. **Error text is scrubbed.**
    - The adapter catches graph exceptions and yields `RUN_ERROR` with `message=str(exc)`. Probe E: a tool raising `RuntimeError("secret provider text")` produced `{"type":"RUN_ERROR","message":"secret provider text"}`.
    - `RunEventLog` replaces `message` on `RUN_ERROR` and `SUBAGENT_ERROR` with `The run failed.`. The manager's own failures set `code` to the exception type, as 3a's `{"error": type}` did.
    - The adapter logs the traceback with `logger.exception("LangGraph run failed")` on `ag_ui_langgraph.agent`, so `_redact_server_logs` adds the `RedactingFilter` to that logger.
16. **Terminal ordering.**
    - `_drive` holds back the adapter's `RUN_FINISHED` or `RUN_ERROR`. `_finish` appends it with a `run_update` that changes the run's status in the same transaction.
    - So a run at rest always has its terminal event persisted, and a stream never ends before the last row.
    - `run_summary` is appended just before every terminal that `_finish` writes.
17. **The cancel path closes the AG-UI run.**
    - Pause, cancel and app stop cancel the drive task. If an AG-UI run is open (`RUN_STARTED` written, no end yet), the `CancelledError` handler appends `RUN_FINISHED` with `outcome={"type":"cancelled"}` before re-raising.
    - So every AG-UI run in the log is closed, and a resume opens a new one.
18. **When a stream ends** (amends brief 6's "terminal sent and the run is not running").
    - A pause writes the cancelled `RUN_FINISHED` while the run is still `running` (the status changes after the task stops), and a resume starts a new `RUN_STARTED` in the same run.
    - So `follow` reads the status once after subscribing:
      - at rest (not `pending` or `running`): end after the replay;
      - otherwise: tail, and end at the first live `RUN_FINISHED` or `RUN_ERROR`, even one that was already sent by the replay.
19. **FastAPI's native SSE** (`fastapi.sse.EventSourceResponse`, `ServerSentEvent`) replaces hand-written frames.
    - Verified in `fastapi/routing.py` and `fastapi/sse.py`: an async-generator endpoint gets a `: ping` comment after 15 s idle (`_PING_INTERVAL`) and the headers `Cache-Control: no-cache` and `X-Accel-Buffering: no`.
    - It works on `POST`. `raw_data` plus `id` writes `data: ...\nid: ...\n\n`.
    - 404s must come from a dependency, because the generator body runs after the headers are sent.
    - The app has no GZip middleware, so the brief's "no gzip" needs no code.
20. **The stream endpoint declares no body** (amends brief 6's "accept an optional `RunAgentInput`").
    - FastAPI never reads an undeclared body, so `@ag-ui/client`'s POST body is accepted and ignored.
    - Declaring `RunAgentInput` would validate a body we discard (a 422 if a client's protocol version differs) and put the AG-UI schema into our OpenAPI.
    - The endpoint reads the path `run_id`, the `Last-Event-ID` header (wins) and `?after=`.
21. **The reconnect test drives `follow` directly** (amends brief 9's "read N frames, disconnect, reconnect").
    - `httpx.ASGITransport.handle_async_request` awaits the whole app before it returns (`httpx/_transports/asgi.py` line 170, `body_parts`), so no test can read part of an SSE response over it.
    - The test reads 3 rows from `follow`, closes it (what a disconnect does to the endpoint's generator), then follows from the last `seq`.
    - The HTTP layer's `Last-Event-ID` and `?after=` are tested on a finished run.
22. **Decisions are checked against the open review.**
    - HITL raises `ValueError` inside the graph on a count or type mismatch, which would fail the run.
    - `_resume` reads the last persisted `RUN_FINISHED` and its interrupt's `responseSchema` (ruling 3), and refuses with `RunStateError` (409) unless the decisions match one by one. It refuses when there is no open review too.
    - The `interrupt_id` is read server-side from `state.interrupts[0].id`. The adapter does not validate it (Spike §3).
23. **Chao2 incidence.** The brief asked whether the lists are recoverable; they are not, so they are now recorded.
    - Candidate files hold merged records whose `source` is only the top-priority source: `identity.merge` sets `merged.source = ordered[0].source` and drops the others.
    - `identity.merge` now records `extra["found_by"]`, the sorted union of every merged record's sources (and of their own `found_by`, so re-merges keep it).
    - `runs/summary.coverage` reads the project's `/candidates/*.jsonl` from the LangGraph store. It builds one incidence list per (file, source) of normalised titles and returns `chao2`'s `(observed, estimated)` and the list count.
    - Candidate files are project-scoped, so coverage spans the project's earlier runs too. Per-run file prefixes are already deferred to step 7.
24. **Reasoning deltas** (`REASONING_MESSAGE_CONTENT`, keyed by `messageId`) coalesce like text deltas. A reasoning model would otherwise write a row per token.
25. **No flush timer** (amends brief 5's "after 250 ms").
    - The age of the pending batch is checked when the next event arrives; no timer task runs.
    - A trailing delta always meets a non-delta event (`TEXT_MESSAGE_END`, `TOOL_CALL_END`), which flushes it.
    - The clock is injectable, and the coalescing tests freeze it.
26. **Fakes.**
    - `ToolFake` gains `_stream` in place, so `script()` is unchanged. It streams text word by word, then each tool call as a name/id chunk and two argument halves, then a closing `chunk_position="last"` chunk.
    - `agent(...)`, `blocking_tool()` and `until(...)` move from `tests/runs/test_manager.py` into `tests/agents/fakes.py`, because the API tests need them too.
27. **Per-run checkpoint threads stay** (3a: `checkpoint_thread_id = str(run.id)`). A new run in a thread does not see earlier turns. Multi-turn context is 3c's (Deferred).
28. **Spec §4.2's `LISTEN/NOTIFY` becomes the in-process `RunHub`.** One app process holds every run and every subscriber (ADR 0011), and persisting first keeps replay exact. Task 10 amends the spec.
29. **One AG-UI run per drive.**
    - The AG-UI `threadId` is `run.checkpoint_thread_id`, which the adapter uses as LangGraph's `thread_id`; a branch shares its parent's.
    - The AG-UI `runId` is a fresh UUID per drive.
    - A Researchly run therefore holds one or more `RUN_STARTED … RUN_FINISHED/RUN_ERROR` pairs: start, each resume, each restart.
30. **The API reaches the database through the manager** (`runs.sessions`). There is one dependency, `Manager`, which answers 503 when `app.state.runs` is missing (a unit-test app with no lifespan).
31. **Money is a JSON string.** `RunOut.budget_usd` and `spent_usd` are `Decimal`, which Pydantic serialises as strings, so no float rounding. The `run_summary` and `cost` events carry floats for display.
32. **A failed event write drops its rows.** `flush` takes the pending rows before writing. If the write or the status change fails, the rows are not retried, and the drive fails the run (`RUN_ERROR` with a conditional `failed` update), as 3a did for any exception.
33. **`STEP_*` events are kept.** The spike found them chatty (about 26 pairs a run), but the 3c run tree needs node boundaries. Measure there before filtering.
34. **`RunManager` owns its hub** (`self.hub = RunHub()`); there is no constructor parameter, since nothing else would pass one.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `backend/pyproject.toml`, `backend/uv.lock`, `cli/uv.lock` | modify | pin `ag-ui-langgraph` and `ag-ui-protocol` |
| `backend/tests/test_lock.py` | modify | assert the new pins in both locks |
| `docs/adr/0012-ag-ui-streaming.md` | create | pin, overrides, event log, stream |
| `backend/tests/agents/fakes.py` | modify | `ToolFake._stream`; `agent`, `blocking_tool`, `until` moved here |
| `backend/tests/agents/test_fakes.py` | create | the fake streams text and tool calls through `astream_events` |
| `backend/src/researchly/runs/events.py` | create | `StoredEvent`, `RunHub`, `RunEventLog`, `replay`, `follow` |
| `backend/tests/runs/test_events.py` | create | coalescing, skipping, scrubbing, attribution, publish-after-persist, follow |
| `backend/src/researchly/runs/agui.py` | create | `ResearchlyAgent` (adapter subclass), `allowed_decisions` |
| `backend/tests/runs/test_agui.py` | create | context, lean snapshots, typed review, continue, branch |
| `backend/src/researchly/runs/manager.py` | modify | drive through the adapter; event log; decision check; summary |
| `backend/tests/runs/test_manager.py` | modify | helpers from fakes; AG-UI event assertions; new tests |
| `backend/src/researchly/app.py` | modify | redact the adapter's logger; routers; 409 handler |
| `backend/tests/test_app.py` | modify | the adapter logger is redacted |
| `backend/src/researchly/services/identity.py` | modify | `extra["found_by"]` in `merge` |
| `backend/tests/services/test_identity.py` | modify | `found_by` union |
| `backend/src/researchly/runs/summary.py` | create | `coverage(store, project_id)` (Chao2) |
| `backend/tests/runs/test_summary.py` | create | coverage over candidate files |
| `backend/src/researchly/agents/middleware.py` | modify | `BudgetGuard` emits `CUSTOM cost` |
| `backend/tests/agents/test_middleware.py` | modify | one cost event per model call |
| `backend/src/researchly/api/deps.py` | create | `Manager` dependency, `get_or_404` |
| `backend/src/researchly/api/projects.py` | create | projects REST |
| `backend/src/researchly/api/threads.py` | create | threads REST with messages |
| `backend/src/researchly/api/runs.py` | create | runs REST and the SSE stream |
| `backend/tests/api/__init__.py`, `backend/tests/api/conftest.py` | create | `sm`, `api` fixtures, `new_thread` helper |
| `backend/tests/api/test_projects.py`, `test_runs.py`, `test_stream.py` | create | API integration tests |
| `frontend/src/api/openapi.json`, `frontend/src/api/schema.d.ts` | regenerate | TS client |
| `docs/superpowers/specs/2026-10-28-researchly-design.md` | modify | §4.2 fan-out, §17 events and persistence |
| `AGENTS.md`, `backend/AGENTS.md`, `docs/progress.md` | modify | gotchas, test fakes, session log |

## Tasks

### Task 1: AG-UI adapter pins and ADR 0012

**Files:**
- Modify: `backend/pyproject.toml`, `backend/uv.lock`, `cli/uv.lock`
- Create: `docs/adr/0012-ag-ui-streaming.md`
- Test: `backend/tests/test_lock.py`

**Interfaces:**
- Produces: `ag_ui_langgraph.LangGraphAgent` and `ag_ui.core` importable from the backend venv, pinned exactly.

- [ ] **Step 1: Write the failing test**

In `backend/tests/test_lock.py`, add two entries to the `PINS` dict (keep the existing ones):

```python
PINS = {
    "ag-ui-langgraph": "0.0.47",  # ADR 0012: the AG-UI adapter
    "ag-ui-protocol": "1.0.0",
    "deepagents": "0.7.32",
    "langchain": "1.4.7",
    "langchain-core": "1.7.3",
    "langchain-litellm": "0.11.1",
    "langgraph": "1.2.19",
    "langgraph-checkpoint-postgres": "3.2.0",
    "litellm": "1.105.1",
}
```

Also change the comment above `PINS` to `# ADR 0011 and 0012: the agent runtime and the AG-UI adapter are pinned exactly; an upgrade is an ADR plus the full suite (spec §6.6, §19.7)`. If that comment is longer than 120 characters, wrap it onto two lines.

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --directory backend pytest tests/test_lock.py -q`
Expected: FAIL in `test_agent_runtime_pins[backend/uv.lock]` and `[cli/uv.lock]`, because `ag-ui-langgraph` and `ag-ui-protocol` are `None`.

- [ ] **Step 3: Pin the dependencies**

In `backend/pyproject.toml`, after the line `"langchain-litellm==0.11.1",` add:

```toml
  "ag-ui-langgraph==0.0.47",  # AG-UI events from LangGraph (ADR 0012)
  "ag-ui-protocol==1.0.0",
```

Then regenerate both locks and check the import:

```bash
uv lock --directory backend
uv lock --directory cli
uv run --directory backend python -c "import ag_ui_langgraph, ag_ui.core; print('ok')"
```

Expected: `ok`. The spike resolved these pins with no other version changing (Spike §1). If `uv lock` moves any existing pin, or reports a conflict, stop and report it; do not loosen a pin.

- [ ] **Step 4: Write ADR 0012**

Create `docs/adr/0012-ag-ui-streaming.md`:

```markdown
# ADR 0012: AG-UI streaming: the adapter pin, our overrides and the run event log

- Status: accepted
- Date: 2026-11-10
- Spec sections: §4.2, §17, §19.7

## Context
Step 3b streams runs to clients as AG-UI events (spec §17). The spike in
`docs/research/2026-11-10-step3b-adapter-spike.md` ran `ag-ui-langgraph` 0.0.47 against our Deep Agents graph. It
works with one mandatory override and two recommended ones. Runs are asyncio tasks in one app process (ADR 0011).

## Decision
- **Pins:** `ag-ui-langgraph==0.0.47` and `ag-ui-protocol==1.0.0`, exact, asserted in both locks by
  `backend/tests/test_lock.py`. An upgrade is an ADR plus the full suite (spec §19.7). `ag-ui-a2ui-toolkit` comes in
  transitively.
- **Adapter subclass:** `runs/agui.py` `ResearchlyAgent` is the only importer of `ag_ui_langgraph`. The adapter keeps
  run state on `self`, so there is one instance per run drive.
  - Flags: `emit_raw_events=False`, `emit_interrupt_outcome=True`, `enable_legacy_on_interrupt_event=False`,
    `subagent_visibility="attributed"`.
  - `get_stream_kwargs`:
    - passes our `RunContext` instance (the adapter's dict fails `RunContext(**...)` on `thread_id`);
    - sets `input=None` to continue a checkpoint's pending tasks (resume after a pause, startup resume, branch);
    - gives a branch's `checkpoint_id` to the stream only, so the adapter's later reads see the new head.
  - `get_state_snapshot` keeps only `todos`.
  - `_interrupts_to_agui`: an HITL review gets `reason` `researchly:<tool>`, `message` (the plan or question) and a
    `responseSchema` listing the allowed decisions per action.
- **Event log** (`runs/events.py`):
  - The drive task is the only writer of its run's `run_event` rows.
  - Rows are persisted first and then published to an in-process `RunHub`, so live and replayed rows are identical
    and clients dedupe by `seq`.
  - Never stored: `RAW` and `MESSAGES_SNAPSHOT`.
  - Text, tool-argument and reasoning deltas coalesce per id until a non-delta event, 2 KB or 250 ms.
  - Error messages are replaced with "The run failed.".
  - A run's terminal event commits in the same transaction as its status.
- **Stream:** `POST /api/v1/runs/{id}/stream` on FastAPI's native SSE (`EventSourceResponse`):
  - `: ping` every 15 s, `Cache-Control: no-cache`, `X-Accel-Buffering: no`;
  - resume point: `Last-Event-ID`, else `?after=`;
  - the request body is not read.

## Consequences
- Postgres `LISTEN/NOTIFY` is not used (spec §4.2 amended): one process holds every run and every subscriber. A
  second app process would need it back.
- Test chat models must stream (`ToolFake._stream`), because the adapter drives `astream_events`.
- `_interrupts_to_agui` is an underscore hook, although its docstring names it an override point. An adapter
  upgrade must re-run `tests/runs/test_agui.py`.
- One Researchly run holds several AG-UI runs, one per drive (start, each resume, each restart).
  - Each AG-UI run opens with `RUN_STARTED` and closes with `RUN_FINISHED` or `RUN_ERROR`.
  - A pause closes the open AG-UI run with the outcome `cancelled`.
- Tool-call events repeat across a resume, so clients key them by `toolCallId`.

## Evidence
- Spike §3: the context dict fails on `thread_id`. Spike §4: without `input=None`, a continue drops the pending tool
  task. Spike §6: snapshot sizes. Spike §7: `ToolFake` cannot stream.
- Plan 3b probes:
  - A branch through the adapter's config runs from the fork, and the closing snapshot shows the new head.
  - A failing tool yields `RUN_ERROR` with the exception text as `message`.
  - `adispatch_custom_event` from middleware reaches the stream as `CUSTOM`.
```

- [ ] **Step 5: Run the lock tests and the suites**

Run: `uv run --directory backend pytest tests/test_lock.py -q`. Expected: PASS (5 passed).
Run: `uv run --directory backend pytest -q`. Expected: 726 passed, 2 skipped.
Run: `uv run --directory cli pytest -q -k "not docker"`. Expected: PASS.

- [ ] **Step 6: Lint, types, commit**

```bash
uv run --directory backend ruff check --fix; uv run --directory backend ruff format
uv run --directory backend ruff check; uv run --directory backend ruff format --check; uv run --directory backend pyright
git add backend/pyproject.toml backend/uv.lock cli/uv.lock backend/tests/test_lock.py docs/adr/0012-ag-ui-streaming.md
git commit -m "build: pin ag-ui-langgraph 0.0.47 and ag-ui-protocol 1.0.0 (ADR 0012)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: A streaming `ToolFake` and the shared agent test helpers

**Files:**
- Modify: `backend/tests/agents/fakes.py`, `backend/tests/runs/test_manager.py`
- Test: `backend/tests/agents/test_fakes.py` (create)

**Interfaces:**
- Produces, in `tests/agents/fakes.py`:
  - `ToolFake._stream(messages, stop=None, run_manager=None, **kwargs) -> Iterator[ChatGenerationChunk]`;
  - `agent(model, tools=(), middleware=(), checkpointer=None, store=None) -> CompiledStateGraph`, which is `create_agent` with `context_schema=RunContext` and an `InMemorySaver` by default;
  - `blocking_tool() -> (fetch, entered: asyncio.Event, gate: asyncio.Event, calls: list[str])`;
  - `until(check: Callable[[], Awaitable[bool]]) -> None`, which polls for up to 5 s.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/agents/test_fakes.py`:

```python
import uuid

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from researchly.agents.context import RunContext

from .fakes import agent, call, script

pytestmark = pytest.mark.anyio
CTX = RunContext(project_id=uuid.uuid4(), run_id=uuid.uuid4())


async def test_tool_fake_streams_text_and_tool_calls():
    @tool
    def echo(x: str) -> str:
        """Echo."""
        return x

    model = script(call("echo", {"x": "a"}), "The answer.")
    graph = agent(model, [echo])
    cfg = {"configurable": {"thread_id": "t"}}
    stream = graph.astream_events({"messages": [HumanMessage("go")]}, cfg, context=CTX, version="v2")
    chunks = [e["data"]["chunk"] async for e in stream if e["event"] == "on_chat_model_stream"]
    assert [c.content for c in chunks if c.content] == ["The ", "answer."]
    assert "".join(tc["args"] or "" for c in chunks for tc in c.tool_call_chunks) == '{"x": "a"}'
    messages = (await graph.aget_state(cfg)).values["messages"]
    assert [m.text for m in messages] == ["go", "", "a", "The answer."] and len(model.seen) == 2
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --directory backend pytest tests/agents/test_fakes.py -q`
Expected: FAIL with `ImportError: cannot import name 'agent'`. If only that import were fixed, the stream would still raise `ValueError: No generations found in stream` (Spike §7).

- [ ] **Step 3: Implement**

In `backend/tests/agents/fakes.py`:

1. Replace the import block at the top with:

```python
import asyncio
import hashlib
import itertools
import json
import uuid
from collections.abc import Awaitable, Callable, Iterator, Sequence
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Literal

import anyio
import numpy as np
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from researchly.agents.context import RunContext
from researchly.connectors.base import ConnectorCaps, ExternalId, PaperRecord, SearchQuery, SourceUnavailable
from researchly.db.models import EMBED_DIM, Message, Project, Run, Thread
```

2. Add this method to `ToolFake`, after `_generate`:

```python
    def _stream(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> Iterator[ChatGenerationChunk]:
        """astream_events (the AG-UI adapter) always streams, and the parent yields nothing for a tool-call-only turn."""
        self.seen.append(list(messages))
        reply = next(self.messages)
        message = AIMessage(reply) if isinstance(reply, str) else reply
        mid = f"lc_run--{uuid.uuid4()}"
        words = message.text.split(" ") if message.text else []
        for i, word in enumerate(words):
            text = word if i == len(words) - 1 else word + " "
            yield ChatGenerationChunk(message=AIMessageChunk(content=text, id=mid))
        for i, tc in enumerate(message.tool_calls):
            raw = json.dumps(tc["args"])
            half = len(raw) // 2
            parts = (
                tool_call_chunk(name=tc["name"], args="", id=tc["id"], index=i),
                tool_call_chunk(args=raw[:half], index=i),
                tool_call_chunk(args=raw[half:], index=i),
            )
            for part in parts:
                yield ChatGenerationChunk(message=AIMessageChunk(content="", id=mid, tool_call_chunks=[part]))
        yield ChatGenerationChunk(message=AIMessageChunk(content="", id=mid, chunk_position="last"))
```

3. Append these helpers at the end of the file (moved from `tests/runs/test_manager.py`, plus a `store` parameter):

```python
def agent(
    model: Any, tools: Sequence[Any] = (), middleware: Sequence[Any] = (), checkpointer: Any = None, store: Any = None
) -> Any:
    """A checkpointed `create_agent` graph with our run context, as the run manager drives it."""
    return create_agent(
        model,
        list(tools),
        middleware=list(middleware),
        checkpointer=checkpointer or InMemorySaver(),
        store=store,
        context_schema=RunContext,
    )


def blocking_tool():
    """A tool that blocks until released; records every call."""
    entered, gate, calls = asyncio.Event(), asyncio.Event(), []

    @tool
    async def fetch(x: str) -> str:
        """Fetch."""
        calls.append(x)
        entered.set()
        await gate.wait()
        return "fetched"

    return fetch, entered, gate, calls


async def until(check: Callable[[], Awaitable[bool]]) -> None:
    with anyio.fail_after(5):
        while not await check():  # noqa: ASYNC110 - polls a DB-backed condition
            await anyio.sleep(0.01)
```

In `backend/tests/runs/test_manager.py`:
- Delete the local `agent`, `until` and `blocking_tool` definitions.
- Delete the now-unused imports `anyio`, `from langchain.agents import create_agent` and `from langgraph.checkpoint.memory import InMemorySaver`.
- Change the fakes import to:

```python
from ..agents.fakes import agent, blocking_tool, call, script, seed_run, until
```

- [ ] **Step 4: Run the tests**

Run: `uv run --directory backend pytest tests/agents/test_fakes.py tests/runs/test_manager.py tests/agents -q`. Expected: PASS. 3a tests drive `ainvoke`, which still goes through `_generate`.
Run: `uv run --directory backend pytest -q`. Expected: 727 passed, 2 skipped.

- [ ] **Step 5: Lint, types, commit**

```bash
uv run --directory backend ruff check --fix; uv run --directory backend ruff format
uv run --directory backend ruff check; uv run --directory backend ruff format --check; uv run --directory backend pyright
git add backend/tests/agents/fakes.py backend/tests/agents/test_fakes.py backend/tests/runs/test_manager.py
git commit -m "test: stream text and tool calls from ToolFake and share the agent test helpers" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The run event log and the in-process hub

**Files:**
- Create: `backend/src/researchly/runs/events.py`
- Test: `backend/tests/runs/test_events.py`

**Interfaces:**
- Consumes: `RunEvent` and `Run` (`db/models.py`), `ag_ui.core.BaseEvent`.
- Produces (in `researchly.runs.events`):
  - constants `LEAD`, `SKIPPED`, `DELTAS`, `ERRORS`, `ERROR_TEXT = "The run failed."`, `ENDS = ("RUN_FINISHED", "RUN_ERROR")` and `LIVE = ("pending", "running")`;
  - the type alias `RunUpdate = Callable[[AsyncSession], Awaitable[None]]`;
  - `StoredEvent(seq: int, type: str, agent: str | None, payload: dict[str, Any])`, a frozen dataclass;
  - `RunHub()`, with `subscribe(run_id)` (a context manager yielding `asyncio.Queue[StoredEvent]`), `publish(run_id, events: list[StoredEvent])`, and the attribute `_subscribers: dict[uuid.UUID, set[Queue]]` that tests read;
  - `RunEventLog(sessions, hub, run_id, *, max_bytes=2048, max_age_s=0.25, clock=time.monotonic)`, with the attribute `open: bool`, `async append(event: BaseEvent, *, run_update: RunUpdate | None = None)` and `async flush(run_update: RunUpdate | None = None)`;
  - `async replay(sessions, run_id, after: int) -> list[StoredEvent]`;
  - `follow(sessions, hub, run_id, after: int) -> AsyncGenerator[StoredEvent, None]`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/runs/test_events.py`:

```python
import asyncio

import anyio
import pytest
from ag_ui.core import (
    CustomEvent,
    MessagesSnapshotEvent,
    RawEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StepStartedEvent,
    SubagentStartedEvent,
    TextMessageContentEvent,
    ToolCallArgsEvent,
)

from researchly.db import migrate
from researchly.db.engine import make_engine, make_sessionmaker
from researchly.runs.events import RunEventLog, RunHub, follow, replay

from ..agents.fakes import seed_run, until

pytestmark = [pytest.mark.anyio, pytest.mark.db]


def frozen() -> float:
    return 0.0


def text(delta: str, mid: str = "m1") -> TextMessageContentEvent:
    return TextMessageContentEvent(message_id=mid, delta=delta)


def step(name: str) -> StepStartedEvent:
    return StepStartedEvent(step_name=name)


@pytest.fixture
async def sm(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


async def test_adjacent_deltas_coalesce_until_a_non_delta_event(sm):
    run = await seed_run(sm)
    log = RunEventLog(sm, RunHub(), run.id, clock=frozen)
    for event in (
        text("The "),
        text("answer"),
        ToolCallArgsEvent(tool_call_id="c1", delta='{"x":'),
        ToolCallArgsEvent(tool_call_id="c1", delta='"a"}'),
        text(".", "m2"),
    ):
        await log.append(event)
    assert await replay(sm, run.id, 0) == []  # deltas wait for the next non-delta event
    await log.append(step("model"))
    got = [(e.seq, e.type, e.payload.get("delta")) for e in await replay(sm, run.id, 0)]
    assert got == [
        (1, "TEXT_MESSAGE_CONTENT", "The answer"),
        (2, "TOOL_CALL_ARGS", '{"x":"a"}'),
        (3, "TEXT_MESSAGE_CONTENT", "."),
        (4, "STEP_STARTED", None),
    ]


async def test_deltas_flush_at_2_kb_and_after_250_ms(sm):
    run = await seed_run(sm)
    now = [0.0]
    log = RunEventLog(sm, RunHub(), run.id, clock=lambda: now[0])
    await log.append(text("a" * 2047))
    assert await replay(sm, run.id, 0) == []
    await log.append(text("b"))  # 2048 bytes
    await log.append(text("c"))
    now[0] = 0.3
    await log.append(text("d"))  # the batch is 300 ms old
    assert [len(e.payload["delta"]) for e in await replay(sm, run.id, 0)] == [2048, 2]


async def test_raw_events_and_message_snapshots_are_skipped_and_errors_scrubbed(sm):
    run = await seed_run(sm)
    log = RunEventLog(sm, RunHub(), run.id)
    await log.append(RawEvent(event={"provider": "secret"}))
    await log.append(MessagesSnapshotEvent(messages=[]))
    await log.append(RunErrorEvent(message="provider said sk-secret", code="APIError"))
    got = [(e.type, e.payload) for e in await replay(sm, run.id, 0)]
    assert got == [("RUN_ERROR", {"type": "RUN_ERROR", "message": "The run failed.", "code": "APIError"})]


async def test_subagent_events_are_attributed_by_name(sm):
    run = await seed_run(sm)
    log = RunEventLog(sm, RunHub(), run.id)
    await log.append(SubagentStartedEvent(subagent_run_id="tools:1", name="scout"))
    await log.append(CustomEvent(name="cost", value={}, subagent_run_id="tools:1"))
    await log.append(CustomEvent(name="cost", value={}, subagent_run_id="tools:2"))
    await log.append(step("model"))
    assert [e.agent for e in await replay(sm, run.id, 0)] == ["scout", "scout", "subagent", "lead"]


async def test_subscribers_get_exactly_the_persisted_rows(sm):
    run = await seed_run(sm)
    hub = RunHub()
    log = RunEventLog(sm, hub, run.id, clock=frozen)
    with hub.subscribe(run.id) as queue:
        for event in (text("a"), text("b"), step("x")):
            await log.append(event)
        live = [queue.get_nowait() for _ in range(queue.qsize())]
    assert live == await replay(sm, run.id, 0) and [e.seq for e in live] == [1, 2]
    assert run.id not in hub._subscribers


async def test_seq_continues_across_logs(sm):
    run = await seed_run(sm)
    await RunEventLog(sm, RunHub(), run.id).append(step("a"))
    await RunEventLog(sm, RunHub(), run.id).append(step("b"))
    assert [e.seq for e in await replay(sm, run.id, 0)] == [1, 2]


async def test_a_failed_run_update_persists_nothing(sm):
    run = await seed_run(sm)

    async def boom(session) -> None:
        raise RuntimeError("no")

    log = RunEventLog(sm, RunHub(), run.id, clock=frozen)
    await log.append(text("a"))
    with pytest.raises(RuntimeError):
        await log.append(RunStartedEvent(thread_id="t", run_id="r"), run_update=boom)
    assert await replay(sm, run.id, 0) == [] and not log.open


async def test_follow_replays_then_tails_until_the_ag_ui_run_ends(sm):
    run = await seed_run(sm, status="running")
    hub = RunHub()
    log = RunEventLog(sm, hub, run.id)
    await log.append(RunStartedEvent(thread_id="t", run_id="r"))
    await log.append(step("model"))
    assert log.open
    got: list[str] = []

    async def consume() -> None:
        async for event in follow(sm, hub, run.id, 1):
            got.append(event.type)

    task = asyncio.create_task(consume())

    async def subscribed() -> bool:
        return run.id in hub._subscribers

    await until(subscribed)
    await log.append(RunFinishedEvent(thread_id="t", run_id="r"))
    with anyio.fail_after(5):
        await task
    assert got == ["STEP_STARTED", "RUN_FINISHED"] and not log.open


async def test_follow_ends_after_the_replay_when_the_run_is_at_rest(sm):
    run = await seed_run(sm, status="completed")
    log = RunEventLog(sm, RunHub(), run.id)
    await log.append(RunStartedEvent(thread_id="t", run_id="r"))
    await log.append(RunFinishedEvent(thread_id="t", run_id="r"))
    with anyio.fail_after(5):
        assert [e.seq async for e in follow(sm, RunHub(), run.id, 0)] == [1, 2]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --directory backend pytest tests/runs/test_events.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'researchly.runs.events'`.

- [ ] **Step 3: Implement**

Create `backend/src/researchly/runs/events.py`:

```python
"""The run event log (spec §17, ADR 0012): AG-UI events persisted to `run_event`, then fanned out in process.

The drive task is the only writer of its run's events. Rows are published only after they commit, so a live
subscriber and a replay see the same rows with the same `seq`, and a client dedupes by `seq`.
"""

import asyncio
import contextlib
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any

from ag_ui.core import BaseEvent
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from researchly.db.models import Run, RunEvent

LEAD = "lead"
SKIPPED = ("RAW", "MESSAGES_SNAPSHOT")  # RAW repeats LangGraph's own events; message snapshots grow quadratically
DELTAS = {"TEXT_MESSAGE_CONTENT": "messageId", "TOOL_CALL_ARGS": "toolCallId", "REASONING_MESSAGE_CONTENT": "messageId"}
ERRORS = ("RUN_ERROR", "SUBAGENT_ERROR")
ERROR_TEXT = "The run failed."  # the adapter puts str(exc) here, which can carry a provider's response
ENDS = ("RUN_FINISHED", "RUN_ERROR")
LIVE = ("pending", "running")

RunUpdate = Callable[[AsyncSession], Awaitable[None]]


@dataclass(frozen=True)
class StoredEvent:
    seq: int
    type: str
    agent: str | None
    payload: dict[str, Any]


class RunHub:
    """Per-run publish/subscribe inside the app process (spec §4.2: replaces LISTEN/NOTIFY)."""

    def __init__(self) -> None:
        self._subscribers: defaultdict[uuid.UUID, set[asyncio.Queue[StoredEvent]]] = defaultdict(set)

    @contextlib.contextmanager
    def subscribe(self, run_id: uuid.UUID) -> Iterator[asyncio.Queue[StoredEvent]]:
        # ponytail: unbounded queue; a stalled client buffers one run's events until it disconnects
        queue: asyncio.Queue[StoredEvent] = asyncio.Queue()
        self._subscribers[run_id].add(queue)
        try:
            yield queue
        finally:
            self._subscribers[run_id].discard(queue)
            if not self._subscribers[run_id]:
                del self._subscribers[run_id]

    def publish(self, run_id: uuid.UUID, events: list[StoredEvent]) -> None:
        for queue in self._subscribers.get(run_id, ()):
            for event in events:
                queue.put_nowait(event)


class RunEventLog:
    """Appends one run's AG-UI events: skips RAW and message snapshots, scrubs errors, coalesces deltas."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        hub: RunHub,
        run_id: uuid.UUID,
        *,
        max_bytes: int = 2048,
        max_age_s: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sessions, self.hub, self.run_id = sessions, hub, run_id
        self.max_bytes, self.max_age_s, self.clock = max_bytes, max_age_s, clock
        self.open = False  # an AG-UI run has started and not yet finished
        self._pending: list[tuple[str, str, dict[str, Any]]] = []  # deltas only: anything else flushes at once
        self._since: float | None = None
        self._seq: int | None = None
        self._agents: dict[str, str] = {}  # subagentRunId -> subagent name, from SUBAGENT_STARTED

    async def append(self, event: BaseEvent, *, run_update: RunUpdate | None = None) -> None:
        kind = event.type.value
        if kind in SKIPPED:
            return
        payload = event.model_dump(mode="json", by_alias=True, exclude_none=True)
        if kind in ERRORS:
            payload["message"] = ERROR_TEXT
        sub = payload.get("subagentRunId")
        if kind == "SUBAGENT_STARTED":
            self._agents[sub] = payload["name"]
        agent = LEAD if sub is None else self._agents.get(sub, "subagent")
        key = DELTAS.get(kind)
        if key is None:
            self._pending.append((kind, agent, payload))
            await self.flush(run_update)
            return
        last = self._pending[-1] if self._pending else None
        if last is not None and last[0] == kind and last[2][key] == payload[key]:
            last[2]["delta"] += payload["delta"]
            delta = last[2]["delta"]
        else:
            self._pending.append((kind, agent, payload))
            delta = payload["delta"]
        if self._since is None:
            self._since = self.clock()
        # no timer task: the batch's age is checked when the next event arrives (an END event always follows)
        if len(delta.encode()) >= self.max_bytes or self.clock() - self._since >= self.max_age_s:
            await self.flush()

    async def flush(self, run_update: RunUpdate | None = None) -> None:
        """Commit the pending rows, and the run's status change, in one transaction; then publish them."""
        rows, self._pending, self._since = self._pending, [], None  # a failed write drops them; the run then fails
        if not rows and run_update is None:
            return
        async with self.sessions.begin() as session:
            base = self._seq
            if base is None:
                last = select(func.max(RunEvent.seq)).where(RunEvent.run_id == self.run_id)
                base = await session.scalar(last) or 0
            stored = [StoredEvent(base + i, kind, agent, payload) for i, (kind, agent, payload) in enumerate(rows, 1)]
            session.add_all(
                RunEvent(run_id=self.run_id, seq=e.seq, type=e.type, agent=e.agent, payload=e.payload) for e in stored
            )
            if run_update is not None:
                await run_update(session)
        self._seq = base + len(stored)
        for event in stored:
            if event.type == "RUN_STARTED":
                self.open = True
            elif event.type in ENDS:
                self.open = False
        self.hub.publish(self.run_id, stored)


async def replay(sessions: async_sessionmaker[AsyncSession], run_id: uuid.UUID, after: int) -> list[StoredEvent]:
    # ponytail: loads the whole tail at once; page it if run logs grow past a few thousand rows
    stmt = (
        select(RunEvent.seq, RunEvent.type, RunEvent.agent, RunEvent.payload)
        .where(RunEvent.run_id == run_id, RunEvent.seq > after)
        .order_by(RunEvent.seq)
    )
    async with sessions() as session:
        rows = (await session.execute(stmt)).all()
    return [StoredEvent(seq=r.seq, type=r.type, agent=r.agent, payload=r.payload) for r in rows]


async def follow(
    sessions: async_sessionmaker[AsyncSession], hub: RunHub, run_id: uuid.UUID, after: int
) -> AsyncGenerator[StoredEvent, None]:
    """Replay the rows after `after`, then tail live rows until the current AG-UI run ends.

    Subscribing first means no row falls between the replay and the tail; rows already replayed are skipped by
    `seq`. A run at rest ends the stream after its replay.
    """
    with hub.subscribe(run_id) as queue:
        async with sessions() as session:
            status = await session.scalar(select(Run.status).where(Run.id == run_id))
        last = after
        for event in await replay(sessions, run_id, after):
            yield event
            last = event.seq
        if status not in LIVE:
            return
        while True:
            event = await queue.get()
            if event.seq > last:
                yield event
                last = event.seq
            if event.type in ENDS:  # even one the replay already sent: that was this run's end
                return
```

- [ ] **Step 4: Run the tests**

Run: `uv run --directory backend pytest tests/runs/test_events.py -q`. Expected: 9 passed.
Run: `uv run --directory backend pytest -q`. Expected: 736 passed, 2 skipped.

- [ ] **Step 5: Lint, types, commit**

```bash
uv run --directory backend ruff check --fix; uv run --directory backend ruff format
uv run --directory backend ruff check; uv run --directory backend ruff format --check; uv run --directory backend pyright
git add backend/src/researchly/runs/events.py backend/tests/runs/test_events.py
git commit -m "feat: persist AG-UI run events and fan them out in process" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The AG-UI adapter subclass

**Files:**
- Create: `backend/src/researchly/runs/agui.py`
- Test: `backend/tests/runs/test_agui.py`

**Interfaces:**
- Consumes: `ag_ui_langgraph.LangGraphAgent` and `RunContext`.
- Produces (in `researchly.runs.agui`):
  - `ResearchlyAgent(graph, context: RunContext, *, continue_pending: bool = False, checkpoint_id: str | None = None)`, whose `.run(RunAgentInput)` yields AG-UI events;
  - `allowed_decisions(interrupt: Mapping[str, Any]) -> list[list[str]] | None`, which reads a stored (camelCase) interrupt.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/runs/test_agui.py`:

```python
import uuid
from typing import Any

import pytest
from ag_ui.core import EventType, ResumeEntry, RunAgentInput, UserMessage
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from researchly.agents.context import RunContext
from researchly.runs.agui import ResearchlyAgent, allowed_decisions

from ..agents.fakes import agent, call, script

pytestmark = pytest.mark.anyio
CTX = RunContext(project_id=uuid.uuid4(), run_id=uuid.uuid4())
CFG: Any = {"configurable": {"thread_id": "t"}}


def ask(text: str = "Q?") -> list[Any]:
    return [UserMessage(id=str(uuid.uuid4()), content=text)]


async def drive(adapter: ResearchlyAgent, messages: list[Any] | None = None, resume: Any = None) -> list[Any]:
    run = RunAgentInput(thread_id="t", run_id=str(uuid.uuid4()), messages=messages or [], resume=resume)
    return [event async for event in adapter.run(run)]


@tool
def submit_plan(plan: str) -> str:
    """Submit the plan."""
    return "approved"


async def test_tools_receive_our_run_context():
    seen = []

    @tool
    def whoami(runtime: ToolRuntime[RunContext]) -> str:
        """Who am I."""
        seen.append(runtime.context)
        return "ok"

    events = await drive(ResearchlyAgent(agent(script(call("whoami", {}), "done"), [whoami]), CTX), ask())
    assert seen == [CTX] and events[-1].type == EventType.RUN_FINISHED  # a dict context fails on thread_id


async def test_state_snapshots_carry_only_todos():
    adapter = ResearchlyAgent(agent(script("done")), CTX)
    events = await drive(adapter, ask())
    assert [e.snapshot for e in events if e.type == EventType.STATE_SNAPSHOT] == [{}]
    assert adapter.get_state_snapshot({"messages": [], "files": {"/a": {}}, "todos": [{"content": "x"}]}) == {
        "todos": [{"content": "x"}]
    }


async def test_a_plan_review_is_a_typed_interrupt_and_an_approval_completes():
    hitl = HumanInTheLoopMiddleware(interrupt_on={"submit_plan": {"allowed_decisions": ["approve", "edit", "reject"]}})
    graph = agent(script(call("submit_plan", {"plan": "1. search"}), "Done."), [submit_plan], [hitl])
    events = await drive(ResearchlyAgent(graph, CTX), ask())
    assert not any(e.type == EventType.CUSTOM for e in events)  # no legacy on_interrupt event
    finished = events[-1].model_dump(mode="json", by_alias=True, exclude_none=True)
    [interrupt] = finished["outcome"]["interrupts"]
    assert (interrupt["reason"], interrupt["message"]) == ("researchly:submit_plan", "1. search")
    assert allowed_decisions(interrupt) == [["approve", "edit", "reject"]]
    assert allowed_decisions({"reason": "langgraph:interrupt"}) is None

    state = await graph.aget_state(CFG)
    decisions = {"decisions": [{"type": "approve"}]}
    resume = [ResumeEntry(interrupt_id=state.interrupts[0].id, status="resolved", payload=decisions)]
    events = await drive(ResearchlyAgent(graph, CTX), resume=resume)
    assert events[-1].type == EventType.RUN_FINISHED and events[-1].outcome is None
    assert (await graph.aget_state(CFG)).values["messages"][-1].text == "Done."


async def test_continue_runs_the_pending_tool_step():
    calls = []

    @tool
    def fetch(x: str) -> str:
        """Fetch."""
        calls.append(x)
        return "fetched"

    model = script(call("fetch", {"x": "a"}), "done")
    graph = create_agent(
        model, [fetch], checkpointer=InMemorySaver(), context_schema=RunContext, interrupt_before=["tools"]
    )
    await drive(ResearchlyAgent(graph, CTX), ask())
    assert (await graph.aget_state(CFG)).next == ("tools",) and calls == []
    await drive(ResearchlyAgent(graph, CTX, continue_pending=True))
    messages = (await graph.aget_state(CFG)).values["messages"]
    assert calls == ["a"] and messages[-1].text == "done"
    assert not any(isinstance(m, ToolMessage) and m.status == "error" for m in messages)  # Spike §4


async def test_a_branch_runs_from_its_fork_and_the_snapshot_shows_the_new_head():
    graph = agent(script("first answer", "second answer"))
    await drive(ResearchlyAgent(graph, CTX), ask())
    [point] = [s async for s in graph.aget_state_history(CFG) if s.next == ("model",)]
    checkpoint_id = point.config["configurable"]["checkpoint_id"]
    events = await drive(ResearchlyAgent(graph, CTX, continue_pending=True, checkpoint_id=checkpoint_id))
    [snapshot] = [e for e in events if e.type == EventType.MESSAGES_SNAPSHOT]
    assert snapshot.messages[-1].content == "second answer"
    assert [m.text for m in (await graph.aget_state(CFG)).values["messages"]] == ["Q?", "second answer"]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --directory backend pytest tests/runs/test_agui.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'researchly.runs.agui'`.

- [ ] **Step 3: Implement**

Create `backend/src/researchly/runs/agui.py`:

```python
"""Our AG-UI adapter (ADR 0012): ag-ui-langgraph's LangGraphAgent with our context, continue and review shape.

The adapter keeps per-run state on `self`, so the run manager builds one instance per drive. This is the only
module importing `ag_ui_langgraph`; it needs only the compiled graph, not deepagents (Spike §8).
"""

from collections.abc import Mapping
from typing import Any, Literal

from ag_ui_langgraph import LangGraphAgent
from langchain_core.runnables import RunnableConfig

from researchly.agents.context import RunContext

REVIEW = "researchly:"


def _review(raw: Any, interrupt: Any) -> Any:
    """HumanInTheLoopMiddleware's interrupt as a typed review: what is asked, and which decisions fit each action."""
    if not isinstance(raw, Mapping) or not raw.get("action_requests"):
        return interrupt
    requests, configs = raw["action_requests"], raw["review_configs"]
    items = [
        {"type": "object", "required": ["type"], "properties": {"type": {"enum": c["allowed_decisions"]}}}
        for c in configs
    ]
    decisions = {"type": "array", "minItems": len(requests), "maxItems": len(requests), "prefixItems": items}
    return interrupt.model_copy(
        update={
            "reason": REVIEW + "+".join(r["name"] for r in requests),
            "message": "\n\n".join(str(r["args"].get("plan") or r["args"].get("question") or "") for r in requests),
            "response_schema": {"type": "object", "required": ["decisions"], "properties": {"decisions": decisions}},
        }
    )


def allowed_decisions(interrupt: Mapping[str, Any]) -> list[list[str]] | None:
    """Per action, the decision types a stored (camelCase) review accepts; None if the interrupt is not a review."""
    try:
        items = interrupt["responseSchema"]["properties"]["decisions"]["prefixItems"]
    except (KeyError, TypeError):
        return None
    return [list(item["properties"]["type"]["enum"]) for item in items]


class ResearchlyAgent(LangGraphAgent):
    def __init__(
        self, graph: Any, context: RunContext, *, continue_pending: bool = False, checkpoint_id: str | None = None
    ) -> None:
        super().__init__(
            name="researchly",
            graph=graph,
            config={"configurable": {"checkpoint_id": checkpoint_id}} if checkpoint_id else None,
            emit_raw_events=False,
            emit_interrupt_outcome=True,
            enable_legacy_on_interrupt_event=False,
            subagent_visibility="attributed",
        )
        self.context, self.continue_pending = context, continue_pending

    def get_stream_kwargs(
        self,
        input: Any,
        subgraphs: bool = False,
        version: Literal["v1", "v2"] = "v2",
        config: RunnableConfig | None = None,
        context: dict[str, Any] | None = None,
        fork: Any | None = None,
    ) -> dict[str, Any]:
        kwargs = super().get_stream_kwargs(
            input, subgraphs=subgraphs, version=version, config=config, context=context, fork=fork
        )
        kwargs["context"] = self.context  # the adapter builds a dict, which RunContext(**...) rejects (Spike §3)
        if self.continue_pending:
            kwargs["input"] = None  # run the checkpoint's pending tasks, not a new step (Spike §4)
        configurable = config.get("configurable", {}) if config else {}
        if config and "checkpoint_id" in configurable:
            kwargs["config"] = {**config, "configurable": dict(configurable)}  # the fork point, for the stream only
            del configurable["checkpoint_id"]  # the adapter re-reads state through this config after the stream
        return kwargs

    def get_state_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        return {"todos": state["todos"]} if "todos" in state else {}  # files and messages stay off the wire

    def _interrupts_to_agui(self, lg_interrupts: Any) -> list[Any]:
        out = super()._interrupts_to_agui(lg_interrupts)
        return [_review(lg.value, i) for lg, i in zip(lg_interrupts, out, strict=True)]
```

- [ ] **Step 4: Run the tests**

Run: `uv run --directory backend pytest tests/runs/test_agui.py -q`. Expected: 5 passed.
Run: `uv run --directory backend pytest -q`. Expected: 741 passed, 2 skipped.

- [ ] **Step 5: Lint, types, commit**

```bash
uv run --directory backend ruff check --fix; uv run --directory backend ruff format
uv run --directory backend ruff check; uv run --directory backend ruff format --check; uv run --directory backend pyright
git add backend/src/researchly/runs/agui.py backend/tests/runs/test_agui.py
git commit -m "feat: subclass the AG-UI LangGraph adapter for our context, continue and plan reviews" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The run manager drives runs through the adapter

**Files:**
- Modify: `backend/src/researchly/runs/manager.py`, `backend/src/researchly/app.py`
- Test: `backend/tests/runs/test_manager.py`, `backend/tests/test_app.py`

**Interfaces:**
- Consumes:
  - `RunEventLog`, `RunHub`, `RunUpdate` (Task 3);
  - `ResearchlyAgent`, `allowed_decisions` (Task 4);
  - from `ag_ui.core`: `ResumeEntry`, `RunAgentInput`, `RunErrorEvent`, `RunFinishedEvent`, `RunFinishedCancelledOutcome`, `RunFinishedInterruptOutcome`, `RunStartedEvent`, `UserMessage`.
- Produces:
  - `RunManager(sessions, build)` with a new attribute `hub: RunHub`. The public methods are unchanged: `create`, `start`, `resume(run_id, decisions=None)`, `pause`, `cancel`, `branch(run_id, checkpoint_id) -> Run`, `resume_running`, `wait`, `shutdown`.
  - `RUN_ERROR = "RUN_ERROR"` is kept; `INTERRUPT` is removed.
  - A run's events are in `run_event` as AG-UI rows.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/runs/test_manager.py`:

1. Change the manager import to `from researchly.runs.manager import RUN_ERROR, RunManager, RunStateError`.

2. Add this helper after `events`:

```python
async def kinds(sm, run_id: uuid.UUID) -> list[str]:
    async with sm() as session:
        stmt = select(RunEvent.type).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)
        return list(await session.scalars(stmt))
```

3. In `test_ask_user_waits_for_the_answer`, replace the two lines

```python
    [event] = await events(sm, run.id, INTERRUPT)
    assert event["interrupts"][0]["action_requests"][0]["name"] == "ask_user"
```

with

```python
    [finished] = await events(sm, run.id, "RUN_FINISHED")
    [interrupt] = finished["outcome"]["interrupts"]
    assert (interrupt["reason"], interrupt["message"]) == ("researchly:ask_user", "Which years?")
```

4. In `test_a_failing_build_fails_the_run_with_an_event`, replace the last line with

```python
    assert await events(sm, run.id, RUN_ERROR) == [{"type": "RUN_ERROR", "message": "The run failed.", "code": "ValueError"}]
```

5. Append these tests:

```python
async def test_a_run_logs_its_ag_ui_events(sm):
    run = await seed_run(sm)
    m = manager(sm, agent(script("The answer.")))
    await m.start(run.id)
    await m.wait(run.id)
    logged = await kinds(sm, run.id)
    assert logged[0] == "RUN_STARTED" and logged[-1] == "RUN_FINISHED"
    assert "TEXT_MESSAGE_CONTENT" in logged and not {"RAW", "MESSAGES_SNAPSHOT"} & set(logged)
    async with sm() as session:
        seqs = list(await session.scalars(select(RunEvent.seq).where(RunEvent.run_id == run.id).order_by(RunEvent.seq)))
    assert seqs == list(range(1, len(seqs) + 1))


async def test_a_pause_closes_the_open_ag_ui_run(sm):
    run = await seed_run(sm)
    fetch, entered, gate, calls = blocking_tool()
    m = manager(sm, agent(script(call("fetch", {"x": "a"}), "done"), [fetch]))
    await m.start(run.id)
    await entered.wait()
    await m.pause(run.id)
    assert [e.get("outcome") for e in await events(sm, run.id, "RUN_FINISHED")] == [{"type": "cancelled"}]
    gate.set()
    await m.resume(run.id)
    await m.wait(run.id)
    assert (await kinds(sm, run.id)).count("RUN_STARTED") == 2
    assert [e.get("outcome") for e in await events(sm, run.id, "RUN_FINISHED")] == [{"type": "cancelled"}, None]


async def test_decisions_must_match_the_open_review(sm):
    run = await seed_run(sm)

    @tool
    def submit_plan(plan: str) -> str:
        """Submit the plan."""
        return "approved"

    hitl = HumanInTheLoopMiddleware(interrupt_on={"submit_plan": {"allowed_decisions": ["approve", "edit", "reject"]}})
    m = manager(sm, agent(script(call("submit_plan", {"plan": "p"}), "Done."), [submit_plan], [hitl]))
    await m.start(run.id)
    await m.wait(run.id)
    for wrong in ([{"type": "respond", "message": "x"}], [{"type": "approve"}, {"type": "approve"}]):
        with pytest.raises(RunStateError, match="open review"):
            await m.resume(run.id, wrong)
    assert (await row(sm, run.id)).status == "awaiting_user"
    await m.resume(run.id, [{"type": "approve"}])
    await m.wait(run.id)
    assert (await row(sm, run.id)).status == "completed" and await answers(sm, run.id) == ["Done."]
```

In `backend/tests/test_app.py`, add `import logging` and `from researchly.security.redact import RedactingFilter, redact` (replacing the existing `redact` import), then append:

```python
def test_the_adapter_logger_is_redacted():
    create_app(Settings(env="test"), token="t" * 43)
    assert any(isinstance(f, RedactingFilter) for f in logging.getLogger("ag_ui_langgraph.agent").filters)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --directory backend pytest tests/runs/test_manager.py tests/test_app.py -q`
Expected: FAIL.
- `test_ask_user_waits_for_the_answer`: no `RUN_FINISHED` rows yet.
- `test_a_failing_build...`: the payload is still `{"error": "ValueError"}`.
- The three new manager tests fail, and so does `test_the_adapter_logger_is_redacted`.

- [ ] **Step 3: Implement**

Replace `backend/src/researchly/runs/manager.py` with:

```python
"""Runs as in-process asyncio tasks over a checkpointed graph (spec §6.5, §16), driven through the AG-UI adapter.

The database row is the source of truth for a run's status; LangGraph's checkpoints hold its progress. Every status
change is a conditional UPDATE, so two callers can never both move a run. The drive task is the only writer of its
run's events (ADR 0012), and a run's terminal event commits together with its new status.
"""

import asyncio
import contextlib
import logging
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from ag_ui.core import (
    ResumeEntry,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedCancelledOutcome,
    RunFinishedEvent,
    RunFinishedInterruptOutcome,
    RunStartedEvent,
    UserMessage,
)
from langchain_core.messages import ToolMessage
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from researchly.agents.context import EFFORTS, EffortName, RunContext
from researchly.agents.middleware import BUDGET_SPENT, LOOP_STOP
from researchly.db.models import Message, Project, Run, RunEvent
from researchly.llm.ledger import spent_usd
from researchly.runs.agui import ResearchlyAgent, allowed_decisions
from researchly.runs.events import RunEventLog, RunHub, RunUpdate

logger = logging.getLogger(__name__)

TERMINAL = ("partial", "completed", "cancelled", "failed")
RUN_ERROR = "RUN_ERROR"
STOPPED = (BUDGET_SPENT, LOOP_STOP)
LIMIT_HIT = "Tool call limit exceeded. Do not make additional tool calls."  # langchain ToolCallLimitMiddleware


class RunStateError(Exception):
    """The run's status does not allow this action."""


def _now() -> datetime:
    return datetime.now(UTC)


class RunManager:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], build: Callable[[Run], Awaitable[Any]]) -> None:
        self.sessions, self.build = sessions, build
        self.hub = RunHub()
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        # a pause or cancel must not interleave with a resume or start of the same run
        # ponytail: in-process locks; one app process
        self._locks: defaultdict[uuid.UUID, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def create(
        self,
        project_id: uuid.UUID,
        thread_id: uuid.UUID,
        question: str,
        *,
        effort: EffortName = "standard",
        budget_usd: Decimal | None = None,
    ) -> Run:
        run_id = uuid.uuid4()
        run = Run(
            id=run_id,
            project_id=project_id,
            thread_id=thread_id,
            effort=effort,
            status="pending",
            budget_usd=EFFORTS[effort].budget_usd if budget_usd is None else budget_usd,
            spent_usd=Decimal(0),
            checkpoint_thread_id=str(run_id),
        )
        async with self.sessions.begin() as session:
            session.add(run)
            await session.flush()
            session.add(Message(thread_id=thread_id, role="user", content={"text": question}, run_id=run_id))
        return run

    async def start(self, run_id: uuid.UUID) -> None:
        async with self._locks[run_id]:
            async with self.sessions.begin() as session:
                await self._set(session, run_id, "running", ("pending",), started_at=_now())
            self._spawn(run_id, None)

    async def resume(self, run_id: uuid.UUID, decisions: list[dict[str, Any]] | None = None) -> None:
        async with self._locks[run_id]:
            await self._resume(run_id, decisions)

    async def _resume(self, run_id: uuid.UUID, decisions: list[dict[str, Any]] | None) -> None:
        async with self.sessions() as session:
            run = await session.get_one(Run, run_id)
            superseded = await self._superseded(session, run)
        if superseded:
            raise RunStateError("a newer branch owns this run's checkpoints; resume the branch instead")
        if decisions:  # a run paused while awaiting the user still has its interrupt pending
            if run.status not in ("paused", "awaiting_user"):
                raise RunStateError(f"run {run_id} is {run.status}; decisions are for a paused or waiting run")
            await self._check(run_id, decisions)
            if any(d.get("type") == "reject" for d in decisions):
                await self._cancel(run_id)
                return
        elif run.status == "awaiting_user":
            raise RunStateError("this run is waiting for the user's decisions")
        async with self.sessions.begin() as session:
            await self._set(session, run_id, "running", ("paused", "awaiting_user"))
        self._spawn(run_id, decisions or None)

    async def _check(self, run_id: uuid.UUID, decisions: list[dict[str, Any]]) -> None:
        """Decisions must answer the open review action by action: HITL fails the run on a mismatch."""
        stmt = select(RunEvent.payload).where(RunEvent.run_id == run_id, RunEvent.type == "RUN_FINISHED")
        async with self.sessions() as session:
            last = await session.scalar(stmt.order_by(RunEvent.seq.desc()).limit(1))
        outcome = (last or {}).get("outcome") or {}
        allowed = allowed_decisions(outcome["interrupts"][0]) if outcome.get("type") == "interrupt" else None
        if (
            allowed is None
            or len(allowed) != len(decisions)
            or any(d.get("type") not in a for d, a in zip(decisions, allowed, strict=True))
        ):
            raise RunStateError(f"these decisions do not answer the run's open review (allowed: {allowed})")

    async def pause(self, run_id: uuid.UUID) -> None:
        async with self._locks[run_id]:
            await self._stop(run_id)
            async with self.sessions.begin() as session:
                await self._set(session, run_id, "paused", ("running", "awaiting_user"))

    async def cancel(self, run_id: uuid.UUID) -> None:
        async with self._locks[run_id]:
            await self._cancel(run_id)

    async def _cancel(self, run_id: uuid.UUID) -> None:
        await self._stop(run_id)
        async with self.sessions.begin() as session:
            await self._set(
                session, run_id, "cancelled", ("pending", "running", "awaiting_user", "paused"), ended_at=_now()
            )

    async def branch(self, run_id: uuid.UUID, checkpoint_id: str) -> Run:
        async with self._locks[run_id]:
            return await self._branch(run_id, checkpoint_id)

    async def _branch(self, run_id: uuid.UUID, checkpoint_id: str) -> Run:
        if run_id in self._tasks:
            raise RunStateError("pause the run before branching it")
        async with self.sessions.begin() as session:
            parent = await session.get_one(Run, run_id)
            if parent.status not in (*TERMINAL, "paused"):
                raise RunStateError(f"run {run_id} is {parent.status}; only a paused or finished run can be branched")
            child = Run(
                id=uuid.uuid4(),
                project_id=parent.project_id,
                thread_id=parent.thread_id,
                effort=parent.effort,
                status="running",
                budget_usd=parent.budget_usd,
                spent_usd=Decimal(0),
                checkpoint_thread_id=parent.checkpoint_thread_id,
                parent_run_id=parent.id,
                branch_from_checkpoint=checkpoint_id,
                started_at=_now(),
            )
            session.add(child)
        self._spawn(child.id, None, checkpoint_id)
        return child

    async def resume_running(self) -> None:
        """At startup: runs a stopped app left `running` continue from their last checkpoint."""
        async with self.sessions() as session:
            runs = (await session.scalars(select(Run).where(Run.status == "running"))).all()
            superseded = {r.id for r in runs if await self._superseded(session, r)}
        for run in runs:
            if run.id in superseded:  # its thread's latest checkpoint belongs to a newer branch
                async with self.sessions.begin() as session:
                    await self._set(session, run.id, "paused", ("running",))
            else:
                self._spawn(run.id, None)

    async def wait(self, run_id: uuid.UUID) -> None:
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.wait({task})  # never raises: _drive records failures on the run

    async def shutdown(self) -> None:
        """App stop: cancel the tasks and leave their runs `running`, so the next start resumes them."""
        for run_id in list(self._tasks):
            await self._stop(run_id)

    def _spawn(
        self, run_id: uuid.UUID, decisions: list[dict[str, Any]] | None, checkpoint_id: str | None = None
    ) -> None:
        task = asyncio.create_task(self._drive(run_id, decisions, checkpoint_id))
        self._tasks[run_id] = task

        def forget(done: asyncio.Task[None]) -> None:
            if self._tasks.get(run_id) is done:
                del self._tasks[run_id]

        task.add_done_callback(forget)

    async def _stop(self, run_id: uuid.UUID) -> None:
        task = self._tasks.get(run_id)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _drive(
        self, run_id: uuid.UUID, decisions: list[dict[str, Any]] | None, checkpoint_id: str | None
    ) -> None:
        log = RunEventLog(self.sessions, self.hub, run_id)
        thread, agui_run = str(run_id), str(uuid.uuid4())  # one AG-UI run per drive
        try:
            async with self.sessions() as session:
                run = await session.get_one(Run, run_id)
                project = await session.get_one(Project, run.project_id)
            thread = run.checkpoint_thread_id  # the AG-UI thread is LangGraph's thread; branches share it
            graph = await self.build(run)
            if checkpoint_id is None and run.branch_from_checkpoint is not None and run.started_at is not None:
                latest = await graph.aget_state({"configurable": {"thread_id": thread}})
                if latest.created_at is None or datetime.fromisoformat(latest.created_at) < run.started_at:
                    checkpoint_id = run.branch_from_checkpoint  # stopped before the branch's first checkpoint
            configurable = {"thread_id": thread}
            if checkpoint_id is not None:
                configurable["checkpoint_id"] = checkpoint_id
            state = await graph.aget_state({"configurable": configurable})
            ctx = RunContext(
                project_id=run.project_id,
                run_id=run.id,
                effort=cast(EffortName, run.effort),
                budget_usd=run.budget_usd,
                domains=frozenset(project.settings.get("domains", [])),
            )
            messages: list[Any] = []
            resume: list[ResumeEntry] | None = None
            if decisions and state.interrupts:
                payload = {"decisions": decisions}
                resume = [ResumeEntry(interrupt_id=state.interrupts[0].id, status="resolved", payload=payload)]
            elif not state.values:  # a new run, or one stopped before its first checkpoint
                question = await self._question(run_id)
                messages = [UserMessage(id=str(question.id), content=str(question.content["text"]))]
            adapter = ResearchlyAgent(
                graph, ctx, continue_pending=resume is None and not messages, checkpoint_id=checkpoint_id
            )
            end: RunFinishedEvent | RunErrorEvent | None = None
            agui_input = RunAgentInput(thread_id=thread, run_id=agui_run, messages=messages, resume=resume)
            async with contextlib.aclosing(adapter.run(agui_input)) as events:
                async for event in events:
                    if isinstance(event, RunFinishedEvent | RunErrorEvent):
                        end = event  # held back: it commits together with the run's new status
                    else:
                        await log.append(event)
            if end is None:
                raise RuntimeError("the AG-UI run ended without a terminal event")
            await self._finish(run, graph, log, end)
        except asyncio.CancelledError:  # pause, cancel or app stop: close the open AG-UI run for subscribers
            if log.open:
                cancelled = RunFinishedEvent(thread_id=thread, run_id=agui_run, outcome=RunFinishedCancelledOutcome())
                await log.append(cancelled)
            raise
        except Exception as exc:  # the type only: messages can carry provider responses
            name = type(exc).__name__
            logger.warning("run %s failed: %s", run_id, name)
            if not log.open:
                await log.append(RunStartedEvent(thread_id=thread, run_id=agui_run))
            await log.append(RunErrorEvent(message=name, code=name), run_update=self._failed(run_id))

    async def _finish(self, run: Run, graph: Any, log: RunEventLog, end: RunFinishedEvent | RunErrorEvent) -> None:
        spent = await spent_usd(self.sessions, run.id)
        text: str | None = None
        if isinstance(end, RunErrorEvent):
            status = "failed"
        elif isinstance(end.outcome, RunFinishedInterruptOutcome):
            status = "awaiting_user"
        else:
            state = await graph.aget_state({"configurable": {"thread_id": run.checkpoint_thread_id}})
            messages = state.values["messages"]
            text = messages[-1].text
            limited = any(isinstance(m, ToolMessage) and m.text == LIMIT_HIT for m in messages)
            status = "partial" if text in STOPPED or limited or spent >= run.budget_usd else "completed"

        async def settle(session: AsyncSession) -> None:
            ended = {} if status == "awaiting_user" else {"ended_at": _now()}
            await self._set(session, run.id, status, ("running",), spent_usd=spent, **ended)
            if text is not None:
                session.add(Message(thread_id=run.thread_id, role="assistant", content={"text": text}, run_id=run.id))

        await log.append(end, run_update=settle)

    @staticmethod
    def _failed(run_id: uuid.UUID) -> RunUpdate:
        async def fail(session: AsyncSession) -> None:
            running = (Run.id == run_id, Run.status == "running")  # a pause or cancel in flight wins
            await session.execute(update(Run).where(*running).values(status="failed", ended_at=_now()))

        return fail

    async def _question(self, run_id: uuid.UUID) -> Message:
        async with self.sessions() as session:
            stmt = select(Message).where(Message.run_id == run_id, Message.role == "user")
            message = await session.scalar(stmt.order_by(Message.created_at).limit(1))
        if message is None:
            raise RunStateError(f"run {run_id} has no question and no checkpoint")
        return message

    @staticmethod
    async def _superseded(session: AsyncSession, run: Run) -> bool:
        if run.started_at is None:
            return False
        newer = select(func.count()).where(
            Run.checkpoint_thread_id == run.checkpoint_thread_id, Run.id != run.id, Run.started_at > run.started_at
        )
        return bool(await session.scalar(newer))

    @staticmethod
    async def _set(
        session: AsyncSession, run_id: uuid.UUID, status: str, only_from: Iterable[str], **values: Any
    ) -> None:
        stmt = (
            update(Run)
            .where(Run.id == run_id, Run.status.in_(list(only_from)))
            .values(status=status, **values)
            .returning(Run.id)
        )
        if await session.scalar(stmt) is None:
            current = await session.scalar(select(Run.status).where(Run.id == run_id))
            raise RunStateError(f"run {run_id} is {current or 'missing'}; it cannot become {status}")
```

In `backend/src/researchly/app.py`, change the tuple in `_redact_server_logs` to:

```python
    for name, kind in (
        ("uvicorn.access", RedactingArgsFilter),
        ("uvicorn.error", RedactingFilter),
        ("ag_ui_langgraph.agent", RedactingFilter),  # logs a failed run's traceback, exception text included
    ):
```

- [ ] **Step 4: Run the tests**

Run: `uv run --directory backend pytest tests/runs tests/test_app.py -q`. Expected: PASS. All 3a manager tests are kept, including the restart and branch tests on the adapter.
Run: `uv run --directory backend pytest -q`. Expected: 745 passed, 2 skipped.
Run: `uv run --directory backend pytest -m db -q`. Expected: 100 passed.

- [ ] **Step 5: Lint, types, commit**

```bash
uv run --directory backend ruff check --fix; uv run --directory backend ruff format
uv run --directory backend ruff check; uv run --directory backend ruff format --check; uv run --directory backend pyright
git add backend/src/researchly/runs/manager.py backend/src/researchly/app.py backend/tests/runs/test_manager.py backend/tests/test_app.py
git commit -m "feat: drive runs through the AG-UI adapter and log their events" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Run summary with Chao2 coverage, and cost events

**Files:**
- Modify: `backend/src/researchly/services/identity.py`, `backend/src/researchly/agents/middleware.py`, `backend/src/researchly/runs/manager.py`
- Create: `backend/src/researchly/runs/summary.py`
- Test: `backend/tests/services/test_identity.py`, `backend/tests/agents/test_middleware.py`, `backend/tests/runs/test_summary.py` (create), `backend/tests/runs/test_manager.py`

**Interfaces:**
- Consumes: `ranking.chao2(lists) -> (observed, estimated)` and `identity.normalize_title`.
- Produces:
  - `identity.merge` sets `extra["found_by"]: list[str]`;
  - `coverage(store: BaseStore | None, project_id: uuid.UUID) -> dict | None`, returning `{"observed": int, "estimated": float, "lists": int}`;
  - `CUSTOM` `cost` events `{"spent_usd": float, "budget_usd": float}`;
  - one `CUSTOM` `run_summary` `{"status", "spent_usd", "budget_usd", "coverage"}` before every terminal that `_finish` writes.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/services/test_identity.py`:

```python
def test_merge_records_every_source_that_found_the_paper():
    first = merge([rec("arxiv", "Attention Is All You Need"), rec("openalex", "Attention is All you Need")])
    assert first.extra["found_by"] == ["arxiv", "openalex"]
    again = merge([first, rec("crossref", "Attention Is All You Need")])  # a repeated search merges into the file
    assert again.extra["found_by"] == ["arxiv", "crossref", "openalex"]
```

Create `backend/tests/runs/test_summary.py`:

```python
import json
import uuid

import pytest
from langgraph.store.memory import InMemoryStore

from researchly.runs.summary import coverage

pytestmark = pytest.mark.anyio


def lines(*records: dict) -> str:
    return "".join(json.dumps(r) + "\n" for r in records)


async def test_coverage_is_chao2_over_sub_question_and_source_lists():
    store, project = InMemoryStore(), uuid.uuid4()
    ns = ("project", str(project))
    q1 = lines(
        {"title": "Attention Is All You Need", "source": "openalex", "extra": {"found_by": ["arxiv", "openalex"]}},
        {"title": "BERT", "source": "openalex", "extra": {"found_by": ["openalex"]}},
        {"title": "GPT", "source": "arxiv", "extra": {}},  # written before found_by: its source counts
    )
    await store.aput(ns, "/candidates/q1.jsonl", {"content": q1})
    legacy = [json.dumps({"title": "attention is all you need!", "source": "crossref", "extra": {}})]
    await store.aput(ns, "/candidates/q2.jsonl", {"content": legacy})  # deepagents' older list-of-lines format
    await store.aput(ns, "/evidence/p1.json", {"content": "{}"})
    other = lines({"title": "Other", "source": "openalex", "extra": {}})
    await store.aput(("project", str(uuid.uuid4())), "/candidates/q1.jsonl", {"content": other})
    # lists: (q1, arxiv) {A, GPT}, (q1, openalex) {A, BERT}, (q2, crossref) {A}; q1=2, q2=0 -> 3 + 2/3 * 1
    assert await coverage(store, project) == {"observed": 3, "estimated": 3.7, "lists": 3}


async def test_no_store_means_no_coverage():
    assert await coverage(None, uuid.uuid4()) is None
```

Append to `backend/tests/agents/test_middleware.py`:

```python
async def test_budget_guard_reports_the_cost_before_each_model_call():
    box = {"v": Decimal("0.25")}
    graph = create_agent(script("fine"), [], middleware=[BudgetGuard(spend(box))], context_schema=RunContext)
    stream = graph.astream_events(GO, context=CTX, version="v2")
    got = [(e["name"], e["data"]) async for e in stream if e["event"] == "on_custom_event"]
    assert got == [("cost", {"spent_usd": 0.25, "budget_usd": 1.0})]
```

In `backend/tests/runs/test_manager.py`, add `import json` and `from langgraph.store.memory import InMemoryStore`, then append:

```python
async def test_a_summary_precedes_the_end_of_the_run(sm):
    run = await seed_run(sm)
    store = InMemoryStore()
    line = json.dumps({"title": "Attention", "source": "openalex", "extra": {"found_by": ["arxiv", "openalex"]}})
    await store.aput(("project", str(run.project_id)), "/candidates/sq1.jsonl", {"content": line + "\n"})
    m = manager(sm, agent(script("The answer."), store=store))
    await m.start(run.id)
    await m.wait(run.id)
    assert (await kinds(sm, run.id))[-2:] == ["CUSTOM", "RUN_FINISHED"]
    [summary] = await events(sm, run.id, "CUSTOM")
    assert summary["name"] == "run_summary"
    coverage = {"observed": 1, "estimated": 1.0, "lists": 2}
    assert summary["value"] == {"status": "completed", "spent_usd": 0.0, "budget_usd": 1.0, "coverage": coverage}
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --directory backend pytest tests/services/test_identity.py tests/agents/test_middleware.py tests/runs/test_summary.py tests/runs/test_manager.py -q`
Expected: FAIL.
- `found_by` raises `KeyError`.
- `researchly.runs.summary` does not exist.
- The cost list is `[]`.
- The last two kinds are `["STATE_SNAPSHOT", "RUN_FINISHED"]`.

- [ ] **Step 3: Implement**

In `backend/src/researchly/services/identity.py`, in `merge`, add this line just before `merged.source = ordered[0].source`:

```python
    # Chao2 incidence (spec §6.3 step 9): the summary counts every source that found the paper, not just the winner
    merged.extra["found_by"] = sorted({s for r in records for s in r.extra.get("found_by", [r.source])})
```

Create `backend/src/researchly/runs/summary.py`:

```python
"""The run summary's coverage figure (spec §6.3 step 9, §10): Chao2 over the project's candidate files."""

import json
import uuid
from typing import Any

from langgraph.store.base import BaseStore

from researchly.services.identity import normalize_title
from researchly.services.ranking import chao2


async def coverage(store: BaseStore | None, project_id: uuid.UUID) -> dict[str, Any] | None:
    """One incidence list per (sub-question file, source). Files are project-wide, so earlier runs count too."""
    if store is None:
        return None
    # ponytail: one page of 1000 project files; page through asearch if a project ever holds more
    items = await store.asearch(("project", str(project_id)), limit=1000)
    lists: dict[tuple[str, str], set[str]] = {}
    for item in items:
        if not (item.key.startswith("/candidates/") and item.key.endswith(".jsonl")):
            continue
        content = item.value.get("content", "")
        text = content if isinstance(content, str) else "\n".join(content)  # deepagents' older format is a list
        for line in text.splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            for source in record.get("extra", {}).get("found_by") or [record["source"]]:
                lists.setdefault((item.key, source), set()).add(normalize_title(record["title"]))
    observed, estimated = chao2(list(lists.values()))
    return {"observed": observed, "estimated": round(estimated, 1), "lists": len(lists)}
```

In `backend/src/researchly/agents/middleware.py`:
- Add `from langchain_core.callbacks.manager import adispatch_custom_event`.
- Replace `BudgetGuard.abefore_model` with:

```python
    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any] | None:
        ctx = runtime.context
        spent = await self.spent(ctx.run_id)
        # the run tree shows cost as it grows (AG-UI CUSTOM "cost", spec §17)
        await adispatch_custom_event("cost", {"spent_usd": float(spent), "budget_usd": float(ctx.budget_usd)})
        if spent >= ctx.budget_usd:
            return {"messages": [AIMessage(BUDGET_SPENT)], "jump_to": "end"}
        return None
```

In `backend/src/researchly/runs/manager.py`:
- Add `CustomEvent` to the `ag_ui.core` import, and add `from researchly.runs.summary import coverage`.
- In `_finish`, insert these lines just before `async def settle(...)`:

```python
        summary = {
            "status": status,
            "spent_usd": float(spent),
            "budget_usd": float(run.budget_usd),
            "coverage": await coverage(getattr(graph, "store", None), run.project_id),
        }
        await log.append(CustomEvent(name="run_summary", value=summary))
```

- [ ] **Step 4: Run the tests**

Run: `uv run --directory backend pytest tests/services/test_identity.py tests/agents tests/runs -q`. Expected: PASS.
Run: `uv run --directory backend pytest -q`. Expected: 750 passed, 2 skipped. Identity-based dedupe in `test_search.py` and the agent tools must still pass, since `found_by` only adds an `extra` key.

- [ ] **Step 5: Lint, types, commit**

```bash
uv run --directory backend ruff check --fix; uv run --directory backend ruff format
uv run --directory backend ruff check; uv run --directory backend ruff format --check; uv run --directory backend pyright
git add backend/src/researchly/services/identity.py backend/src/researchly/runs/summary.py backend/src/researchly/agents/middleware.py backend/src/researchly/runs/manager.py backend/tests/services/test_identity.py backend/tests/runs/test_summary.py backend/tests/agents/test_middleware.py backend/tests/runs/test_manager.py
git commit -m "feat: end each run with a cost and Chao2 coverage summary and stream cost before model calls" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Projects and threads API

**Files:**
- Create: `backend/src/researchly/api/deps.py`, `backend/src/researchly/api/projects.py`, `backend/src/researchly/api/threads.py`
- Modify: `backend/src/researchly/app.py`
- Test: `backend/tests/api/__init__.py` (empty), `backend/tests/api/conftest.py`, `backend/tests/api/test_projects.py`

**Interfaces:**
- Consumes: `RunManager.sessions`, `RunManager.create`, and the models `Project`, `Thread`, `Message`.
- Produces:
  - `api.deps`:
    - `run_manager(request) -> RunManager` (503 without one);
    - `Manager = Annotated[RunManager, Depends(run_manager)]`;
    - `get_or_404(runs, model, row_id) -> model`, with the detail `"<model> <id> not found"`.
  - Routes:
    - `POST /api/v1/projects` → 201 `ProjectOut` (`createProject`);
    - `GET /api/v1/projects` → `list[ProjectOut]`, newest first (`listProjects`);
    - `GET /api/v1/projects/{project_id}` (`getProject`);
    - `PATCH /api/v1/projects/{project_id}` with `{name}` (`renameProject`);
    - `POST /api/v1/projects/{project_id}/threads` → 201 `ThreadOut` (`createThread`);
    - `GET /api/v1/projects/{project_id}/threads`, newest first (`listThreads`);
    - `GET /api/v1/threads/{thread_id}` → `ThreadDetail` with `messages: list[MessageOut]` (`getThread`).
  - Test fixtures in `tests/api/conftest.py`:
    - `TOKEN`;
    - `sm`;
    - `api` (a factory: `async with api(graph) as (client, manager)`);
    - `new_thread(client) -> dict`.

- [ ] **Step 1: Write the failing tests**

Create an empty `backend/tests/api/__init__.py`.

Create `backend/tests/api/conftest.py`:

```python
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from researchly.app import create_app
from researchly.config import Settings
from researchly.db import migrate
from researchly.db.engine import make_engine, make_sessionmaker
from researchly.db.models import Run
from researchly.runs.manager import RunManager
from researchly.security.localhost_guard import TOKEN_HEADER

TOKEN = "t" * 43


@pytest.fixture
async def sm(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


@pytest.fixture
def api(sm):
    """`async with api(graph) as (client, manager)`: the app over ASGITransport, on a manager whose runs use `graph`."""

    @contextlib.asynccontextmanager
    async def open_api(graph: Any = None) -> AsyncIterator[tuple[httpx.AsyncClient, RunManager]]:
        async def build(run: Run) -> Any:
            return graph

        manager = RunManager(sm, build)
        app = create_app(Settings(env="test"), token=TOKEN)
        app.state.runs = manager  # ASGITransport runs no lifespan
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1", headers={TOKEN_HEADER: TOKEN}
            ) as client:
                yield client, manager
        finally:
            await manager.shutdown()

    return open_api


async def new_thread(client: httpx.AsyncClient) -> dict[str, Any]:
    project = (await client.post("/api/v1/projects", json={"name": "p"})).json()
    return (await client.post(f"/api/v1/projects/{project['id']}/threads", json={})).json()
```

Create `backend/tests/api/test_projects.py`:

```python
import uuid

import httpx
import pytest

from researchly.app import create_app
from researchly.config import Settings
from researchly.security.localhost_guard import TOKEN_HEADER

from .conftest import TOKEN

pytestmark = [pytest.mark.anyio, pytest.mark.db]


async def test_projects_are_created_listed_read_and_renamed(api):
    async with api() as (client, _):
        first = (await client.post("/api/v1/projects", json={"name": "Attention"})).json()
        created = await client.post("/api/v1/projects", json={"name": "Retrieval", "description": "RAG"})
        assert created.status_code == 201
        second = created.json()
        assert second["description"] == "RAG" and second["created_at"]
        assert [p["id"] for p in (await client.get("/api/v1/projects")).json()] == [second["id"], first["id"]]
        assert (await client.get(f"/api/v1/projects/{first['id']}")).json()["name"] == "Attention"
        renamed = await client.patch(f"/api/v1/projects/{first['id']}", json={"name": "Transformers"})
        assert renamed.status_code == 200 and renamed.json()["name"] == "Transformers"
        assert (await client.post("/api/v1/projects", json={"name": ""})).status_code == 422
        assert (await client.get(f"/api/v1/projects/{uuid.uuid4()}")).status_code == 404
        assert (await client.patch(f"/api/v1/projects/{uuid.uuid4()}", json={"name": "x"})).status_code == 404


async def test_threads_list_newest_first_and_show_their_messages(api):
    async with api() as (client, m):
        project = (await client.post("/api/v1/projects", json={"name": "p"})).json()
        url = f"/api/v1/projects/{project['id']}/threads"
        first = (await client.post(url, json={"title": "one"})).json()
        created = await client.post(url, json={})
        assert created.status_code == 201
        second = created.json()
        assert second["title"] is None and second["project_id"] == project["id"]
        assert [t["id"] for t in (await client.get(url)).json()] == [second["id"], first["id"]]
        run = await m.create(uuid.UUID(project["id"]), uuid.UUID(first["id"]), "Why?")
        detail = (await client.get(f"/api/v1/threads/{first['id']}")).json()
        assert detail["title"] == "one"
        assert [(x["role"], x["text"], x["run_id"]) for x in detail["messages"]] == [("user", "Why?", str(run.id))]
        assert (await client.post(f"/api/v1/projects/{uuid.uuid4()}/threads", json={})).status_code == 404
        assert (await client.get(f"/api/v1/threads/{uuid.uuid4()}")).status_code == 404


async def test_the_api_answers_503_without_a_run_manager():
    app = create_app(Settings(env="test"), token=TOKEN)  # no lifespan ran, so there is no manager
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        response = await client.get("/api/v1/projects", headers={TOKEN_HEADER: TOKEN})
    assert response.status_code == 503
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --directory backend pytest tests/api/test_projects.py -q`
Expected: FAIL. Every route answers 404 because no routers are mounted yet.

- [ ] **Step 3: Implement**

Create `backend/src/researchly/api/deps.py`:

```python
"""Shared dependencies of the /api/v1 routers: the run manager (and through it, the database) and 404 lookups."""

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from researchly.db.base import Base
from researchly.runs.manager import RunManager


def run_manager(request: Request) -> RunManager:
    runs = getattr(request.app.state, "runs", None)
    if runs is None:  # the lifespan has not started it (unit-test apps have none)
        raise HTTPException(503, "the run manager is not running")
    return runs


Manager = Annotated[RunManager, Depends(run_manager)]


async def get_or_404[T: Base](runs: RunManager, model: type[T], row_id: uuid.UUID) -> T:
    async with runs.sessions() as session:
        row = await session.get(model, row_id)
    if row is None:
        raise HTTPException(404, f"{model.__name__.lower()} {row_id} not found")
    return row
```

Create `backend/src/researchly/api/projects.py`:

```python
"""Projects (spec §17, F1)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from researchly.api.deps import Manager, get_or_404
from researchly.db.models import Project

router = APIRouter(prefix="/api/v1", tags=["projects"])


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class ProjectRename(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime


@router.post("/projects", status_code=201, operation_id="createProject")
async def create_project(body: ProjectIn, runs: Manager) -> ProjectOut:
    async with runs.sessions.begin() as session:
        project = Project(name=body.name, description=body.description)
        session.add(project)
        await session.flush()
        await session.refresh(project)  # created_at comes from the database
        return ProjectOut.model_validate(project)


@router.get("/projects", operation_id="listProjects")
async def list_projects(runs: Manager) -> list[ProjectOut]:
    async with runs.sessions() as session:
        rows = await session.scalars(select(Project).order_by(Project.created_at.desc()))
        return [ProjectOut.model_validate(p) for p in rows]


@router.get("/projects/{project_id}", operation_id="getProject")
async def get_project(project_id: uuid.UUID, runs: Manager) -> ProjectOut:
    return ProjectOut.model_validate(await get_or_404(runs, Project, project_id))


@router.patch("/projects/{project_id}", operation_id="renameProject")
async def rename_project(project_id: uuid.UUID, body: ProjectRename, runs: Manager) -> ProjectOut:
    async with runs.sessions.begin() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, f"project {project_id} not found")
        project.name = body.name
        return ProjectOut.model_validate(project)
```

Create `backend/src/researchly/api/threads.py`:

```python
"""Threads and their messages (spec §17, F1)."""

import uuid
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from researchly.api.deps import Manager, get_or_404
from researchly.db.models import Message, Project, Thread

router = APIRouter(prefix="/api/v1", tags=["threads"])


class ThreadIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ThreadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    title: str | None
    created_at: datetime


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    text: str
    run_id: uuid.UUID | None
    created_at: datetime


class ThreadDetail(ThreadOut):
    messages: list[MessageOut]


@router.post("/projects/{project_id}/threads", status_code=201, operation_id="createThread")
async def create_thread(project_id: uuid.UUID, body: ThreadIn, runs: Manager) -> ThreadOut:
    await get_or_404(runs, Project, project_id)
    async with runs.sessions.begin() as session:
        thread = Thread(project_id=project_id, title=body.title)
        session.add(thread)
        await session.flush()
        await session.refresh(thread)
        return ThreadOut.model_validate(thread)


@router.get("/projects/{project_id}/threads", operation_id="listThreads")
async def list_threads(project_id: uuid.UUID, runs: Manager) -> list[ThreadOut]:
    await get_or_404(runs, Project, project_id)
    stmt = select(Thread).where(Thread.project_id == project_id).order_by(Thread.created_at.desc())
    async with runs.sessions() as session:
        return [ThreadOut.model_validate(t) for t in await session.scalars(stmt)]


@router.get("/threads/{thread_id}", operation_id="getThread")
async def get_thread(thread_id: uuid.UUID, runs: Manager) -> ThreadDetail:
    thread = await get_or_404(runs, Thread, thread_id)
    stmt = select(Message).where(Message.thread_id == thread_id).order_by(Message.created_at, Message.id)
    async with runs.sessions() as session:
        messages = [
            MessageOut(id=m.id, role=m.role, text=str(m.content.get("text", "")), run_id=m.run_id, created_at=m.created_at)
            for m in await session.scalars(stmt)
        ]
    return ThreadDetail(**ThreadOut.model_validate(thread).model_dump(), messages=messages)
```

In `backend/src/researchly/app.py`, change `from researchly.api import system` to `from researchly.api import projects, system, threads`. Then replace `app.include_router(system.router)` with:

```python
    for router in (system.router, projects.router, threads.router):
        app.include_router(router)
```

- [ ] **Step 4: Run the tests**

Run: `uv run --directory backend pytest tests/api -q`. Expected: 3 passed.
Run: `uv run --directory backend pytest -q`. Expected: 753 passed, 2 skipped.

- [ ] **Step 5: Lint, types, commit**

```bash
uv run --directory backend ruff check --fix; uv run --directory backend ruff format
uv run --directory backend ruff check; uv run --directory backend ruff format --check; uv run --directory backend pyright
git add backend/src/researchly/api/deps.py backend/src/researchly/api/projects.py backend/src/researchly/api/threads.py backend/src/researchly/app.py backend/tests/api/__init__.py backend/tests/api/conftest.py backend/tests/api/test_projects.py
git commit -m "feat: projects and threads REST API" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Runs API

**Files:**
- Create: `backend/src/researchly/api/runs.py`
- Modify: `backend/src/researchly/app.py`
- Test: `backend/tests/api/test_runs.py`

**Interfaces:**
- Consumes: `Manager` and `get_or_404` (Task 7); `RunManager.create/start/pause/resume/cancel/branch/wait` (Task 5).
- Produces:
  - Models: `RunIn{message, effort}` and `RunOut`. `Decision` is a union discriminated on `type`: `approve`, `edit{edited_action{name, args}}`, `reject{message?}`, `respond{message}`. Also `ResumeIn{decisions?}` and `BranchIn{checkpoint_id}`.
  - The dependency `ExistingRun`, which answers 404 for an unknown run (Task 9 reuses it).
  - Routes:
    - `POST /api/v1/threads/{thread_id}/runs` → 201 (`createRun`);
    - `GET /api/v1/runs/{run_id}` (`getRun`);
    - `POST /api/v1/runs/{run_id}/pause` (`pauseRun`), `/resume` (`resumeRun`), `/cancel` (`cancelRun`), each answering with the run;
    - `POST /api/v1/runs/{run_id}/branch` → 201 with the child run (`branchRun`).
  - `RunStateError` → 409 `{"detail": str(exc)}`, through an app exception handler.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/api/test_runs.py`:

```python
import uuid
from decimal import Decimal

import pytest
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool

from ..agents.fakes import agent, blocking_tool, call, script
from .conftest import new_thread

pytestmark = [pytest.mark.anyio, pytest.mark.db]


@tool
def submit_plan(plan: str) -> str:
    """Submit the plan."""
    return "approved"


async def test_a_run_is_created_started_and_finishes(api):
    async with api(agent(script("The answer."))) as (client, m):
        thread = await new_thread(client)
        created = await client.post(f"/api/v1/threads/{thread['id']}/runs", json={"message": "Why?", "effort": "quick"})
        assert created.status_code == 201
        run = created.json()
        assert run["effort"] == "quick" and Decimal(run["budget_usd"]) == Decimal("0.1")
        assert run["thread_id"] == thread["id"] and run["parent_run_id"] is None
        await m.wait(uuid.UUID(run["id"]))
        done = (await client.get(f"/api/v1/runs/{run['id']}")).json()
        assert done["status"] == "completed" and done["ended_at"]
        messages = (await client.get(f"/api/v1/threads/{thread['id']}")).json()["messages"]
        assert [x["text"] for x in messages] == ["Why?", "The answer."]


async def test_a_plan_is_approved_through_the_api(api):
    hitl = HumanInTheLoopMiddleware(interrupt_on={"submit_plan": {"allowed_decisions": ["approve", "edit", "reject"]}})
    graph = agent(script(call("submit_plan", {"plan": "1. search"}), "Done."), [submit_plan], [hitl])
    async with api(graph) as (client, m):
        thread = await new_thread(client)
        run = (await client.post(f"/api/v1/threads/{thread['id']}/runs", json={"message": "Why?"})).json()
        await m.wait(uuid.UUID(run["id"]))
        assert (await client.get(f"/api/v1/runs/{run['id']}")).json()["status"] == "awaiting_user"
        url = f"/api/v1/runs/{run['id']}/resume"
        wrong = await client.post(url, json={"decisions": [{"type": "respond", "message": "x"}]})
        assert wrong.status_code == 409 and "open review" in wrong.json()["detail"]
        assert (await client.post(url, json={"decisions": [{"type": "maybe"}]})).status_code == 422
        assert (await client.post(url, json={"decisions": [{"type": "approve"}]})).status_code == 200
        await m.wait(uuid.UUID(run["id"]))
        assert (await client.get(f"/api/v1/runs/{run['id']}")).json()["status"] == "completed"
        messages = (await client.get(f"/api/v1/threads/{thread['id']}")).json()["messages"]
        assert messages[-1]["text"] == "Done."


async def test_pause_and_resume_continue_the_pending_tool(api):
    fetch, entered, gate, calls = blocking_tool()
    graph = agent(script(call("fetch", {"x": "a"}), "done"), [fetch])
    async with api(graph) as (client, m):
        thread = await new_thread(client)
        run = (await client.post(f"/api/v1/threads/{thread['id']}/runs", json={"message": "Why?"})).json()
        await entered.wait()
        paused = await client.post(f"/api/v1/runs/{run['id']}/pause")
        assert paused.status_code == 200 and paused.json()["status"] == "paused"
        gate.set()
        assert (await client.post(f"/api/v1/runs/{run['id']}/resume")).status_code == 200  # no body: continue
        await m.wait(uuid.UUID(run["id"]))
        assert (await client.get(f"/api/v1/runs/{run['id']}")).json()["status"] == "completed"
    messages = (await graph.aget_state({"configurable": {"thread_id": run["id"]}})).values["messages"]
    assert calls == ["a", "a"]  # the pending tool step re-ran (Spike §4: continue, not a new step)
    assert not any(isinstance(x, ToolMessage) and x.status == "error" for x in messages)


async def test_unknown_ids_are_404_and_illegal_moves_409(api):
    async with api(agent(script("x"))) as (client, m):
        assert (await client.get(f"/api/v1/runs/{uuid.uuid4()}")).status_code == 404
        for action in ("pause", "resume", "cancel"):
            assert (await client.post(f"/api/v1/runs/{uuid.uuid4()}/{action}")).status_code == 404
        missing = await client.post(f"/api/v1/threads/{uuid.uuid4()}/runs", json={"message": "x"})
        assert missing.status_code == 404
        thread = await new_thread(client)
        assert (await client.post(f"/api/v1/threads/{thread['id']}/runs", json={"message": ""})).status_code == 422
        run = (await client.post(f"/api/v1/threads/{thread['id']}/runs", json={"message": "Why?"})).json()
        await m.wait(uuid.UUID(run["id"]))
        conflict = await client.post(f"/api/v1/runs/{run['id']}/pause")
        assert conflict.status_code == 409 and "completed" in conflict.json()["detail"]
        assert (await client.post(f"/api/v1/runs/{run['id']}/branch", json={})).status_code == 422


async def test_a_run_is_branched_from_a_checkpoint(api):
    graph = agent(script("first answer", "second answer"))
    async with api(graph) as (client, m):
        thread = await new_thread(client)
        run = (await client.post(f"/api/v1/threads/{thread['id']}/runs", json={"message": "Why?"})).json()
        await m.wait(uuid.UUID(run["id"]))
        cfg = {"configurable": {"thread_id": run["id"]}}
        [point] = [s async for s in graph.aget_state_history(cfg) if s.next == ("model",)]
        checkpoint_id = point.config["configurable"]["checkpoint_id"]
        branched = await client.post(f"/api/v1/runs/{run['id']}/branch", json={"checkpoint_id": checkpoint_id})
        assert branched.status_code == 201
        child = branched.json()
        assert child["parent_run_id"] == run["id"] and child["branch_from_checkpoint"] == checkpoint_id
        await m.wait(uuid.UUID(child["id"]))
        assert (await client.get(f"/api/v1/runs/{child['id']}")).json()["status"] == "completed"
        messages = (await client.get(f"/api/v1/threads/{thread['id']}")).json()["messages"]
        assert [x["text"] for x in messages] == ["Why?", "first answer", "second answer"]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --directory backend pytest tests/api/test_runs.py -q`
Expected: FAIL. The run routes answer 404, so `created.status_code == 201` fails.

- [ ] **Step 3: Implement**

Create `backend/src/researchly/api/runs.py`:

```python
"""Runs (spec §17): create in a thread, read, pause, resume with decisions, cancel, branch."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from researchly.agents.context import EffortName
from researchly.api.deps import Manager, get_or_404
from researchly.db.models import Run, Thread

router = APIRouter(prefix="/api/v1", tags=["runs"])


class RunIn(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    effort: EffortName = "standard"


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    thread_id: uuid.UUID
    parent_run_id: uuid.UUID | None
    effort: str
    status: str
    plan: dict[str, Any] | None
    budget_usd: Decimal  # a JSON string: no float rounding of money
    spent_usd: Decimal
    branch_from_checkpoint: str | None
    started_at: datetime | None
    ended_at: datetime | None


class EditedAction(BaseModel):
    name: str
    args: dict[str, Any]


class Approve(BaseModel):
    type: Literal["approve"]


class Edit(BaseModel):
    type: Literal["edit"]
    edited_action: EditedAction


class Reject(BaseModel):
    type: Literal["reject"]
    message: str | None = None


class Respond(BaseModel):
    type: Literal["respond"]
    message: str


Decision = Annotated[Approve | Edit | Reject | Respond, Field(discriminator="type")]


class ResumeIn(BaseModel):
    decisions: list[Decision] | None = None  # one per action of the open review, in order


class BranchIn(BaseModel):
    checkpoint_id: str = Field(min_length=1)


async def existing_run(run_id: uuid.UUID, runs: Manager) -> Run:
    return await get_or_404(runs, Run, run_id)


ExistingRun = Annotated[Run, Depends(existing_run)]


async def _out(runs: Manager, run_id: uuid.UUID) -> RunOut:
    return RunOut.model_validate(await get_or_404(runs, Run, run_id))


@router.post("/threads/{thread_id}/runs", status_code=201, operation_id="createRun")
async def create_run(thread_id: uuid.UUID, body: RunIn, runs: Manager) -> RunOut:
    thread = await get_or_404(runs, Thread, thread_id)
    # ponytail: no cap on concurrent runs; one local user. Add an app-wide cap when runs contend (spec §16)
    run = await runs.create(thread.project_id, thread.id, body.message, effort=body.effort)
    await runs.start(run.id)
    return await _out(runs, run.id)


@router.get("/runs/{run_id}", operation_id="getRun")
async def get_run(run: ExistingRun) -> RunOut:
    return RunOut.model_validate(run)


@router.post("/runs/{run_id}/pause", operation_id="pauseRun")
async def pause_run(run: ExistingRun, runs: Manager) -> RunOut:
    await runs.pause(run.id)
    return await _out(runs, run.id)


@router.post("/runs/{run_id}/resume", operation_id="resumeRun")
async def resume_run(run: ExistingRun, runs: Manager, body: ResumeIn | None = None) -> RunOut:
    decisions = [d.model_dump(exclude_none=True) for d in body.decisions] if body and body.decisions else None
    await runs.resume(run.id, decisions)
    return await _out(runs, run.id)


@router.post("/runs/{run_id}/cancel", operation_id="cancelRun")
async def cancel_run(run: ExistingRun, runs: Manager) -> RunOut:
    await runs.cancel(run.id)
    return await _out(runs, run.id)


@router.post("/runs/{run_id}/branch", status_code=201, operation_id="branchRun")
async def branch_run(run: ExistingRun, body: BranchIn, runs: Manager) -> RunOut:
    child = await runs.branch(run.id, body.checkpoint_id)
    return await _out(runs, child.id)
```

In `backend/src/researchly/app.py`:
- Change `from fastapi import FastAPI` to `from fastapi import FastAPI, Request`.
- Change `from fastapi.responses import PlainTextResponse` to `from fastapi.responses import JSONResponse, PlainTextResponse`.
- Change the api import to `from researchly.api import projects, runs, system, threads`.
- Change the manager import to `from researchly.runs.manager import RunManager, RunStateError`.
- Replace the router loop from Task 7 with:

```python
    @app.exception_handler(RunStateError)
    async def run_state_conflict(request: Request, exc: RunStateError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    for router in (system.router, projects.router, threads.router, runs.router):
        app.include_router(router)
```

- [ ] **Step 4: Run the tests**

Run: `uv run --directory backend pytest tests/api -q`. Expected: 8 passed.
Run: `uv run --directory backend pytest -q`. Expected: 758 passed, 2 skipped.

- [ ] **Step 5: Lint, types, commit**

```bash
uv run --directory backend ruff check --fix; uv run --directory backend ruff format
uv run --directory backend ruff check; uv run --directory backend ruff format --check; uv run --directory backend pyright
git add backend/src/researchly/api/runs.py backend/src/researchly/app.py backend/tests/api/test_runs.py
git commit -m "feat: runs REST API with plan decisions, pause, resume, cancel and branch" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The AG-UI SSE stream endpoint

**Files:**
- Modify: `backend/src/researchly/api/runs.py`
- Test: `backend/tests/api/test_stream.py`

**Interfaces:**
- Consumes: `follow` and `replay` (Task 3); `ExistingRun` and `Manager` (Tasks 7–8); `RunManager.hub` (Task 5).
- Produces: `POST /api/v1/runs/{run_id}/stream` (`streamRun`, `text/event-stream`).
  - Each frame is `data: <event JSON>\nid: <seq>\n\n`, with `: ping` after 15 s idle.
  - It reads the `Last-Event-ID` header (wins) or `?after=` (default 0), and ignores any body.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/api/test_stream.py`:

```python
import asyncio
import json
import uuid
from typing import Any

import pytest

from researchly.runs.events import follow, replay

from ..agents.fakes import agent, blocking_tool, call, script, until
from .conftest import new_thread

pytestmark = [pytest.mark.anyio, pytest.mark.db]

# Spike §2 and the plan's probes; MESSAGES_SNAPSHOT is not stored, and the deltas are coalesced
SHAPE = [
    "RUN_STARTED",
    "STEP_STARTED:model",
    "TOOL_CALL_START:fetch",
    "TOOL_CALL_ARGS",
    "TOOL_CALL_END",
    "STEP_FINISHED:model",
    "STEP_STARTED:tools",
    "TOOL_CALL_RESULT",
    "STATE_SNAPSHOT",
    "STEP_FINISHED:tools",
    "STEP_STARTED:model",
    "TEXT_MESSAGE_START",
    "TEXT_MESSAGE_CONTENT",
    "TEXT_MESSAGE_END",
    "STEP_FINISHED:model",
    "STATE_SNAPSHOT",
    "CUSTOM:run_summary",
    "RUN_FINISHED",
]


def frames(body: str) -> list[tuple[int, dict[str, Any]]]:
    out = []
    for block in body.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if line and not line.startswith(":"))
        if "data" in fields:
            out.append((int(fields["id"]), json.loads(fields["data"])))
    return out


def shape(payload: dict[str, Any]) -> str:
    name = payload.get("stepName") or payload.get("toolCallName") or payload.get("name")
    return f"{payload['type']}:{name}" if name else payload["type"]


async def test_a_run_streams_its_events_end_to_end(api):
    fetch, entered, gate, calls = blocking_tool()
    async with api(agent(script(call("fetch", {"x": "a"}), "The answer."), [fetch])) as (client, m):
        thread = await new_thread(client)
        run = (await client.post(f"/api/v1/threads/{thread['id']}/runs", json={"message": "Why?"})).json()
        run_id = uuid.UUID(run["id"])
        await entered.wait()
        # ASGITransport returns only when the stream ends: start it, wait for it to subscribe, then let the run finish
        stream = asyncio.create_task(client.post(f"/api/v1/runs/{run['id']}/stream", json={"threadId": "ignored"}))

        async def subscribed() -> bool:
            return run_id in m.hub._subscribers

        await until(subscribed)
        gate.set()
        response = await stream
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache" and response.headers["x-accel-buffering"] == "no"
    got = frames(response.text)
    assert [seq for seq, _ in got] == list(range(1, len(got) + 1))
    assert [shape(p) for _, p in got] == SHAPE
    summary = {"status": "completed", "spent_usd": 0.0, "budget_usd": 1.0, "coverage": None}
    assert got[-2][1]["value"] == summary


async def test_last_event_id_and_after_replay_from_the_next_event(api):
    async with api(agent(script("The answer."))) as (client, m):
        thread = await new_thread(client)
        run = (await client.post(f"/api/v1/threads/{thread['id']}/runs", json={"message": "Why?"})).json()
        await m.wait(uuid.UUID(run["id"]))
        url = f"/api/v1/runs/{run['id']}/stream"
        full = frames((await client.post(url)).text)
        by_header = frames((await client.post(url, headers={"Last-Event-ID": "3"}, params={"after": 1})).text)
        by_query = frames((await client.post(url, params={"after": 3})).text)
    assert by_header == by_query == full[3:] and by_header[0][0] == 4  # the header wins over ?after=


async def test_a_reconnect_resumes_without_gaps_or_duplicates(api):
    fetch, entered, gate, calls = blocking_tool()
    async with api(agent(script(call("fetch", {"x": "a"}), "The answer."), [fetch])) as (client, m):
        thread = await new_thread(client)
        run = (await client.post(f"/api/v1/threads/{thread['id']}/runs", json={"message": "Why?"})).json()
        run_id = uuid.UUID(run["id"])
        await entered.wait()
        first = follow(m.sessions, m.hub, run_id, 0)  # what the endpoint iterates
        got = [await anext(first) for _ in range(3)]
        await first.aclose()  # the client disconnects
        assert run_id not in m.hub._subscribers and run_id in m._tasks  # the run goes on
        gate.set()
        got += [e async for e in follow(m.sessions, m.hub, run_id, got[-1].seq)]  # reconnect with Last-Event-ID
        await m.wait(run_id)
        assert [e.seq for e in got] == list(range(1, len(got) + 1))
        assert got == await replay(m.sessions, run_id, 0) and got[-1].type == "RUN_FINISHED"
        assert (await client.get(f"/api/v1/runs/{run['id']}")).json()["status"] == "completed"


async def test_streaming_an_unknown_run_is_404(api):
    async with api() as (client, _):
        assert (await client.post(f"/api/v1/runs/{uuid.uuid4()}/stream")).status_code == 404
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --directory backend pytest tests/api/test_stream.py -q`
Expected: FAIL.
- The end-to-end test times out in `until(subscribed)` because the route is missing.
- The other tests get 404 or 405.

- [ ] **Step 3: Implement**

In `backend/src/researchly/api/runs.py`:

1. Extend the imports:

```python
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, Query
from fastapi.sse import EventSourceResponse, ServerSentEvent

from researchly.runs.events import follow
```

2. Change the module docstring to `"""Runs (spec §17): create in a thread, read, pause, resume with decisions, cancel, branch, and the AG-UI stream."""`.

3. Append:

```python
@router.post("/runs/{run_id}/stream", operation_id="streamRun", response_class=EventSourceResponse)
async def stream_run(
    run: ExistingRun,
    runs: Manager,
    after: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[int | None, Header(ge=0)] = None,
) -> AsyncIterator[ServerSentEvent]:
    """The run's AG-UI events as SSE (ADR 0012), from `Last-Event-ID` (or `?after=`) on. Any request body is ignored.

    A disconnect ends only this generator: the run goes on, and a reconnect replays from its last `seq`.
    """
    start = after if last_event_id is None else last_event_id
    async for event in follow(runs.sessions, runs.hub, run.id, start):
        yield ServerSentEvent(raw_data=json.dumps(event.payload), id=str(event.seq))
```

- [ ] **Step 4: Run the tests**

Run: `uv run --directory backend pytest tests/api -q`. Expected: 12 passed.
- If `SHAPE` differs, compare the actual list with Spike §2 and the probe sequence in ruling 12's evidence before changing anything.
- Update `SHAPE` only if the difference is the adapter's order, not a missing or extra stored type. A missing or extra stored type means the event log is wrong.

Run: `uv run --directory backend pytest -q`. Expected: 762 passed, 2 skipped.
Run: `uv run --directory backend pytest -m db -q`. Expected: 113 passed.

- [ ] **Step 5: Lint, types, commit**

```bash
uv run --directory backend ruff check --fix; uv run --directory backend ruff format
uv run --directory backend ruff check; uv run --directory backend ruff format --check; uv run --directory backend pyright
git add backend/src/researchly/api/runs.py backend/tests/api/test_stream.py
git commit -m "feat: stream a run's AG-UI events over SSE with Last-Event-ID replay" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: OpenAPI client, spec deltas, AGENTS.md and progress

**Files:**
- Regenerate: `frontend/src/api/openapi.json`, `frontend/src/api/schema.d.ts`
- Modify: `docs/superpowers/specs/2026-10-28-researchly-design.md`, `AGENTS.md`, `backend/AGENTS.md`, `docs/progress.md`

**Interfaces:**
- Consumes: the routes from Tasks 7–9.
- Produces: TS types for `createProject`, `listProjects`, `getProject`, `renameProject`, `createThread`, `listThreads`, `getThread`, `createRun`, `getRun`, `pauseRun`, `resumeRun`, `cancelRun`, `branchRun` and `streamRun`.

- [ ] **Step 1: Run every check and note the counts**

```bash
uv run --directory backend pytest -q
uv run --directory backend pytest -m db -q
uv run --directory cli pytest -q -k "not docker"
uv run --no-project --python 3.12 scripts/repo_checks.py
```

Expected: backend 762 passed and 2 skipped; `-m db` 113 passed; CLI passes; the repo checks pass. Use these real counts in Step 4.

- [ ] **Step 2: Regenerate the TypeScript client**

```bash
pnpm -C frontend api:schema
pnpm -C frontend typecheck
pnpm -C frontend lint
```

Expected: `openapi.json` and `schema.d.ts` change, adding the 14 operations above, and typecheck and lint pass. Check `grep -c '"operationId"' frontend/src/api/openapi.json`: it should be 16 (the 14 new ones, plus `system` and `health`).

- [ ] **Step 3: Amend the spec**

In `docs/superpowers/specs/2026-10-28-researchly-design.md`:

Replace §4.2's line `- Live progress fan-out uses Postgres \`LISTEN/NOTIFY\`.` with:

```markdown
- Live progress fan-out is in process: a run's drive task persists each AG-UI event to `run_event`, then publishes it to the run's subscribers (`RunHub`, ADR 0012). One app process holds every run, so Postgres `LISTEN/NOTIFY` is not needed.
```

Replace §17's `**AG-UI endpoint:**` line with:

```markdown
- **AG-UI endpoint:** `POST /api/v1/runs/{id}/stream` (SSE; `Last-Event-ID` or `?after=` sets the resume point; the request body is ignored). Events:
  - `RUN_STARTED/FINISHED/ERROR` (one AG-UI run per drive: start, resume or restart);
  - `STEP_STARTED/FINISHED`, `TEXT_MESSAGE_*`, `TOOL_CALL_*`, and `SUBAGENT_*` attributed to the scout or reader;
  - `STATE_SNAPSHOT` (todos);
  - custom `cost` (before each model call) and `run_summary` (status, cost, Chao2 coverage).
  - Plan approval and questions arrive as `RUN_FINISHED` with an interrupt outcome (`reason` `researchly:submit_plan` or `researchly:ask_user`, `message`, `responseSchema`); `POST /runs/{id}/resume` answers them.
  - Citation chips come from `[cite:...]` in the answer (3c).
```

Replace §17's line `- All events are persisted to \`run_event\`, so reconnecting replays from the last sequence number.` with:

```markdown
- Every event except `RAW` and `MESSAGES_SNAPSHOT` is persisted to `run_event` before it is sent, so reconnecting replays from the last sequence number. Text, tool-argument and reasoning deltas are coalesced, and error messages are replaced with "The run failed.".
```

- [ ] **Step 4: AGENTS.md, backend/AGENTS.md, progress**

In `AGENTS.md`, under `## Gotchas`, after the `- Agent runtime (ADR 0011):` block, add:

```markdown
- AG-UI streaming (ADR 0012):
  - `runs/agui.py` is the only importer of `ag_ui_langgraph`. Build one `ResearchlyAgent` per run drive (the adapter keeps run state on `self`). It passes our `RunContext` instance, continues a pending checkpoint with `input=None`, and keeps only `todos` in `STATE_SNAPSHOT`.
  - `RunEventLog` is the only writer of `run_event`: it persists, then publishes to the in-process `RunHub`. It never stores `RAW` or `MESSAGES_SNAPSHOT`, coalesces deltas, and scrubs error text. A run's terminal event commits with its status.
  - Test chat models must stream: `ToolFake` implements `_stream`, because the adapter drives `astream_events`.
  - `httpx.ASGITransport` buffers a whole SSE response and runs no lifespan: API tests set `app.state.runs`, and the stream tests start the request as a task.
```

In `backend/AGENTS.md`, replace the line starting `- LLM in tests:` with:

```markdown
- LLM in tests: `ToolFake` (`tests/agents/fakes.py`), a `GenericFakeChatModel` that accepts tools, records prompts and streams text and tool-call chunks (the AG-UI adapter uses `astream_events`), scripted with `call(...)`/`script(...)`. `agent(...)`, `blocking_tool()` and `until(...)` build and drive test graphs. Integration tests use `RecordReplayChatModel` in replay mode. Test packages import the fakes relatively (`from ..agents.fakes import ...`).
```

In `docs/progress.md`, insert this before `## Latest plan`, with the real counts from Step 1:

```markdown
## 2026-11-10 — Step 3b projects, threads and runs API with AG-UI streaming (plan docs/superpowers/plans/2026-11-10-step3b-runs-api.md)
- Done (feat/step3b-runs-api):
  - **Pins:** ag-ui-langgraph 0.0.47 and ag-ui-protocol 1.0.0 (ADR 0012).
  - **Adapter subclass** `runs/agui.py`: our `RunContext`, continue with `input=None`, branch through the stream's config, todos-only snapshots, typed plan and question reviews.
  - **Event log** `runs/events.py`: persist, then publish to the in-process hub. No RAW or message snapshots; deltas coalesced; errors scrubbed; the terminal event commits with the status.
  - **Run manager** drives runs through the adapter: decisions are checked against the open review; a pause closes the AG-UI run as cancelled; every run ends with a `run_summary` (Chao2 coverage from `found_by`); `BudgetGuard` streams `cost`.
  - **API:** projects, threads and runs REST under `/api/v1`, and `POST /api/v1/runs/{id}/stream` on FastAPI's native SSE. The TS client is regenerated.
  - ADR 0012; spec §4.2 and §17 amended.
- Tests: backend <passed> passed, 2 skipped; `-m db` <db passed> passed; CLI (`-k "not docker"`) <cli passed> passed; pyright 0 errors.
- Rulings: see the plan (rulings 11–34).
- Deferred: see the plan's Deferred list (3c UI, E2E and flips; thread search; citation events; cost estimate; checkpoint listing; multi-turn context).
- No features.json flips: F1 and F2 flip with the 3c E2E specs.
- Next: plan 3c (chat UI, run tree, plan review, E2E).
```

Then set the path under `## Latest plan` to `docs/superpowers/plans/2026-11-10-step3b-runs-api.md`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/openapi.json frontend/src/api/schema.d.ts docs/superpowers/specs/2026-10-28-researchly-design.md AGENTS.md backend/AGENTS.md docs/progress.md
git commit -m "docs: step 3b spec deltas, AGENTS gotchas and progress; regenerate the API client" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

## Deferred

| Item | Why not now | When |
|---|---|---|
| Chat UI, run tree, plan panel, E2E specs, `features.json` F1/F2 flips | step split (ruling 1) | 3c |
| Thread search (F1.1 "searchable") | no UI to search from yet | 3c or later |
| `CITATION` events | 3c renders chips from `[cite:...]` in the answer | 3c |
| Pre-run cost estimate and threshold confirmation (F1.7) | needs the UI | 3c |
| An endpoint listing a run's checkpoints (to pick a branch point) | the branch picker is UI | 3c |
| Branch with a new user message; multi-turn context across runs in a thread | per-run checkpoint threads (ruling 27) | 3c |
| `run_event` retention and compaction | no growth problem yet | step 9 |
| A global concurrent-run cap | one local user; marked with a `ponytail:` comment in `create_run` | when runs contend |
| Bounded subscriber queues | one local client; marked with a `ponytail:` comment in `RunHub.subscribe` | multi-client |
| Filtering `STEP_*` from persistence | the run tree may need them (ruling 33) | measure in 3c |
| Per-run coverage (candidate files are project-wide) | needs per-run file prefixes | step 7 |
| `@ag-ui/client` 1.0.4 and adapter upgrades | an ADR plus `tests/runs/test_agui.py` | when needed |
| `LISTEN/NOTIFY` fan-out | one app process (ruling 28) | a multi-process deployment |

## Open risks

- **Duplicate tool-call events across a resume.** A continue re-emits `TOOL_CALL_START/ARGS/END` for the pending call (Spike §7; the planning probe saw the same on `continue_pending`). Clients must key tool calls by `toolCallId` and accept a second `START`.
- **A stream can subscribe in the pause window.** A pause writes `RUN_FINISHED` (cancelled) and then, after the task stops, sets `paused`. A stream that subscribes in that gap reads `running` and tails until the next AG-UI run ends or the client reconnects. The window is milliseconds.
- **The closing event is written during cancellation.** The `CancelledError` handler writes to the database while its task is being cancelled. A second cancel during that write loses the closing event, and live subscribers then wait until they reconnect.
- **The 250 ms age check uses the wall clock** in the manager and API tests. A very slow machine could split a delta and break `SHAPE` in `test_stream.py`. The coalescing unit tests freeze the clock.
- **Private adapter hooks.** `_interrupts_to_agui` is an underscore method (its docstring names it an override point). The branch trick relies on the adapter reusing the config it passes to `get_stream_kwargs`. Both are pinned and covered by `tests/runs/test_agui.py`.
- **Adapter tracebacks reach the server log.** The adapter logs failures with `logger.exception`, so exception text, possibly a provider's, reaches the local server log. `RedactingFilter` removes registered secrets, not arbitrary text.
- **Coverage is project-wide.** It counts candidates from the project's earlier runs too (ruling 23).

## Self-review

- **Spec coverage.**
  - §3 F1 (projects, threads, runs, streaming answers): Tasks 7–9. F1.1 search and F1.7 cost estimate are deferred.
  - §4.2 (resume on startup kept; fan-out): Tasks 3 and 5, with the spec delta in Task 10.
  - §4.3 (localhost guard): every API test passes the token header through `LocalhostGuard`.
  - §6.3 step 9 (run summary: cost, coverage): Task 6.
  - §6.4 (budgets): `BudgetGuard` keeps its stop and adds `cost`, in Task 6.
  - §6.5 (pause, resume, branch, idempotent tools): Tasks 4 and 5, through the API in Task 8.
  - §16 (`run_event`): Task 3, with no migration needed.
  - §17 (REST, AG-UI endpoint, persisted events, pinned adapter, OpenAPI client): Tasks 1, 3 and 7–10.
  - §19.1 (untrusted text): `TOOL_CALL_RESULT` for `task` shows the raw subagent JSON to the user only; the Lead still sees the wrapped message (Spike §7). Nothing new reaches a prompt.
  - §19.7 (pins): Task 1.
  - §21 and §24 (tests and process): TDD per task; DB tests marked.
- **Placeholder scan.** No TBD or "similar to Task N". The `<passed>` markers in Task 10 Step 4 are filled from Step 1's output by design.
- **Type consistency.**
  - `RunEventLog(sessions, hub, run_id, *, max_bytes, max_age_s, clock)`, `.open`, `.append(event, *, run_update)` and `.flush(run_update)` match their uses in `_drive`, `_finish` and the tests.
  - `RunUpdate` is the type of `settle` and `_failed(run_id)`.
  - `follow(sessions, hub, run_id, after)` and `replay(sessions, run_id, after)` match `stream_run` and `test_stream.py`.
  - `ResearchlyAgent(graph, context, *, continue_pending, checkpoint_id)` and `allowed_decisions(interrupt)` match `_drive` and `_check`.
  - `RunManager(sessions, build)` and `.hub` match `app.run_manager`, `tests/api/conftest.py` and `test_stream.py`.
  - `coverage(store, project_id)` matches `_finish`.
  - `agent(model, tools, middleware, checkpointer, store)`, `blocking_tool()` and `until(check)` match every caller.
  - `get_or_404(runs, model, row_id)` and `Manager` match all three routers.
  - `ExistingRun` is shared by Tasks 8 and 9.
- **Counts.**
  - New tests per task: Task 2 +1, Task 3 +9 (db), Task 4 +5, Task 5 +4 (3 db), Task 6 +5 (1 db), Task 7 +3 (db), Task 8 +5 (db), Task 9 +4 (db).
  - Total: 36 new tests, giving 764 collected (762 passed, 2 skipped), of which 25 are db (113 db tests in all).
