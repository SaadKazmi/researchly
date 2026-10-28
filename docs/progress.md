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
- Step 1a tool kit installed: plugins (pyright-lsp, typescript-lsp, security-guidance, pr-review-toolkit, claude-md-management, frontend-design), LangChain skills (7, MIT, langchain-skills@126c384), MCP docs-langchain; Claude Code 2.1.77. No `data` plugin exists in this CLI's config (nothing to disable).
- Step 1a complete: harness, hooks (fail-closed, UTF-8, control-plane guard), agents, plugins, CI repo-checks green on PR #1. Correction: the data plugin is still active (not found by CLI 2.1.77 config) — disable manually via /plugin. Next: docs/superpowers/plans/2026-10-28-step1b-backend-core.md
