# ADR 0011: Agent runtime: pins, the selector loop, LangGraph's tables, the Reader and the guards

- Status: accepted
- Date: 2026-11-08
- Spec sections: §6.1–6.6, §10, §16, §19.1

## Context
Step 3a adds the agent runtime: the Deep Agents factory, the Lead, scouts and readers, ranking, budgets and limits,
and a run manager with resume. Research is in `docs/research/` (step 3: agent runtime, streaming UI, reader and
ranking). The decisions below are the ones that constrain later steps.

## Decision
- **Pins:** `deepagents==0.7.32`, `langchain==1.4.7`, `langchain-core==1.7.3`, `langgraph==1.2.19`,
  `langgraph-checkpoint-postgres==3.2.0`. deepagents 0.7.32 needs `langchain-core` ≥ 1.7, so the 1.6.12 pin moved.
  `backend/tests/test_lock.py` asserts them in both locks. `factory.py` imports `deepagents._models.get_model_provider`,
  a private helper: re-check it on every deepagents bump.
- **Selector loop:** psycopg's async driver cannot run on Windows' Proactor loop. uvicorn runs with
  `--loop asyncio:SelectorEventLoop` (native and Docker), and the app refuses a Proactor loop at startup unless
  `env == "test"`.
- **LangGraph's tables** (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`, `store`,
  `store_migrations`) come from the libraries' idempotent `setup()`, called by `provision.setup_agent_tables` after
  Alembic. Alembic never manages them, and `test_models_match_migrations` skips them. The app shares one psycopg pool
  (autocommit, `prepare_threshold=0`, `dict_row`) between `AsyncPostgresSaver` and `AsyncPostgresStore`.
- **Reader:** PaperQA2's retrieve–summarise step (RCS), reimplemented in `services/evidence.py` with no paper-qa
  dependency. Hybrid retrieval inside the paper, a rerank gate (`tau_chunk`), then one quarantined, tool-less,
  structured-output call per chunk at role `mid` (relevance 0–10, a verbatim quote, a claim). A quote must match the
  source after normalisation, or it is retried once and then dropped. The stored quote is sliced from the source, never
  the model's string. Cards go to the `evidence` table and to `/projects/evidence/{paper_id}.json`. Without chunks the
  abstract is the source. `evidence.gather` releases its DB connection before the parallel model calls, and
  `gather_evidence` takes a per-paper advisory lock before ingesting, so two readers never ingest one paper twice.
  Tool-cache expiry uses the database clock.
- **Guards** are LangChain middleware that keep no state of their own: they read the ledger or count earlier tool calls
  in `state["messages"]`, so a resumed run is guarded as if it never stopped.
  - BudgetGuard: a wrap-up note to the Lead at 80% of the budget; at 100%, `jump_to: end` with a stop message, and the
    run ends `partial`. There is no `BudgetExhausted` exception.
  - LoopGuard: the 3rd identical call, or a same-tool query with word Jaccard ≥ 0.8 to an earlier one, gets a tool
    error; after 2 such errors the agent ends.
  - EffortGuard: scout and reader task caps per tier, and `asyncio.wait_for` per task (scout 300 s, reader 480 s).
  - Model and tool call limits per subagent invocation from the tier (`ModelCallLimitMiddleware`,
    `ToolCallLimitMiddleware(exit_behavior="end")`). Quick runs use `exit_behavior="continue"` instead, so the Lead still
    answers past the cap; the run then ends `partial`.
- **Project files:** tools write full candidate records and evidence cards to the project store; a
  `FilesystemPermission` deny rule keeps every agent's file tools off both folders, so paper text reaches agents only
  through tool results inside `<untrusted>` tags.
- **Run manager:** in-process asyncio tasks; status changes are conditional updates; `running` runs resume at startup;
  a branch shares its parent's checkpoint thread and starts from a chosen checkpoint (a branch resumed before its first
  checkpoint restarts from its fork), and a run whose thread has a newer run refuses to resume.

## Consequences
- Every deepagents, langchain or langgraph bump re-runs the factory and middleware tests and needs an ADR amendment.
- The extractor at `mid` costs more per paper than `cheap`; quote fidelity is worth it (step 4 measures it).
- Tools must stay idempotent: a paused tool step runs again on resume.
- Watch: checkpoint growth (pruning is deferred), and whether `get_model_provider` still turns the general-purpose
  subagent off after a bump (`test_factory.py` checks it). LoopGuard and EffortGuard count from `state["messages"]`,
  so if Deep Agents' summarisation drops old messages their counts restart.

## Evidence
- Probes against the pinned versions (step 3a planning): middleware jump-to-end, wrap-up injection, loop and effort
  guards, HITL respond and edit, subagent inheritance of permissions, StoreBackend writes from tools, cancel-and-resume
  re-running the tool step, and branching from a checkpoint.
- PaperQA2's RCS design and LitSearch-seeded rerank gates: the step 3 research notes in `docs/research/`.
