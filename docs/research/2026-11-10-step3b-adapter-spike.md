# Spike 3b: ag-ui-langgraph 0.0.47 against our real Deep Agents graph

Date 2026-11-10. Verdict: **GO**, with one mandatory subclass override, two recommended ones, and one behaviour to avoid (the adapter's own "continue"). Everything below was run with fake models (no network LLM), InMemorySaver, a standard-effort `build_agent`, `emit_raw_events=False, emit_interrupt_outcome=True, subagent_visibility="attributed"`. Repo untouched (`git status` shows only the pre-existing `scratch_dbg.py`).

## 1. Resolution
`--with` resolves cleanly and changes nothing: deepagents 0.7.32, langchain 1.4.7, langchain-core 1.7.3, langgraph 1.2.19, ag-ui-langgraph 0.0.47, ag-ui-protocol 1.0.0 (also litellm 1.105.1, langchain-litellm 0.11.1). The backend env already carried these pins. 42 packages went into the ephemeral overlay only.

## 2. Event sequences (0 RAW events)
(a) First run until the `submit_plan` interrupt (19 events):
`RUN_STARTED, STEP_STARTED/FINISHED x4 (flat node names), STEP_STARTED, TOOL_CALL_START, TOOL_CALL_ARGS x2, TOOL_CALL_END, STEP_FINISHED, STEP_STARTED, STATE_SNAPSHOT, MESSAGES_SNAPSHOT, STEP_FINISHED, CUSTOM(on_interrupt), RUN_FINISHED(outcome=interrupt)`

(b) Resume with approve (79 events, counts): RUN_STARTED 1, STEP_STARTED/FINISHED 26/26, STATE_SNAPSHOT 4, MESSAGES_SNAPSHOT 2, TOOL_CALL_START 3, TOOL_CALL_ARGS 5, TOOL_CALL_END 3, TOOL_CALL_RESULT 2, SUBAGENT_STARTED 1, SUBAGENT_FINISHED 1, TEXT_MESSAGE_START/CONTENT(2)/END, RUN_FINISHED (no outcome).

TOOL_CALL_* are emitted (START/ARGS/END from streamed chunks, RESULT from tool end). SUBAGENT_* are emitted and attributed for `task`:
```
TOOL_CALL_START  {"toolCallId":"task-1","toolCallName":"task","parentMessageId":"lc_run--7baf..."}
SUBAGENT_STARTED {"subagentRunId":"tools:53891b03-b1b5-7c93-64eb-8a6b0d8b3225","name":"scout","description":"Find papers on attention.","parentToolCallId":"task-1","parentMessageId":"lc_run--7baf..."}
TOOL_CALL_START  {"subagentRunId":"tools:5389...","toolCallId":"SubagentResult-2","toolCallName":"SubagentResult","parentMessageId":"lc_run--69ff...::tools:5389..."}
SUBAGENT_FINISHED{"subagentRunId":"tools:5389...","result":"{\"status\":\"complete\",\"summary\":\"1 paper\",...}"}
TOOL_CALL_RESULT {"messageId":"task-1","toolCallId":"task-1","content":"{\"status\":\"complete\",...}","role":"tool"}
```
`name` is our subagent name (scout/reader), `subagentRunId` is `tools:<langgraph task id>`, inner events carry `subagentRunId`.

## 3. Interrupt shape and hooks
`RUN_FINISHED.outcome` for the plan (ask_user is identical with name `ask_user` and `allowed_decisions:["respond"]`):
```json
{"type":"interrupt","interrupts":[{"id":"af9a2d5b0806188f76bb6aea8cffd8cd","reason":"langgraph:interrupt",
 "metadata":{"langgraph":{"raw":{"action_requests":[{"name":"submit_plan","args":{"plan":"1. search"},
   "description":"Tool execution requires approval\n\nTool: submit_plan\nArgs: {...}"}],
   "review_configs":[{"action_name":"submit_plan","allowed_decisions":["approve","edit","reject"]}]},
   "ns":null,"resumable":null,"when":null}}}]}
```
No `message`, `toolCallId` or `responseSchema` (the adapter only reads those top-level keys of a dict value). The legacy `CUSTOM name=on_interrupt` carries the same raw dict as a JSON string (still emitted by default).

**The default `_build_command_from_agui_resume` already works for HITL**: a single resolved entry becomes `Command(resume=payload)`, and `{"decisions":[...]}` is exactly HumanInTheLoopMiddleware's resume format. Verified: approve (submit_plan), respond (ask_user, then the next interrupt surfaced), and two parallel action_requests (ask_user + submit_plan in one turn: still ONE AG-UI interrupt, resumed by one entry carrying two decisions; both tool messages produced). So **neither interrupt hook is required**. An optional `_interrupts_to_agui` override only makes the wire shape friendlier (reason/message/responseSchema); shown in `Lean` below. Caveats: `interrupt_id` on a single-entry resume is not validated (a bogus id still resumes with the payload), so validate it in our API layer against the open interrupt; several simultaneous raw interrupts would need the `__agui_resume_map__` path (not the case with one Lead HITL middleware).

**Mandatory override (found by failure): `get_stream_kwargs`.** The adapter merges `config["configurable"]` (including `thread_id`) into a context *dict*, and LangGraph then calls `RunContext(**dict)`:
`RUN_ERROR "RunContext.__init__() got an unexpected keyword argument 'thread_id'"`. Fix: pass our real `RunContext` instance. The adapter keeps per-run state on `self`, so create one agent instance per run (its FastAPI helper clones per request). See `OurAgent` below.

## 4. Pause then continue
The adapter has NO "continue from checkpoint" for pending tool tasks. `run()` on a thread with `messages=[]` and no resume:
- Thread sits at an interrupt: it re-emits `RUN_STARTED, CUSTOM(on_interrupt), RUN_FINISHED(outcome=interrupt)` (idempotent, no re-run). Useful for reconnect.
- Run was cancelled mid-resume (checkpoint `next=('tools',)`, approved plan not yet executed): it starts a NEW invocation (`input` is a non-None dict), the pending `tools` task is dropped and deepagents' dangling-call patch inserts `ToolMessage(status="error", "Tool call submit_plan with id ... did not complete - no result was recorded. It may have been cancelled or interrupted.")`. `submit_plan` never ran (our `run.plan` DB write skipped) and the model is called again. Lossy: do not use for pause/resume.

Working fix (C3): override `get_stream_kwargs` to set `kwargs["input"] = None` when we know it is a continue. The pending `tools` task executes (submit_plan result appears, plan persisted), the run finishes, 6 clean messages, no error message, no duplicates. See `ContinueAgent` below.

## 5. messages=[] versus one new message
All on a fresh thread:
- Seeded via `aupdate_state` + `messages=[]`: works, model sees `[System, Human]`, no duplicate.
- Only the one new user message: works, same result.
- Seeded AND same message id passed: no duplicate (merge by id).
- Seeded AND different id, same text: DUPLICATE (two Human messages). Ids must be stable: use our `message.id` as the AG-UI message id.
- `messages=[]`, nothing seeded: runs with no user message (model sees only the system prompt), no error. Guard in our code.
- Second turn passing only the new message: checkpoint gets `[u1, ai, u2, ai]`; MESSAGES_SNAPSHOT has all four. From the code: re-sending an EXISTING human id (or an old id as the last message) triggers the adapter's time-travel "regenerate" path. Pass only new messages.

Recommendation: pass the single new user message with our stable message id; use `messages=[]` only with `resume` (or with the `input=None` continue).

## 6. Size
Tiny runs (Lead + 1 scout): first run STATE_SNAPSHOT 1 (~0.4 KB) + MESSAGES_SNAPSHOT 1 (~0.3 KB). Resume run: STATE_SNAPSHOT 4 (2.9 KB total, max 1.4 KB), MESSAGES_SNAPSHOT 2 (2.1 KB total). Snapshots are the whole state each time, so they grow quadratically with transcript length.

STATE_SNAPSHOT contains `messages` (duplicating MESSAGES_SNAPSHOT), `files` (deepagents state files) and sometimes `tools`. The `files` state IS included: a 50 KB `write_file` to `/notes/big.txt` gave STATE_SNAPSHOT 101 KB (file content plus the tool args in messages), MESSAGES_SNAPSHOT 50 KB. A write under `/projects/**` (our StoreBackend route) does NOT land in `files` (snapshot `files: {}`, 51 KB just from messages), so candidates/evidence blobs stay out; only non-/projects scratch writes bloat. Override `get_state_snapshot(self, state)` to return `{}` (or a whitelist): STATE_SNAPSHOT drops to 39 bytes (`{"type":"STATE_SNAPSHOT","snapshot":{}}`) and the adapter accepted it. MESSAGES_SNAPSHOT still carries all messages, so bound or skip it in the persister.

## 7. Surprises
- Duplicate tool-call events across a resume: `submit_plan-0` gets START/ARGS/END in run 1 (streamed) and START/ARGS/END/RESULT again in run 2. Consumers must key by toolCallId and tolerate a second START.
- TOOL_CALL_RESULT for `task` carries the raw SubagentResult JSON, NOT our `<untrusted source="subagent result">` wrapper (added to the ToolMessage in graph state afterwards). Display only; the Lead still sees the wrapped one.
- The subagent's structured-output call `SubagentResult` appears as an attributed TOOL_CALL with no RESULT.
- Ids: `lc_run--<uuid>` per model turn; tool results use the toolCallId as messageId. Every event except snapshots/CUSTOM carries an id-ish field (checked).
- Errors surface as `RUN_ERROR` events with a logged traceback; `run()` never raised in any scenario.
- The existing `ToolFake` (GenericFakeChatModel) yields no chunk for an empty-content tool call, and the adapter uses `astream_events` (always streaming), so tests die with "No generations found in stream". The spike's `StreamFake` streams tool-call chunks (name/id first, then arg deltas); step 3b tests need the same.
- Each resume replays `STEP_STARTED` for the interrupted node and a run emits ~26 STEP pairs (very chatty; filter from persistence if unused).
- Resume on a thread with no checkpoint just starts a fresh run.

## 8. deepagents dependency
`ag_ui_langgraph` does not import `deepagents` (grep: comments/docstrings only). It detects delegation by SHAPE (a `task` call with `description` + `subagent_type`) plus namespaces and `lc_agent_name` metadata, so it needs only a plain `CompiledStateGraph` with a checkpointer. Our adapter module can live outside `agents/factory.py` without breaking the deepagents-import rule.

## Required / recommended overrides
| Override | Need | What it does |
|---|---|---|
| `get_stream_kwargs` | mandatory | replace `kwargs["context"]` with our `RunContext` instance (dict fails on `thread_id`) |
| `get_stream_kwargs` (same hook) | for pause/continue | `kwargs["input"] = None` when continuing a pending checkpoint |
| `get_state_snapshot` | recommended | return `{}` or a whitelist so `files`/`messages` stay out of STATE_SNAPSHOT |
| `_interrupts_to_agui` | optional | set `reason`, `message`, `response_schema` |
| `_build_command_from_agui_resume` | NOT needed | default passes `{"decisions": [...]}` through verbatim |

## Final spike code

### spike.py (the helpers; the exploration scripts that drove the scenarios above are not kept)

~~~python
"""Spike 3b: ag-ui-langgraph 0.0.47 against our real Deep Agents graph (fake models, no network)."""
import asyncio, json, sys, uuid, collections, contextlib
from dataclasses import asdict
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, r"C:\Users\saad.kazmi\Desktop\Researchly\backend")  # makes `tests.agents.fakes` importable
import os; os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
from tests.agents.fakes import FakeEmbedder, KeywordReranker, StubFetcher, ToolFake, call
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from pydantic import ConfigDict


