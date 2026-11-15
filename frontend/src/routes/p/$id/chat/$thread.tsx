import { createFileRoute } from "@tanstack/react-router"

import { ChatThread } from "@/features/chat/thread"
import { ThreadList } from "@/features/chat/thread-list"

export const Route = createFileRoute("/p/$id/chat/$thread")({ component: Chat })

function Chat() {
  const { id, thread } = Route.useParams()
  return (
    <div className="flex flex-1 gap-6">
      <ThreadList projectId={id} />
      <ChatThread projectId={id} threadId={thread} />
    </div>
  )
}
