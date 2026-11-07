# Step 3 research: AG-UI streaming, run lifecycle, chat UI

Date 2026-11-07 (all web pages and registries accessed 2026-11-07; "today" for `minimumReleaseAge` is 2026-11-07, cutoff 2026-10-31). Probes ran on Windows 10 with uv scratch envs (`research3b/` in the session scratchpad); nothing was added to the repo.

## Recommendations

1. **Backend: use `ag-ui-langgraph==0.0.47` as a library, not its FastAPI endpoint.** Call `LangGraphAgent.run(RunAgentInput)` inside our own run task and persist/publish every event. Pin `ag-ui-protocol==1.0.0` beside it. Set `emit_raw_events=False`, `emit_interrupt_outcome=True`, `subagent_visibility="attributed"`. Keep our own emitter as the fallback, not the plan. (Q1)
2. **The run is a background `asyncio` task** that appends to `run_event` (single writer, per-run `seq`) and publishes to an in-process fan-out. The SSE endpoint subscribes first, replays `seq > last` from the DB, then tails. Browser disconnect never touches the task. (Q2)
3. **Frontend: `@assistant-ui/react@0.15.32` + `@assistant-ui/react-ag-ui@0.1.1` + `@ag-ui/client@1.0.3` (exact).** `1.0.4` is 2 days old and blocked until 2026-11-12. No `trustPolicyExclude` entry is needed (proven by an install under our policy). Register tool UIs with the toolkit API (`Tools({ toolkit })`), not the deprecated `makeAssistantToolUI`. (Q3)
4. **Run tree and plan panel are our own components** fed from AG-UI state and CUSTOM events (`useAgUiState`, a small event store beside the runtime). assistant-ui renders the thread only. (Q3)
5. **Reconnect:** a thin `HttpAgent` subclass whose `fetch` adds the last seq; on reload, replay the whole run into a fresh agent. Send the user's message with `POST /runs`, attach with `POST /runs/{id}/stream`. (Q2, Q3)
6. **SSE behind `LocalhostGuard` works unchanged** (pure ASGI middleware; probe: chunks arrive on time, disconnect cancels the generator). The Vite proxy config already fixes `Origin`. (Q4)
7. **Tests:** frozen AG-UI transcripts (Vitest scripted agent and Playwright `route.fulfill`) for UI behaviour; the real stack with `RecordReplay` for journeys. Never assert on message ids, interrupt ids or token timing. (Q5)
8. **Plan blockers found:** `backend/pyproject.toml` pins `langchain-core==1.6.12`, which conflicts with `deepagents>=0.7.32` (needs `>=1.7.0`); step 3 must bump it (ADR, plus `uv lock --directory cli`). (Q1)

---

## Q1. `ag-ui-langgraph`: maturity and what it emits

**Answer.** Mature enough to use as an event source, with caveats. It is 0.0.x but releases roughly every 2-4 weeks and the source is large (4.6k-line `agent.py`) and actively hardened (subagents, interrupts, resume[], run errors all landed in the last 3 months). It is the reference LangGraph integration of the AG-UI project, so the TS client and the Python adapter move together.

**Evidence**
- PyPI https://pypi.org/project/ag-ui-langgraph/ : latest **0.0.47, 2026-10-08**; before it 0.0.46 (2026-10-01), 0.0.45 (2026-09-09), 0.0.44 (2026-08-27), 0.0.43 (2026-08-16). Requires `langgraph>=0.6,<2`, `langchain>=1.2.0`, `langchain-core>=0.3`, `ag-ui-protocol>=0.1.22`, `ag-ui-a2ui-toolkit>=0.0.4`, `pydantic>=2`; extra `[fastapi]` (`fastapi>=0.115.12`). Python `>=3.10,<3.15`.
- `ag-ui-protocol` https://pypi.org/project/ag-ui-protocol/ : **1.0.0, 2026-09-17** (0.1.22 on 2026-08-31). Only dep is `pydantic>=2.11.2`.
- Resolution probes (`uv pip compile --exclude-newer 2026-10-31`):
  - `langchain-core==1.6.12` (our current pin) + `deepagents>=0.7.32` + `ag-ui-langgraph`: **unsatisfiable** ("deepagents>=0.7.32 depends on langchain-core>=1.7.0").
  - `litellm==1.105.1` + `langchain-litellm==0.11.1` + `ag-ui-langgraph==0.0.47` + `deepagents>=0.7.32`: resolves to `deepagents==0.7.32`, `langchain-core==1.7.3`, `langchain==1.4.7`, `langgraph==1.2.19`, `ag-ui-protocol==1.0.0`. So `langchain-litellm 0.11.1` accepts core 1.7.x; only our `langchain-core` pin must move.
