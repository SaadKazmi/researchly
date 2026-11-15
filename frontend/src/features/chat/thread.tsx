import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  ExportedMessageRepository,
  MessagePrimitive,
  ThreadPrimitive,
} from "@assistant-ui/react"
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui"
import { useQuery } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import {
  type Effort,
  type EffortName,
  effortsQuery,
  LIVE_STATUSES,
  runQuery,
  type ThreadDetail,
  threadQuery,
} from "@/api/queries"
import { Button } from "@/components/ui/button"
import { createAgent, newRunRef } from "@/features/chat/agent"
import { RunStateProvider } from "@/features/chat/run-state"

const DEFAULT_THRESHOLD = 1

/**
 * Above this many dollars a run asks before it starts (F1.7).
 * ponytail: one localStorage number, no server round-trip. It moves into Settings with F9.
 */
function threshold(): number {
  const stored = Number(localStorage.getItem("researchly.budget-threshold"))
  return Number.isFinite(stored) && stored > 0 ? stored : DEFAULT_THRESHOLD
}

const dollars = (value: string | number) => `$${Number(value).toFixed(2)}`

export function ChatThread({
  threadId,
}: {
  projectId: string
  threadId: string
}) {
  const { t } = useTranslation()
  const { data: thread } = useQuery(threadQuery(threadId))
  const last = thread?.messages.at(-1)
  // only a trailing user message can belong to a run that has not answered yet
  const candidate = last?.role === "user" ? (last.run_id ?? null) : null
  const { data: run, isPending: runPending } = useQuery(runQuery(candidate))

  // `runQuery(null)` is disabled, and a disabled query stays pending forever, so the run only
  // gets a say when there is a candidate to fetch
  if (!thread || (candidate !== null && runPending))
    return <p>{t("chat.loadingThread")}</p>
  const live = run && LIVE_STATUSES.includes(run.status) ? run.id : null
  return <Runtime key={threadId} thread={thread} live={live} />
}

function Runtime({
  thread,
  live,
}: {
  thread: ThreadDetail
  live: string | null
}) {
  const { t } = useTranslation()
  const { data: efforts = [] } = useQuery(effortsQuery)
  const [effort, setEffort] = useState<EffortName>("standard")
  // one ref and one agent per mounted thread; the key above remounts them when the thread changes
  const [ref] = useState(() => {
    const created = newRunRef(thread.messages, effort)
    created.runId = live // a known live run is attached to, not created again
    return created
  })
  const [agent] = useState(() => createAgent(thread.id, thread.messages, ref))
  // the ref starts on the same tier the state does and the two move together from here. A RunRef
  // is a mutable box the fetch adapter also writes to (ADR 0012), which the compiler's
  // immutability rule cannot express; the write is in a handler, never during a render.
  const chooseEffort = (next: EffortName) => {
    // eslint-disable-next-line react-hooks/immutability -- RunRef is mutable by contract
    ref.effort = next
    setEffort(next)
  }
  // the agent's `initialMessages` only shape what the next run sends; the transcript on screen is
  // hydrated through the history adapter, the one seam the runtime reads on mount
  const [history] = useState(() => ({
    load: async () => ({
      ...ExportedMessageRepository.fromArray(
        // the same two roles `toAgentMessages` keeps: the API types `role` as a plain string
        thread.messages.map((message) => ({
          id: message.id,
          role:
            message.role === "user"
              ? ("user" as const)
              : ("assistant" as const),
          content: message.text,
        }))
      ),
      // a run still going gets picked up where it left off instead of asked again (ruling 9). The
      // runtime has to drive it: events from `agent.runAgent()` never reach the transcript.
      unstable_resume: live !== null,
    }),
    append: async () => {}, // the backend stores every message as the run produces it
  }))
  const runtime = useAgUiRuntime({ agent, adapters: { history } })

  const selected = efforts.find((option) => option.name === effort)
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <RunStateProvider agent={agent}>
        <section
          aria-label={t("chat.conversation")}
          className="flex min-w-0 flex-1 flex-col gap-3"
        >
          <ThreadPrimitive.Root className="flex min-h-0 flex-1 flex-col">
            <ThreadPrimitive.Viewport className="flex-1 overflow-y-auto">
              <ThreadPrimitive.Messages components={{ Message: Bubble }} />
            </ThreadPrimitive.Viewport>
            <Composer
              effort={effort}
              efforts={efforts}
              selected={selected}
              onEffort={chooseEffort}
            />
          </ThreadPrimitive.Root>
        </section>
      </RunStateProvider>
    </AssistantRuntimeProvider>
  )
}

