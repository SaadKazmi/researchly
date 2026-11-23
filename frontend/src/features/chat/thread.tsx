import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  ExportedMessageRepository,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
} from "@assistant-ui/react"
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui"
import { useQuery, useQueryClient } from "@tanstack/react-query"
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
import { Review, useOpenReview } from "@/features/chat/review"
import { RunPanel } from "@/features/chat/run-panel"
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

export function ChatThread({ threadId }: { threadId: string }) {
  return <ThreadView key={threadId} threadId={threadId} />
}

function ThreadView({ threadId }: { threadId: string }) {
  const { t } = useTranslation()
  // the runtime reads the transcript once, on mount, so it must not start from a cached copy: a
  // run may have answered since the thread was last open
  const {
    data: thread,
    isError,
    isFetchedAfterMount,
    dataUpdatedAt: threadAt,
    refetch,
  } = useQuery({ ...threadQuery(threadId), refetchOnMount: "always" })
  // the thread names its newest run while that run is live: a branch adds no message, so the
  // trailing question can name a superseded parent
  const candidate = thread?.live_run_id ?? null
  const {
    data: run,
    isPending: runPending,
    dataUpdatedAt: runAt,
  } = useQuery(runQuery(candidate))
  // a run that had already ended when it was read may have stored its answer after the thread was
  // read: read the thread once more. It cannot loop: the reread is newer, and an ended run stops polling
  const stale =
    run !== undefined &&
    !LIVE_STATUSES.includes(run.status) &&
    runAt >= threadAt
  // the gates hold only until the conversation opens: a later refetch that fails, or that moves
  // `candidate`, must never unmount a live runtime
  const [opened, setOpened] = useState(false)
  useEffect(() => {
    if (stale && !opened) void refetch()
  }, [stale, opened, refetch])

  // `runQuery(null)` is disabled, and a disabled query stays pending forever, so the run only
  // gets a say when there is a candidate to fetch
  const ready =
    thread !== undefined &&
    isFetchedAfterMount &&
    !(candidate !== null && runPending) &&
    !(stale && !isError) // a failed reread opens on the copy we have
  if (ready && !opened) setOpened(true)
  if (thread && (opened || ready)) {
    // read once, on the Attachment's mount
    const live = run && LIVE_STATUSES.includes(run.status) ? run.id : null
    return <Attachment thread={thread} live={live} />
  }
  if (isError && !thread) return <p>{t("chat.threadError")}</p>
  return <p>{t("chat.loadingThread")}</p>
}

/**
 * The run the panel follows, and the one way to re-attach the transcript to a run: remount the
 * runtime with that run as its live one, so its history adapter resumes the stream on mount. Events
 * from `agent.runAgent()` never reach the transcript.
 */
function Attachment({
  thread: initial,
  live,
}: {
  thread: ThreadDetail
  live: string | null
}) {
  const queryClient = useQueryClient()
  const [view, setView] = useState({
    thread: initial,
    runId: live,
    generation: 0,
  })
  const reattach = (next?: string) => {
    // the remount hydrates from this: the copy the view opened with lacks every message sent since
    void queryClient
      .fetchQuery({ ...threadQuery(initial.id), staleTime: 0 })
      .catch(() => undefined) // re-attach on the copy we have rather than not at all
      .then((thread) =>
        setView((current) => ({
          thread: thread ?? current.thread,
          runId: next ?? current.runId,
          generation: current.generation + 1,
        }))
      )
  }
  const onRun = (id: string) =>
    setView((current) => ({ ...current, runId: id }))
  // keyed on the generation alone: a run the composer starts moves `runId` (through `onRun`) while
  // the mounted runtime already streams it, so `runId` in the key would remount it mid-stream
  return (
    <Runtime
      key={view.generation}
      thread={view.thread}
      runId={view.runId}
      onRun={onRun}
      onReattach={reattach}
    />
  )
}

