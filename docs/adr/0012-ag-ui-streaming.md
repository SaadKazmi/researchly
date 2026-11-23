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
  - `get_state_snapshot` keeps only `todos`. That is spec §19.1, not a payload-size trim: it drops `messages` and
    the state-backed files, which carry quarantined web and PDF text, and a `STATE_SNAPSHOT` holding either would
    put that text straight on the wire. (`/projects/**` is routed to the store, not state, so it is never in a
    snapshot to begin with.)
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
- **Frontend (3c):** the browser speaks the same pinned protocol.
  - Pins, exact: `@assistant-ui/react@0.15.32`, `@assistant-ui/react-ag-ui@0.1.1`, `@ag-ui/client@1.0.4`
    (`@ag-ui/core@1.0.4` ships `PROTOCOL_VERSION = "1.0"`, which is the backend's `ag-ui-protocol==1.0.0`).
    `frontend/tests/pins.test.ts` asserts them; an upgrade is an ADR plus the chat tests, as on the Python side.
  - One `HttpAgent` per thread, with an injected `fetch`: a new user message creates the run
    (`POST /threads/{id}/runs`), anything else attaches, and every request opens
    `POST /runs/{id}/stream?after=<lastSeq>`. Every attach comes from a freshly mounted runtime, so it
    replays from `after=0`: re-attaching (after a resume, an answered review or a branch) is a remount. A
    stream that drops mid-run is not reconnected yet (deferred to 3d); the run's answer appears on reload.
  - The run tree, plan panel and budget come from our own reducer over `agent.subscribe({ onEvent })`.
    assistant-ui's nested subagent messages are not used: they are lost on reload and their interrupts
    cannot be resumed.
  - Interrupts are answered with `POST /runs/{id}/resume`, because the stream endpoint ignores its body.
    `useAgUiInterrupts` and `useAgUiSubmitInterruptResponses` are therefore unused.

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
- The browser never sends `RunAgentInput.resume`; a decision travels as REST `decisions` and the agent re-attaches.
- `@ag-ui/client` 1.0.4 is the first version whose zod is a peer dependency, so the SPA keeps one zod major.

## Evidence
- Spike §3: the context dict fails on `thread_id`. Spike §4: without `input=None`, a continue drops the pending tool
  task. Spike §6: snapshot sizes. Spike §7: `ToolFake` cannot stream.
- Plan 3b probes:
  - A branch through the adapter's config runs from the fork, and the closing snapshot shows the new head.
  - A failing tool yields `RUN_ERROR` with the exception text as `message`.
  - `adispatch_custom_event` from middleware reaches the stream as `CUSTOM`.
