import i18n from "i18next"
import { initReactI18next } from "react-i18next"

import en from "./locales/en.json"

// English ships first (spec §18); resources are bundled, so initialise synchronously
void i18n.use(initReactI18next).init({
  resources: { en: { translation: en } },
  lng: "en",
  fallbackLng: "en",
  initAsync: false,
  interpolation: { escapeValue: false }, // React escapes
})

export default i18n
