import type { HttpAgent } from "@ag-ui/client"
import {
  createContext,
  useContext,
  useEffect,
  useReducer,
  type ReactNode,
} from "react"

/** Only the fields we read. Typing this structurally keeps @ag-ui/core out of our dependency list. */
export type AgUiEvent = { type: string } & Record<string, unknown>

export type NodeStatus = "running" | "done"
export type AgentNode = {
  id: string
  name: string
  status: NodeStatus
  startedAt: number
  endedAt: number | null
}
export type ToolNode = AgentNode & { agentId: string }
export type Todo = { content: string; status: string }
export type Cost = { spentUsd: number; budgetUsd: number; tokens: number }
export type Summary = {
  status: string
  spentUsd: number
  budgetUsd: number
  // found over estimated (Chao2, spec §10); null when nothing was found to estimate from
  coverage: number | null
}
export type ReviewAction = { name: string; text: string; decisions: string[] }
export type Review = { id: string; actions: ReviewAction[] }

export type RunState = {
  agents: AgentNode[]
  tools: ToolNode[]
  todos: Todo[]
  cost: Cost | null
  review: Review | null
  summary: Summary | null
  failed: boolean
  // when the last event arrived: a running node's elapsed time is measured to it
  now: number
}

export const LEAD = "lead"

export const EMPTY: RunState = {
  agents: [],
  tools: [],
  todos: [],
  cost: null,
  review: null,
  summary: null,
  failed: false,
  now: 0,
}

function num(value: unknown): number {
  return typeof value === "number" ? value : Number(value ?? 0)
}

/** The backend sends `{observed, estimated, lists}` (runs/summary.py), or null without a store. */
function share(value: unknown): number | null {
  const { observed, estimated } = (value ?? {}) as Record<string, unknown>
  return typeof observed === "number" &&
    typeof estimated === "number" &&
    estimated > 0
    ? observed / estimated
    : null
}

function started(id: string, name: string): AgentNode {
  return { id, name, status: "running", startedAt: Date.now(), endedAt: null }
}

function finish<T extends AgentNode>(nodes: T[], id: string): T[] {
  return nodes.map((n) =>
    n.id === id ? { ...n, status: "done" as const, endedAt: Date.now() } : n
  )
}

function closeAll<T extends AgentNode>(nodes: T[]): T[] {
  return nodes.map((n) =>
    n.status === "running"
      ? { ...n, status: "done" as const, endedAt: Date.now() }
      : n
  )
}

function withLead(agents: AgentNode[]): AgentNode[] {
  return agents.some((a) => a.id === LEAD)
    ? agents
    : [started(LEAD, LEAD), ...agents]
}

/** An HITL review (ADR 0012): its actions come from the raw interrupt, its decisions from the schema. */
function toReview(interrupt: Record<string, unknown>): Review {
  const schema = interrupt.responseSchema as
    | {
        properties?: {
          decisions?: {
            prefixItems?: { properties?: { type?: { enum?: string[] } } }[]
          }
        }
      }
    | undefined
  const items = schema?.properties?.decisions?.prefixItems ?? []
  const raw = (
    interrupt.metadata as
      { langgraph?: { raw?: { action_requests?: unknown[] } } } | undefined
  )?.langgraph?.raw?.action_requests
  const requests = (raw ?? []) as {
    name?: string
    args?: { plan?: string; question?: string }
  }[]
  const names = String(interrupt.reason ?? "")
    .replace(/^researchly:/, "")
    .split("+")
  return {
    id: String(interrupt.id),
    actions: items.map((item, index) => ({
      name: requests[index]?.name ?? names[index] ?? "",
      text:
        requests[index]?.args?.plan ??
        requests[index]?.args?.question ??
        String(interrupt.message ?? ""),
      decisions: item.properties?.type?.enum ?? [],
    })),
  }
}

export function reduce(state: RunState, event: AgUiEvent): RunState {
  const next = apply(state, event)
  return next === state ? state : { ...next, now: Date.now() }
}

