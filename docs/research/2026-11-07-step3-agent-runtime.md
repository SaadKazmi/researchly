# Step 3 research: Deep Agents runtime, persistence, run control, budgets, tests

Date 2026-11-07. Everything below was checked against installed code or run locally (Windows 10, Python 3.12.14, uv scratch venv resolved with `--exclude-newer 2026-10-31`, Postgres 18.6 in a throwaway `pgvector/pgvector:pg18` container). No real LLM was called: probes use `GenericFakeChatModel` subclasses and LiteLLM's `mock_response`. Probe scripts were kept out of the repo (session scratchpad `research3a/`); the essential lines are quoted below.

## Recommendations

1. **Pin `deepagents==0.7.32`** (2026-10-23, the newest release at least 7 days old; 0.7.33/0.7.34 are from 2026-11-05). It **requires `langchain-core>=1.7.0`**, so the current `langchain-core==1.6.12` pin must move to **1.7.3**. Add exact pins: `langchain==1.4.7`, `langgraph==1.2.19`, `langgraph-checkpoint-postgres==3.2.0`. `langchain-litellm==0.11.1` and `litellm==1.105.1` stay. The full backend lock resolves (185 packages) with no torch. (§1)
2. **Windows loop: start uvicorn with `--loop asyncio:SelectorEventLoop`** (native argv and the Dockerfile `CMD`). uvicorn 0.54 accepts any `module:callable` loop factory; probed: the default gives `ProactorEventLoop` and psycopg async fails, the custom factory gives `_WindowsSelectorEventLoop` and works. No separate loop thread is needed. Add a fail-fast check in the lifespan. (§3)
3. **Checkpoint/store tables come from the libraries' own `setup()`, not from Alembic.** Call the sync `PostgresSaver.setup()` and `PostgresStore.setup()` in `db/provision.py` right after `migrate.upgrade()`. Exclude their 6 tables from `test_models_match_migrations` the same way BM25 objects are excluded. (§3)
4. **Runs are in-process asyncio tasks** (as spec §4.2 already says), owned by a small `RunManager`, not procrastinate jobs. Resume on startup = `ainvoke(None, config)` for every `running` run. Pause = cancel the task and set status `paused`; resume uses the same call. Branch = `aupdate_state(past_config, ...)` or `ainvoke(None, past_config)`. All of these were probed against Postgres. (§4)
5. **HITL:** `ask_user` is a stub tool with `interrupt_on={"ask_user": {"allowed_decisions": ["respond"]}}`. Plan approval is a `submit_plan` tool with `["approve", "edit", "reject"]`, where edit means an edited plan and reject means cancel. Resume with `Command(resume={"decisions": [...]})`. This was probed across a process restart. (§2, §4)
6. **Budgets are two custom middleware classes.** `BudgetGuard` uses `abefore_model`: at ≥100% it ends the run with `jump_to="end"` and a final AI message, so there is no exception and the status is `partial`. It uses `awrap_model_call` to append the 80% wrap-up message. `LoopGuard` uses `awrap_tool_call` and returns an error `ToolMessage`. Spend comes from the existing `LedgerCallback`, attached to each model instance through its `callbacks` field. `metadata["lc_agent_name"]` gives the agent name. (§5)
7. **Disable the default `general-purpose` subagent** with `register_harness_profile(<provider key>, HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))`. Without it, the Lead sees an extra all-tools subagent. Probed. (§2)
8. **Set `profile={"max_input_tokens": context_window}` on every registry model.** Without it, Deep Agents' built-in summarization falls back to a trigger at 170k tokens, which is wrong for small and local models. (§2)
9. **Tests:** Deep Agents accepts any `BaseChatModel` that implements `bind_tools`. `RecordReplayChatModel` recorded and replayed a Lead plus 2 parallel scouts identically. `GenericFakeChatModel` needs a 2-line subclass that adds `bind_tools`, because the base class raises `NotImplementedError`. (§6)
10. **Spec fixes.** §6.4 "at 100% raises `BudgetExhausted`" should become "ends the run as `partial`", or the exception should be raised only in the Lead. §6.6 should read "deepagents 0.7.32", and its `StoreBackend` namespace example should read `rt.context.project_id`. §16's mention of procrastinate tables applies only to background jobs, not runs. (§4, §5)

