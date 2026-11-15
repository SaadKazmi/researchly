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
  coverage: number
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
}

function num(value: unknown): number {
  return typeof value === "number" ? value : Number(value ?? 0)
}

function started(id: string, name: string): AgentNode {
  return { id, name, status: "running", startedAt: Date.now(), endedAt: null }
}

function finish<T extends AgentNode>(nodes: T[], id: string): T[] {
  return nodes.map((n) =>
    n.id === id ? { ...n, status: "done" as const, endedAt: Date.now() } : n
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
  switch (event.type) {
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
    case "TOOL_CALL_END":
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
          coverage: num(value.coverage),
        }
        return { ...state, summary }
      }
      return state
    }
    case "RUN_FINISHED": {
      const outcome = event.outcome as
        { type?: string; interrupts?: Record<string, unknown>[] } | undefined
      if (outcome?.type !== "interrupt" || !outcome.interrupts?.length)
        return state
      return { ...state, review: toReview(outcome.interrupts[0]) }
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
      onEvent: ({ event }: { event: unknown }) => dispatch(event as AgUiEvent),
    })
    return () => subscription.unsubscribe()
  }, [agent])
  return <Context.Provider value={state}>{children}</Context.Provider>
}
