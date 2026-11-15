/// <reference types="node" />
import { readFileSync } from "node:fs"
import { resolve } from "node:path"

export type Frame = { seq: number; type: string; data: string }

/**
 * The committed transcript, parsed. One source of truth with the Playwright specs.
 * A plain path, not `new URL(..., import.meta.url)`: Vite rewrites that into an asset import and
 * refuses a file outside the frontend root.
 */
export function frames(name = "standard-run"): Frame[] {
  const text = readFileSync(
    resolve(import.meta.dirname, "../../e2e/fixtures/agui", `${name}.sse`),
    "utf-8"
  )
  return text
    .split("\n\n")
    .filter((block) => block.trim())
    .map((block) => {
      const seq = Number(/^id: (\d+)$/m.exec(block)![1])
      const data = /^data: (.*)$/m.exec(block)![1]
      return { seq, type: JSON.parse(data).type as string, data }
    })
}

/** The run as it stands when the plan interrupt arrives: everything up to and including that RUN_FINISHED. */
export function untilInterrupt(name = "standard-run"): Frame[] {
  const all = frames(name)
  return all.slice(
    0,
    all.findIndex(
      (f) => f.type === "RUN_FINISHED" && f.data.includes('"interrupt"')
    ) + 1
  )
}

/** Frames as `POST /runs/{id}/stream` writes them: `data:` first, then `id:` (fastapi's `format_sse_event`). */
export function sse(list: Frame[]): Response {
  const body = list.map((f) => `data: ${f.data}\nid: ${f.seq}\n\n`).join("")
  return new Response(body, {
    headers: { "content-type": "text/event-stream" },
  })
}