## 1. Versions and compatibility

PyPI JSON API, accessed 2026-11-07 (`https://pypi.org/pypi/<name>/json`):

| Package | Latest (date) | ≥7-day candidate | Notes |
|---|---|---|---|
| deepagents | 0.7.34 (2026-11-05) | **0.7.32** (2026-10-23) | 0.7.33 and 0.7.34 are too new; none are yanked |
| langchain | 1.4.9 (11-04) | **1.4.7** (10-21) | deepagents 0.7.32 needs ≥1.4.7 |
| langchain-core | 1.7.6 (11-05) | **1.7.3** (10-29) | 1.7.4 (11-02) is too new |
| langgraph | 1.2.20 (11-05) | **1.2.19** (10-30) | langchain 1.4.7 needs `>=1.2.12,<1.3` |
| langgraph-checkpoint-postgres | 3.2.0 (10-14) | **3.2.0** | needs `langgraph-checkpoint>=4.3`, psycopg-pool |
| langchain-litellm | 0.11.2 (10-25) | keep 0.11.1 | needs `langchain-core>=1.6.1,<2`, so 1.7.3 is fine |
| ag-ui-langgraph | 0.0.47 (10-08) | 0.0.47 | resolves with the stack above (`langgraph<2`, `langchain>=1.2`) |

`deepagents==0.7.32` `requires_dist`: `langchain>=1.4.7,<2`, `langchain-core>=1.7.0,<2`, `langchain-anthropic>=1.7.6,<2`, `langchain-google-genai>=4.4.2,<5`, `langsmith>=0.14.12`, `wcmatch`. **`langchain-anthropic` and `langchain-google-genai` are hard dependencies**, so the anthropic and google-genai SDKs get installed. They are inert unless used (see Risks).

```
$ printf "deepagents==0.7.32\nlangchain-core==1.6.12\n" > bad.in
$ uv pip compile bad.in --python-version 3.12 --exclude-newer 2026-10-31
  × No solution found ... deepagents>=0.7.32 depends on langchain-core>=1.7.0,<2.0.0 ... unsatisfiable.

$ uv pip compile req.in --python-version 3.12 --exclude-newer 2026-10-31   # deepagents==0.7.32, langchain-litellm==0.11.1, litellm==1.105.1, langgraph-checkpoint-postgres, psycopg[binary], psycopg-pool
deepagents==0.7.32  langchain==1.4.7  langchain-core==1.7.3  langgraph==1.2.19  langgraph-checkpoint==4.3.1
langgraph-checkpoint-postgres==3.2.0  langgraph-prebuilt==1.1.1  langgraph-sdk==0.4.11  langsmith==0.14.16
langchain-anthropic==1.7.7  anthropic==2.3.0  langchain-google-genai==4.4.2  google-genai==2.34.0
psycopg==3.3.6  psycopg-pool==3.3.3  ...  (102 packages; grep -i torch -> none)
```

