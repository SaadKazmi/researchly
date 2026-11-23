import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import { api } from "@/api/client"
import { LIVE_STATUSES, runQuery, unwrap } from "@/api/queries"
import type { components } from "@/api/schema"
import { Button } from "@/components/ui/button"
import { type ReviewAction, useRunState } from "@/features/chat/run-state"

type Decision = NonNullable<
  components["schemas"]["ResumeIn"]["decisions"]
>[number]
type DecisionType = Decision["type"]

// in the order the buttons show; the first two are the primary answer
const BUTTONS: [DecisionType, string][] = [
  ["respond", "review.send"],
  ["approve", "review.approve"],
  ["edit", "review.approveEdits"],
  ["reject", "review.reject"],
]

/**
 * The review waiting on the user, if any. It comes only from the stream, which replays it on every
 * re-attach, so a run that has ended (a reject, a cancel) closes it. A paused run still waits on it,
 * and a run not read yet is given the benefit of the doubt.
 */
export function useOpenReview(runId: string | null) {
  const { review } = useRunState()
  const { data: run } = useQuery(runQuery(runId))
  return run === undefined || LIVE_STATUSES.includes(run.status) ? review : null
}

/** The plan to approve, edit or reject (F1.3) and the questions to answer (F1.2) of a paused run. */
export function Review({
  runId,
  onAnswered,
}: {
  runId: string
  onAnswered: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const review = useOpenReview(runId)
  // one draft per action, keyed by its position: the plan starts from the agent's text, an answer starts empty
  const [drafts, setDrafts] = useState<Record<number, string>>({})
  // one decision per action: the backend refuses a resume that does not answer every one of them
  const [chosen, setChosen] = useState<Record<number, DecisionType>>({})

  const resume = useMutation({
    mutationFn: async (decisions: Decision[]) =>
      unwrap(
        await api.POST("/api/v1/runs/{run_id}/resume", {
          params: { path: { run_id: runId } },
          body: { decisions },
        })
      ),
    onSuccess: (updated) => {
      // a reject cancels the run: the cached status closes the review, and there is nothing to follow
      queryClient.setQueryData(runQuery(runId).queryKey, updated)
      if (updated.status === "running") onAnswered()
    },
  })

  if (!review) return null
  const draft = (index: number, action: ReviewAction) =>
    drafts[index] ?? (action.decisions.includes("respond") ? "" : action.text)
  const payload = (index: number, type: DecisionType): Decision => {
    const action = review.actions[index]!
    if (type === "respond") return { type, message: draft(index, action) }
    if (type === "edit")
      return {
        type,
        edited_action: {
          name: action.name,
          args: { plan: draft(index, action) },
        },
      }
    return { type }
  }
  // the request goes out once every action has its decision, in the order of the actions, each
  // built from its draft as it stands now; an answer cleared since it was given holds it back
  const decide = (index: number, type: DecisionType) => {
    const next = { ...chosen, [index]: type }
    setChosen(next)
    const ready = (action: ReviewAction, i: number) =>
      next[i] !== undefined &&
      (next[i] !== "respond" || draft(i, action).trim() !== "")
    if (review.actions.every(ready))
      resume.mutate(review.actions.map((_, i) => payload(i, next[i]!)))
  }
  // a successful answer remounts this panel; until then nothing else may go out
  const busy = resume.isPending || resume.isSuccess

  return (
    <section
      aria-label={t("review.panel")}
      className="flex flex-col gap-2 rounded-md border p-3"
    >
      {review.actions.map((action, index) => {
        const value = draft(index, action)
        const answering = action.decisions.includes("respond")
        return (
          <div key={index} className="flex flex-col gap-2">
            <h2 className="text-sm font-medium">
              {answering ? t("review.question") : t("review.plan")}
            </h2>
            {answering && <p className="text-sm">{action.text}</p>}
            <textarea
              aria-label={
                answering ? t("review.answer") : t("review.planLabel")
              }
              value={value}
              onChange={(event) =>
                setDrafts({ ...drafts, [index]: event.target.value })
              }
              className="min-h-24 w-full rounded-md border p-2 text-sm"
            />
            <div className="flex flex-wrap gap-2">
              {BUTTONS.filter(([type]) => action.decisions.includes(type)).map(
                ([type, label]) => (
                  <Button
                    key={type}
                    type="button"
                    size="sm"
                    variant={
                      type === "respond" || type === "approve"
                        ? "default"
                        : "outline"
                    }
                    // with several actions, a decision waits for the others: show which one it is
                    aria-pressed={chosen[index] === type}
                    disabled={
                      busy ||
                      (type === "respond" && !value.trim()) ||
                      (type === "edit" && value === action.text)
                    }
                    onClick={() => decide(index, type)}
                  >
                    {t(label)}
                  </Button>
                )
              )}
            </div>
          </div>
        )
      })}
      {/* the shell owns the page's one status and one alert role: this is a polite live region */}
      <p aria-live="polite" className="text-sm empty:hidden">
        {resume.isError ? t("review.failed") : ""}
      </p>
    </section>
  )
}
