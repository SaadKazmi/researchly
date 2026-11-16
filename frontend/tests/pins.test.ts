import { expect, test } from "vitest"

import pkg from "../package.json"

// ADR 0012: the AG-UI protocol boundary is pinned on both sides. A caret here could pick up a
// client whose PROTOCOL_VERSION no longer matches the backend's ag-ui-protocol 1.0.0.
const PINS = {
  "@assistant-ui/react": "0.15.32",
  "@assistant-ui/react-ag-ui": "0.1.1",
  "@ag-ui/client": "1.0.4",
}

test("the AG-UI and assistant-ui packages are pinned exactly", () => {
  const dependencies = pkg.dependencies as Record<string, string>
  for (const [name, version] of Object.entries(PINS)) {
    expect(dependencies[name]).toBe(version)
  }
})
