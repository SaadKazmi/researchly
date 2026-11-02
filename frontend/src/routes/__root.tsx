import { createRootRoute, Link, Outlet } from "@tanstack/react-router"
import { useTranslation } from "react-i18next"

import { AuthNotice } from "@/components/auth-notice"
import { StatusIndicator } from "@/components/status-indicator"
import { Version } from "@/components/version"

export const Route = createRootRoute({ component: Shell })

const link = "block rounded-md px-2 py-1 text-sm hover:bg-muted"
const active = { className: "bg-muted font-medium" }

function Shell() {
  const { t } = useTranslation()
  return (
    <div className="flex min-h-svh">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:m-2 focus:rounded-md focus:bg-background focus:p-2"
      >
        {t("shell.skip")}
      </a>
      <aside className="flex w-56 shrink-0 flex-col gap-6 border-r p-4">
        <div>
          <p className="font-semibold">{t("app.name")}</p>
          <Version />
        </div>
        <nav aria-label={t("shell.nav")}>
          <ul className="flex flex-col gap-1">
            <li>
              <Link
                to="/"
                className={link}
                activeProps={active}
                activeOptions={{ exact: true }}
              >
                {t("nav.projects")}
              </Link>
            </li>
            <li>
              <Link to="/memory" className={link} activeProps={active}>
                {t("nav.memory")}
              </Link>
            </li>
            <li>
              <Link to="/usage" className={link} activeProps={active}>
                {t("nav.usage")}
              </Link>
            </li>
            <li>
              <Link
                to="/settings/$"
                params={{ _splat: "" }}
                className={link}
                activeProps={active}
              >
                {t("nav.settings")}
              </Link>
            </li>
          </ul>
        </nav>
        <StatusIndicator />
      </aside>
      <main
        id="main"
        tabIndex={-1}
        className="flex flex-1 flex-col gap-4 p-6 outline-none"
      >
        <AuthNotice />
        <Outlet />
      </main>
    </div>
  )
}