- FastAPI helper: `add_langgraph_fastapi_endpoint(app, agent, path)` (`endpoint.py`, 59 lines): POST taking `RunAgentInput`, clones the agent per request, `StreamingResponse(encoder.encode(event))`. The run lives inside the response generator, so a disconnect cancels it. Unsuitable for us as is; the useful part is `LangGraphAgent.run()`, a plain async generator.

**Events (probe, fake chat model + 2 nodes + interrupt + a compiled subgraph node, `emit_raw_events=False`)**
- Run: `RUN_STARTED`, `RUN_FINISHED`, `RUN_ERROR`. Errors become a terminal `RUN_ERROR` yielded from `run()` (no exception), per its README.
- Steps: `STEP_STARTED/FINISHED` with `stepName` = LangGraph node name (also for nodes inside the subgraph; the names are flat, there is no parent id on STEP events).
- Text: `TEXT_MESSAGE_START/CONTENT/END`, one `CONTENT` per token (observed: `hello`, ` `, `world` ... as separate events). Message ids are `lc_run--<uuid7>`, so random per run.
- Tools: `TOOL_CALL_START/ARGS/END/RESULT` (source: `agent.py` `_handle_single_event`; not exercised by the probe because the fake model cannot emit tool calls). Reasoning: `REASONING_*`.
- State: `STATE_SNAPSHOT` after nearly every step (observed 9 in one resumed run) containing the **whole graph state including all messages**, plus `MESSAGES_SNAPSHOT` at step edges. `STATE_DELTA` only via its `state_streaming` middleware (`middlewares/state_streaming.py`). Unbounded snapshots are the main persistence-size risk (see Q2).
- Custom: LangGraph custom events (`adispatch_custom_event`) are forwarded as `CUSTOM` (`agent.py` ~L3646). This is how `CITATION`, `COST`, run-tree node events and budget updates should leave our graph. `RAW` re-emits every LangGraph event unless `emit_raw_events=False` (default True, huge; turn it off).
- Subagents (`SUBAGENT_STARTED/FINISHED/ERROR`, `subagent_run_id` on text/tool events, `parentToolCallId`): **opt-in** via `subagent_visibility="attributed"` (default `"inline"` flattens subagent work into the parent; `"hidden"` suppresses it). They are derived from LangGraph namespaces plus the `lc_agent_name` metadata, i.e. built for Deep Agents' `task` tool. This is the run-tree source for scouts/readers. I did not run a real Deep Agents graph; that is spike 1.
- The TS client `@ag-ui/client` <= 0.0.57 throws on `SUBAGENT_*`; 1.0.3 knows them, so the pin matters.

