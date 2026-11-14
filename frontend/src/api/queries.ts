import { queryOptions } from "@tanstack/react-query"

import { api, ApiError } from "./client"
import type { components } from "./schema"

export type Project = components["schemas"]["ProjectOut"]
export type Thread = components["schemas"]["ThreadOut"]
export type ThreadDetail = components["schemas"]["ThreadDetail"]
export type Message = components["schemas"]["MessageOut"]
export type Run = components["schemas"]["RunOut"]
export type Effort = components["schemas"]["EffortOut"]
export type EffortName = components["schemas"]["RunIn"]["effort"]
export type Checkpoint = components["schemas"]["CheckpointOut"]

/** A run that has not reached rest: the UI keeps streaming and polling while it is in one of these. */
export const LIVE_STATUSES = ["pending", "running", "paused", "awaiting_user"]

export function unwrap<T>({
  data,
  response,
}: {
  data?: T
  response: Response
}): T {
  if (data === undefined) throw new ApiError(response.status)
  return data
}

export const healthQuery = queryOptions({
  queryKey: ["health"],
  queryFn: async () => unwrap(await api.GET("/health")),
  refetchInterval: 5_000,
})

export const systemQuery = queryOptions({
  queryKey: ["system"],
  queryFn: async () => unwrap(await api.GET("/api/v1/system")),
})

export const projectsQuery = queryOptions({
  queryKey: ["projects"],
  queryFn: async () => unwrap(await api.GET("/api/v1/projects")),
})

export const effortsQuery = queryOptions({
  queryKey: ["efforts"],
  queryFn: async () => unwrap(await api.GET("/api/v1/efforts")),
  staleTime: Infinity, // a static catalogue
})

export const threadsQuery = (projectId: string, q?: string) =>
  queryOptions({
    queryKey: ["threads", projectId, q ?? ""],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/projects/{project_id}/threads", {
          params: { path: { project_id: projectId }, query: q ? { q } : {} },
        })
      ),
  })

export const threadQuery = (threadId: string) =>
  queryOptions({
    queryKey: ["thread", threadId],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/threads/{thread_id}", {
          params: { path: { thread_id: threadId } },
        })
      ),
  })

export const runQuery = (runId: string | null) =>
  queryOptions({
    queryKey: ["run", runId],
    enabled: runId !== null,
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/runs/{run_id}", {
          params: { path: { run_id: runId! } },
        })
      ),
    // the stream carries the events; this keeps status, spend and the control buttons honest
    refetchInterval: ({ state }: { state: { data?: Run } }) =>
      state.data && LIVE_STATUSES.includes(state.data.status) ? 2_000 : false,
  })

export const checkpointsQuery = (runId: string) =>
  queryOptions({
    queryKey: ["checkpoints", runId],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/runs/{run_id}/checkpoints", {
          params: { path: { run_id: runId } },
        })
      ),
  })
