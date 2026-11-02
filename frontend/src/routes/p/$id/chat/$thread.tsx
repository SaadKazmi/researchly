import { createFileRoute } from "@tanstack/react-router"

import { Placeholder } from "@/components/placeholder"

export const Route = createFileRoute("/p/$id/chat/$thread")({
  component: () => <Placeholder titleKey="routes.chat" />,
})
