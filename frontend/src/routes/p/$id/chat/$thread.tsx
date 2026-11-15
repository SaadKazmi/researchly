import { createFileRoute } from "@tanstack/react-router"
import { useTranslation } from "react-i18next"

import { ThreadList } from "@/features/chat/thread-list"

export const Route = createFileRoute("/p/$id/chat/$thread")({ component: Chat })

function Chat() {
  const { id } = Route.useParams()
  const { t } = useTranslation()
  return (
    <div className="flex flex-1 gap-6">
      <ThreadList projectId={id} />
      <h1 className="text-2xl font-semibold">{t("routes.chat")}</h1>
    </div>
  )
}
