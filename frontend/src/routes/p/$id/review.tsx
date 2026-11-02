import { createFileRoute } from "@tanstack/react-router"

import { Placeholder } from "@/components/placeholder"

export const Route = createFileRoute("/p/$id/review")({
  component: () => <Placeholder titleKey="routes.review" />,
})
