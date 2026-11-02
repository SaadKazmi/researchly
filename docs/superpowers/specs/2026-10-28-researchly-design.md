# Researchly — Product & Architecture Design Spec

- **Date:** 2026-10-28
- **Status:** Approved design, pending written-spec review
- **Repo:** https://github.com/SaadKazmi/researchly (private until first release)
- **Owner:** Saad Kazmi

---

## 1. Summary

Researchly is an open-source, local-first, multi-agent research assistant. A user opens a chat about a topic or idea. Agents then:

- search open scholarly sources and the web;
- rank the best papers, explaining each score;
- read the papers into verified evidence;
- build citation and concept graphs the user can traverse;
- summarize, explain and visualize concepts;
- write research notebooks and full research books with checked citations and figures.

It has long-term memory. It runs on the user's own machine. **The only thing that costs the user money is their own LLM API key.** Every other component is free, keyless, needs no signup, and is installed automatically.

### 1.1 Goals
- One complete product, shipped as a single release. There is a build order, but no "v1 / v2".
- Grounded outputs: every factual sentence in generated content links to a stored evidence passage, and every reference resolves to a real paper.
- Local-first and private: one user, data on their machine, keys in the OS keychain.
- Works with any major LLM provider, including fully offline through Ollama.
- Zero cost beyond the LLM key: no paid services, no required API keys or accounts for any data source, no licensed runtimes (e.g., Docker Desktop is never required).
- Installs with one command, without admin rights, and sets up everything end to end. It always picks the best-available option for the user: Docker mode if a container engine exists (started automatically), native otherwise. The strongest tools (SearXNG, GROBID, BM25) are preferred in every mode where they can run.
- Tested end to end: by deterministic tests, by a simulated-user agent, by red-team tools and by benchmark evals.

### 1.2 Non-goals
- Multi-user accounts, teams, roles, or hosting for others.
- Redistributing paper full text or Semantic Scholar data.
- Scraping Google Scholar or getting around any source's rate limits or terms.
- A mobile app.

---

## 2. Decision log

| # | Decision | Rationale |
|---|---|---|
| D1 | Single user, local-first | User decision |
| D2 | One-line install script (no admin) → uv → `researchly` CLI → `researchly setup`. **Best-available runtime:** if a container engine is installed (Docker Engine/Desktop, Podman, Rancher Desktop, Colima, OrbStack), use **Docker mode** and start the engine if it is stopped. Otherwise use the **native** runtime. Never *require* or *install* a container engine. Both modes are tested | User rule: best outcome first (full SearXNG, GROBID, BM25, container sandbox). Researchly cannot install an engine for the user: Docker Desktop is paid for companies with ≥250 employees or ≥$10M revenue, and free engines need admin on Windows/macOS |
| D3 | Distribution: GitHub (SaadKazmi/researchly), GitHub Releases + GHCR images; install one-liners in README | User decision |
| D4 | Prefer existing, best-in-class frameworks; trust reported benchmarks | User rule |
| D5 | Python backend, TypeScript/React frontend | Scholarly/ML ecosystem is Python; best UI libraries are React |
| D6 | Orchestration: LangChain **Deep Agents** on **LangGraph**, pinned exact version behind a thin factory | Durable checkpoints, HITL, subagents, skills, virtual filesystem, Postgres persistence |
| D7 | Single **PostgreSQL 18** for relational, vector (pgvector), BM25, graph tables, jobs, cache, checkpoints | Fewest moving parts; strong enough at target scale |
| D8 | Memory: winner of a **Mem0 vs Hindsight** bake-off, run in build step 6 | Both on Postgres. Mem0's top scores are platform-reported; Hindsight reports OSS scores |
| D9 | **LiteLLM** (Python SDK, in-process; no proxy server) is the multi-provider layer, with hardened pinning (§19.7) | User decision. Broadest provider coverage plus built-in cost tracking. Mitigates the 2026 PyPI compromises (1.82.7/8, 1.107.0) by pinning |
| D10 | Dev-only **ai-provider** (Claude Code via Agent SDK) stands in for a user's API key. All LLM traffic recordable and replayable | User decision. CI needs no keys |
| D11 | **user-simulator** agent drives the real UI via Playwright with personas and edge cases | User decision |
| D12 | Opus wherever Fable would be used | User has no Fable access |
| D13 | Agent topology: hub-and-spoke. Parallel reading, single sequential writer. Verifier separate from writer. No agent debate | MAST, Google scaling study, Anthropic, Cognition, Open Deep Research findings |
| D14 | **Zero-cost rule:** only the LLM key may cost money. All scholarly sources work keyless by default; optional free keys only raise limits | User requirement |
| D15 | Keyless data plan (§8): OpenAlex **singleton** lookups ($0) are the enrichment hub; search fans out to arXiv, Crossref, Europe PMC/PubMed, HF Papers and capped OpenAlex search; citations come from OpenCitations + OpenAlex `referenced_works` + Crossref. Semantic Scholar is optional-key only; Unpaywall is dropped | Live-checked 2026-10-28: S2 keyless returned 429 on 7 of 8 calls; Unpaywall requires an email; OpenAlex singletons report `Cost-USD: 0` |

---

## 3. Features (product requirements)

Each feature has an ID. `docs/features.json` mirrors these IDs, and each carries E2E steps and `passes: false` until it is verified.

### F1 Chat & research runs
- **F1.1** Projects, each with multiple chat threads. Chat history persists and is searchable.
- **F1.2** Clarification: before a Standard or Deep run, the Lead asks at most 3 clarifying questions, which the user may skip.
- **F1.3** Plan review: the Lead shows an editable research plan (sub-questions, sources, effort, estimated cost). The user can approve, edit or cancel. This is skipped for Quick.
- **F1.4** Effort levels:
  - **Quick**: 0 subagents, at most 10 tool calls.
  - **Standard**: 2–4 scouts.
  - **Deep**: up to 8 scouts plus readers.
  - Enforced in code (§6.4).
- **F1.5** Live run view: a tree of agents → tool calls showing status, elapsed time, tokens and cost. It streams over AG-UI.
- **F1.6** Run control: pause, resume, cancel, and branch-and-rerun from any checkpoint. Runs survive an app restart.
- **F1.7** Budgets:
  - A pre-run cost estimate, shown as a range. If it is above the user's threshold, the user must confirm.
  - A hard per-run budget. At 80% the Lead must wrap up; at 100% the run stops gracefully and keeps partial results.
- **F1.8** Answers carry inline `[n]` citation chips. Hovering shows the quote; clicking opens the reader at the passage.

### F2 Discovery
- **F2.1** Multi-source search across the keyless §8 connectors plus web search through **SearXNG** (local, free, the default in every mode where it can run). Optional extras: Tavily, Exa or Brave with the user's own key. `ddgs` is an explicit opt-in only, because it scrapes and carries ToS risk.
- **F2.2** Identity resolution and dedupe across sources (DOI, arXiv ID, OpenAlex ID, S2 ID, then a fuzzy title+year+first-author match).
- **F2.3** Explainable ranking (§10): a per-paper score breakdown; presets **Balanced**, **Seminal** and **Recent**.
- **F2.4** Coverage indicator: an estimate of search completeness from the discovery-rate curve, with a "search more" action.
- **F2.5** Saved searches and alerts: scheduled re-runs; new matching papers show up in an inbox.

### F3 Library
- **F3.1** Collections, tags, notes and highlights per project.
- **F3.2** Full-text acquisition is legal open access only (OpenAlex `best_oa_location`, arXiv, Europe PMC, CORE). The license is stored per paper.
- **F3.3** Parsing pipeline (§9.1) into structured sections, figures, tables, equations and references.
- **F3.4** PDF reader (pdf.js) with passage highlighting that jumps from any citation.
- **F3.5** Import BibTeX, RIS and CSL-JSON; Zotero read through its local API, write through its web API. Export BibTeX and CSL-JSON.

### F4 Understanding
- **F4.1** Multi-level summaries: TL;DR, key contributions, abstract-level, per section, and a plain-language ↔ expert slider. Every sentence is grounding-checked.
- **F4.2** Explanations of equations, figures (vision model given the figure, its caption and the referencing paragraphs) and tables.
- **F4.3** Grounded Q&A over the library and fresh search, with the verification pipeline (§12.2).
- **F4.4** Paper comparison: a side-by-side of method, data, results and limitations.
- **F4.5** Extraction tables: user-defined columns; every cell holds a value, a quote and a source passage. Export as CSV.
- **F4.6** Contradiction finder, using PaperQA2's contradiction detection.
- **F4.7** Gaps and trends:
  - Topic trend charts.
  - Gap detection: pairs of concepts that are each well studied but rarely appear together, plus thinly covered sub-questions.
- **F4.8** Research ideas: generated as **hypotheses**, each with an automatic novelty check (§12.4) and an explicit label saying it is untested.

### F5 Graphs & visuals
- **F5.1** Citation graph:
  - Prior and derivative works.
  - Similarity from co-citation and bibliographic coupling (Connected Papers style).
- **F5.2** Concept graph: SciERC-style entities (Task, Method, Dataset, Metric) and relations, linked across papers.
- **F5.3** Timeline view of papers and concepts.
- **F5.4** Traversal:
  - Expand on click, filters (year, venue, citations, OA, cluster), Louvain clusters, search inside the graph.
  - Pin papers to a collection.
- **F5.5** Field view: a GPU-rendered (cosmos.gl) overview of more than 100k nodes.
- **F5.6** Mind maps (reusing the graph engine) and small editable diagrams (React Flow).
- **F5.7** In-answer visuals:
  - Vega-Lite charts, Mermaid diagrams, KaTeX maths, Excalidraw sketches.
  - All are declarative specs, schema-validated before rendering.

### F6 Notebooks & books
- **F6.1** Research notebook: a block document with text, citations, figures and **executable code cells** run in the sandboxed kernel (§19.4). Pyodide is used for light client-side cells.
- **F6.2** Research book generator:
  - Pipeline: evidence-first → perspective-guided outline → section-by-section writing → verification → critique (§12).
  - The user can edit the references, outline and text at each stage.
- **F6.3** Block editor (Plate): AI block-level rewrite, expand, condense and "cite this", plus comments.
- **F6.4** Exports:
  - PDF (Typst), DOCX and LaTeX (Pandoc + citeproc), `.ipynb` (nbformat), a MyST / Jupyter Book 2 static site, Markdown and BibTeX.
  - Any CSL citation style.
- **F6.5** Full-text redistribution guard: exports never embed paper full text beyond quoted passages unless the paper's license is CC BY or CC0.

### F7 Studio outputs
- **F7.1** Audio overview: a two-voice script, synthesized with local TTS (default Kokoro-82M, Apache-2.0) or the user's provider TTS.
- **F7.2** Study guide, FAQ, flashcards (CSV/Anki export) and a slide deck (exported as Reveal.js HTML and PPTX via Pandoc).
- **F7.3** Systematic review workflow:
  - Protocol, then screening (title/abstract, then full text), then extraction table.
  - Ends with a PRISMA 2020 flow diagram.
  - Recall is reported honestly, with the user making the final screening decisions.

