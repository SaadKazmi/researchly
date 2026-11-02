import { useQuery } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"

import { systemQuery } from "@/api/queries"

export function Version() {
  const { t } = useTranslation()
  const { data } = useQuery(systemQuery)
  if (!data) return null
  return (
    <p className="text-xs text-muted-foreground">
      {t("app.version", { version: data.version })}
    </p>
  )
}