Full backend check: a copy of `backend/pyproject.toml` with `langchain-core==1.7.3` plus the four new pins, then `uv lock` (the project's own `exclude-newer = "7 days"`). Result: `Resolved 185 packages`, `httpx 0.28.1` unchanged, no `torch` or `onnxruntime-gpu`. New packages compared with today's lock: anthropic, bracex, deepagents, docstring-parser, google-auth, google-genai, langchain, langchain-anthropic, langchain-google-genai, langgraph, langgraph-checkpoint, langgraph-checkpoint-postgres, langgraph-prebuilt, langgraph-sdk, ormsgpack, psycopg-pool, pyasn1, pyasn1-modules, wcmatch. Remember `uv lock --directory cli` too.

**Recommendation:** make one dependency commit with the bump plus the full backend suite, because `langchain-core` 1.6 → 1.7 is a minor bump under existing code (ledger and RecordReplay tests). Write ADR 0011, "agent runtime pins", per spec §6.6.

## 2. `create_deep_agent` API at 0.7.32

Signature (read from `deepagents/graph.py`):

```python
create_deep_agent(model: str | BaseChatModel | None = None, tools=None, *, system_prompt: str | SystemMessage | None = None,
    middleware=(), subagents: Sequence[SubAgent | CompiledSubAgent | AsyncSubAgent] | None = None,
    skills: list[str] | None = None, memory: list[str] | None = None, permissions: list[FilesystemPermission] | None = None,
    backend: BackendProtocol | None = None, interrupt_on: dict[str, bool | InterruptOnConfig] | None = None,
    response_format=None, state_schema=None, context_schema=None, checkpointer=None, store=None,
    debug=False, name: str | None = None, cache=None) -> CompiledStateGraph
```

Imports:

| Thing | Import |
|---|---|
| factory, specs | `from deepagents import create_deep_agent, SubAgent, CompiledSubAgent, FilesystemPermission, HarnessProfile, GeneralPurposeSubagentProfile, register_harness_profile` |
| backends | `from deepagents.backends import CompositeBackend, StateBackend, StoreBackend, FilesystemBackend` |
| middleware | `from langchain.agents.middleware import TodoListMiddleware, ModelCallLimitMiddleware, ToolCallLimitMiddleware, ToolRetryMiddleware, ModelRetryMiddleware, ModelFallbackMiddleware, ContextEditingMiddleware, ClearToolUsesEdit, SummarizationMiddleware, HumanInTheLoopMiddleware, InterruptOnConfig, AgentMiddleware, ModelRequest, hook_config` |
| structured output | `from langchain.agents.structured_output import ToolStrategy, ProviderStrategy` |
| resume | `from langgraph.types import Command` |

**Middleware order** (docstring): base stack is Filesystem → SubAgent → Summarization → PatchToolCalls. Then come **user `middleware=`**. The tail is profile extras → Skills → AnthropicPromptCaching (a no-op for non-`ChatAnthropic`) → Memory → HumanInTheLoop (when `interrupt_on` is set) → UnsupportedContent. **TodoList is not in the stack**: the probe's bound tools were `delete, edit_file, glob, grep, ls, read_file, task, write_file`. Pass `TodoListMiddleware()` explicitly to the Lead. There is no `execute` tool with a non-sandbox backend.

**Constructors (installed signatures):**
- `ModelCallLimitMiddleware(*, thread_limit=None, run_limit=None, exit_behavior: 'end'|'error' = 'end')`
- `ToolCallLimitMiddleware(*, tool_name=None, thread_limit=None, run_limit=None, exit_behavior: 'continue'|'error'|'end' = 'continue')`. With `"end"`, every pending call in the same `AIMessage` also gets an error `ToolMessage`, and the agent stops with an explanatory AI message. Use `run_limit`; it must be ≤ `thread_limit`.
- `ToolRetryMiddleware(*, max_retries=2, tools=None, retry_on=default_retry_on, on_failure='continue', backoff_factor=2.0, initial_delay=1.0, max_delay=60.0, jitter=True)`. `ModelRetryMiddleware` has the same shape without `tools`.
- `ModelFallbackMiddleware(first_model, *additional_models)` takes instances, so pass registry-built models for the same role from other providers.
- `ContextEditingMiddleware(*, edits=None, token_count_method='approximate', token_counter=None)` with `ClearToolUsesEdit(trigger=100000, clear_at_least=0, keep=3, clear_tool_inputs=False, exclude_tools=(), placeholder='[cleared]')`.
- `SummarizationMiddleware` is already in the base stack. Its defaults (`deepagents/middleware/summarization.py`) are `trigger=("fraction", 0.85)`, `keep=("fraction", 0.10)` when `model.profile["max_input_tokens"]` exists, **else `("tokens", 170000)` / `("messages", 6)`**. `ChatLiteLLM(...).profile` is `None` by default and accepts `profile={"max_input_tokens": n}` (probed), so the registry should set it from `model_info().context_window`.
- `HumanInTheLoopMiddleware(interrupt_on, *, description_prefix=..., edit_notice=..., interrupt_mode='batched')`. Decisions are `approve | edit | reject | respond`. `InterruptOnConfig` keys are `allowed_decisions, description, args_schema, when`.

**Subagents.** `SubAgent` is a TypedDict with `name, description, system_prompt, tools, model, middleware, interrupt_on, skills, permissions, response_format, mode ("isolated"|"fork")`. Use the default isolated mode and never `AsyncSubAgent`. The `task` tool args are `description, subagent_type`. Subagents get the filesystem tools by default; narrow them with `permissions=[FilesystemPermission(operations=["write"], paths=[...], mode="deny")]`. Probe: the Lead issued 2 parallel `task` calls to `scout` with `response_format=ToolStrategy(SubagentResult)`. Each scout saw a `SubagentResult` tool, and the Lead received `{"status":"complete","summary":"s1","files":[],"gaps":[]}` as JSON `ToolMessage` content.

**General-purpose subagent.** It is added unless a profile disables it. Probe on the `task` description: before registration it lists `- general-purpose: ... has access to all tools`, after `register_harness_profile("toolfake", HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))` only `- scout`. The profile key is the model's provider: `_KeyedChatLiteLLM` resolves to `litellm` (probed: `ChatLiteLLM -> provider 'litellm'`), and custom classes resolve to their lowercased class name, such as `recordreplaychatmodel` or `claudecodechatmodel`. `profiles` is a **beta** API.

**Backends.** `StoreBackend(*, namespace: Callable[[Runtime], tuple[str, ...]], store=None)` and `CompositeBackend(default, routes: dict[str, BackendProtocol], *, artifacts_root="/")`. The project id comes from `context_schema`. Probed:

```python
backend = CompositeBackend(default=StateBackend(),
    routes={"/projects/": StoreBackend(namespace=lambda rt: ("project", rt.context.project_id))})
agent = create_deep_agent(..., backend=backend, store=store, context_schema=Ctx)
await agent.ainvoke(inp, cfg, context=Ctx(project_id="p1"))
# write_file('/projects/plan.md') -> store.asearch(("project","p1")) keys: ['/plan.md']
```

**Skills.** `skills=["/skills/"]` are backend paths. Route `/skills/` to `FilesystemBackend(root_dir=<package>/agents/skills, virtual_mode=True)` and deny writes there with `FilesystemPermission(operations=["write"], paths=["/skills/**"], mode="deny")`. Also set them per subagent through `SubAgent["skills"]`. This setup was not probed end to end.

**HITL fit (spec F1.2, F1.3):**
- `ask_user(question)` is a tool that is never executed. `interrupt_on={"ask_user": {"allowed_decisions": ["respond"]}}`. The resume `{"decisions": [{"type": "respond", "message": answer}]}` becomes a success `ToolMessage`. If the user skips, respond with "user skipped". The Lead prompt caps questions at 3; a `ToolCallLimitMiddleware(tool_name="ask_user", run_limit=3)` enforces it.
- `submit_plan(plan: Plan)` uses `allowed_decisions=["approve", "edit", "reject"]`. Edit carries the user's edited plan through `edited_action.args`, and reject cancels the run. For Quick runs, leave `submit_plan` off the Lead's tools.
- Interrupts raised inside subagents surface on the parent as `__interrupt__` (docs: deepagents human-in-the-loop). Keep HITL on the Lead only (R1).

Sources (accessed 2026-11-07): https://docs.langchain.com/oss/python/langchain/human-in-the-loop#interrupt-decision-types, https://docs.langchain.com/oss/javascript/deepagents/human-in-the-loop#interrupts-on-tool-calls, plus installed source `deepagents/graph.py` and `deepagents/middleware/subagents.py`.

## 3. Persistence on Postgres and the Windows loop

**The problem, reproduced:**

```
$ python -I probe_loop.py proactor   # asyncio.run(AsyncPostgresSaver.from_conn_string(URL).setup())
FAIL InterfaceError Psycopg cannot use the 'ProactorEventLoop' to run in async mode. Please use a compatible event loop, for instance by running 'asyncio.run(..., loop_factory=asyncio.SelectorEventLoop)'
$ python -I probe_loop.py selector
setup ok on _WindowsSelectorEventLoop
```

**uvicorn 0.54** (`uvicorn/config.py:get_loop_factory`): when `--loop` is not one of the built-in names, it is imported as a dotted path and used as the factory directly. Its built-in `asyncio` factory returns `ProactorEventLoop` on win32. Probe (tiny ASGI app doing `psycopg.AsyncConnection.connect` + `select 1`, run with `backend/.venv` uvicorn):

```
--loop auto                     -> ProactorEventLoop FAIL InterfaceError
--loop asyncio:SelectorEventLoop -> _WindowsSelectorEventLoop ok 1
```

Options considered:

| Option | Verdict |
|---|---|
| `uvicorn --loop asyncio:SelectorEventLoop` | **Chosen.** One argv change in `native.py` and `infra/app.Dockerfile` (a no-op on Linux). Everything runs on one loop, so the AG-UI stream, graph and DB share it. On Windows, Selector cannot run asyncio subprocesses: no current code uses `create_subprocess_*` (light cells use sync `subprocess.run`, and the dev `claude-code` provider already runs its own Proactor thread). `select()` is capped at 512 sockets, which is fine for one user. |
| Graph on a dedicated selector-loop thread | Works, but every stream and cancel must cross loops (`run_coroutine_threadsafe` plus thread-safe queues). More code for no gain. |
| Sync `PostgresSaver` in threads | Blocks a thread per checkpoint write and still needs async DB for the rest of the app. Rejected. |

Add a guard in `_lifespan`: on win32, if `type(asyncio.get_running_loop())` is a Proactor loop, refuse to start with a clear message. A developer who runs plain `uvicorn` would otherwise get a psycopg error deep inside a run. `services/capabilities.write_capability` ("sync on purpose: uvicorn on Windows runs a Proactor loop") can stay as it is.

**Pool and constructors:** `AsyncPostgresSaver(conn, pipe=None, serde=None)`, `AsyncPostgresStore(conn, *, pipe=None, deserializer=None, index=None, ttl=None)`. `conn` may be an `AsyncConnectionPool`, and its connections need `autocommit=True, prepare_threshold=0, row_factory=dict_row`; `from_conn_string` uses exactly these (`aio.py:87`). Open the pool in the lifespan (`AsyncConnectionPool(url, open=False, kwargs=...)` then `await pool.open()`), share it between saver and store, and use about `max_size=10` (the server has `max_connections` 40). The URL must drop SQLAlchemy's `+psycopg` suffix. Use `index=None`: no vector index in the store, because semantic memory is step 6.

**Who creates the tables.** `setup()` is idempotent and versioned by its own tables. The checkpointer creates `checkpoint_migrations` (10 migrations, max v=9), `checkpoints`, `checkpoint_blobs` and `checkpoint_writes`; the store creates `store` and `store_migrations` (4, plus 3 vector migrations only with `index=`). Some indexes use `CREATE INDEX CONCURRENTLY`, which cannot run inside Alembic's transaction. Copying the SQL into a revision would freeze it, and the next library upgrade would need the old revision edited or `checkpoint_migrations` hand-synced. **Decision:** library-owned tables are managed by the library, the same way extension-owned objects are by the extension.
- `db/provision.py`: after `migrate.upgrade(app_url)`, run sync `PostgresSaver.from_conn_string(u).setup()` and `PostgresStore.from_conn_string(u).setup()` as `researchly_app`. Sync avoids the loop question, and provision is already sync. Tests call the same function.
- `test_models_match_migrations`: extend `include_object` to skip `checkpoint*` and `store*` tables. Add a test that `setup()` runs twice cleanly and as the non-superuser owner.
- Upgrades to `langgraph-checkpoint-postgres` run their migrations automatically on the next `researchly start`, after the pre-migration backup (§16.1).

## 4. Running and controlling runs

**Probed against Postgres (`probe_agent.py`, two separate processes):**

```
# process 1: Lead calls ask_user -> interrupt (durability="sync")
interrupt: {"action_requests": [{"name": "ask_user", "args": {"question": "Which years?"}, ...
next: ('HumanInTheLoopMiddleware.after_model',)
# process 2 ("restart"): new saver/store/agent, same thread_id
after restart next: ('HumanInTheLoopMiddleware.after_model',) interrupts: 1
final: done | tool msg: ['2020-2025', 'Updated file /projects/plan.md']
checkpoints: 23
branch final: branched done | from 1f1dac5a-938      # ainvoke(None, past_snapshot.config)
checkpoints after branch: 30
```

```
# probe_cancel.py: cancel the asyncio task during a 2 s tool, then resume
cancelled; next = ('tools',)
resumed final: finished | slow tool ran 2 times
```

**Semantics for the run manager:**
- **Start:** `thread_id = run.checkpoint_thread_id` (a UUID, <255 chars), with `context=RunCtx(project_id, run_id, effort)`.
- **Interrupt (clarify or plan):** the graph returns `__interrupt__`, the run status becomes `awaiting_user`, and the task ends. The answer arrives through `ainvoke(Command(resume={"decisions": [...]}), cfg)` in a new task.
- **Pause:** cancel the task, set status `paused`. **Resume** (user action or app start): `ainvoke(None, cfg)`. The step that was in flight **re-runs**, as the slow tool ran twice in the probe, so tools with side effects must go through `tool_cache` or idempotent keys (spec §6.3 already requires this). A cooperative pause, where a `before_model` hook calls `interrupt()`, would avoid the re-run but has to cope with parallel subagents. Skip it until the re-run is shown to cost real money.
- **Cancel:** cancel the task, set status `cancelled`, keep the files.
- **Resume after restart:** in the lifespan, for each run with status `running`, schedule `ainvoke(None, cfg)`. `awaiting_user` and `paused` runs stay put.
- **Branch-and-rerun (F1.6):** `history = [s async for s in agent.aget_state_history(cfg)]`. Pick a **Lead-level** snapshot (subagent checkpoints sit under a `checkpoint_ns` and are not branch points). Either `await agent.ainvoke(None, snap.config)` to replay, or `new_cfg = await agent.aupdate_state(snap.config, values)` then `ainvoke(None, new_cfg)` to fork with edits. Both create new checkpoints on the same thread; history is never rewritten (docs: use-time-travel). Store `parent_run_id` and `branch_from_checkpoint` on a new `run` row that shares `checkpoint_thread_id`. Store-backed `/projects/` files are **not** versioned by checkpoints, so a branch sees current files (see Risks).
- **Durability:** pass `durability="sync"` for book runs and `"async"` (the default) otherwise, as a kwarg to `ainvoke`/`astream`. Per the docs, `"async"` "has a small risk" of losing the last checkpoint on a crash, and resume then repeats one step.

**Job queue?** Spec §4.2 already says: "Research runs execute as asyncio tasks... Scheduled and background jobs... run on procrastinate". Keep that. Reasons:
- one user and one process;
- a run must share the event loop with its SSE stream and its `asyncio.Task` handle for cancel;
- LangGraph checkpoints already provide durability and resume;
- procrastinate would add a worker, a lock and tables, and still not supply cancel or interrupt semantics.

Add procrastinate only when the first scheduled job lands (alerts, step 9).

Sources (accessed 2026-11-07): https://docs.langchain.com/oss/python/langgraph/checkpointers#durability-modes, https://docs.langchain.com/oss/python/langgraph/use-time-travel#fork, https://docs.langchain.com/oss/python/langgraph/checkpointers#replay, https://docs.langchain.com/oss/python/langgraph/fault-tolerance#protect-side-effects-with-an-idempotency-key, https://docs.langchain.com/oss/python/langgraph/persistence#postgressaver-thread_id-too-long.

## 5. Budgets, ledger, loop detection

**Cost per call.** `langchain-litellm` 0.11.1 puts usage and cost on the message (probe with `model_kwargs={"mock_response": ...}`, no network):

```
usage_metadata: {'input_tokens': 10, 'output_tokens': 20, 'total_tokens': 30}
response_cost: 0.00011 provider: litellm               # AIMessage.response_metadata["response_cost"]
stream usage_metadata: {'input_tokens': 8, 'output_tokens': 2, ...}   # with stream_options={"include_usage": True}
```

Keep the existing `LedgerCallback` (`usage_metadata` plus our `cost_usd()`), so pricing follows `capabilities.yaml` overrides and RecordReplay or fake models cost the same way. `response_cost` is a cross-check, not the source.

**Wiring.** Every `BaseChatModel` has a `callbacks` field (probed on `ChatLiteLLM`). The factory builds one model per (run, role) and sets `model.callbacks = [LedgerCallback(run_id, role, provider, model)]`. Per-call `metadata` carries the agent name. Probe:

```
[lead-model]  lc_agent_name=lead  node=model thread=t ckpt_ns=model:bf3d...
[scout-model] lc_agent_name=scout node=model thread=t ckpt_ns=tools:ca4b...
```

So `LedgerCallback` should take `agent` from `metadata["lc_agent_name"]` instead of its constructor. Fallback models need their own ledger callback.

**Budget guard and loop detector (probed with `create_agent`, which the deep agent wraps):**

```python
class BudgetGuard(AgentMiddleware):
    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        if spent(runtime.context.run_id) >= budget:            # SELECT sum(cost_usd) FROM llm_call WHERE run_id=...
            return {"messages": [AIMessage("Budget exhausted; stopping with partial results.")], "jump_to": "end"}
    async def awrap_model_call(self, request: ModelRequest, handler):
        if spent(...) >= 0.8 * budget:
            request = request.override(messages=[*request.messages, HumanMessage(WRAP_UP)])
        return await handler(request)

class LoopGuard(AgentMiddleware):
    async def awrap_tool_call(self, request, handler):      # sha256(tool, normalized args) seen 3x -> error ToolMessage
        ...
        return ToolMessage("Repeated identical call; change strategy.", tool_call_id=tc["id"], status="error")
```

```
model calls: 4 | last human seen by model: WRAP UP: budget at 80%, write the answer now.
tool results: ['results', 'results', 'Repeated identical call; change strategy.', 'Repeated identical call; change strategy.']
final: Budget exhausted; stopping with partial results.
```

Notes:
- `ModelRequest.override` accepts `model, system_message, messages, tool_choice, tools, response_format, model_settings, state`.
- `jump_to="end"` inside a subagent ends only that subagent: it returns its last message to the Lead, and the Lead's own guard then ends the run. That gives the "graceful stop, partial results" behaviour without an exception crossing the `task` tool.
- Keep LoopGuard counters in graph state, a middleware `state_schema` field, rather than on the instance. Otherwise they reset on resume and are shared across runs.
- The "2 loop errors then `partial`" rule is a counter in the same state plus `jump_to="end"`.
- Effort caps (max scouts or readers) are a `wrap_tool_call` on the Lead that counts `task` calls by `subagent_type`. `ToolCallLimitMiddleware(tool_name="task")` cannot tell the two subagent types apart.
- Per-subagent timeouts are `asyncio.wait_for(handler(request), t)` in the same wrapper, returning an error `ToolMessage` on timeout.

## 6. Testing

- **Any `BaseChatModel` works** if it implements `bind_tools`. `create_agent` calls `bind_tools` and nothing provider-specific is required. `GenericFakeChatModel` does **not** implement it, so a test helper needs `class ToolFake(GenericFakeChatModel): def bind_tools(self, tools, **kw): return self`. Script `AIMessage(tool_calls=[...])` replies in order. A Lead and its subagents each get their own fake.
- **RecordReplay through a deep agent** (`probe_rr.py`, imports `researchly.llm.record_replay` from `backend/src`): record mode with fake inner models for a Lead and 2 parallel scouts wrote 4 lines. Replay with `inner=None` produced message contents identical to the recording, ending in `lead done`. Keys are content-addressed, so parallel order does not matter.
- **Key churn:** the key covers the full prompt, including Deep Agents' built-in system prompt and tool descriptions. Any `deepagents`/`langchain` bump re-keys every agent fixture, so upgrade ADRs must include re-recording (spec §6.6 already asks for "full suite"). There were no time or uuid sources in prompts, except in `async_subagents.py`, which we do not use.
- DB-level tests (resume, branch, HITL) run on the existing `@pytest.mark.db` Postgres with `anyio_backend` already set to SelectorEventLoop (`backend/tests/conftest.py`). Unit tests can use `InMemorySaver` and `InMemoryStore`.

## Decisions for the plan

1. Dependency commit: pin `langchain-core==1.7.3`, `langchain==1.4.7`, `langgraph==1.2.19`, `deepagents==0.7.32` and `langgraph-checkpoint-postgres==3.2.0` (psycopg-pool comes with it). Run `uv lock` in backend and `uv lock --directory cli`, then the full suite. Write ADR 0011.
2. uvicorn gets `--loop asyncio:SelectorEventLoop` in `cli/.../native.py` and `infra/app.Dockerfile`. The lifespan refuses to start on a Proactor loop.
3. `db/provision.py` runs sync `PostgresSaver.setup()` and `PostgresStore.setup()` after Alembic. The migration test excludes `checkpoint*` and `store*`, and a new test runs setup twice as the app role.
4. Lifespan opens one `AsyncConnectionPool` (autocommit, `prepare_threshold=0`, `dict_row`, max 10) shared by `AsyncPostgresSaver` and `AsyncPostgresStore`.
5. `agents/factory.py` is the only `deepagents` importer. It builds the Lead with `TodoListMiddleware`, BudgetGuard, LoopGuard, effort and timeout wrappers, `ModelCallLimitMiddleware`, `ToolCallLimitMiddleware(exit_behavior="end")`, `ToolRetryMiddleware` and `ModelFallbackMiddleware`. It disables the general-purpose subagent by harness profile, defines Scout and Reader as `SubAgent` with `response_format=ToolStrategy(SubagentResult)`, and uses `CompositeBackend(StateBackend, {"/projects/": StoreBackend(ns from context), "/skills/": read-only FilesystemBackend})`.
6. Registry: set `profile={"max_input_tokens": ...}` and per-run `callbacks=[LedgerCallback]`. `LedgerCallback` reads the agent from `lc_agent_name`.
7. HITL: `ask_user` uses `respond` and is capped at 3. `submit_plan` uses approve, edit and reject, and is skipped for Quick.
8. `runs/manager.py`: an in-process `dict[run_id, Task]`. It handles start, resume (`None` or `Command`), pause and cancel (`task.cancel()`), branch (`aget_state_history` → `aupdate_state` / `ainvoke(None, cfg)`), and on startup re-attaches runs in status `running`. No procrastinate.
9. `durability="sync"` for book runs, `"async"` otherwise.
10. Test helper `ToolFake` for unit tests, RecordReplay for integration, DB tests for resume, branch and HITL across a fresh saver instance.

## Open risks

- **Fixture churn:** every `deepagents`/`langchain` bump changes built-in prompts and tool text, so all agent fixtures must be re-recorded. Budget re-recording time into each upgrade ADR.
- **Checkpoint volume:** 23 checkpoints for a 3-call toy run, because each middleware hook is a node. Deep runs will write thousands of rows, and blobs hold full message lists (Deep Agents uses a delta channel for `messages`). Add pruning of finished runs' checkpoints, keeping the final one and the branch points, and measure size in step 3.
- **Branches do not rewind files:** `/projects/` lives in the Store, outside checkpoints. A branch from an early checkpoint sees later files. Either write run outputs under `/projects/{pid}/runs/{run_id}/` so a branch gets its own prefix, or accept it and document it.
- **Re-run on resume:** the in-flight step repeats after pause, crash or `"async"` durability. Every side-effecting tool must be idempotent or cached; LLM calls in that step are paid twice.
- **Beta APIs:** `register_harness_profile` and `GeneralPurposeSubagentProfile` are marked beta, and the profile registry is process-global and keyed by provider. A pinned version limits the blast radius; cover it with a test that asserts the `task` description lists only our subagents.
- **Extra SDKs:** `langchain-anthropic`, `anthropic`, `langchain-google-genai` and `google-genai` are hard dependencies of deepagents. They are unused, but they enlarge the supply-chain surface (§19.7). `langsmith` is pulled in too; tracing stays off unless `LANGSMITH_TRACING` is set, so CI should assert it is unset.
- **Anthropic prompt caching:** the built-in `AnthropicPromptCachingMiddleware` only acts on `ChatAnthropic`, so through `ChatLiteLLM` it is a no-op. `cache_control` must be added by our own middleware or through LiteLLM params (spec §7.4). This is unverified and needs a step-3 probe with recorded fixtures.
- **Selector loop on Windows:** any future `asyncio.create_subprocess_*` in the app process breaks on Windows. Add a repo check, or keep the rule "subprocesses are sync, in threads".
- **langchain-core 1.6 → 1.7** under existing code is untested here; the dependency commit's full suite is the check.
