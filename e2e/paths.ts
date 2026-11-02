import { resolve } from "node:path"

export const repo = resolve(import.meta.dirname, "..")
export const artifacts = resolve(import.meta.dirname, ".artifacts") // gitignored
export const authFile = resolve(artifacts, "auth.json")
export const appLog = resolve(artifacts, "logs", "app.log")