class StreamFake(ToolFake):
    """ToolFake that also streams tool calls: GenericFakeChatModel yields no chunk for an empty-content tool call,
    and astream_events (what the adapter uses) always streams, so it dies with 'No generations found in stream'."""

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        import json
        self.seen.append(list(messages))
        msg = next(self.messages)
        mid = "lc_run--" + str(uuid.uuid4())  # a real provider gives each model turn a fresh id
        words = msg.content.split(" ") if isinstance(msg.content, str) and msg.content else []
        for i, w in enumerate(words):
            yield ChatGenerationChunk(message=AIMessageChunk(content=w + (" " if i < len(words) - 1 else ""), id=mid))
        for j, tc in enumerate(msg.tool_calls):  # like a real provider: name+id first with empty args, then args deltas
            raw = json.dumps(tc["args"]); half = len(raw) // 2
            for part in (dict(name=tc["name"], args="", id=tc["id"]), dict(args=raw[:half]), dict(args=raw[half:])):
                yield ChatGenerationChunk(message=AIMessageChunk(content="", id=mid, tool_call_chunks=[
                    dict(index=j, type="tool_call_chunk", **{"name": None, "id": None, **part})]))
        yield ChatGenerationChunk(message=AIMessageChunk(content="", id=mid, chunk_position="last"))