function Runtime({
  thread,
  runId,
  onRun,
  onReattach,
}: {
  thread: ThreadDetail
  /** The run the panel shows. On mount, it is also the live run the transcript attaches to. */
  runId: string | null
  onRun: (id: string) => void
  onReattach: (runId?: string) => void
}) {
  const { t } = useTranslation()
  const { data: efforts = [] } = useQuery(effortsQuery)
  const [effort, setEffort] = useState<EffortName>("standard")
  // one ref and one agent per mount; the keys above remount them when the thread changes or a run
  // is re-attached
  const [ref] = useState(() => {
    const created = newRunRef(thread.messages, effort)
    created.runId = runId // a known live run is attached to, not created again
    created.onRun = onRun // a run the composer starts is reported up, so the panel follows it
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
      unstable_resume: runId !== null,
    }),
    append: async () => {}, // the backend stores every message as the run produces it
  }))
  const runtime = useAgUiRuntime({ agent, adapters: { history } })

  const selected = efforts.find((option) => option.name === effort)
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <RunStateProvider agent={agent}>
        <div className="flex min-w-0 flex-1 gap-4">
          <section
            aria-label={t("chat.conversation")}
            className="flex min-w-0 flex-1 flex-col gap-3"
          >
            <ThreadPrimitive.Root className="flex min-h-0 flex-1 flex-col">
              <ThreadPrimitive.Viewport className="flex-1 overflow-y-auto">
                <ThreadPrimitive.Messages components={{ Message: Bubble }} />
              </ThreadPrimitive.Viewport>
              {/* an answer re-reads the thread and remounts on the same run, whose stream carries the resumed drive */}
              {runId && (
                <Review
                  key={runId} // a run the composer starts gets a fresh panel, not one already answered
                  runId={runId}
                  onAnswered={() => onReattach()}
                />
              )}
              <Composer
                effort={effort}
                efforts={efforts}
                selected={selected}
                runId={runId}
                onEffort={chooseEffort}
              />
            </ThreadPrimitive.Root>
          </section>
          {runId && (
            <RunPanel key={runId} runId={runId} onReattach={onReattach} />
          )}
        </div>
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
  runId,
}: {
  effort: EffortName
  efforts: Effort[]
  selected: Effort | undefined
  onEffort: (effort: EffortName) => void
  runId: string | null
}) {
  const { t } = useTranslation()
  // what the composer's own submit checks: an empty draft or a run in flight sends nothing
  const canSend = useAuiState((s) => s.composer.canSend && !s.thread.isRunning)
  // a run waiting on the user takes its answer from the review, never a second run on top of it
  const reviewing = useOpenReview(runId) !== null
  const [pending, setPending] = useState<HTMLFormElement | null>(null)
  // set by `confirm` for exactly the one submit it makes; the guard spends it
  const confirmed = useRef(false)
  const dialog = useRef<HTMLDialogElement>(null)
  const over =
    selected !== undefined && Number(selected.budget_usd) > threshold()

  useEffect(() => {
    if (pending) dialog.current?.showModal()
    else dialog.current?.close()
  }, [pending])

  const confirm = () => {
    confirmed.current = true
    pending?.requestSubmit()
    setPending(null)
  }

  return (
    <div
      onSubmitCapture={(event) => {
        // the composer itself would still send this one (Enter submits the form): stop it here
        if (reviewing) {
          confirmed.current = false // a confirmed send the review swallowed is spent all the same
          event.preventDefault()
          event.stopPropagation()
          return
        }
        // a send the composer will drop, one under the threshold, or the confirmed one goes through
        if (
          !canSend ||
          (selected !== undefined && !over) ||
          confirmed.current
        ) {
          confirmed.current = false
          return
        }
        event.preventDefault() // capture runs before the composer's own submit
        event.stopPropagation()
        // no catalogue, no budget to judge: nothing goes out until it loads
        if (selected) setPending(event.target as HTMLFormElement)
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
            disabled={!canSend || reviewing || selected === undefined}
            className="ml-auto h-8 rounded-md bg-primary px-3 text-sm text-primary-foreground"
          >
            {t("chat.send")}
          </button>
        </div>
      </ComposerPrimitive.Root>
      {/* `close` follows Escape, Cancel and the effect's close() alike: each one drops the send */}
      <dialog
        ref={dialog}
        onClose={() => setPending(null)}
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
