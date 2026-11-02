import { expect, test as setup } from "@playwright/test"

import { authFile } from "../paths"

setup("sign in through the login link", { tag: "@smoke" }, async ({ page }) => {
  const login = new URL(process.env.RESEARCHLY_E2E_LOGIN_URL!)
  await page.goto(login.href)
  await expect(page).toHaveURL(`${login.origin}/`) // the token is stripped from the address bar
  const cookie = (await page.context().cookies()).find((c) => c.name === "researchly_token")
  expect(cookie).toMatchObject({
    value: login.searchParams.get("token"),
    httpOnly: true,
    sameSite: "Strict",
  })
  await page.context().storageState({ path: authFile })
})
