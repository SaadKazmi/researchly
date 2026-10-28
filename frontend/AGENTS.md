# Frontend guide

- Vite + React 19 + TypeScript strict + TanStack Router/Query; shadcn/ui (Base UI) + Tailwind v4.
- API types come from the generated OpenAPI client (`src/api/schema.d.ts`); never hand-write API types.
- Chat uses assistant-ui with the AG-UI runtime; tool UIs live in `src/features/chat/tools/`.
- Rendering LLM output: `react-markdown` + `rehype-sanitize` only; Mermaid `securityLevel: "strict"`; Vega-Lite specs validated before render; no `dangerouslySetInnerHTML`.
- Graph test hooks: expose `window.__researchly.graph` (node count, camera) for Playwright; never assert on canvas pixels.
- Tests: Vitest + Testing Library for components; Playwright for flows (in `e2e/`).
