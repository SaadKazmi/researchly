import { useQuery } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"

import { healthQuery } from "@/api/queries"
import { cn } from "@/lib/utils"

const DOT = {
  checking: "bg-muted-foreground",
  connected: "bg-emerald-600",
  disconnected: "bg-destructive",
}

export function StatusIndicator() {
  const { t } = useTranslation()
  const { isPending, isError } = useQuery(healthQuery)
  const state = isPending ? "checking" : isError ? "disconnected" : "connected"
  return (
    <p role="status" className="mt-auto flex items-center gap-2 text-sm">
      <span aria-hidden className={cn("size-2 rounded-full", DOT[state])} />
      {t(`status.${state}`)}
    </p>
  )
}
