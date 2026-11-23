import { expect, test } from "vitest"

import { EMPTY, reduce, type RunState } from "@/features/chat/run-state"

import { frames, untilInterrupt } from "./sse"

function play(list = frames()): RunState {
  return list.reduce(
    (state, frame) => reduce(state, JSON.parse(frame.data)),
    EMPTY
  )
}

test("the plan interrupt becomes a review with its allowed decisions", () => {
  const state = play(untilInterrupt())
  expect(state.review?.actions).toEqual([
    {
      name: "submit_plan",
      text: expect.stringContaining("1. How does retrieval augmentation"),
      decisions: ["approve", "edit", "reject"],
    },
  ])
  expect(state.todos.map((todo) => todo.status)).toEqual([
    "completed",
    "pending",
  ])
  expect(state.cost).toEqual({ spentUsd: 0.012, budgetUsd: 1, tokens: 1840 })
  expect(state.summary).toBeNull()
})

test("the whole run builds a tree of the lead, its subagent and their tool calls", () => {
  const state = play()
  expect(state.review).toBeNull() // the resumed drive supersedes the answered interrupt
  expect(state.agents.map((a) => [a.name, a.status])).toEqual([
    ["lead", "done"], // the run finished: nothing in it is still running
    ["scout", "done"],
  ])
  expect(state.tools.map((t) => [t.name, t.agentId, t.status])).toEqual([
    ["submit_plan", "lead", "done"],
    ["search_papers", "sub-1", "done"],
  ])
  expect(state.summary).toEqual({
    status: "completed",
    spentUsd: 0.86,
    budgetUsd: 1,
    coverage: 22 / 30.1, // observed over the Chao2 estimate
  })
  expect(state.cost?.tokens).toBe(42100)
  expect(state.failed).toBe(false)
})

test("a repeated tool call across a resume does not duplicate its node", () => {
  const start = {
    type: "TOOL_CALL_START",
    toolCallId: "call-plan",
    toolCallName: "submit_plan",
  }
  const state = [
    start,
    { type: "TOOL_CALL_END", toolCallId: "call-plan" },
    start,
  ].reduce(reduce, EMPTY)
  expect(state.tools).toHaveLength(1)
})

test("a run error is remembered without its message", () => {
  const state = reduce(EMPTY, { type: "RUN_ERROR", message: "The run failed." })
  expect(state.failed).toBe(true)
  expect(JSON.stringify(state)).not.toContain("The run failed.")
})

test("a failed subagent stops spinning in the tree", () => {
  const started = {
    type: "SUBAGENT_STARTED",
    subagentRunId: "sub-9",
    name: "scout",
  }
  const state = [
    started,
    { type: "SUBAGENT_ERROR", subagentRunId: "sub-9" },
  ].reduce(reduce, EMPTY)
  expect(state.agents.find((a) => a.id === "sub-9")?.status).toBe("done")
})

test("resuming a run clears the previous drive's outcome, not just its review", () => {
  // the whole point of RUN_STARTED: a resumed drive supersedes whatever the last one ended with.
  // Without this, a run that errored or finished once keeps showing that verdict while it runs on,
  // so a resumed run renders as already failed and its stale summary sits under a live tree.
  const ended = [
    { type: "RUN_ERROR", message: "The run failed." },
    {
      type: "CUSTOM",
      name: "run_summary",
      value: {
        status: "failed",
        spent_usd: 0.4,
        budget_usd: 1.0,
        coverage: { observed: 1, estimated: 10, lists: 2 },
      },
    },
  ].reduce(reduce, EMPTY)
  expect(ended.failed).toBe(true)
  expect(ended.summary).not.toBeNull()

  const resumed = reduce(ended, { type: "RUN_STARTED", threadId: "thread-1" })
  expect(resumed.failed).toBe(false)
  expect(resumed.summary).toBeNull()
})

test("a tool runs until its result arrives, not until its arguments end", () => {
  // TOOL_CALL_END only closes the streamed arguments; the tool itself finishes at TOOL_CALL_RESULT
  const start = {
    type: "TOOL_CALL_START",
    toolCallId: "call-search",
    toolCallName: "search_papers",
  }
  const argued = [
    start,
    { type: "TOOL_CALL_END", toolCallId: "call-search" },
  ].reduce(reduce, EMPTY)
  expect(argued.tools[0]?.status).toBe("running")
  const answered = reduce(argued, {
    type: "TOOL_CALL_RESULT",
    toolCallId: "call-search",
    content: "added 12 candidates",
  })
  expect(answered.tools[0]?.status).toBe("done")
})

test("a finished run closes its tools as well as its agents", () => {
  const state = [
    { type: "TOOL_CALL_START", toolCallId: "c1", toolCallName: "note" },
    { type: "RUN_FINISHED", outcome: { type: "success" } },
  ].reduce(reduce, EMPTY)
  expect(state.tools[0]?.status).toBe("done")
})

test("a paused run leaves its tool running, to resume the same tree", () => {
  const state = [
    { type: "TOOL_CALL_START", toolCallId: "c1", toolCallName: "note" },
    { type: "RUN_FINISHED", outcome: { type: "cancelled" } },
  ].reduce(reduce, EMPTY)
  expect(state.tools[0]?.status).toBe("running")
})
