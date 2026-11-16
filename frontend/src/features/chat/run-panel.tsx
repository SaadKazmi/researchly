import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"

import { api } from "@/api/client"
import {
  checkpointsQuery,
  LIVE_STATUSES,
  runQuery,
  unwrap,
} from "@/api/queries"
import { Button } from "@/components/ui/button"
import { useRunState } from "@/features/chat/run-state"

const WRAP_UP = 0.8 // the budget guard injects its wrap-up instruction here (spec §6.4)
const dollars = (value: string | number) => `$${Number(value).toFixed(2)}`

export function RunPanel({
  runId,
  onReattach,
}: {
  runId: string
  onReattach: (runId?: string) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const state = useRunState()
  const { data: run } = useQuery(runQuery(runId))
  const [branching, setBranching] = useState(false)
  const [checkpoint, setCheckpoint] = useState("")

  const control = useMutation({
    // the three endpoints share a path shape and take no body; the cast names one of them for the types
    mutationFn: async (action: "pause" | "resume" | "cancel") =>
      unwrap(
        await api.POST(
          `/api/v1/runs/{run_id}/${action}` as "/api/v1/runs/{run_id}/pause",
          {
            params: { path: { run_id: runId } },
          }
        )
      ),
    onSuccess: async (updated) => {
      await queryClient.invalidateQueries({ queryKey: ["run", runId] })
      if (updated.status === "running") onReattach() // a resume opens a new AG-UI run on the same stream
    },
  })

  const branch = useMutation({
    mutationFn: async (checkpointId: string) =>
      unwrap(
        await api.POST("/api/v1/runs/{run_id}/branch", {
          params: { path: { run_id: runId } },
          body: { checkpoint_id: checkpointId },
        })
      ),
    onSuccess: (child) => {
      setBranching(false)
      onReattach(child.id)
    },
  })

  const live = run !== undefined && LIVE_STATUSES.includes(run.status)
  // the transcript was hydrated before the run's answer was stored: once the run is over, refetch it
  const ended = state.summary !== null || (run !== undefined && !live)
  const threadId = run?.thread_id
  useEffect(() => {
    if (ended && threadId)
      void queryClient.invalidateQueries({ queryKey: ["thread", threadId] })
  }, [ended, threadId, queryClient])

  const cost =
    state.cost ??
    (run
      ? {
          spentUsd: Number(run.spent_usd),
          budgetUsd: Number(run.budget_usd),
          tokens: 0,
        }
      : null)
  const share = cost && cost.budgetUsd > 0 ? cost.spentUsd / cost.budgetUsd : 0
  const notice =
    share >= 1 ? t("run.stopped") : share >= WRAP_UP ? t("run.wrapUp") : ""

  return (
    <aside
      aria-label={t("run.panel")}
      className="flex w-72 shrink-0 flex-col gap-4 border-l pl-4"
    >
      {cost && (
        <section className="flex flex-col gap-1">
          <progress
            aria-label={t("run.budget")}
            value={cost.spentUsd}
            max={cost.budgetUsd}
            className="w-full"
          />
          <p className="text-sm">
            {t("run.spent", {
              spent: dollars(cost.spentUsd),
              budget: dollars(cost.budgetUsd),
            })}
          </p>
          {cost.tokens > 0 && (
            <p className="text-xs text-muted-foreground">
              {t("run.tokens", { tokens: cost.tokens })}
            </p>
          )}
        </section>
      )}
      {/* the shell owns the page's one status and one alert role: these are polite live regions,
          always mounted so a screen reader hears the text arrive */}
      <p aria-live="polite" className="text-sm empty:hidden">
        {notice}
      </p>

      {state.summary && (
        <p className="text-sm font-medium">
          {t("run.summary", {
            status: state.summary.status,
            spent: dollars(state.summary.spentUsd),
            coverage: Math.round(state.summary.coverage * 100),
          })}
        </p>
      )}
      <p aria-live="polite" className="text-sm empty:hidden">
        {state.failed ? t("run.failed") : ""}
      </p>

      <section className="flex flex-col gap-1">
        <h2 className="text-sm font-medium">{t("run.activity")}</h2>
        <ul
          aria-label={t("run.activity")}
          className="flex flex-col gap-1 text-sm"
        >
          {state.agents.map((agent) => (
            <li key={agent.id}>
              <span className={agent.status === "running" ? "font-medium" : ""}>
                {agent.name}
              </span>
              <span className="sr-only">
                {" "}
                {t(`run.status.${agent.status}`)}
              </span>
              <ul className="ml-4 flex flex-col text-muted-foreground">
                {state.tools
                  .filter((tool) => tool.agentId === agent.id)
                  .map((tool) => (
                    <li key={tool.id}>
                      {tool.name}
                      <span className="sr-only">
                        {" "}
                        {t(`run.status.${tool.status}`)}
                      </span>
                    </li>
                  ))}
              </ul>
            </li>
          ))}
        </ul>
      </section>

      {state.todos.length > 0 && (
        <section className="flex flex-col gap-1">
          <h2 className="text-sm font-medium">{t("run.todos")}</h2>
          <ul
            aria-label={t("run.todos")}
            className="flex flex-col gap-1 text-sm"
          >
            {state.todos.map((todo) => (
              <li
                key={todo.content}
                className={
                  todo.status === "completed"
                    ? "text-muted-foreground line-through"
                    : ""
                }
              >
                {todo.content}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="flex flex-wrap gap-2">
        {run?.status === "running" && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={control.isPending}
            onClick={() => control.mutate("pause")}
          >
            {t("run.pause")}
          </Button>
        )}
        {run?.status === "paused" && (
          <Button
            type="button"
            size="sm"
            disabled={control.isPending}
            onClick={() => control.mutate("resume")}
          >
            {t("run.resume")}
          </Button>
        )}
        {live && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={control.isPending}
            onClick={() => control.mutate("cancel")}
          >
            {t("run.cancel")}
          </Button>
        )}
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => setBranching((open) => !open)}
        >
          {t("run.branch")}
        </Button>
      </section>
      <p aria-live="polite" className="text-sm empty:hidden">
        {control.isError || branch.isError ? t("run.actionFailed") : ""}
      </p>

      {branching && (
        <BranchPicker
          runId={runId}
          value={checkpoint}
          pending={branch.isPending}
          onChange={setCheckpoint}
          onBranch={branch.mutate}
        />
      )}
    </aside>
  )
}

function BranchPicker({
  runId,
  value,
  pending,
  onChange,
  onBranch,
}: {
  runId: string
  value: string
  pending: boolean
  onChange: (checkpointId: string) => void
  onBranch: (checkpointId: string) => void
}) {
  const { t } = useTranslation()
  const { data: checkpoints = [] } = useQuery(checkpointsQuery(runId))
  const chosen = value || checkpoints[0]?.checkpoint_id || ""
  return (
    <section className="flex flex-col gap-2">
      <label className="text-sm">
        <span className="sr-only">{t("run.checkpoint")}</span>
        <select
          aria-label={t("run.checkpoint")}
          value={chosen}
          onChange={(event) => onChange(event.target.value)}
          className="h-8 w-full rounded-md border px-2 text-sm"
        >
          {checkpoints.map((entry) => (
            <option key={entry.checkpoint_id} value={entry.checkpoint_id}>
              {/* the saver may not record either: show what it has, the id when it has neither */}
              {[
                entry.created_at &&
                  new Date(entry.created_at).toLocaleTimeString(),
                entry.step !== null && t("run.step", { step: entry.step }),
              ]
                .filter(Boolean)
                .join(" · ") || entry.checkpoint_id}
            </option>
          ))}
        </select>
      </label>
      <Button
        type="button"
        size="sm"
        disabled={!chosen || pending}
        onClick={() => onBranch(chosen)}
      >
        {t("run.branchFrom")}
      </Button>
    </section>
  )
}