function Bubble() {
  return (
    <MessagePrimitive.Root className="mb-3 text-sm whitespace-pre-wrap">
      <MessagePrimitive.Parts />
    </MessagePrimitive.Root>
  )
}

function Composer({
  effort,
  efforts,
  selected,
  onEffort,
}: {
  effort: EffortName
  efforts: Effort[]
  selected: Effort | undefined
  onEffort: (effort: EffortName) => void
}) {
  const { t } = useTranslation()
  const [pending, setPending] = useState<HTMLFormElement | null>(null)
  const dialog = useRef<HTMLDialogElement>(null)
  const over =
    selected !== undefined && Number(selected.budget_usd) > threshold()

  useEffect(() => {
    if (pending) dialog.current?.showModal()
    else dialog.current?.close()
  }, [pending])

  const confirm = () => {
    const form = pending
    setPending(null)
    form?.requestSubmit() // the guard lets it through: `pending` is null again
  }

  return (
    <div
      onSubmitCapture={(event) => {
        if (!over || pending) return
        event.preventDefault() // capture runs before the composer's own submit
        event.stopPropagation()
        setPending(event.target as HTMLFormElement)
      }}
    >
      <ComposerPrimitive.Root className="flex flex-col gap-2 border-t pt-3">
        <ComposerPrimitive.Input
          aria-label={t("chat.ask")}
          placeholder={t("chat.ask")}
          className="min-h-16 w-full resize-none rounded-md border p-2 text-sm"
        />
        <div className="flex items-center gap-3">
          {/* no tiers, nothing to choose between: the selector waits for the catalogue */}
          {efforts.length > 0 && (
            <label className="text-sm">
              <span className="sr-only">{t("chat.effort")}</span>
              <select
                aria-label={t("chat.effort")}
                value={effort}
                onChange={(event) => onEffort(event.target.value as EffortName)}
                className="h-8 rounded-md border px-2 text-sm"
              >
                {efforts.map((option) => (
                  <option key={option.name} value={option.name}>
                    {t(`chat.efforts.${option.name}`)}
                  </option>
                ))}
              </select>
            </label>
          )}
          {selected && (
            <p className="text-sm text-muted-foreground">
              {t("chat.estimate", {
                low: dollars(selected.estimate_low_usd),
                high: dollars(selected.estimate_high_usd),
                budget: dollars(selected.budget_usd),
              })}
            </p>
          )}
          {/* our own submit button, not ComposerPrimitive.Send: that one is a type="button" that
              calls send() straight off its own click, so the budget guard below would never see
              it. A submit puts the click and the Enter key on the one path the guard watches. */}
          <button
            type="submit"
            className="ml-auto h-8 rounded-md bg-primary px-3 text-sm text-primary-foreground"
          >
            {t("chat.send")}
          </button>
        </div>
      </ComposerPrimitive.Root>
      <dialog
        ref={dialog}
        aria-label={t("chat.confirmTitle")}
        className="rounded-md border p-4"
      >
        <p className="text-sm">
          {t("chat.confirmBody", {
            effort: t(`chat.efforts.${effort}`),
            budget: dollars(selected?.budget_usd ?? 0),
          })}
        </p>
        <div className="mt-3 flex justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setPending(null)}
          >
            {t("chat.cancel")}
          </Button>
          <Button type="button" size="sm" onClick={confirm}>
            {t("chat.confirm")}
          </Button>
        </div>
      </dialog>
    </div>
  )
}
