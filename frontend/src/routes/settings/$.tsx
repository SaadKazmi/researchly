import { createFileRoute } from "@tanstack/react-router"

import { Placeholder } from "@/components/placeholder"

export const Route = createFileRoute("/settings/$")({
  component: () => <Placeholder titleKey="routes.settings" />,
})
