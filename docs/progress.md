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

### Carried into step 1b (must-do before dependent work)
- Task 0: make hooks worktree-aware (strip `.claude/worktrees/<name>/`, use that worktree as effective root) and verify with a real `isolation: worktree` implementer dispatch both CLAUDE_PROJECT_DIR and the worktree base branch (docs: defaults to the default branch, not session HEAD) — before any worktree implementer task.
- Ratify or remove backend/AGENTS.md conventions without spec basis (pyright strict, researchly.errors/RFC 9457, pytest-asyncio, PoliteClient at connectors/http.py).
- Add pyrightconfig (extraPaths for .claude/hooks and scripts) so the IDE resolves hook/test imports; pin pytest + uv version in CI once backend lockfile exists; setup-uv enable-cache per lockfile.
- Hook hardening backlog: wrap format/typecheck/session_context mains (exit 0 on error), subprocess timeouts, `git status -z` parsing, statusline null-field tolerance, Bash-matcher guard for control-plane paths when Bash allow rules are added.
- Spec §23.2 still says the data plugin "is disabled"; it is not (see below).

### User actions outstanding
- `claude update` (2.1.77 is old; then confirm `/hooks` lists 4 events and `/agents` lists 9 agents with opus/sonnet/haiku models).
- Disable the `data` plugin via `/plugin` (CLI couldn't find it).
- Authenticate context7 (`claude mcp list` shows "Needs authentication").
- Re-run `npm i -g pyright` once VS Code's pyright-langserver isn't holding the install dir (current install was repaired by manual copy).
