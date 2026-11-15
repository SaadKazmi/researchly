import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { useDeferredValue, useState } from "react"
import { useTranslation } from "react-i18next"

import { api } from "@/api/client"
import { threadsQuery, unwrap } from "@/api/queries"
import { Button } from "@/components/ui/button"

export function ThreadList({ projectId }: { projectId: string }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [q, setQ] = useState("")
  const search = useDeferredValue(q) // typing stays responsive; the list catches up
  const {
    data: threads,
    isPending,
    isError,
  } = useQuery(threadsQuery(projectId, search.trim() || undefined))

  const create = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/projects/{project_id}/threads", {
          params: { path: { project_id: projectId } },
          body: {},
        })
      ),
    onSuccess: async (thread) => {
      await queryClient.invalidateQueries({ queryKey: ["threads", projectId] })
      await navigate({
        to: "/p/$id/chat/$thread",
        params: { id: projectId, thread: thread.id },
      })
    },
  })

  return (
    <nav
      aria-label={t("chat.threads")}
      className="flex w-56 shrink-0 flex-col gap-2"
    >
      <input
        type="search"
        aria-label={t("chat.search")}
        placeholder={t("chat.search")}
        value={q}
        onChange={(event) => setQ(event.target.value)}
        className="h-8 rounded-md border px-2 text-sm"
      />
      <Button
        type="button"
        size="sm"
        onClick={() => create.mutate()}
        disabled={create.isPending}
      >
        {t("chat.newThread")}
      </Button>
      {isPending || isError || threads.length === 0 ? (
        // a pending or failed fetch also leaves `data` undefined, so only a successful empty
        // list may say "no threads". No `role="status"`/`role="alert"`: the shell owns those.
        <p className="text-sm text-muted-foreground">
          {t(
            isPending
              ? "chat.loading"
              : isError
                ? "chat.error"
                : // an empty list means two different things: a project with nothing in it yet,
                  // and a search that matched nothing. The same trimmed text the query used
                  // decides which, so the message always matches the list it is explaining.
                  search.trim()
                  ? "chat.noMatches"
                  : "chat.noThreads"
          )}
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {threads.map((thread) => (
            <li key={thread.id}>
              <Link
                to="/p/$id/chat/$thread"
                params={{ id: projectId, thread: thread.id }}
                className="block truncate rounded-md px-2 py-1 text-sm hover:bg-muted"
                activeProps={{ className: "bg-muted font-medium" }}
              >
                {thread.title ?? t("chat.untitled")}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </nav>
  )
}