def script(*replies):
    return StreamFake(messages=iter([AIMessage(r) if isinstance(r, str) else r for r in replies]))

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from ag_ui.core import RunAgentInput, UserMessage
from ag_ui_langgraph import LangGraphAgent
from ag_ui_langgraph.utils import agui_messages_to_langchain  # noqa

from researchly.agents.context import EFFORTS, RunContext
from researchly.agents.factory import build_agent
from researchly.agents.tools import ToolDeps


class FakeSession:
    @contextlib.asynccontextmanager
    async def begin(self): yield
    async def get_one(self, model, pk): return SimpleNamespace(plan=None)
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


async def nothing_spent(run_id): 
    from decimal import Decimal; return Decimal(0)


def make_graph(lead_script, scout_script, saver=None):
    deps = ToolDeps(sessions=lambda: FakeSession(), connectors={}, searxng=None, fetcher=StubFetcher(),
                    embedder=FakeEmbedder(), reranker=KeywordReranker("attention"), extractor=script())
    return build_agent(EFFORTS["standard"], lead=lead_script, scout=scout_script, reader=script(), deps=deps,
                       spent=nothing_spent, checkpointer=saver or InMemorySaver(), store=InMemoryStore())


def std_scripts():
    lead = script(
        call("submit_plan", {"plan": "1. search"}),
        call("task", {"description": "Find papers on attention.", "subagent_type": "scout"}),
        "The answer.",
    )
    scout = script(call("SubagentResult", {"status": "complete", "summary": "1 paper", "files": [], "gaps": []}))
    return lead, scout


