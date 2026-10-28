@AGENTS.md

# Claude Code specifics
- Process skills: use superpowers (brainstorming → writing-plans → subagent-driven-development, TDD, systematic-debugging, verification-before-completion). ponytail guards against over-engineering.
- Subagents live in `.claude/agents/`: architect/spec-reviewer/debugger (opus), implementer/test-writer/user-simulator/ai-provider (sonnet), test-runner/Explore (haiku). Route noisy test output through `test-runner`.
- Library docs: prefer the `docs-langchain` MCP for LangGraph/Deep Agents/LangChain and context7 for other libraries; verify APIs before using them.
- Browser checks: `playwright-cli` in the main thread; Playwright MCP only inside `user-simulator`.
- Use `/clear` between unrelated tasks; plans and progress live on disk, not in the conversation.