**Interrupts (HITL)**
- `interrupt(value)` in a node ends the stream with: legacy `CUSTOM name="on_interrupt"` (value is a JSON string) and, with `emit_interrupt_outcome=True`, `RUN_FINISHED{outcome:{type:"interrupt", interrupts:[{id, reason:"langgraph:interrupt", message, toolCallId, responseSchema, expiresAt, metadata.langgraph.{raw,ns,resumable,when}}]}}`. Probe output confirmed this; the `message` is taken from a `message` key of a dict value, and the raw dict (our plan) is in `metadata.langgraph.raw`.
- Resume: `RunAgentInput.resume=[{interrupt_id, status:"resolved"|"cancelled", payload}]` (preferred) or `forwarded_props.command.resume` (deprecated). Single resolved entry: `interrupt()` returns `payload` verbatim; cancelled returns `{"__agui_cancelled__": true, ...}`; several: `{"__agui_resume_map__": {...}}`. Probe: resuming with `resume=[...]` and `messages=[]` ran the rest of the graph to `RUN_FINISHED`. Interrupt ids differ per run (checkpoint-derived), so clients must read them from the event.
- Subclass hooks `_interrupts_to_agui` / `_build_command_from_agui_resume` exist for `HumanInTheLoopMiddleware` payloads (`action_requests`), which Deep Agents uses; we will need them for approve/edit/reject.
- assistant-ui's own README warns that nested (subagent) interrupts have no resume path yet. Keep the plan-approval and ask_user interrupts on the **Lead** (root graph), as spec §6.3 already does.

**Design mismatch to know about.** The adapter assumes the client owns the transcript (it merges `RunAgentInput.messages` into the checkpoint by id and strips subagent messages) and echoes full `MESSAGES_SNAPSHOT`s. Our server owns threads (`message` table). We pass only the new user message (or `messages=[]` plus `resume`), and treat snapshots as display data.

**Own emitter instead?** Mapping `astream(stream_mode=["messages","updates","custom"], subgraphs=True)` to `ag-ui-protocol` events means re-implementing token/tool-arg chunking, tool-call end detection, message-id continuity, interrupt mapping and subgraph attribution: a few hundred lines and a long tail of edge cases the adapter already fixed (its tests and changelog show them). Rejected for step 3; the persistence layer sits behind `agent.run()`'s output (an `AsyncIterator[BaseEvent]`), so swapping the source later changes one call.

**Recommendation.** Use the adapter via `run()`; pin `ag-ui-langgraph==0.0.47` and `ag-ui-protocol==1.0.0` exactly; upgrades by PR with the replay tests (spec §19.7 style). Bump `langchain-core` (>=1.7) with deepagents in the same ADR. Strip or bound `STATE_SNAPSHOT` (output schema or a persister filter) before it hits `run_event`.

## Q2. Running independent of the HTTP stream

**Answer.** Yes: background task + durable log + in-memory fan-out. This is the standard pattern (event sourcing with live tail) and fits the spec's existing `run_event(run_id, seq, ...)` and "reconnecting replays from the last sequence number".

**Pattern**
1. `POST /api/v1/runs` creates the `run` row (status `pending`) and starts `asyncio.create_task(execute(run_id))` held in a registry `{run_id: RunHandle}` (strong reference, else it can be garbage collected). Concurrency cap = effort limits plus a small global cap.
2. `execute` iterates `agent.run(input)`, and for each event: assign `seq` (in-memory counter, initialised from `max(seq)` for the run so a restart continues), insert into `run_event`, update `run.status/spent_usd` on RUN_FINISHED/interrupt/error, then `handle.publish(seq, event)`. One writer per run means no seq races.
3. `POST /api/v1/runs/{id}/stream` (body = `RunAgentInput`, header `Last-Event-ID` or `?after=`): (a) subscribe to `handle` first, buffering; (b) `SELECT ... WHERE seq > :after ORDER BY seq`; (c) emit those; (d) drain the buffer skipping `seq <= last_sent`; (e) tail until a terminal event, or immediately end if the run is already terminal/interrupted and everything was replayed. Each frame carries `id: <seq>`.
4. Client disconnect: Starlette cancels only the response generator (probe: "generator cancelled on disconnect"); the task is independent. Cancel/pause are explicit REST calls that cancel the task and write status. Pause = cancel at a checkpoint and `status=paused`; resume = new task with `Command(resume=...)` or a continue-from-checkpoint invocation (spike: confirm the adapter continues a thread with no new message and no interrupt).
5. App restart: on startup, runs in `running` are re-launched from the LangGraph checkpoint (spec §4.2); the event log continues at `max(seq)+1`. The in-flight event at the crash may be lost or duplicated; consumers dedupe by `seq`, and the client rebuilds from `MESSAGES_SNAPSHOT`.

