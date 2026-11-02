import { useTranslation } from "react-i18next"

export function Placeholder({ titleKey }: { titleKey: string }) {
  const { t } = useTranslation()
  return (
    <section className="flex flex-col gap-2">
      <h1 className="text-2xl font-semibold">{t(titleKey)}</h1>
      <p className="text-muted-foreground">{t("placeholder")}</p>
    </section>
  )
}
