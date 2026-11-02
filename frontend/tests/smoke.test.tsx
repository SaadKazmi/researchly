import { render, screen } from "@testing-library/react"
import { expect, test } from "vitest"

import { Button } from "@/components/ui/button"

test("renders a shadcn button", () => {
  render(<Button>Go</Button>)
  expect(screen.getByRole("button", { name: "Go" })).toBeInTheDocument()
})