### F8 Memory
- **F8.1** Memory of user profile and preference facts, scoped per user and per project (§15).
- **F8.2** Memory panel: view, search, edit, pin, delete, per-project forget, export. Every memory shows its source turn.
- **F8.3** Procedural preferences (citation style, preferred and excluded venues, reading level) are always applied.
- **F8.4** Per-project playbook: lessons appended after each run as small additions, ACE-style (§15.3).
- **F8.5** The app notifies the user when a memory is written. Memories that change behaviour need confirmation.

### F9 Settings
- **F9.1** LLM providers: Anthropic, OpenAI, Google Gemini, OpenRouter, Ollama, any OpenAI-compatible endpoint. Dev builds also offer the `claude-code` provider.
- **F9.2** Model routing per role (§7.3), with good defaults per provider.
- **F9.3** Sources work with no configuration. Optional free keys (OpenAlex, Semantic Scholar, NCBI, OpenAIRE, CORE) and an optional personal `mailto` (Crossref polite pool) only raise limits. Each shows what it unlocks.
- **F9.4** Budgets and confirmation thresholds.
- **F9.5** Data directory, backup schedule, theme and language.

### F10 Platform
- **F10.1** CLI: `researchly setup | onboard | start | stop | status | config | update | logs | backup | restore | migrate-runtime | db shell | bundle export|import | doctor | uninstall`.
- **F10.2** Usage dashboard: tokens and cost by run, agent, model and day; cache-hit rate.
- **F10.3** Offline mode: Ollama for the LLM and embeddings; the cache serves previously fetched papers.
- **F10.4** MCP server: exposes library search, paper lookup and evidence search to external MCP clients (stdio + Streamable HTTP, current spec 2026-07-28).
- **F10.5** MCP client: the user can attach external MCP tool servers to the agents, with a per-tool permission prompt.
- **F10.6** Accessibility: keyboard navigation everywhere, WCAG 2.2 AA contrast, screen-reader labels, and a graph accessible as a table view.

---

## 4. System architecture

```
 Browser (React SPA) ── REST + AG-UI (SSE) ──┐        researchly CLI (Python, uv tool)
                                             ▼                │ manages runtime ↓
┌──────────────────────── app (Python 3.12, FastAPI, uvicorn) ───────────────────────────┐
│ api/        REST routers, AG-UI endpoint, MCP server, localhost guard (§4.3)           │
│ runs/       run manager, budgets, procrastinate jobs, resume-on-start                  │
│ agents/     Deep Agents factory, roster, prompts, skills (SKILL.md), middleware         │
│ services/   ranking · dedupe · graph · ingest/parse · embed · search · export · studio │
│ connectors/ OpenAlex · arXiv · Crossref · OpenCitations · DataCite · EuropePMC · HF … │
│ llm/        provider registry · capability table · routing · RecordReplay · ledger     │
│ memory/     memory adapter (Mem0 | Hindsight) · playbook                              │
│ security/   safe fetcher (SSRF) · spotlighting · sanitizer · keychain · permissions    │
│ db/         SQLAlchemy 2 models · Alembic migrations                                   │
└──────────────┬──────────────┬───────────────┬──────────────────┬───────────────────────┘
               │              │               │                  │
        Postgres 18      SearXNG        Kernel sandbox      GROBID (optional)
        pgvector,        (web search)   (Jupyter, no net)   MinerU (optional GPU)
        pg_textsearch
```

### 4.1 Runtime modes
**Runtime selection (best outcome first).** Runs at `setup` and on every `start`:
1. **Detect a container engine.** Docker Engine or Desktop, Podman (`podman compose` / Docker-compatible socket), Rancher Desktop, Colima or OrbStack, through CLI presence and the socket/context.
2. **If one is found but stopped, start it for the user**, then wait for readiness (with progress output and a timeout):

   | Engine | Start command |
   |---|---|
   | Docker Desktop on Windows | Launch `Docker Desktop.exe` |
   | Docker Desktop on macOS | `open -a Docker` |
   | Docker Desktop on Linux | `systemctl --user start docker-desktop` |
   | Rootless Docker Engine | `systemctl --user start docker` |
   | Podman | `podman machine start` |
   | Colima | `colima start` |
   | Rancher Desktop | `rdctl start` |
   | OrbStack | `orb start` |

   If starting needs elevation (e.g., the rootful `docker.service`), the CLI explains the single command to run and offers native mode for now.
3. **Engine ready → Docker mode.** This is the full-capability path.
4. **No engine, or it fails to start → native mode**, chosen automatically. No container engine is ever installed for the user, because of admin rights and licensing. A first-run note explains that installing one unlocks extra capability, and `doctor` lists exactly what is degraded.
5. The user can pin a mode with `researchly config set runtime docker|native|auto` (default `auto`).

When Docker Desktop is the engine, a one-time informational note mentions its commercial licensing terms. It never blocks.

| Component | Docker mode (preferred when an engine exists) | Native mode (fallback; no admin) |
|---|---|---|
| Postgres | `ghcr.io/saadkazmi/researchly-postgres` (from `pgvector/pgvector:pg18` + pg_trgm + pg_textsearch) | **`pg0-embedded`** wheel: PG 18 + pgvector 0.8.5, MIT, wheels for win-x64, macOS arm64 (14+) / x64 (15+), Linux x64/arm64 (glibc ≥2.35). Fallback: conda-forge `postgresql` 18 + `pgvector` via pixi (old macOS/Linux, or if pg0 lacks `pg_trgm`). Decided by the step-1 spike |
| Keyword search | pg_textsearch BM25 | Built-in `tsvector` + `ts_rank_cd` |
| App + workers | Container | uv-managed Python 3.12 (python-build-standalone) |
| Web search | **SearXNG** container (always on) | **SearXNG** native process, installed from source into an app-private venv and served by granian. Linux/macOS are supported; Windows is made to work in the step-1 spike. If it truly cannot run, web search is off and `doctor` says why |
| PDF parsing | Docling + **GROBID** container (references, header) | Docling (`docling-slim`, ONNX-runtime models extra; no torch/CUDA). **GROBID** native via `jdk4py` (JDK 21+ wheel) on Linux/macOS. Windows native: LLM reference-string parsing + Crossref/OpenAlex matching |
| Code cells | Pyodide in Deno for light cells; full kernel in a locked-down container (§19.4) | Pyodide in Deno (default); full CPython kernel opt-in inside the OS sandbox (§19.4) |
| Exports | In the container | `pypandoc_binary`, `typst` wheel, `nodejs-wheel` + `mystmd` (app-private npm prefix) |
| Headless browser | In the container | `playwright install chromium` (user cache). On Linux with missing system libs, `doctor` prints the one command to fix it, and fetching degrades to httpx + trafilatura |
| Models | Shared model cache mounted into containers | Embeddings, reranker, grounding checker and Kokoro TTS downloaded via `huggingface_hub` on first use (resumable, no account, Apache/MIT models only) |
| Frontend | Static build served by the app | Same |

**Capability detection:** at startup the app records a `capabilities` row (`bm25_engine`, `sandbox_level`, `web_search`, `grobid`, `browser`, `gpu`, `ollama`). Services read it. The UI surfaces any degraded capabilities in Settings → System, and `researchly doctor` prints them along with fixes.

**Known platform gaps** (documented; `doctor` explains them):
- Windows on ARM has no pg0/jdk4py wheels, so it runs x64 emulation or the conda-forge fallback.
- macOS older than 14 (arm64) or 15 (x64), and Linux with glibc older than 2.35, use the conda-forge fallback.

### 4.2 Process model
- A single app process runs the API plus async workers. Research runs execute as asyncio tasks that report progress through the DB and stream AG-UI events.
- Scheduled and background jobs (alerts, ingestion batches, embedding backfills, exports) run on **procrastinate** (Postgres-backed).
- On startup, any run in state `running` is resumed from its LangGraph checkpoint (`thread_id`).
- Live progress fan-out uses Postgres `LISTEN/NOTIFY`.
- **Native supervision:** the `researchly` CLI is the supervisor. It spawns Postgres, SearXNG, the optional GROBID and the app as child processes: inside a Job Object with KILL_ON_JOB_CLOSE on Windows, and a process group with SIGTERM then SIGKILL on POSIX.
  - Ports are free `127.0.0.1` ports recorded in `state.json`.
  - Health checks use `/health` and `pg_isready`, with restart-with-backoff.
  - Logs are rotated in the platform data directory (`platformdirs`).
  - Start at login is opt-in and needs no admin: `systemd --user`, `~/Library/LaunchAgents`, or the HKCU Run key.

### 4.3 Localhost guard
The app binds to `127.0.0.1` by default. Cross-origin requests are rejected. A random per-install token is issued at `onboard`, stored in the OS keychain, and required in a header. This protects against other local sites calling the API (CSRF/DNS-rebinding). The `Host` header must be `localhost` or `127.0.0.1`.

---

## 5. Repository layout

```
researchly/
  AGENTS.md  CLAUDE.md  README.md  LICENSE (Apache-2.0)  install.sh  install.ps1
  backend/        pyproject.toml, uv.lock, src/researchly/{api,runs,agents,services,connectors,llm,memory,security,db}, tests/
  frontend/       package.json, pnpm-lock.yaml, src/{routes,components,features/{chat,runs,library,reader,graph,editor,studio,memory,settings}}, tests/
  cli/            src/researchly_cli/ (install, onboard, runtime docker|native, doctor, backup)
  e2e/            playwright.config.ts, specs/, journeys/, personas/, fixtures/{llm,http}, user-sim/
  evals/          Inspect AI tasks, judge rubrics, calibration sets
  infra/          compose.yaml, Dockerfiles, native/ (postgres bundles, searxng settings)
  docs/           superpowers/{specs,plans}/, adr/, progress.md, features.json
  .claude/        agents/, skills/, hooks/, rules/, settings.json
  .github/        workflows/ (ci.yml, nightly.yml, release.yml)
```

License: **Apache-2.0**. All runtime dependencies must be OSI-licensed and compatible. AGPL/GPL tools (Pandoc) are invoked only as separate CLI processes, never linked.

---

## 6. Agent system

### 6.1 Topology rules
- **R1 Hub-and-spoke.** The Lead is the only agent that talks to the user. There is no peer-to-peer agent chat.
- **R2 Parallelize reading, never writing.** Scouts and Readers fan out; the Writer is one agent writing sequentially.
- **R3 Effort scaling** is defined in the Lead prompt and enforced by middleware (§6.4).
- **R4 Files, not prose.** Subagents write artifacts to the project filesystem and return a `SubagentResult` (below). The Writer reads evidence files directly.
- **R5 Delegation contract.** Every `task` call includes `objective`, `output_schema`, `allowed_tools`, `sources`, `stop_condition` and `budget`.
- **R6 Layered verification**, separate from the Writer: deterministic checks, then a grounding model, then a rubric critic.
- **R7 No debate.** For high-stakes claims use self-consistency (n=3) or a critic on a different provider.
- **R8 Skill over agent.** New procedures are SKILL.md files loaded by existing agents.
- **R9 Deterministic work is code.** Ranking, dedupe, graph construction and identity resolution are tools.
- **R10 Playbook learning** is append-only (ACE).

