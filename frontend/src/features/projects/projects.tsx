import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import { api } from "@/api/client"
import { projectsQuery, threadsQuery, unwrap } from "@/api/queries"
import { Button } from "@/components/ui/button"

export function Projects() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data: projects, isPending, isError } = useQuery(projectsQuery)
  const [name, setName] = useState("")

  const create = useMutation({
    mutationFn: async (value: string) =>
      unwrap(await api.POST("/api/v1/projects", { body: { name: value } })),
    onSuccess: async () => {
      setName("")
      await queryClient.invalidateQueries({ queryKey: projectsQuery.queryKey })
    },
  })

  // a project has no route of its own (spec §18): opening one opens its newest
  // thread, which the API returns first (threads.py orders by created_at desc)
  const open = useMutation({
    mutationFn: async (projectId: string) => {
      const threads = await queryClient.fetchQuery(threadsQuery(projectId))
      const thread =
        threads[0] ??
        unwrap(
          await api.POST("/api/v1/projects/{project_id}/threads", {
            params: { path: { project_id: projectId } },
            body: {},
          })
        )
      return { projectId, threadId: thread.id }
    },
    onSuccess: ({ projectId, threadId }) =>
      navigate({
        to: "/p/$id/chat/$thread",
        params: { id: projectId, thread: threadId },
      }),
  })

  return (
    <section className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">{t("routes.projects")}</h1>
      <form
        className="flex items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          if (name.trim()) create.mutate(name.trim())
        }}
      >
        <label className="flex flex-col gap-1 text-sm">
          {t("projects.name")}
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="h-8 rounded-md border px-2"
          />
        </label>
        <Button type="submit" disabled={create.isPending}>
          {t("projects.create")}
        </Button>
      </form>
      {isPending || isError || projects.length === 0 ? (
        // an empty list and a failed fetch look the same in `data`, so never say "no projects yet"
        // until the fetch has actually succeeded. Neither message takes `role="status"` or
        // `role="alert"`: the shell already owns one of each, and a second breaks their lookups.
        <p className="text-muted-foreground">
          {t(
            isPending
              ? "projects.loading"
              : isError
                ? "projects.error"
                : "projects.empty"
          )}
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {projects.map((project) => (
            <li key={project.id}>
              <a
                href={`/p/${project.id}/chat`}
                className="block rounded-md px-2 py-1 hover:bg-muted"
                onClick={(event) => {
                  event.preventDefault() // the destination thread is only known after the lookup
                  // a second click before the first lookup returns would create a second thread
                  if (!open.isPending) open.mutate(project.id)
                }}
              >
                {project.name}
              </a>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
