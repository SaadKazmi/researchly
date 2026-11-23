import { useMutation } from "@tanstack/react-query"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import { api } from "@/api/client"
import { unwrap } from "@/api/queries"
import type { components } from "@/api/schema"
import { Button } from "@/components/ui/button"
import { type ReviewAction, useRunState } from "@/features/chat/run-state"

type Decision = NonNullable<
  components["schemas"]["ResumeIn"]["decisions"]
>[number]

/** The plan to approve, edit or reject (F1.3) and the questions to answer (F1.2) of a paused run. */
export function Review({
  runId,
  onAnswered,
}: {
  runId: string
  onAnswered: () => void
}) {
  const { t } = useTranslation()
  const { review } = useRunState()
  // one draft per action, keyed by its position: the plan starts from the agent's text, an answer starts empty
  const [drafts, setDrafts] = useState<Record<number, string>>({})
  // one decision per action: the backend refuses a resume that does not answer every one of them
  const [chosen, setChosen] = useState<Record<number, Decision>>({})

  const resume = useMutation({
    mutationFn: async (decisions: Decision[]) =>
      unwrap(
        await api.POST("/api/v1/runs/{run_id}/resume", {
          params: { path: { run_id: runId } },
          body: { decisions },
        })
      ),
    onSuccess: () => onAnswered(),
  })

  if (!review) return null
  const draft = (index: number, action: ReviewAction) =>
    drafts[index] ?? (action.decisions.includes("respond") ? "" : action.text)
  // the request goes out once every action has its decision, in the order of the actions
  const decide = (index: number, decision: Decision) => {
    const next = { ...chosen, [index]: decision }
    setChosen(next)
    const decisions = review.actions.map((_, i) => next[i])
    if (decisions.every((d): d is Decision => d !== undefined))
      resume.mutate(decisions)
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
        const allows = (type: string) => action.decisions.includes(type)
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
              {answering && (
                <Button
                  type="button"
                  size="sm"
                  disabled={!value.trim() || busy}
                  onClick={() =>
                    decide(index, { type: "respond", message: value })
                  }
                >
                  {t("review.send")}
                </Button>
              )}
              {allows("approve") && (
                <Button
                  type="button"
                  size="sm"
                  disabled={busy}
                  onClick={() => decide(index, { type: "approve" })}
                >
                  {t("review.approve")}
                </Button>
              )}
              {allows("edit") && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={value === action.text || busy}
                  onClick={() =>
                    decide(index, {
                      type: "edit",
                      edited_action: {
                        name: action.name,
                        args: { plan: value },
                      },
                    })
                  }
                >
                  {t("review.approveEdits")}
                </Button>
              )}
              {allows("reject") && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() => decide(index, { type: "reject" })}
                >
                  {t("review.reject")}
                </Button>
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