```python
class SubagentResult(BaseModel):
    status: Literal["complete", "partial", "failed"]
    summary: str            # <= 1500 tokens
    files: list[str]        # paths under /projects/{pid}/
    gaps: list[str]         # unanswered sub-questions / missing evidence
    metrics: dict[str, float]  # tokens, cost, calls, duration
```

### 6.2 Roster
| Agent | Kind | Model role | Tools / skills | Output |
|---|---|---|---|---|
| **Lead** | Deep agent (main) | `strong` | `task`, `write_todos`, filesystem, `memory_recall`, `ask_user` (HITL), `rank_candidates`, `build_graph`, `search_library` | `brief.md`, `plan.md`, final answer |
| **Scout** ×1–8 | Subagent (isolated, parallel) | `cheap` | connector tools, `web_search`, `write_file` | `candidates/{subq}.jsonl` |
| **Reader** ×N | Subagent (isolated, parallel) | `mid` | `fetch_fulltext`, PaperQA2 `gather_evidence`, `read_file`, `write_file` | `evidence/{paper_id}.json` (evidence cards) |
| **Quarantined Extractor** | Tool-less LLM call | `cheap` | none; structured output only | typed extraction from untrusted text (§19.1) |
| **Writer** | Subagent (single) | `strong` | filesystem; skills: `report`, `notebook`, `book-chapter`, `viz-vegalite`, `viz-mermaid`, `study-guide`, `slides`, `audio-script` | `drafts/*.md`, notebook/book blocks |
| **Verifier** | Subagent | `mid` + code | `resolve_reference`, `match_quote`, `check_grounding` | `verification/{section}.json` |
| **Critic** | Subagent | `critic` (different provider if configured) | rubric scoring | `critique/{section}.json`; max 2 revision rounds |
| **Memory curator** | Post-run hook | `cheap` | memory adapter `add`, playbook append | memory items, playbook deltas |
| Ranker / Graph builder / Deduper | Tools (code) | `cheap` only for concept extraction | — | DB rows, `graph.json` |

### 6.3 Run lifecycle
1. **Intake.** The Lead loads the core memory blocks and playbook, classifies effort, and asks clarifying questions (F1.2).
2. **Plan.** The Lead writes `brief.md` and `plan.md` (sub-questions, sources, budget). This is a HITL interrupt for plan approval (F1.3).
3. **Scout.** Scouts run in parallel, one per sub-question, starting broad and then narrowing. Results are deduped and identity-resolved.
4. **Rank.** The `rank_candidates` tool scores and selects the top-K per sub-question.
5. **Read.** Readers run in parallel per paper batch. They acquire full text (or the abstract only), run PaperQA2 evidence gathering, and write evidence cards incrementally.
6. **Graph.** `build_graph` updates the citation and concept tables and writes `graph.json` for the run.
7. **Synthesize.** For Quick/Standard answers, the Lead or Writer composes from the evidence cards. For notebooks and books, the Writer runs the §12 pipeline.
8. **Verify → Critique → Revise.** At most 2 rounds.
9. **Deliver.** The answer streams with citation chips and the run summary (cost, coverage, gaps).
10. **Learn.** The memory curator writes memories and playbook deltas.

Every step is a LangGraph checkpoint boundary. Nodes with side effects (DB writes, file writes, external fetches) are idempotent and keyed by `sha256(step, inputs)`.

### 6.4 Budgets, limits and termination
- **Effort tiers:**

  | Tier | Max scouts | Max readers | Max model calls / subagent | Max tool calls / subagent | Default budget |
  |---|---|---|---|---|---|
  | Quick | 0 | 0 | — | 10 (Lead) | $0.10 |
  | Standard | 4 | 4 | 15 | 25 | $1.00 |
  | Deep | 8 | 12 | 25 | 40 | $5.00 |

- **Enforcement:**
  - `ModelCallLimitMiddleware`, `ToolCallLimitMiddleware(exit_behavior="end")`, and per-subagent `asyncio.wait_for` timeouts (scout 5 min, reader 8 min, writer 20 min per section).
  - The budget guard reads the ledger before each model call. At 80% it injects a wrap-up instruction to the Lead; at 100% it raises `BudgetExhausted`, which ends the run with status `partial`.
- **Loop detection:** the same `(tool, normalized_args)` hash 3 times, or near-duplicate queries (Jaccard ≥ 0.8), produces a tool error telling the agent to change strategy. After 2 such errors the subagent ends as `partial`.
- **Stop rule for the Lead:** each sub-question has ≥ k evidence cards (k=3 Standard, 5 Deep), or the budget is reached, or the coverage estimate has plateaued.

### 6.5 Reliability
- **Tool cache:** `tool_cache(key=sha256(tool, args), result, created_at, ttl)` gives free retries and deterministic replay.
- **Retries:**
  - `ToolRetryMiddleware` with exponential backoff and jitter. 429/503 count as retryable; for S2, 500 does too.
  - `ModelRetry` + `ModelFallback` across the user's configured providers for the same role.
- **Structured output:** Pydantic `response_format`. On a validation failure, retry once with the error text, then degrade to `status="partial"`.
- **Salvage:** evidence cards are written per paper, so partial runs remain useful.
- **Durability:** `durability="sync"` for book runs; `"async"` otherwise.

### 6.6 Deep Agents configuration
- `deepagents` is pinned to an exact version (≥0.7.32), and `langgraph` and `langchain` likewise. Upgrades go through an ADR plus the full test suite.
- Agents are created only through `agents/factory.py`, which is the only module that imports `deepagents`.
- **Backend:** `CompositeBackend(default=StateBackend(), routes={"/projects/": StoreBackend(namespace=lambda rt: ("project", rt.project_id))})` on `AsyncPostgresStore`.
- **Checkpointer:** `AsyncPostgresSaver`.
- TodoList middleware is enabled explicitly (it is opt-in in 0.7).
- Only synchronous isolated subagents are used. The beta async and fork modes are not.
- **Skills:** `backend/src/researchly/agents/skills/<name>/SKILL.md`, attached per subagent through `skills=`. Skill descriptions are kept clearly distinct from each other.

---

## 7. LLM layer

### 7.1 Providers
- Implemented with the **LiteLLM Python SDK**, wrapped as a LangChain chat model (`langchain-litellm` `ChatLiteLLM`) so Deep Agents and LangGraph consume it unchanged. This covers Anthropic, OpenAI, Gemini, OpenRouter, Ollama and any OpenAI-compatible endpoint. The LiteLLM proxy server is not used.
- Provider-specific features pass through LiteLLM: Anthropic `cache_control` and context-management params, OpenAI prompt-cache keys, reasoning/effort settings. If a feature does not survive LiteLLM, that provider may use its native LangChain package (`langchain-anthropic` etc.) behind the same registry interface. This exception is recorded in an ADR.
- `llm/registry.py` maps `provider:model` to a configured chat model. API keys are fetched from the keychain at call time only (§19.2).

### 7.2 Capability table
`llm/capabilities.yaml` records, per model: `tools`, `json_schema`, `vision`, `prompt_cache` (type + min tokens), `context_window`, `max_output`, `server_compaction`, `context_editing`, and prices (input / cached / output per MTok).
- Prices and context windows come from LiteLLM's model cost map (`litellm.model_cost`, `litellm.completion_cost`), with `capabilities.yaml` overriding where needed. Cost-map auto-fetch over the network is disabled (`LITELLM_LOCAL_MODEL_COST_MAP=True`), so prices change only when the pinned LiteLLM version is upgraded.
- Agents degrade gracefully when a capability is missing. For example, without `json_schema` the agent falls back to tool-call JSON with validation.

### 7.3 Role routing
| Role | Used by | Default (Anthropic key) | Default (OpenAI key) | Default (Ollama only) |
|---|---|---|---|---|
| `strong` | Lead, Writer | Opus 5.5 | GPT flagship | largest local model configured |
| `mid` | Reader, Verifier | Sonnet 5.5 | GPT mid tier | same |
| `cheap` | Scout, extraction, summarization, memory, query rewrite | Haiku 5.5 | GPT mini tier | small local model |
| `critic` | Critic | different provider's `strong` if available, else `strong` | — | — |
| `embed` | Embeddings | local Qwen3-Embedding-0.6B (fastembed) | same | same |
| `vision` | Figure explanation | `mid` if it has vision | — | — |

The user can override any role in Settings. Exact model IDs for each provider tier are set in `capabilities.yaml` during build step 1, checked against each provider's current model list, and refreshed by the same price-update script. Code never hard-codes model IDs.

### 7.4 Context management
- **Prompt order, for caching:** stable system prompt → tools → core memory blocks + playbook → dynamic content. Anthropic gets `cache_control` breakpoints; OpenAI and Gemini rely on their automatic prefix caching.
- **Tool-result clearing:** Anthropic uses `clear_tool_uses` context editing; other providers use `ContextEditingMiddleware(ClearToolUsesEdit)`.
- **Summarization:** Deep Agents' built-in summarization on context overflow. Before compaction, findings are flushed to the notes files.
- **Subagents** receive only their delegation contract and referenced files, never the Lead's history.

### 7.5 Ledger
Every LLM call writes an `llm_calls` row: `run_id, agent, role, provider, model, input_tokens, cached_tokens, output_tokens, cost_usd, latency_ms, status`. This row feeds budgets (§6.4), the live run meter (F1.5) and the usage dashboard (F10.2).

### 7.6 RecordReplay
`llm/record_replay.py` wraps any chat model.
- **Key:** `sha256(canonical_json(messages, tools, response_schema, role, model_alias))`.
- **Storage:** `e2e/fixtures/llm/<suite>/*.jsonl`, committed.
- **Modes:** `record`, `replay` (a missing fixture raises an error with the key and a diff hint against the nearest stored key) and `off`.
- **Scrubbing:** before writing, the recorder scrubs API keys, the localhost token, emails and memory contents. A CI check greps fixtures for secret patterns.
- CI always runs in `replay` mode.

