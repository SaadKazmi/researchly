import { createFileRoute } from "@tanstack/react-router"

import { Projects } from "@/features/projects/projects"

export const Route = createFileRoute("/")({ component: Projects })
