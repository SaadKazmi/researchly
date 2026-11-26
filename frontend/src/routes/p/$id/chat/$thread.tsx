import { createFileRoute } from "@tanstack/react-router"
import { useTranslation } from "react-i18next"

import { ChatThread } from "@/features/chat/thread"
import { ThreadList } from "@/features/chat/thread-list"

export const Route = createFileRoute("/p/$id/chat/$thread")({ component: Chat })

function Chat() {
  const { t } = useTranslation()
  const { id, thread } = Route.useParams()
  return (
    <div className="flex flex-1 gap-6">
      <h1 className="sr-only">{t("routes.chat")}</h1>
      <ThreadList projectId={id} />
      <ChatThread threadId={thread} />
    </div>
  )
}