**Persistence volume (the part to design).** Per-token `TEXT_MESSAGE_CONTENT` plus full-state `STATE_SNAPSHOT` would make `run_event` huge for a Deep run. Persister rules to adopt: (a) coalesce consecutive `TEXT_MESSAGE_CONTENT`/`TOOL_CALL_ARGS` of the same id for ~250 ms or 2 KB into one row (live subscribers still get the fine-grained events from the fan-out; replay is coarser); (b) store only the **latest** `STATE_SNAPSHOT`/`MESSAGES_SNAPSHOT` per step window, or convert to `STATE_DELTA` (`fast-json-patch` semantics on the client); (c) never persist `RAW`; (d) `payload` jsonb, with `type` and `agent` columns as in §16; (e) retention follows the run.

**Alternatives.** procrastinate (already in the data model) is for durable jobs and gives restart-survival, but the AG-UI generator needs an in-process async iterator and an in-memory tail; keep runs in-process and use the checkpoint for restart (as the spec says). Postgres `LISTEN/NOTIFY` for the tail is unnecessary for a single-process local app; revisit only if the API ever runs multi-process.

**Recommendation.** Build exactly that; put `RunEventLog` (append/replay) and `RunHub` (publish/subscribe) in `services/`, with a test that kills the SSE client mid-run, reconnects with `after=N`, and asserts a gapless, duplicate-free sequence.

## Q3. Frontend: assistant-ui and the AG-UI runtime

**Answer.** `@assistant-ui/react-ag-ui` is real and actively maintained but young (first publish 2025-11-19, reached 0.1.0 on 2026-10-22). It is usable for step 3 with exact pins, and a fallback path exists. It renders the thread (messages, tool calls, interrupts, subagent nesting); the run tree/plan panel must be ours.

**Packages (npm registry JSON, publish dates; all have SLSA provenance, no install scripts)**

| Package | Version | Published | Age on 2026-11-07 |
|---|---|---|---|
| `@assistant-ui/react` | 0.15.32 | 2026-10-28 | 10 d, ok |
| `@assistant-ui/react-ag-ui` | 0.1.1 | 2026-10-28 | 10 d, ok |
| `@ag-ui/client` | **1.0.3** (latest is 1.0.4, 2026-11-05) | 2026-10-16 | 22 d, ok; 1.0.4 is 2 d, **blocked** |
| `@ag-ui/core`, `@ag-ui/proto`, `@ag-ui/encoder` | 1.0.3 (via client) | 2026-10-16 | ok |
| `@assistant-ui/core` 0.3.32, `store` 0.3.22, `generative-ui` 0.0.8, `assistant-stream` 0.3.55 | | 2026-10-19..28 | ok |
| `@assistant-ui/react-markdown` | 0.14.24 | 2026-10-22 | ok (optional; we have react-markdown + rehype-sanitize) |

- `react-ag-ui` depends on `@ag-ui/client ^1.0.3`; `@ag-ui/client@1.0.3` pins `@ag-ui/core|proto|encoder` at 1.0.3 exactly.
- **Supply-chain probe (pnpm 12.15.0, `minimumReleaseAge: 10080`, `trustPolicy: no-downgrade`, no excludes), clean directory:** `pnpm add @assistant-ui/react@0.15.32 @assistant-ui/react-ag-ui@0.1.1 @ag-ui/client@1.0.3` succeeded, `+110` packages, no prompts. `pnpm add @ag-ui/client@1.0.4` failed: "@ag-ui/proto@1.0.4 was published at 2026-11-05T13:37:09Z, within the minimumReleaseAge cutoff (2026-10-31T16:10:56Z)". So **pin `@ag-ui/client` exactly to 1.0.3**; a plain `^1.0.3` would also resolve to 1.0.3 today but would pick 1.0.4 after ~2026-11-12 and drift from our `ag-ui-protocol` 1.0.0.
- Lockfile scan of those 114 resolved packages against the registry: **zero** with `install`/`preinstall`/`postinstall` scripts; no `allowBuilds` needed. 24 lack provenance but none are downgrades (they never had it: `rxjs@7.8.1`, `fast-json-patch@3.1.1`, `untruncate-json@0.0.1`, `react-remove-scroll*`, `@protobuf-ts/protoc@2.11.1`, ...), and the real pnpm install under `no-downgrade` passed. **No `trustPolicyExclude` entry is required.** Re-run the install in the repo (different tree: vite, TanStack) before merging; if `trustPolicy` flags anything there, stop and ask.
- Transitive weight: `@assistant-ui/react` pulls `radix-ui` (aggregate), `zustand`, `zod ^4`, `assistant-cloud`; `assistant-cloud` is only used when its adapter is configured (unverified; check no network call at import in the spike). Our own components remain Base UI; assistant-ui's Radix dependency is internal to its primitives.

