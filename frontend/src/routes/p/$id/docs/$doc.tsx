import { createFileRoute } from "@tanstack/react-router"

import { Placeholder } from "@/components/placeholder"

export const Route = createFileRoute("/p/$id/docs/$doc")({
  component: () => <Placeholder titleKey="routes.document" />,
})
