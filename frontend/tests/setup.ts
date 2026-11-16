import "@testing-library/jest-dom/vitest"
import { cleanup, configure } from "@testing-library/react"
import { afterEach } from "vitest"

// The first render of the assistant-ui tree costs ~2.5s of warm-up in this worker; every later one
// is ~60ms. The default 1s window is shorter than that, so whichever test renders the chat first
// would fail on the clock rather than on the DOM. Since the chat lives on the thread route, that is
// now any test rendering `/p/:id/chat/:thread` - including the thread-list tests, which do not
// otherwise touch assistant-ui. 4s was still short enough to flake roughly one run in three across
// parallel workers, so this sits far above the worst measured warm-up rather than just past the
// typical one. It is a ceiling, not a delay: a passing assertion returns as soon as it matches, and
// `testTimeout` in vite.config.ts is kept above it so a spent window reports as a real failure.
configure({ asyncUtilTimeout: 10_000 })

// jsdom 30 parses <dialog> but implements none of its methods. The budget confirmation uses the
// native modal, so give the element just enough to track its own open state.
HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) {
  this.open = true
}
HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) {
  this.open = false
}

// The composer's auto-resizing textarea observes itself; jsdom has no layout, so nothing to report.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
}

// assistant-ui scrolls the window; jsdom defines scrollTo only to log "Not implemented", so `??=`
// would keep that stub and this overrides it.
window.scrollTo = () => {}

afterEach(cleanup)