**Compatibility**
- React: peer `^18 || ^19` (OK with our `^19.3.1`). Tailwind v4/shadcn: assistant-ui's styled components are installed through the shadcn registry (`npx shadcn@latest add @assistant-ui/thread ...`); per its installation docs (https://www.assistant-ui.com/docs/installation.md via context7) "styles whose name starts with `base-` receive Base UI flavored components", with registry URL `https://r.assistant-ui.com/{style}/{name}.json`. That matches our Base UI shadcn. The registry code is copied into the repo (we own it; strings must be moved to i18next per frontend/AGENTS.md).
- TypeScript 6.0.3: a strict `tsc` (`skipLibCheck:false`, bundler resolution, `jsx: react-jsx`) over a file importing `HttpAgent`, `AssistantRuntimeProvider`, `useAgUiRuntime`, `useAgUiInterrupts`, `useAgUiState`, `useAgUiSubmitInterruptResponses` produced no errors in these packages (one unrelated error in a Radix `.d.mts` needing Node types; our config has `@types/node`).

**Runtime API (from the installed `react-ag-ui` 0.1.1 sources and README)**
- `useAgUiRuntime({ agent })` where `agent` is an `@ag-ui/client` `AbstractAgent` (memoise it). Options: `adapters.history`, `adapters.threadList` (deprecated/experimental), `resumeTranscript: "full" | "appended"`, `autoCancelPendingToolCalls`, `getRunContext`, `onError`, `onCancel`, `showThinking`.
- Interrupts: `useAgUiInterrupts()` returns pending `AgUiInterrupt{id, reason, message, toolCallId, responseSchema, expiresAt, metadata}`; `useAgUiSubmitInterruptResponses()(entries[])` sends `RunAgentInput.resume`; `useAgUiSteerAway(message, responses?)` cancels open interrupts and starts a new user turn. Tool-call interrupts integrate with approval gates (`respondToToolApproval`; stock `ToolFallback` has Allow/Deny). Reason strings `tool_call | input_required | confirmation | string`: we can use `confirmation` for plan approval and `input_required` for clarifying questions, but our interrupt value must carry `reason`/`responseSchema` keys for the adapter to forward them (`interrupts.py` reads `reason`, `message`, `toolCallId`, `responseSchema`, `expiresAt` from a dict value).
- Shared state: `useAgUiState<T>()` and `useAgUiSetState()` expose the latest `STATE_SNAPSHOT/DELTA` (parser handles both, `event-parser.ts`). `CUSTOM` events are parsed (`event-parser.ts` L409) but there is no public hook for arbitrary custom events: read them from our own subscription on the agent (`agent.subscribe({ onCustomEvent })`, AG-UI client API) and keep a small event store for the run tree. Subagents: `SUBAGENT_*` events become nested messages on the spawning tool call (`ToolCallMessagePart.messages`, `PartPrimitive.Messages`); nesting is **lost on reload** and nested interrupts cannot be resumed (README limits). So the run tree must not depend on assistant-ui's nested messages.
- Tool-call UIs: `makeAssistantToolUI`, `useAssistantToolUI`, `makeAssistantTool` are **deprecated**; the current API puts `render` on a toolkit entry registered with `Tools({ toolkit })` (https://www.assistant-ui.com/docs/migrations/toolkit-tools.md). Human tools use `execute: humanTool()` + `render` calling `addResult` once (https://www.assistant-ui.com/docs/tools/defining-tools). For our server-side tools (search, rank, read) we only need `render` for display (`ToolCallMessagePartComponent`); for plan edit / ask_user use the interrupt hooks above, rendered in a message-level component, not as model tools.
- Reconnect: the runtime has `ThreadHistoryAdapter.load()` returning `unstable_resume: true` plus `resume()` (experimental, stream format unverified for AG-UI) and `AbstractAgent.connectAgent()`. Do not rely on either for step 3; use our own replay (below).

**Alternatives**
- `@assistant-ui/react` + `useExternalStoreRuntime` fed by our SSE parser: full control of message shape, but we re-implement tool-call/interrupt wiring that `react-ag-ui` already has; keep as the fallback if `react-ag-ui` blocks on a reconnect or multi-interrupt case.
- `@ag-ui/client` directly (no assistant-ui): trivial for streaming (`HttpAgent.runAgent` + subscriber), but we would write the composer, thread, markdown/streaming, scroll and a11y ourselves. Reject.
- `@assistant-ui/react-data-stream` (0.12.40): it speaks the Vercel/assistant-stream protocol, not AG-UI; rejected (spec fixes AG-UI).

**Recommended wiring**
```ts
class ResearchlyAgent extends HttpAgent {          // @ag-ui/client 1.0.3
  // run(input): new user message -> POST /api/v1/runs, then stream
  // fetch: adds Last-Event-ID / ?after=<seq> from a per-run counter
}
const runtime = useAgUiRuntime({ agent, resumeTranscript: "appended", onError })
```
`HttpAgent` already supports an injected `fetch` and an overridable `requestInit(input)`; credentials default to `same-origin`, so the HttpOnly cookie rides along (Q4). On page load with an active or finished run: create a fresh agent, replay from `seq 0` (the log is the source of truth; coalesced text keeps it small), then tail.

## Q4. SSE through FastAPI, LocalhostGuard and Vite

**Answer.** No blocking issue. The guard is a plain ASGI class (`security/localhost_guard.py`), not `BaseHTTPMiddleware`, so it does not buffer or wrap the body; it checks Host, Origin (same-origin) and the token on `/api` before passing the scope through.

**Evidence (local probe: uvicorn + the real `LocalhostGuard` + a `StreamingResponse` POST that emits one frame every 0.5 s)**
- No cookie/header: `401 {"detail":"missing or invalid token"}`.
- With `Cookie: researchly_token=tok`, `Sec-Fetch-Site: same-origin`, `Origin: http://localhost:<port>`: frames arrived at 14.9, 15.4, 15.9, 16.4, 16.9 s (incrementally, no buffering).
- Client disconnect after 1 s: the generator received `asyncio.CancelledError` ("generator cancelled on disconnect"). That is why the run must live in its own task (Q2).
- POST-based SSE is fine with `fetch` (what `@ag-ui/client` uses, `Accept: text/event-stream`, body JSON). `EventSource` cannot POST and cannot set headers, which is exactly why AG-UI uses fetch streaming; the cookie suffices (`credentials` default `same-origin`).
- Browser fetches send `Origin` on POST: same-origin passes the guard. `Sec-Fetch-Site` is `same-origin` for the SPA, so the cookie branch (`_authenticated`) applies.
- Vite dev: `frontend/vite.config.ts` proxies `/api` with `changeOrigin: true` and forces `headers.origin = app`, so the guard's Origin check passes. `server.proxy` forwards SSE as a stream (no compression plugin, no response buffering in http-proxy); I did not run the Vite proxy in this session: verify with one SSE test in the spike (dev mode only; production has no proxy).

**Practical rules for the endpoint**
- Headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no`, `Content-Type: text/event-stream`; do not add `GZipMiddleware` (it buffers SSE). Use the `encoder.get_content_type()` from `ag_ui.encoder.EventEncoder`, and set the `id:` line ourselves (the encoder emits `data:` only; verify in the spike).
- Heartbeat comment frame (`: ping`) every ~15 s while tailing, so intermediaries and `fetch` timeouts stay quiet; Windows/uvicorn needs no special config.
- Disconnect detection: do not poll `request.is_disconnected()`; just let `CancelledError` propagate from the generator and unsubscribe in `finally`.
- Tag the route `operation_id` and declare the body model so the OpenAPI client regenerates (CI check); the streaming response itself is untyped in OpenAPI, so hand-write only the event types we import from `@ag-ui/core` (this is not an API type).

## Q5. Testing with Playwright and replayed LLM fixtures

**Answer.** Feasible. Determinism problems come from ids and timing, not from assistant-ui.

**Hard parts**
- **Random ids.** AG-UI message ids are `lc_run--<uuid7>`; interrupt ids are checkpoint-derived and differ per run (probe: two runs, two different ids); run/thread ids are ours. Never assert or key on them. `RecordReplay` keys on `sha256(messages, tools, schema, role, alias)`, and messages carry these ids only if they enter prompts; keep ids out of prompts and out of the `RecordReplay` key.
- **Token streaming and snapshots.** Replayed LLM output must be chunked deterministically (fixed chunk size), and the coalescing/flush timers in the persister (Q2) must be test-disabled or clock-injected, otherwise `run_event` row counts vary. Assert on rendered text, not on event counts.
- **Reconnect tests** need a hook to drop the stream: Playwright `page.route` to abort one `/stream` request mid-flight, or `context.setOffline(true)`, then assert the transcript is complete and duplicate-free.
- **assistant-ui internals** are in flux (0.15.x, many `unstable_`/deprecated markers; weekly releases). Exact pins plus `data-testid` hooks on our own wrappers (plan panel, run tree rows, interrupt form) keep tests off its DOM. Use `getByRole` and aria snapshots for the thread (it renders semantic roles).
- **Interrupts**: the UI must read the interrupt id from the event, never from fixtures. In E2E the fixture only holds LLM turns, so a plan-approval step is "click Approve" and works with replay unchanged.

**Layers**
1. **Vitest (fast):** a scripted `AbstractAgent` subclass emitting a fixed event list (text, tool call, interrupt, state, subagent) into `useAgUiRuntime`; assert thread, tool UIs, plan panel and run tree. No network.
2. **Playwright with a frozen transcript:** `page.route('**/api/v1/runs/*/stream', route => route.fulfill({ contentType: 'text/event-stream', body: <recorded SSE text> }))` (committed `e2e/fixtures/agui/*.sse`, recorded once from the real stack with ids scrubbed or normalised). `route.fulfill` delivers the body at once, so it tests rendering, not incremental streaming; good for the panel and interrupt specs.
3. **Playwright full stack (`@smoke`, journeys):** real backend with `RecordReplay` + HTTP cassettes; one Quick run is the `@smoke` chat spec. Streaming is live but fast.
4. **Backend integration (no browser):** drive `agent.run()` with `RecordReplay`, assert event order/types via a normaliser (strip ids, timestamps, token granularity), plus the seq gapless test (Q2). Snapshot the normalised event type sequence per scenario.

## Decisions for the plan

1. Dependencies, backend: add `ag-ui-langgraph==0.0.47`, `ag-ui-protocol==1.0.0`, `langgraph`, `langchain`, `deepagents` (exact pins, ADR); **bump `langchain-core` from 1.6.12 to >=1.7 (1.7.3 resolves)**; `uv lock` backend and cli. Re-run the connector/LLM test suites after the bump.
2. Dependencies, frontend (exact, no caret): `@assistant-ui/react@0.15.32`, `@assistant-ui/react-ag-ui@0.1.1`, `@ag-ui/client@1.0.3` (re-check on/after 2026-11-12 whether 1.0.4 is worth taking; it needs the ADR/pin bump together with `ag-ui-protocol`). No `trustPolicyExclude`, no `allowBuilds` additions expected; confirm in the repo install.
3. Use `LangGraphAgent.run()` as a library inside our run task (factory in `agents/`), flags `emit_raw_events=False`, `emit_interrupt_outcome=True`, `subagent_visibility="attributed"`; subclass `_interrupts_to_agui` / `_build_command_from_agui_resume` for plan approval (approve/edit/reject) and `ask_user`.
4. Interrupt payload contract: Lead-only dicts `{reason: "confirmation"|"input_required", message, responseSchema, plan?: {...}}`; resume payload `{decision, edits?}` / `{answers}`; cancel sentinel handled in the graph.
5. Persister: coalesce text/tool-arg deltas, bound snapshots, drop `RAW`, `seq` assigned by the single writer, `Last-Event-ID`/`?after` on the stream endpoint, heartbeat, `X-Accel-Buffering: no`, no gzip.
6. Run lifecycle service: in-process task registry (`RunHub`), `RunEventLog`, startup resume of `running` runs from checkpoints, REST pause/resume/cancel; a reconnect test is part of "done".
7. Run tree and cost/budget/citations come from our CUSTOM events (`adispatch_custom_event`: `NODE_STATE`, `COST`, `CITATION`) and `SUBAGENT_*`/`TOOL_CALL_*`, stored in a small client store beside the runtime; not from assistant-ui nested messages. Plan panel from `STATE_SNAPSHOT/DELTA` (`useAgUiState`).
8. Tool UIs via `Tools({ toolkit })` in `src/features/chat/tools/`; registry-installed assistant-ui components copied under `src/features/chat/` with strings moved to `en.json`.
9. Send path: `POST /runs` (message, effort, resume) then `POST /runs/{id}/stream`; the custom `ResearchlyAgent` wraps both; reload = replay from seq 0.
10. Test plan as in Q5: scripted-agent Vitest, frozen-SSE Playwright, one `@smoke` full-stack Quick run, backend event-sequence normaliser.

## Open risks

1. **Adapter x Deep Agents unproven.** Not run against a real `create_deep_agent` graph with the `task` tool, `HumanInTheLoopMiddleware` and Postgres checkpointer. Spike 1 (before the plan locks): one Lead + one scout + plan interrupt; verify SUBAGENT_* attribution, tool events, interrupt/resume, pause-then-continue-from-checkpoint without a new message, and `RunAgentInput.messages=[]` behaviour.
2. **`STATE_SNAPSHOT` size and cost** (full state incl. messages after every step). Need an output/state schema or a filter; measure bytes per Quick run in the spike.
3. **0.0.x churn** in the adapter (weekly releases, behaviours behind flags, e.g. `emit_interrupt_outcome` was opt-in because released clients stalled). Mitigate with exact pins, normalised-sequence tests and the own-emitter fallback.
4. **assistant-ui is fast-moving** (0.15.x, deprecations like `makeAssistantToolUI`; AG-UI runtime reached 0.1.0 two weeks ago; nested interrupts and nested-message-on-reload are documented gaps). Mitigate by keeping the run tree outside it and wrapping it behind `features/chat/`.
5. **Reconnect with `react-ag-ui`**: replaying into a fresh agent is our design; `unstable_resume`/`connectAgent` were not exercised. If a replayed `RUN_STARTED`/`MESSAGES_SNAPSHOT` confuses the runtime state (duplicate user message), fall back to hydrating via `adapters.history`.
6. **Interrupt `reason`/`responseSchema` passthrough** depends on our dict keys; unverified against the TS side with `HumanInTheLoopMiddleware` payloads.
7. **Install under the repo's tree** (vite 8, TanStack, shadcn) not run; `trustPolicy`/`minimumReleaseAge` may flag a different package. If so: flag, stop, ask the user (no exclude added).
8. **Vite dev proxy for SSE** not exercised here (config looks correct); production path (FastAPI serves SPA) was.
9. **Windows asyncio:** psycopg async needs `SelectorEventLoop` and the dev `claude-code` provider runs on a Proactor thread; the run task and `RecordReplay` must be tested on both loops (AGENTS.md gotcha).
10. **Disk growth** of `run_event` for long Deep runs even after coalescing; need a retention/compaction rule (e.g. drop token-level rows once the final `MESSAGES_SNAPSHOT` is stored).