class OurAgent(LangGraphAgent):
    """Minimal subclass. One instance per run (the adapter keeps per-run state on self)."""

    def __init__(self, *a, run_context=None, **kw):
        super().__init__(*a, **kw)
        self.run_context = run_context

    def get_stream_kwargs(self, *a, **kw):
        # The adapter merges config["configurable"] (incl. thread_id) into a context *dict*; langgraph then does
        # RunContext(**dict) and fails on thread_id. Hand it our real RunContext instance instead.
        kwargs = super().get_stream_kwargs(*a, **kw)
        kwargs["context"] = self.run_context
        return kwargs


def make_agent(graph, cls=None, ctx=None):
    cls = cls or OurAgent
    return cls(name="researchly", graph=graph, emit_raw_events=False, emit_interrupt_outcome=True,
               subagent_visibility="attributed", run_context=ctx)


def inp(thread, messages=(), resume=None, state=None):
    return RunAgentInput(thread_id=thread, run_id=str(uuid.uuid4()), state=state or {}, messages=list(messages),
                         tools=[], context=[], forwarded_props={}, resume=resume)


async def collect(agent, i):
    out = []
    try:
        async for e in agent.run(i):
            out.append(e)
    except Exception as ex:  # run() should not raise
        out.append(ex)
    return out


def seq(events): return [getattr(e.type, "value", str(e.type)) if hasattr(e, "type") else repr(e) for e in events]


def compress(types):
    out = []
    for t in types:
        if out and out[-1][0] == t: out[-1][1] += 1
        else: out.append([t, 1])
    return " ".join(f"{t}x{n}" if n > 1 else t for t, n in out)


def js(e): return e.model_dump_json(by_alias=True, exclude_none=True)


def summary(events, label):
    print(f"\n=== {label}: {len(events)} events")
    print(compress(seq(events)))
    cnt = collections.Counter(seq(events)); print(dict(cnt))
    for t in ("STATE_SNAPSHOT", "MESSAGES_SNAPSHOT"):
        sizes = [len(js(e)) for e in events if seq([e])[0] == t]
        print(t, "count", len(sizes), "total bytes", sum(sizes), "max", max(sizes, default=0))
    noid = [seq([e])[0] for e in events if hasattr(e, "type") and "SNAPSHOT" not in seq([e])[0]
            and not any(getattr(e, k, None) for k in ("message_id", "tool_call_id", "run_id", "step_name", "subagent_run_id", "name"))]
    print("events without any id-ish field:", noid[:5])
~~~

### The continue and lean-snapshot overrides (from the exploration scripts)

~~~python
class ContinueAgent(OurAgent):
    def get_stream_kwargs(self, *a, **kw):
        kw2 = super().get_stream_kwargs(*a, **kw)
        if getattr(self, "continue_pending", False):
            kw2["input"] = None  # resume pending tasks from the checkpoint instead of starting a new invocation
        return kw2


class Lean(ContinueAgent):
    def get_state_snapshot(self, state): return {}  # MESSAGES_SNAPSHOT carries the messages

    def _interrupts_to_agui(self, lgs):
        out = super()._interrupts_to_agui(lgs)
        for lg, i in zip(lgs, out):
            v = lg.value
            if isinstance(v, dict) and v.get("action_requests"):
                i.reason = "researchly:" + "+".join(r["name"] for r in v["action_requests"])
                i.message = str(v["action_requests"][0]["args"])
                i.response_schema = {"allowed_decisions": [c["allowed_decisions"] for c in v["review_configs"]]}
        return out
~~~
