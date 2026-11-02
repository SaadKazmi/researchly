import { useQuery } from "@tanstack/react-query"
import { Trans } from "react-i18next"

import { ApiError } from "@/api/client"
import { systemQuery } from "@/api/queries"

export function AuthNotice() {
  const { error } = useQuery(systemQuery)
  if (!(error instanceof ApiError && error.status === 401)) return null
  return (
    <p
      role="alert"
      className="rounded-md border border-destructive/50 p-3 text-sm"
    >
      <Trans
        i18nKey="auth.signIn"
        components={{ code: <code className="font-mono" /> }}
      />
    </p>
  )
}