### 7.7 Dev-only `claude-code` provider (the "ai-provider" agent)
- `llm/dev/claude_code.py` defines `ClaudeCodeChatModel(BaseChatModel)`, which calls `claude_agent_sdk.query()` with:
  - `tools=[]` and `system_prompt` set to the app's system prompt;
  - `max_turns=2` (structured output spends one turn on the CLI's internal output tool; live probe 2026-11-02: `max_turns=1` always hit `error_max_turns`) and `setting_sources=[]`;
  - `cwd` set to an empty temp dir.
- **Tool calls are emulated** with a JSON schema that is a `oneOf` of `{type:"final", content}` and `{type:"tool_calls", calls:[{name, args}]}`, built from the bound tools. Results map to `AIMessage.tool_calls`.
- **Structured output** passes through as the schema. Streaming maps `text_delta` events to chunks.
- **Model:** Sonnet by default, Haiku when `RESEARCHLY_DEV_PROVIDER_MODEL=haiku` (cheap bulk recording).
- **Gating:** available only when `RESEARCHLY_ENV=development` and `RESEARCHLY_DEV_PROVIDER=claude-code`. It is excluded from release builds by a build flag, and a release test asserts it is not registered.
- **Terms:** it uses the developer's own local Claude Code login, for individual development use only. It is never shipped and never used in CI.

---

## 8. Scholarly data

### 8.1 Connectors (keyless by default)
Every default connector works with **no key, no account and no email**. Limits below were live-checked from response headers on 2026-10-28, or taken from the docs where noted. The connector test suite re-verifies them nightly (`@live`).

| Tier | Source | Role in Researchly | Keyless limits / cost (observed) | License / terms |
|---|---|---|---|---|
| 1 | **OpenAlex** | **Enrichment hub** through singleton lookups: abstract, topics, `referenced_works`, `related_works`, `cited_by_count`, `fwci` / percentile, `best_oa_location`, `is_retracted`, licenses. Batch DOI resolution (`doi:a\|b\|…`, up to 100 per call). Autocomplete. Capped search | Keyless daily budget **$0.10 per IP** (`X-RateLimit-Limit-USD: 0.1`). **Singletons and autocomplete cost $0**. Filter/list $0.0001 per call; search $0.001 per call. Content/PDF API needs a key (not used) | CC0 |
| 1 | **arXiv** | Primary preprint search; metadata; HTML/LaTeX source; PDFs | 1 request / 3 s, single connection | Metadata CC0; per-paper licenses; no implied endorsement |
| 1 | **Crossref** | Search fallback, DOI metadata, references, licenses, funders, retractions | Public pool: single 5 rps (concurrency 1), list 1 rps (concurrency 1). Polite pool (10 rps / 3 rps) only if the user adds their own `mailto` | Metadata open |
| 1 | **OpenCitations Index/Meta v2** | DOI↔DOI citations in both directions; citation counts (covers arXiv DataCite DOIs) | 180 req/min/IP (docs) | CC0 |
| 1 | **DataCite** | arXiv ID ↔ `10.48550/arXiv.<id>` DOI mapping; datasets/software | No published limit; be polite | Open |
| 1 | **Europe PMC** | Biomedical search, citations/references, OA full-text XML | No limit headers; capped at 5 rps | Per-article licenses |
| 1 | **PubMed E-utilities** | Biomedical search | 3 rps (`X-Ratelimit-Limit: 3`) | Public |
| 1 | **Hugging Face Papers** | ML search, upvotes, linked repos/models/datasets | API 500 / 5 min; search 50 / 5 min | Public |
| 1 | **PwC archive** (HF `pwc-archive`, frozen July 2025) | Local paper→code table, downloaded at runtime (not bundled) | One download | Verify license (likely CC-BY-SA); do not redistribute |
| 2 | OpenReview API v2 | ML venue decisions and reviews (venue prior) | 20 / min | Public |
| 2 | bioRxiv/medRxiv | Preprints ↔ published DOIs | Polite 1 req/s | Per-paper licenses |
| 2 | CORE v3 | Repository OA full-text fallback | ~10 / min | Per-item |
| 2 | OpenAIRE Graph v1 | Secondary search, EU repositories, OA links | Budget **60 / hour** (docs; live header disagreed) | CC-BY: attribution shown |
| 2 | DBLP | CS venues and bibliography | Polite; honor `Retry-After` (live check pending) | CC0 |
| 2 | ACL Anthology | NLP metadata (local package data) | None (local) | Open |
| Opt | arXiv OAI-PMH local harvest | Optional offline index of chosen arXiv categories (SQLite/Postgres, a few GB) | Polite incremental harvest | CC0 metadata |
| Opt | OpenCitations Meta dump (8.4 GB) | Optional offline DOI→metadata store | One download | CC0 |

**Optional free keys** (Settings → Sources; off by default, never required):

| Key | Effect |
|---|---|
| OpenAlex | 10× budget ($1/day) |
| Semantic Scholar | Unlocks a TLDR / influential-citations / recommendations connector at 1 rps. Keyless S2 returned 429 on 7 of 8 calls in live checks, so it is **never** a keyless default |
| NCBI | 10 rps |
| OpenAIRE | 7,200 / hour |
| CORE | Higher limits |

OpenAlex offers automatic key provisioning by email. It is used **only** if the user explicitly consents and enters their own email.

**Excluded:**
- Unpaywall: requires an email; OpenAlex `best_oa_location` covers it.
- Semantic Scholar keyless and S2 datasets: key required.
- Google Scholar: no API, ToS.
- Internet Archive Scholar / fatcat: bot wall, unreachable.
- Wikidata/Scholia: anonymous traffic gets 429.
- Lens.org and BASE: key or IP registration required.
- SSRN, Altmetric, Scite, NASA ADS: key-gated.
- Kaggle arXiv dump: needs an account.

### 8.1.1 Routing (keyless)
- **Search fan-out**, then local dedupe and rerank:
  - arXiv;
  - Crossref `query`;
  - OpenAlex `search`, capped at **≤50/day per install**, with the rest of the budget kept for filters;
  - Europe PMC / PubMed when the query classifier says biomedical;
  - HF Papers when it says ML;
  - OpenAIRE and DBLP as low-rate extras in Deep runs.
- **Identity:** arXiv ID → DataCite DOI. Batch DOI resolution goes through one OpenAlex `doi:` filter call (1 credit per 100 DOIs), or through singletons ($0) when the budget is low.
- **Enrichment:** OpenAlex singleton ($0) first, then Crossref singleton (license, funders, retraction relations).
- **References (backward):** the union of OpenAlex `referenced_works` ($0), Crossref references and OpenCitations `/references`.
- **Citations (forward):** OpenCitations `/citations` first. OpenAlex `cites:` (1 credit per 200 citers) only for top-k seed papers, capped at 1,000 citers each. Europe PMC for biomedical papers.
- **Full text:** OpenAlex `best_oa_location.pdf_url`, then arXiv HTML/LaTeX/PDF, then Europe PMC OA XML, then CORE. Always fetched directly from the host, never redistributed.
- **Budget guard:** read `X-RateLimit-Remaining-USD` on every OpenAlex response. At ≤20% remaining, stop paid operations (search, list) and fall back to free singletons and the other sources. The UI shows "OpenAlex daily budget low", and the budget resets at midnight UTC.
- **Shared IP caveat:** the keyless OpenAlex budget is per IP, so university or corporate NAT users may run out early. Researchly degrades gracefully and suggests the optional free key.

### 8.2 Connector contract
```python
class Connector(Protocol):
    name: str
    async def search(self, query: SearchQuery) -> list[PaperRecord]: ...
    async def get(self, ids: list[ExternalId]) -> list[PaperRecord]: ...
    async def citations(self, id: ExternalId, direction: Literal["in", "out"]) -> list[ExternalId]: ...
    def capabilities(self) -> ConnectorCaps: ...   # search, citations, fulltext, embeddings, ...
```
- All HTTP goes through one `PoliteClient`:
  - per-host token buckets from `limits.yaml`, set just below the observed limits:

    | Host | Rate | Concurrency |
    |---|---|---|
    | OpenAlex | 5 rps | 2 |
    | Crossref single | 5 rps | 2 |
    | Crossref list | 1 rps | 1 |
    | OpenCitations | 2 rps | — |
    | arXiv | 1 per 3 s | 1 |
    | OpenReview | 1 per 3 s | — |
    | OpenAIRE | 1 per min | — |
    | PubMed | 2 rps | — |
    | Europe PMC | 5 rps | — |
    | HF search | 8 per min | — |
    | CORE | 8 per min | — |

  - honors `Retry-After` and 429/503;
  - exponential backoff with jitter, plus a **circuit breaker per source**;
  - User-Agent `Researchly/<ver> (+https://github.com/SaadKazmi/researchly)`. A `mailto` is added **only** if the user sets their own; there is no shared project email;
  - `robots.txt` checks for non-API fetches;
  - ETag/If-None-Match;
  - responses cached in `http_cache` (Postgres) keyed by normalized ID. TTLs: metadata and references 60 days, citation counts 7 days, search results 24 h. A singleton is never re-fetched within its TTL, and DOIs are deduped across agents before any call.
- Each connector ships with recorded cassettes plus a contract test (the `add-scholarly-connector` skill).
- When a quota is exhausted (e.g., the OpenAlex daily budget), the connector raises `QuotaExhausted`. The Lead is told, the UI shows a banner, and other sources continue.

### 8.3 Identity resolution
1. Exact match on DOI (including the arXiv DataCite DOI `10.48550/arXiv.<id>`), arXiv ID, OpenAlex ID, PMID or S2 ID (when the optional key is used), via the `paper_ids` table.
2. Otherwise a fuzzy match on normalized title (trigram ≥ 0.92) + year ± 1 + first-author surname.
3. Merging keeps provenance per field (`field_sources` JSONB).

### 8.4 Licensing
- Each paper stores `license` and `license_source`.
- Full text is cached locally for the user's personal research.
- Exports (F6.5) respect licenses.
- The UI always links back to the source landing page.
- A data-attribution line appears in the footer of results and graph views ("Data: OpenAlex (CC0), OpenCitations (CC0), Crossref, arXiv, Europe PMC, OpenAIRE (CC-BY) …"), listing only the sources used. S2 attribution is added when the optional S2 key is used.

---

## 9. Ingestion & retrieval

### 9.1 Full-text pipeline
1. **arXiv HTML** (`arxiv.org/html/<id>`), else **LaTeX source** parsed to sections (pylatexenc/plasTeX-lite), else the PDF.
2. **PDF**: **Docling** in-process (ONNX-runtime models; no torch) for body text, tables and formulas. **GROBID** adds the header and references whenever it can run (Docker mode, or native Linux/macOS). **MinerU / PaddleOCR-VL** is an optional GPU mode.
3. **References**: preferred from OpenAlex `referenced_works`, Crossref and OpenCitations. Parsed references (GROBID, or the LLM parsing reference strings followed by Crossref/OpenAlex matching) are a fallback.
4. **Output**: `paper_sections`, `paper_figures` (image + caption + referencing paragraphs), `paper_tables`, `paper_equations` and `paper_refs`.

### 9.2 Chunking & embeddings
- **All papers:** title + abstract are embedded.
- **Ingested papers only:** full text is chunked by section, with ~350-token chunks, 15% overlap, and the section path recorded.
- **Default embedder:** Qwen3-Embedding-0.6B (Apache-2.0) as ONNX on CPU through onnxruntime (fastembed if it supports the model; otherwise an ONNX export loaded directly, confirmed in step 2), Matryoshka-truncated to 1024 dims, stored as `halfvec(1024)`.
- **Alternatives:** Ollama embeddings, provider embeddings, or bge-m3.
- Every vector set records `embed_model_id` and `dim`. Changing the model triggers a background re-embed job.
- **Paper-to-paper similarity:** SPECTER2 (`allenai/specter2_base`, Apache-2.0) computed **locally** from title + abstract (`paper_specter halfvec(768)`). This replaces the S2-hosted vectors, with no key needed.

### 9.3 Search
- **Hybrid:** BM25 (pg_textsearch, or the tsvector fallback) + dense (pgvector HNSW, `iterative_scan=relaxed_order`), fused with RRF (k=60).
- **Rerank:** cross-encoder **bge-reranker-v2-m3** (Apache-2.0, ONNX via onnxruntime) on the top 50. Optionally an LLM rerank with the `cheap` role.
- **Contextual retrieval:** opt-in per collection. The `cheap` model writes a 50–100 token context prefix per chunk, using prompt caching per paper. A cost estimate is shown first.
- **Graph-aware expansion:** personalized PageRank over the citation + concept subgraph (NetworkX), seeded by top hits, HippoRAG-style.
- **Scale guard:** if the chunk count exceeds 5M, install pgvectorscale (StreamingDiskANN) in the same DB. An ADR is required before any separate vector DB.

---

## 10. Ranking

Candidates first pass a **relevance gate** (cross-encoder score ≥ τ, or an LLM yes/no with the `cheap` role on borderline cases). Then:

```
score = w_rel*rel + w_imp*impact + w_inf*influential + w_graph*centrality
      + w_vel*velocity + w_venue*venue_prior + w_prac*practical
```

| Component | Definition (normalized 0–1 within candidate set) | Balanced | Seminal | Recent |
|---|---|---|---|---|
| rel | Cross-encoder relevance to the query/sub-question | 0.45 | 0.35 | 0.40 |
| impact | OpenAlex `citation_normalized_percentile` / FWCI from free singletons. These are null for preprints, so the fallback is a **local percentile** of `cited_by_count` within the same OpenAlex topic × year, computed from the cache | 0.20 | 0.30 | 0.05 |
| influential | Keyless proxy: the share of citers that are themselves in the candidate/seed set (co-citation by seeds), plus citing-context weight when full text is ingested (cited in more than one section). Uses S2 `influentialCitationCount` when the optional key is set | 0.10 | 0.15 | 0.05 |
| centrality | PageRank + co-citation strength in the local candidate graph | 0.10 | 0.15 | 0.05 |
| velocity | Citations in the last 24 months / total | 0.05 | 0.00 | 0.25 |
| venue_prior | Curated venue tier + OpenReview acceptance/oral | 0.05 | 0.05 | 0.05 |
| practical | Code available + log1p(HF upvotes) (ML papers) | 0.05 | 0.00 | 0.15 |

- Author h-index is not used.
- Weights live in `ranking.yaml`, and the user may edit them.
- Every paper shows its breakdown (F2.3).
- Ranking quality is regression-tested on LitSearch / PaperFindingBench (§21.6).

---

## 11. Graphs

- **Tables:**
  - `citation(src_paper_id, dst_paper_id, source, is_influential)`
  - `concept(id, canonical_name, type, embedding)`
  - `paper_concept(paper_id, concept_id, section, confidence)`
  - `concept_edge(src, dst, relation, weight, evidence_count)`
- **Similarity edges** (computed per view, not stored globally): co-citation and bibliographic coupling counts within the candidate set.
- **Concept extraction:**
  - The `cheap` model extracts per section, using the SciERC/SciER schema (Task, Method, Dataset, Metric; used-for, compare, evaluated-on).
  - Cross-paper linking: embedding clusters, then an LLM pass that merges canonical names.
  - OpenAlex Topics form the backbone hierarchy.
- **Algorithms:** NetworkX/igraph on subgraphs pulled per request (PageRank, Louvain, shortest paths). Traversal of 1–3 hops uses recursive CTEs.
- **API:** `GET /graph/{view}?seed=&depth=&filters=` returns Graphology-serializable JSON. Expansion is incremental.

---

## 12. Writing & verification pipeline

### 12.1 Long-form pipeline (reports, notebooks, books)
1. **Evidence first.** Retrieve → rerank → quote extraction → thematic clustering (Ai2 ScholarQA pattern). Output: `evidence/` + `clusters.json`.
2. **Outline.** STORM-style perspective-guided question generation leads to the outline. The user edits the outline and reference groups (InteractiveSurvey UX); this is a HITL interrupt.
3. **Section loop (sequential).** For each section:
   - targeted retrieval;
   - if evidence is below threshold, retrieve more or revise the plan (SurveyGen-I);
   - citation tracing, which prefers the original source over secondary citations;
   - write.
   A shared section memory (key terms, claims already made) prevents repetition across sections.
4. **Cite-then-generate.** The Writer may cite only evidence-card IDs. Each sentence is generated from the selected spans ("Attribute First, then Generate").
5. **Verify** (§12.2), then **Critique**, then **Revise**, for at most 2 rounds.
6. **Assemble.** Figures (Vega-Lite / Mermaid specs, or kernel-rendered plots), the bibliography (CSL) and front matter.

### 12.2 Verification layers
1. **Deterministic:**
   - every `[cite:<evidence_id>]` resolves to a stored evidence card;
   - the quoted span exists in the chunk (normalized string match);
   - every reference resolves to a real record (DOI via Crossref/DataCite/OpenAlex, fuzzy title-author-year match, graded Exact / Minor / Major drift), and retracted papers are flagged (OpenAlex `is_retracted`, Crossref retraction relations).
2. **Grounding model:** sentences are decomposed into atomic claims, and each (claim, cited passage) pair is scored by **FactCG-DeBERTa-L** (MIT, CPU). **MiniCheck-Flan-T5-Large** is the alternative.
3. **Escalation:** low-confidence pairs (0.35–0.65) go to the `mid` LLM as judge.
4. **Action:** unsupported claims are re-retrieved, then rewritten, then dropped or flagged "unsupported" in the UI.
5. **Metrics:** citation precision/recall (ALCE-style) per document, stored and shown.

### 12.3 Critic rubric
Binary, query-specific rubric items covering: factual accuracy, citation accuracy, completeness against the plan's sub-questions, source quality, coherence, and limitations stated. Critic output is a list of failed items with locations, and the Writer revises only those.

### 12.4 Novelty check (ideas, F4.8)
1. Keyword + snippet retrieval across connectors.
2. Embedding filter.
3. LLM facet comparison (problem, method, data, claim) against the top 20.
4. A novelty score with the nearest prior work listed.

### 12.5 Gap detection (F4.7)
- **Swanson rare-link:** concept pairs with high individual frequency but low co-occurrence, ranked by the bridge concepts they share.
- **Coverage gaps:** plan sub-questions with fewer than k evidence cards.

---

## 13. Notebooks, books, exports

- **Canonical document model:** a JSON block tree (Plate value) stored in `documents.content`, plus a `bibliography` (CSL-JSON). Block types: heading, paragraph, citation, figure (vega-lite | mermaid | image | excalidraw), math, code cell (+ outputs), table, callout, extraction-table embed, graph embed.
- **Serializers:**
  - block tree ↔ MyST Markdown;
  - block tree → `.ipynb` (nbformat);
  - MyST → Pandoc (DOCX, LaTeX, HTML) with citeproc + CSL;
  - Typst for PDF;
  - mystmd / Jupyter Book 2 for the static site.
- **Code execution:**
  - The kernel gateway runs in the sandbox (§19.4); outputs are stored with the cell.
  - Pyodide runs lightweight cells client-side (charts on data already in the doc).
- **Versioning:** each save stores a `document_versions` row. AI edits are stored as suggestions the user accepts or rejects.

---

## 14. Studio outputs
- **Audio overview:** the Writer's `audio-script` skill produces a two-host script grounded in evidence. TTS is Kokoro-82M (local, ONNX) or provider TTS. Output is an MP3 plus a transcript with citations.
- **Study guide, FAQ, flashcards, slides:** Writer skills over evidence. Slides use Pandoc to produce Reveal.js and PPTX.
- **Systematic review:**
  - **Protocol:** question (PICO or free form), inclusion and exclusion criteria.
  - **Search log:** all queries and sources, with counts.
  - **Dedupe.**
  - **Screening:** an LLM suggests include/exclude with reasons; the user decides; inter-rater stats are kept when the user reviews a sample.
  - **Full-text screening.**
  - **Extraction table.**
  - **PRISMA 2020 flow diagram** generated from the logged counts (Mermaid/SVG).

---

## 15. Memory

### 15.1 Scope split
- **Memory framework** (Mem0 or Hindsight, per D8): user profile facts and preferences, and per-project facts the user stated. Scope: `user_id="local"`, `agent_id="researchly"`, project mapped to `run_id`/metadata as the chosen framework supports.
- **Researchly tables** (not the framework): papers, evidence, notes, documents, playbook, chat transcripts (episodic, immutable).

### 15.2 Bake-off (build step 6, before integration)
- **Harness:** a LongMemEval-S subset (≥100 questions) plus a Researchly fixture set (≥50 multi-session preference/fact questions), using identical LLM and embedder roles.
- **Metrics:** accuracy, p95 latency, tokens per add/search, and Postgres footprint.
- **Decision rule:** highest accuracy wins, unless it is more than 2× worse on latency or tokens. The result is recorded as an ADR.
- Both implement `memory/adapter.py: MemoryAdapter` (`add`, `search`, `list`, `update`, `delete`, `history`, `forget_scope`, `export`). The loser's adapter is deleted after the ADR.

### 15.3 Policies
- **Write sources:** only the user's own turns and explicit "remember this" actions. Never web or PDF content (memory-poisoning defence).
- **Write timing:** asynchronously at session end and every 10 turns. Writes that change behaviour (preferences that alter outputs) need user confirmation (F8.5).
- **Retrieval:**
  - The core blocks (profile, project, procedural preferences, playbook head) are always in the cached prefix.
  - Other memories are fetched just in time through the `memory_recall` tool, scoped to the project.
- **Playbook (ACE):** after each run, the curator proposes ≤5 bullet deltas (`add` / `deprecate`) to `playbook_items(project_id, text, helpful_count, harmful_count, status)`. Items are never rewritten wholesale. The top-N by helpfulness are loaded.
- **Decay:** unpinned memories not accessed in 180 days are archived (restorable).

---

## 16. Data model (core tables)

```
project(id, name, description, created_at, settings jsonb)
thread(id, project_id, title, created_at)
message(id, thread_id, role, content jsonb, run_id, created_at)
run(id, project_id, thread_id, effort, status[pending|planning|awaiting_user|running|paused|partial|completed|cancelled|failed],
    plan jsonb, budget_usd, spent_usd, checkpoint_thread_id, parent_run_id, branch_from_checkpoint, started_at, ended_at)
run_event(id, run_id, seq, type, agent, payload jsonb, ts)                -- AG-UI event log (replayable)
llm_call(id, run_id, agent, role, provider, model, in_tok, cached_tok, out_tok, cost_usd, latency_ms, status, ts)
paper(id, title, abstract, year, venue_id, doi, license, license_source, oa_url, is_oa, cited_by, fwci, pct, field_sources jsonb, updated_at)
paper_ids(paper_id, scheme[doi|arxiv|openalex|s2|pmid|pmcid|dblp|acl|ads], value)  UNIQUE(scheme,value)
author(id, name, orcid, openalex_id) ; paper_author(paper_id, author_id, position)
venue(id, name, type, tier)
paper_embedding(paper_id, model_id, vec halfvec)                         -- title+abstract
paper_specter(paper_id, vec halfvec(768))
paper_section(id, paper_id, path, text) ; paper_figure(...) ; paper_table(...) ; paper_equation(...)
chunk(id, paper_id, section_id, ord, text, context_prefix, tokens, bm25 index)
chunk_embedding(chunk_id, model_id, vec halfvec)
citation(src, dst, source, is_influential) ; concept(...) ; paper_concept(...) ; concept_edge(...)
collection(id, project_id, name) ; collection_paper(collection_id, paper_id, tags[], note)
annotation(id, paper_id, project_id, anchor jsonb, text, color)
evidence(id, run_id, project_id, paper_id, chunk_id, claim, quote, relevance, created_at)
verification(id, document_id, sentence_ref, claim, evidence_id, label, score, method)
document(id, project_id, kind[answer|notebook|book|study_guide|faq|slides|review], title, content jsonb, bibliography jsonb, status)
document_version(id, document_id, content jsonb, author[user|agent], ts)
extraction_table(id, project_id, columns jsonb) ; extraction_cell(table_id, paper_id, column, value, quote, evidence_id)
saved_search(id, project_id, query jsonb, schedule, last_run_at) ; alert_item(id, saved_search_id, paper_id, seen)
review(id, project_id, protocol jsonb) ; screening_decision(review_id, paper_id, stage, decision, reason, by)
playbook_item(id, project_id, text, helpful, harmful, status, created_at)
tool_cache(key, tool, result jsonb, created_at, ttl) ; http_cache(key, url, status, headers, body, etag, expires_at) UNLOGGED
setting(key, value jsonb) ; capability(key, value jsonb) ; provider_config(id, provider, base_url, models jsonb, key_ref)
-- plus: LangGraph checkpoint tables, Deep Agents store tables, procrastinate tables, memory-framework tables
```
Migrations use Alembic. Every migration has an upgrade → downgrade → upgrade test.

### 16.1 Postgres operations
- **One database** (`researchly`) with two roles:
  - `researchly_app`: owns the schema and runs migrations.
  - `researchly_ro`: read-only. Used by the MCP server tools and by dev tooling (`psql`, postgres-mcp).
- **Credentials:** random passwords generated at `onboard` and stored in the OS keychain (§19.2). They are never written to compose files; the CLI injects them as environment variables at start.
- **Network:**
  - Docker: the DB is on the internal compose network only, with no host port published. `researchly start --expose-db` publishes it on `127.0.0.1` for power users.
  - Native: it binds to `127.0.0.1` on a free port (default 54329), with Unix sockets on macOS/Linux.
- **Data location:** `<data_dir>/pg` (`~/.researchly` by default; configurable at onboard). Docker uses a bind mount there, so both modes keep data in the same visible place.
- **Same major version in both modes: PostgreSQL 18.** Native uses `pg0-embedded`, which bundles PG 18 + pgvector 0.8.5 (fallback: conda-forge PG 18 + pgvector). Docker uses the PG 18 image. This keeps `pg_dump` portable across modes. The step-1 spike verifies, on all five platform targets:
  - `CREATE EXTENSION vector, pg_trgm` works;
  - start time;
  - data directory relocation to `<data_dir>/pg`.
  If `pg_trgm` is missing from pg0, native mode uses the conda-forge fallback, and this is recorded in an ADR.
- **Extensions:**
  - `vector` and `pg_trgm` are always present. `pg_textsearch` is present in Docker mode only.
  - The Alembic baseline creates the extensions that are available and records the BM25 engine in `capability`.
  - Search code goes through one `KeywordIndex` interface with two implementations (pg_textsearch and tsvector), and both are tested.
- **Migrations on start:** `researchly start` takes an automatic pre-migration backup, runs `alembic upgrade head`, then starts the app. If a migration fails, start aborts and shows how to restore the backup. `doctor` reports the migration state.
- **Tuning:** at onboard the CLI sets a memory-aware `postgresql.conf` override:
  - `shared_buffers` 25% of RAM, capped at 4 GB;
  - `maintenance_work_mem` 1 GB for HNSW builds;
  - `work_mem` 32 MB;
  - `max_connections` 40;
  - `wal_compression` on.
- **Backups:**
  - `researchly backup`: `pg_dump -Fc` plus a tarball of `<data_dir>/files` (PDFs, exports). Daily automatic backups, keeping 7.
  - `researchly restore <file>` verifies the dump before replacing.
  - Backup and restore are covered by E2E tests in both modes.
- **Upgrades:**
  - Minor Postgres versions come with the image or wheel update in `researchly update`.
  - A major version change triggers an automatic dump → new cluster → restore, keeping the old data directory until the user confirms.
- **Switching modes** (Docker ↔ native): `researchly migrate-runtime` does a backup, then a restore into the other runtime, then re-detects capabilities. This works because both modes share a major version.
- **Developer access:** `researchly db shell` opens `psql` as `researchly_ro` (add `--rw` for the app role in dev builds only). Tests use testcontainers with the same image as Docker mode, plus the native-mode CI matrix.

---

## 17. API & streaming
- **REST** (`/api/v1`): projects, threads, runs (create / pause / resume / cancel / branch), papers, collections, search, graph, documents, exports, memory, settings, providers, usage, system.
- **AG-UI endpoint:** `POST /api/v1/runs/{id}/stream` (SSE). Events: `RUN_STARTED/FINISHED/ERROR`, `STEP_STARTED/FINISHED` (per agent), `TEXT_MESSAGE_*`, `TOOL_CALL_*`, `STATE_SNAPSHOT/DELTA` (plan, budget, todos), plus custom `CITATION`, `COST`, `INTERRUPT`.
- All events are persisted to `run_event`, so reconnecting replays from the last sequence number.
- The `ag-ui-langgraph` adapter is pinned.
- **OpenAPI** is generated from FastAPI. The TypeScript client is generated with `openapi-typescript`, and a CI check fails if it is stale.
- **MCP server:** mounted at `/mcp` (Streamable HTTP) plus a `researchly mcp` stdio command. Read-only tools: `search_library`, `get_paper`, `search_evidence`, `list_projects`.

---

## 18. Frontend
- **Stack:**
  - Vite + React 19 + TypeScript (strict) + TanStack Router + TanStack Query.
  - shadcn/ui (Base UI primitives) + Tailwind v4.
  - Built as a static SPA served by FastAPI.
- **Chat:** assistant-ui with its AG-UI runtime.
  - Tool-call UIs map each tool to a component: search results, paper card, plan editor, interrupt approve/edit/reject, cost confirm.
  - Run tree panel; plan panel (shared state).
- **Graph:** Sigma.js v4 + Graphology, with ForceAtlas2 in a web worker and Louvain clustering; cosmos.gl for the field view; React Flow for small diagrams; a table fallback view.
- **Reader:** pdf.js with an anchor-based highlight layer, plus an HTML reader for arXiv HTML.
- **Editor:** Plate with an AI plugin (streamed suggestions), math, tables and comments.
- **Visual renderers:** vega-embed (Vega-Lite only, JSON-schema validated), Mermaid (`securityLevel:"strict"`), KaTeX and Excalidraw. Any other HTML preview goes in a sandboxed iframe (§19.5).
- **Routes:**
  - `/` projects
  - `/p/:id/chat/:thread`
  - `/p/:id/library`
  - `/p/:id/graph`
  - `/p/:id/docs/:doc`
  - `/p/:id/studio`
  - `/p/:id/review`
  - `/memory`
  - `/usage`
  - `/settings/*`
  - `/onboarding`
- **State:** TanStack Query for server state; Zustand only for local UI state if needed.
- **Accessibility:** Radix/Base UI semantics, focus management, `prefers-reduced-motion`, axe checks in E2E.
- **i18n:** UI strings are extracted with i18next (English shipped). Agents answer in the user's language.

---

## 19. Security
Threat references: OWASP Top 10 for LLM Applications 2025 and OWASP Top 10 for Agentic Applications 2026 (ASI01–ASI10).

### 19.1 Prompt injection (ASI01/02)
- **Plan-then-execute + dual LLM:** the Lead and Writer never receive raw web or PDF text. Untrusted content is processed by the **Quarantined Extractor**, a tool-less LLM call with structured output only (claims, quotes, metadata), or by deterministic parsers.
- **Spotlighting:** any untrusted text that must reach a model is datamarked and wrapped in `<untrusted source="...">`, with a system instruction to treat it as data.
- **Provenance tags** on values (CaMeL-inspired): `trusted | user | untrusted`. Tools that take arguments derived from untrusted values cannot perform writes without a HITL confirmation.
- **No exfiltration tools:** no arbitrary POST, email or shell. Fetchers are GET-only. MCP client tools need per-tool approval.

### 19.2 Secrets
- API keys live in the OS keychain (`keyring`). Where no keychain backend exists (headless Linux), keys go in an AES-GCM encrypted file whose key is derived from a passphrase or from a machine key in a `0600` file. `doctor` reports which store is in use.
- Keys never enter LangGraph state, checkpoints, the store, logs, traces or fixtures. They are fetched inside the provider call. A logging filter redacts known key patterns.

### 19.3 SSRF-safe fetching
- Resolve DNS once. Reject private, loopback, link-local, CGNAT, metadata (169.254.169.254), IPv6 ULA and IPv4-mapped addresses. Connect to the pinned IP with SNI.
- Re-check every redirect (max 5), cap the response size (50 MB PDF, 5 MB HTML), and allowlist content types.
- In Docker mode the fetcher runs in the app container on a network with no route to `db` except through the app, and the DB listens only on the internal network.
- Unit tests cover known bypasses (DNS rebinding, decimal and octal IPs, IPv6 forms, redirect to internal).

### 19.4 Code execution (ASI05)
- **Light cells (every mode): Pyodide inside Deno.** Deno's permission flags are the real boundary: no network, read and write only in the cell's temp dir, no subprocesses or FFI. Pure Python, numpy, pandas and matplotlib work, and behaviour is identical on every OS. Import blocking is never treated as a security boundary (DEF CON 34 showed Pyodide ctypes escapes when restrictions were Python-level only).
- **Full CPython kernel, Docker mode:** a Jupyter kernel container per project session with `--network none`, read-only root plus a tmpfs workdir, limits of 2 CPU, 2 GB memory and 256 PIDs, a 60 s per-cell timeout, a non-root user, `no-new-privileges`, dropped capabilities, and the `runsc` (gVisor) runtime if installed.
- **Full CPython kernel, native mode** (opt-in), in the OS sandbox. This is the same approach Claude Code's sandbox-runtime and Codex use:
  - **macOS:** `sandbox-exec` with a generated Seatbelt profile. Writes are denied except in the workdir, and network goes through a localhost proxy that denies all traffic.
  - **Linux:** bubblewrap + seccomp when `bwrap` and user namespaces are available. Otherwise Landlock (no root; network rules on kernel ≥6.7) + rlimits.
  - **Windows:** restricted token + ACLs + Job Object (unelevated). An optional stronger mode adds a dedicated sandbox user + firewall rules, which costs one UAC prompt and is offered, not required.
  - **Fail closed:** if the sandbox cannot be established, the kernel does not start (avoids the silent-fallback class of CVE-2026-57144).
- **Every** AI-proposed code cell needs a user click to run.

### 19.5 Output rendering
- `react-markdown` without `rehype-raw`, plus `rehype-sanitize`. Links are limited to `https:` and `mailto:` and show their real host.
- Remote images don't load automatically; they go through a click-to-load proxy.
- Mermaid runs in strict mode. Vega-Lite specs are schema-validated, with no `href` or `url` data loading except from the app API.
- HTML previews run in `<iframe sandbox="allow-scripts">` on a separate origin with a strict CSP.
- The app's own CSP is `default-src 'self'` with no inline scripts.

### 19.6 Memory poisoning (ASI06)
See §15.3: writes come only from user turns, memories carry provenance, behaviour-changing writes need confirmation, and the user can view and edit everything.

### 19.7 Supply chain (ASI04)
- **Python:**
  - `uv sync --locked`, with `exclude-newer` set to 7 days for dependency updates.
  - pip-audit and OSV in CI.
  - CI watches for unexpected `.pth`/`sitecustomize` files.
- **JS:** `pnpm install --frozen-lockfile` with `minimumReleaseAge` set to 7 days.
- **GitHub Actions:** pinned by commit SHA, minimal `permissions`, Dependabot/Renovate with a cooldown.
- **Releases:** a CycloneDX SBOM per release, images signed with cosign (keyless), and the installer verifies image digests and checksums of release assets.
- **LiteLLM hardening** (D9):
  - Pin an exact version with hashes in `uv.lock`. Choose a release that is ≥14 days old and not listed in any advisory (OSV/GHSA/PyPI yanks); explicitly exclude 1.82.7, 1.82.8 and 1.107.0.
  - Upgrade only through a dedicated PR that checks advisories and diffs the wheel for new `.pth`/`sitecustomize` files.
  - Never use LiteLLM Docker images or `latest` tags.
  - Disable LiteLLM telemetry and the remote cost-map fetch.
  - LiteLLM is imported only in `llm/registry.py`.

---

## 20. Observability & cost
- **Tracing:** OpenTelemetry using the GenAI semantic conventions (pinned version). By default spans go to a local SQLite/console exporter in debug mode.
- **Langfuse:** optional, through the `langfuse` compose profile.
- **Structured JSON logs** with `run_id` and `agent`. Logs are viewable through `researchly logs` and in Settings → System.
- **Usage dashboard** (F10.2) built from `llm_call`.

---

## 21. Testing strategy

### 21.1 Unit (pytest, Vitest)
- Agents and graphs: `GenericFakeChatModel` with scripted tool calls; `InMemorySaver` and an in-memory store.
- Services: ranking math, dedupe, identity resolution, the SSRF filter, serializers, budget guard, loop detector.
- Frontend components: Vitest + Testing Library.

### 21.2 Integration
- Testcontainers Postgres (pgvector + pg_textsearch image), with migrations applied.
- Connectors use vcrpy cassettes (auth headers filtered). LLM calls use RecordReplay in replay mode.
- Agent runs end to end through the API (no browser) on replayed fixtures.

### 21.3 E2E (Playwright Test)
- The full stack is started by the CLI in Docker mode and in native mode (CI matrix), with LLM replay and HTTP replay.
- **Feature specs**, one per F-ID, tagged `@F2.3` etc.
- **Journey specs** (`@journey`) that combine features as a real user would. Minimum set:
  1. Onboard → add provider key → create project → Standard run → approve plan → watch run tree → answer with citations → open citation in reader.
  2. Deep run → pause → restart the app → resume → completes with partial-budget warning handling.
  3. Search → rank presets → add to collection → citation graph expand → field view → pin to collection.
  4. Book generation → edit outline → section loop → verification flags → fix → export PDF + DOCX + ipynb.
  5. Notebook with code cell → run in sandbox → chart → export site.
  6. Memory: state a preference → confirm → new thread respects it → edit/delete in memory panel.
  7. Systematic review → screening → extraction table → PRISMA diagram.
  8. Offline mode with Ollama fixture → cached papers → answer.
  9. Saved search → alert → inbox.
  10. Budget exhaustion → partial result salvage → branch rerun with higher budget.
  11. Studio: audio overview + flashcards export.
  12. MCP server queried by an external client fixture.
- **Assertions:** aria snapshots for structure; `toHaveScreenshot` (inside the Playwright Docker image) for key views; graph test hooks (`window.__researchly.graph.nodeCount()`, camera state) instead of comparing pixels; `page.clock` for timers; axe accessibility scans.
- **Tags:** `@smoke` (fast, runs at session start and on every PR), `@journey` (every PR), `@live` (nightly, real APIs).

### 21.4 user-simulator (exploratory, nightly + on demand)
- **Matrix** (`e2e/personas/*.yaml`):
  - Personas: novice student, domain expert, impatient user, non-English speaker, adversarial user, accessibility user (keyboard only).
  - Each persona has goals.
  - Perturbations: vague wording, contradictory constraints, mid-run cancel / refine / language switch, huge requests, rapid resubmits, Unicode/emoji, prompt-injection attempts, feature combinations.
  - Persona Policies make the simulator non-cooperative.
- **Driver:** the `user-simulator` Claude Code agent (Sonnet) uses Playwright MCP (`--headless --isolated --output-dir e2e/.artifacts`) against the stack, with **ai-provider** answering LLM calls in record mode and HTTP replay or the live sources.
- **Scoring:**
  1. Hard checks first: no console errors, no 5xx, run reaches `completed` / `cancelled` / `partial` (not `failed`), citations resolve, no unsanitized HTML.
  2. Then a rubric judge with structured output: task success, groundedness, recovery from the perturbation, safety, UX friction. Each score needs an evidence quote and a severity.
- **Calibration:** the judge is checked against 50 human-labelled sessions before its scores gate anything.
- **Output:** each failure becomes `e2e/user-sim/reports/<date>/<id>.md` with repro steps, a trace and screenshots. The Playwright Generator agent drafts a spec, which a human or reviewer agent approves. The spec then runs on the recorded fixtures as a regression test.

### 21.5 Red team
- **promptfoo redteam** in CI, against the HTTP API target, with OWASP LLM + agentic plugins (prompt injection through fetched documents, memory poisoning, tool misuse, PII leakage, excessive agency). Severity ≥ high fails the build.
- **garak** and **PyRIT** (`microsoft/PyRIT`) run nightly. A curated set of malicious PDFs and HTML fixtures is used for indirect injection.

### 21.6 Quality evals (Inspect AI, nightly + before release)
| Capability | Benchmark | Metric | Gate |
|---|---|---|---|
| Paper retrieval / ranking | LitSearch, PaperFindingBench (AstaBench), PaSa RealScholarQuery | Recall@20/50, nDCG@10 | No regression > 2 pts |
| Evidence QA | LitQA2 | Accuracy, abstention-aware precision | No regression |
| Long-form synthesis | AstaBench SQA / ScholarQABench | Rubric score, citation F1 | No regression |
| Broad scholarly QA | ResearchQA (sampled) | Rubric coverage by item type | Tracked |
| Reports and books | DeepResearch Bench (RACE/FACT), DeepResearch Bench II binary rubrics | Pass rate, citation accuracy | No regression |
| Grounding checker | LLM-AggreFact subset + own labeled pairs | Balanced accuracy | ≥ baseline |
| Memory | LongMemEval subset + own fixtures | Accuracy | Used by the bake-off and tracked |
| RAG units | DeepEval faithfulness / context recall | Scores | Tracked |

- **Judge:** a pinned model and version, binary rubric items, pairwise comparisons scored with both orderings, calibrated on 150–300 human labels per task (target Cohen's κ ≥ 0.6), with length bias and flip rate tracked.
- Evals use the maintainer's API keys and never run on forks.

---

## 22. CI/CD & release
- **`ci.yml`** (every PR):
  - ruff, pyright, eslint, tsc;
  - unit tests, integration tests (testcontainers);
  - OpenAPI client freshness check;
  - build images, start the stack (Docker), and run Playwright `@smoke` + `@journey` on replay;
  - native-mode smoke on `ubuntu`, `windows` and `macos` runners;
  - promptfoo redteam on replay;
  - fixture secret scan, pip-audit / OSV.
  - Traces and reports are uploaded on failure.
- **`nightly.yml`:** `@live` E2E against real sources and live LLM (maintainer key), user-simulator sessions, Inspect evals, garak/PyRIT, dependency audit. It opens an issue on regression.
- **`release.yml`** (tag `v*`):
  - build and push multi-arch images to GHCR, CycloneDX SBOM, cosign sign;
  - build CLI wheels;
  - publish a GitHub Release with `install.sh` / `install.ps1` checksums;
  - post-release install test on all three OSes.
- **Versioning:** SemVer, with Conventional Commits generating the changelog. The repo stays private until the first release tag, then becomes public.
- **Installer** (modelled on the uv and pixi installers):
  - **Steps:**
    1. `install.sh` / `install.ps1` detects the OS and architecture.
    2. It installs `uv` to the user directory if missing (uv's own installer, PATH changes controlled).
    3. `uv tool install researchly --python 3.12` (Python from python-build-standalone).
    4. `researchly setup` runs runtime selection (§4.1): it detects the container engine and starts it if needed, or prepares native components.
    5. It then fetches models and browsers lazily and resumably, runs `onboard` (LLM key, optional source keys), runs `doctor`, and prints the local URL.
  - **No admin, ever.** Everything goes to user directories: `~/.local/bin` and `%LOCALAPPDATA%`. Idempotent and re-runnable. Never uses `sudo`; when an OS-level fix would help (e.g., Playwright Linux libs), `doctor` prints the single command for the user to run.
  - **Integrity:** the script verifies SHA-256 hashes of everything it downloads. curl does not set the macOS quarantine flag, so Gatekeeper will not check it for us. Release assets carry GitHub artifact attestations (Sigstore), and the CLI is published to PyPI with Trusted Publishing.
  - **Windows:** the documented one-liner is `powershell -ExecutionPolicy ByPass -c "irm …/install.ps1 | iex"`. Optional Authenticode signing through SignPath Foundation (free for OSI projects).
  - **Networks:** honors `HTTPS_PROXY`, `SSL_CERT_FILE` / `UV_NATIVE_TLS`, `UV_INDEX_URL` and `HF_ENDPOINT` for corporate networks and mirrors.
  - **Air-gapped:** `researchly bundle export|import` makes a tarball of the uv cache, models and browsers.
  - **`researchly update`:** `uv tool upgrade researchly` plus pulling the matching images (Docker mode), then migrations. It checks GitHub Releases at most daily (opt-out).
  - **`researchly uninstall [--purge-data]`:** removes the tool, caches, autostart entries and PATH lines, and data only with `--purge-data`.
  - **Footprint (estimate, measured in step 9):** core ~0.6 GB, models ~1.5–2.5 GB, optional JDK + GROBID ~0.6 GB. First run downloads only the core; everything else is fetched on first use.

---

## 23. Development process & tooling

### 23.1 Workflow
- **Specs and plans:** this spec, then `superpowers:writing-plans` produces `docs/superpowers/plans/*.md`, with tasks of 2–5 minutes each.
- **Harness files:**
  - `docs/features.json`: F-IDs with E2E steps and `passes`. Agents may only flip `passes`; a hook guards the structure.
  - `docs/progress.md`: session log.
  - `docs/adr/`: decisions.
- **Session loop:**
  1. Read `progress.md`, `features.json` and the git log.
  2. Start the stack and run `@smoke`.
  3. Pick one task.
  4. Write the failing test, then the implementation (superpowers TDD), done by the **implementer** subagent in a worktree.
  5. The **spec-reviewer** checks the diff against the spec and plan.
  6. Run unit and integration tests, then the relevant E2E spec or journey.
  7. Commit (Conventional Commits).
  8. Update progress; flip `passes` only after the E2E test passes.
- **Principles in AGENTS.md** (Karpathy guidelines): think before coding, simplicity first, surgical changes, goal-driven execution. ponytail also enforces minimal code.

### 23.2 `.claude/` setup
- **AGENTS.md** (<200 lines, the source of truth): product summary, repo map, exact commands, conventions ("LLM calls only through `llm/`", "deepagents only imported in `agents/factory.py`", "all HTTP through `PoliteClient`"), gotchas, workflow contract, Karpathy principles, compact instructions.
- **CLAUDE.md:** `@AGENTS.md` plus Claude-specific notes.
- **Nested files:** `backend/AGENTS.md` and `frontend/AGENTS.md`. Path-scoped rules go in `.claude/rules/*.md` (connectors, migrations, agents, security, frontend-rendering).
- **Subagents** (`.claude/agents/`):

| Agent | Model | Tools | Purpose |
|---|---|---|---|
| architect | opus | Read, Grep, Glob, WebFetch | ADRs, design decisions, splitting work up (read-only) |
| spec-reviewer | opus | Read, Grep, Glob, Bash(read-only git) | Checks a diff against the spec/plan; cannot edit |
| debugger | opus | all | Root-cause analysis of failures and flaky tests |
| implementer | sonnet | all, `isolation: worktree` | One plan task, test-first |
| test-writer | sonnet | all | Unit and integration tests |
| user-simulator | sonnet | Read, Write(reports), Bash, Playwright MCP (inline) | Persona-driven exploratory UI sessions |
| ai-provider | sonnet (haiku for bulk) | Read, Write (`e2e/fixtures/llm/`), Bash | Curates LLM fixtures: drives recording sessions through the dev `claude-code` provider (§7.7), reviews recorded responses for realism, re-records stale fixtures after prompt changes |
| test-runner | haiku | Bash, Read | Runs suites and returns failures only |
| Explore (override) | haiku | Read, Grep, Glob | Cheap code search |

- **Project skills:** `run-stack`, `db-migration`, `agent-graph`, `add-scholarly-connector`, `e2e-scenario`, `user-sim-session`, and `release` (`disable-model-invocation: true`).
- **Plugins** (project scope): `pyright-lsp`, `typescript-lsp`, `security-guidance`, `pr-review-toolkit`, `claude-md-management`, `frontend-design`. Already installed: superpowers, ponytail, modern-web-guidance. The `data` plugin is disabled for this project.
- **Library skills:** a selective install of `langchain-ai/langchain-skills` (langgraph-fundamentals, langgraph-persistence, langgraph-human-in-the-loop, deep-agents-core, deep-agents-memory, deep-agents-orchestration, langchain-middleware), the shadcn/ui skill, and the playwright-cli skills.
- **MCP servers:** context7, the LangChain docs MCP (`https://docs.langchain.com/mcp`), and Playwright MCP only inside user-simulator. GitHub, Docker and Postgres are used through their CLIs (`gh`, `docker`, `psql`).
- **Hooks** (Python, for Windows compatibility):
  - PostToolUse `Edit|Write`: format (ruff / prettier).
  - PreToolUse: `ask` on `.github/workflows/*` and already-applied Alembic revisions; guards the structure of `features.json`.
  - Stop: typecheck changed packages, with a loop guard.
  - SessionStart `compact|resume`: inject `progress.md`, the current plan path and `git status`.
  - `permissions.deny`: `.env*` and lockfile edits.
- **Token settings:**
  - Per-agent `model:` frontmatter sets each subagent's model. `CLAUDE_CODE_SUBAGENT_MODEL` is not set, because Claude Code checks it before agent frontmatter, so it would override every agent's model.
  - Effort: medium by default, high for planning and debugging.
  - `autoCompactWindow` set to 300000.
  - A status line showing context %, cost and effort.
  - `/clear` between tasks, and noisy output routed to haiku agents.

---

## 24. Build order
There is a single release; the steps below are dependency order only. Each step ends with its F-IDs passing E2E.

1. **Foundation:**
   - Repo, `.claude/` setup, AGENTS/CLAUDE.md, CI skeleton, compose, Postgres image.
   - Alembic baseline, app skeleton, frontend shell.
   - LLM layer (registry, capabilities, ledger, RecordReplay, dev `claude-code` provider).
   - Localhost guard, keychain.
   - CLI skeleton (`setup`/`start`/`stop`/`doctor`) with runtime selection and engine auto-start.
   - **Platform spikes** (risks 1–3), each run on all five targets in CI and each producing an ADR:
     - pg0 Postgres (`vector`, `pg_trgm`, start time);
     - native SearXNG on Windows;
     - native sandbox per OS;
     - Docling-ONNX without torch;
     - ONNX embedder/reranker loading.
2. **Data:**
   - PoliteClient + http_cache, Tier-1 connectors, identity resolution.
   - Ingestion pipeline, embeddings, hybrid search + rerank.
   - Tier-2 and Tier-3 connectors.
3. **Agent core:**
   - Deep Agents factory; Lead, Scout and Reader (PaperQA2).
   - Ranking, budgets and limits, run manager, resume.
   - AG-UI streaming, chat UI, run tree, plan review, interrupts.
   - (F1, F2)
4. **Understanding:** Verifier pipeline, grounded Q&A, summaries, explanations, comparison, extraction tables, contradictions, PDF reader (F3, F4.1–F4.6).
5. **Graphs:** citation/concept extraction, graph API, Sigma views, field view, timeline, mind maps, in-answer visuals (F5).
6. **Memory:** bake-off (ADR), adapter, memory panel, playbook, procedural preferences (F8).
7. **Writing:** Writer + Critic, the long-form pipeline, documents and editor, notebooks + kernel sandbox, exports (F6).
8. **Studio & analysis:** gaps, trends and ideas with novelty check; audio, study guide, FAQ, flashcards, slides; systematic review + PRISMA (F4.7–F4.8, F7).
9. **Platform:** full installer (bash + PowerShell), native mode parity, onboarding wizard, backup/restore, update, usage dashboard, offline mode, MCP server and client, Zotero, saved searches and alerts, accessibility pass (F9, F10, F2.5, F3.5).
10. **Hardening:** full journey suite in both modes, user-simulator campaign, red-team fixes, performance budgets (§25), the eval suite at baseline, security review, release pipeline, flipping the repo public.

---

## 25. Non-functional requirements
- **Performance targets** on a reference laptop (8-core CPU, 16 GB RAM, no GPU):
  - App cold start under 10 s (Docker with warm images, and native).
  - Library search p95 under 300 ms at 100k papers / 1M chunks.
  - Graph view interactive (≥30 fps pan/zoom) at 20k nodes; field view at 100k nodes.
  - Embedding throughput ≥ 50 chunks/s on CPU.
  - First AG-UI event within 1 s of run start.
- **Reliability:**
  - A run survives app restart and network loss.
  - No data loss on crash: Postgres WAL, plus document autosave every 5 s.
- **Backup:** `researchly backup` runs `pg_dump` plus a tarball of the data directory (PDFs, fixtures excluded). `restore` is verified in E2E.
- **Privacy:** no telemetry by default. An opt-in, anonymous crash report can be added later only via an ADR.

---

## 26. Risks & spikes
| Risk | Mitigation | When |
|---|---|---|
| 1. Native Postgres (pg0 is a single-vendor project; `pg_trgm` presence unverified) | Step-1 spike on all 5 targets; conda-forge PG 18 + pgvector via pixi as fallback. (`pgserver` rejected: stale since 2024, PG 16. `pgserver-search` rejected: missing platforms) | Step 1 |
| 2. Native SearXNG on Windows (no official support) | Spike: app-private venv + granian. If it fails, Windows native runs without web search and `doctor` explains the trade-off; Docker mode always has it | Step 1 |
| 3. Native sandboxes (`sandbox-exec` deprecated but working; Ubuntu 24.04 AppArmor blocks bwrap user namespaces; Pyodide escape history) | Landlock fallback on Linux; fail closed; Deno permissions as the boundary for Pyodide; security tests per OS | Step 1, 10 |
| Keyless OpenAlex budget shared per IP (NAT) | Free singletons first, ≤50 searches/day, budget guard at 20%, other sources fill in, optional free key suggested | Step 2 |
| Container engine auto-start fails or needs elevation | Timeout → explain → native fallback for this session | Step 1 |
| Torch/CUDA bloat via Docling on Linux | ONNX-runtime models extra; a CI check that `torch` is absent from the native lock | Step 1 |
| LiteLLM supply chain | Exact pin with hashes, ≥14-day-old non-advisory release, upgrade PR checklist, no Docker image, telemetry off (§19.7) | Ongoing |
| Mem0 vs Hindsight quality | Bake-off with an ADR | Step 6 |
| Deep Agents churn | Pinned version, single factory module, upgrade ADR + full suite | Ongoing |
| Scholarly API limits/quotas | Per-host buckets below observed limits, circuit breakers, caching, quotas surfaced in UI, multiple keyless sources, optional free keys | Step 2 |
| Dev `claude-code` provider latency (seconds per call) | Use only for recording and exploration; CI uses replay | Step 1 |
| Multi-agent token cost (~15× chat) | Effort tiers, budgets, estimates, cheap-role routing, caching | Step 3 |
| Tauri/desktop not in scope | Browser UI only; revisit by ADR | — |
| Linux WebKitGTK WebGL issues | N/A (browser-based) | — |

---

## 27. Glossary
- **Evidence card:** `{claim, quote, chunk_id, paper_id, relevance}`, the only thing the Writer may cite.
- **Core blocks:** always-loaded memory (profile, project, procedural preferences, playbook head).
- **Effort tier:** Quick / Standard / Deep limits from §6.4.
- **Replay mode:** LLM and HTTP served from committed fixtures; a missing fixture fails the test.