function apply(state: RunState, event: AgUiEvent): RunState {
  switch (event.type) {
    case "RESET": // ours, not AG-UI's: a run the composer starts in this mount gets its own panel
      return EMPTY
    case "RUN_STARTED": // a new drive supersedes the previous one's outcome
      return {
        ...state,
        agents: withLead(state.agents),
        review: null,
        summary: null,
        failed: false,
      }
    case "SUBAGENT_STARTED": {
      const id = String(event.subagentRunId)
      if (state.agents.some((a) => a.id === id)) return state
      return {
        ...state,
        agents: [...withLead(state.agents), started(id, String(event.name))],
      }
    }
    case "SUBAGENT_FINISHED":
    case "SUBAGENT_ERROR":
      return {
        ...state,
        agents: finish(state.agents, String(event.subagentRunId)),
      }
    case "TOOL_CALL_START": {
      const id = String(event.toolCallId)
      if (state.tools.some((t) => t.id === id)) return state // a resume repeats its pending call
      const node = started(id, String(event.toolCallName))
      return {
        ...state,
        agents: withLead(state.agents),
        tools: [
          ...state.tools,
          { ...node, agentId: String(event.subagentRunId ?? LEAD) },
        ],
      }
    }
    case "TOOL_CALL_RESULT": // not TOOL_CALL_END, which only closes the streamed arguments
      return { ...state, tools: finish(state.tools, String(event.toolCallId)) }
    case "STATE_SNAPSHOT":
      return {
        ...state,
        todos: (event.snapshot as { todos?: Todo[] } | undefined)?.todos ?? [],
      }
    case "CUSTOM": {
      const value = (event.value ?? {}) as Record<string, unknown>
      if (event.name === "cost") {
        const cost = {
          spentUsd: num(value.spent_usd),
          budgetUsd: num(value.budget_usd),
          tokens: num(value.tokens),
        }
        return { ...state, cost }
      }
      if (event.name === "run_summary") {
        const summary = {
          status: String(value.status),
          spentUsd: num(value.spent_usd),
          budgetUsd: num(value.budget_usd),
          coverage: share(value.coverage),
        }
        // the run's true total: each cost event is sent before a model call, so it trails by one
        const cost = {
          spentUsd: summary.spentUsd,
          budgetUsd: summary.budgetUsd,
          tokens: num(value.tokens),
        }
        return { ...state, summary, cost }
      }
      return state
    }
    case "RUN_FINISHED": {
      const outcome = event.outcome as
        { type?: string; interrupts?: Record<string, unknown>[] } | undefined
      if (outcome?.type === "interrupt" && outcome.interrupts?.length)
        return { ...state, review: toReview(outcome.interrupts[0]) }
      // the drive is over: nothing in it is still running. A pause finishes it as cancelled and an
      // interrupt waits on the user, and both resume the same tree
      if (outcome?.type === "cancelled") return state
      return {
        ...state,
        agents: closeAll(state.agents),
        tools: closeAll(state.tools),
      }
    }
    case "RUN_ERROR":
      return { ...state, failed: true }
    default:
      return state
  }
}

const Context = createContext<RunState>(EMPTY)

export function useRunState(): RunState {
  return useContext(Context)
}

export function RunStateProvider({
  agent,
  children,
}: {
  agent: HttpAgent
  children: ReactNode
}) {
  const [state, dispatch] = useReducer(reduce, EMPTY)
  useEffect(() => {
    const subscription = agent.subscribe({
      // before the request goes out, so nothing of the new run can arrive first. A mount's first
      // attach starts EMPTY anyway; every later run in the same mount is a new question
      onRunInitialized: () => dispatch({ type: "RESET" }),
      onEvent: ({ event }: { event: unknown }) => dispatch(event as AgUiEvent),
    })
    return () => subscription.unsubscribe()
  }, [agent])
  return <Context.Provider value={state}>{children}</Context.Provider>
}
