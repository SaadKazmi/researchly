import "@testing-library/jest-dom/vitest"
import { cleanup, configure } from "@testing-library/react"
import { afterEach } from "vitest"

// The first render of the assistant-ui tree costs ~2.5s of warm-up in this worker; every later one
// is ~60ms. The default 1s window is shorter than that, so whichever test renders the chat first
// would fail on the clock rather than on the DOM.
configure({ asyncUtilTimeout: 4_000 })

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

afterEach(cleanup)
