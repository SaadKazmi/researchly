import { execFileSync } from "node:child_process"
import { mkdirSync } from "node:fs"
import { resolve } from "node:path"

import { artifacts, repo } from "./paths"

// A fixed data dir reuses one pg0 instance across runs (warm start); the file keyring keeps the
// developer's real OS keychain out of it (spec §16.1)
const env = {
  ...process.env,
  RESEARCHLY_DATA_DIR: resolve(artifacts, "data"),
  RESEARCHLY_LOG_DIR: resolve(artifacts, "logs"),
  PYTHON_KEYRING_BACKEND: "keyring.backends.fail.Keyring",
}

function researchly(...args: string[]): string {
  return execFileSync("uv", ["run", "--directory", resolve(repo, "cli"), "researchly", ...args], {
    env,
    encoding: "utf-8",
    timeout: 600_000, // a cold first start installs and builds the web UI, then initialises pg0
  })
}

export default function globalSetup() {
  mkdirSync(artifacts, { recursive: true })
  researchly("config", "set", "runtime", "native") // CI runners have Docker; E2E pins native mode
  const out = researchly("start")
  console.log(out.replace(/token=[0-9a-f]+/g, "token=[REDACTED]"))
  const login = out.match(/http:\/\/127\.0\.0\.1:\d+\/\?token=[0-9a-f]+/)?.[0]
  if (!login) {
    researchly("stop")
    throw new Error(`no login link in the output of researchly start:\n${out}`)
  }
  process.env.RESEARCHLY_E2E_LOGIN_URL = login
  process.env.RESEARCHLY_E2E_URL = new URL(login).origin
  return () => {
    researchly("stop")
  }
}
