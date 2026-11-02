import { createFileRoute } from "@tanstack/react-router"

import { Placeholder } from "@/components/placeholder"

export const Route = createFileRoute("/usage")({
  component: () => <Placeholder titleKey="routes.usage" />,
})
