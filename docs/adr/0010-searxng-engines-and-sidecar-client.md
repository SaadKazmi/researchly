# ADR 0010: SearXNG engines, URL plumbing and the sidecar client

- Status: accepted
- Date: 2026-11-06
- Spec sections: F2.1, §4.1, §8.1, §19.3

## Context
Step 1e installs and supervises SearXNG (ADR 0004), but nothing used it, and it ran upstream's default engine set. That set includes scholarly engines (arXiv, Crossref, PubMed, Semantic Scholar) that would spend the same per-IP quotas as our connectors. The app also had no way to find SearXNG, and every HTTP call had to go through `PoliteClient`, whose SSRF guard refuses loopback and private addresses. Research is in `docs/research/2026-11-06-step2c-sidecars.md`.

## Decision
- **Engines:** `use_default_settings.engines.keep_only: [brave, mojeek, yahoo, github]`:
  - general web from Brave, Mojeek and Yahoo; code from GitHub (category `it`);
  - `mojeek` is un-inactivated and `yahoo` enabled;
  - `search.formats: [json]`, so the HTML UI's search answers 403;
  - `outgoing.request_timeout: 5.0`;
  - the scholarly engines stay off: the connectors own those sources and their quotas.
- **Brave, Mojeek and Yahoo scrape result pages**, as `ddgs` does. F2.1 rejected `ddgs` as a default for that reason. SearXNG stays the default anyway:
  - it runs locally and needs no key;
  - nothing scraper-shaped is vendored into Researchly;
  - it suspends an engine that answers with a CAPTCHA instead of retrying;
  - it reports `unresponsive_engines` per query;
  - `RESEARCHLY_WEB_SEARCH=off` turns it off.

  `ddgs` stays opt-in and unbuilt.
- **URL plumbing:** the CLI tells the app where SearXNG is through `RESEARCHLY_SEARXNG_URL`:
  - native: `http://127.0.0.1:<port>`, with the port picked before the app is spawned;
  - Docker: `http://searxng:8080`;
  - off: unset or empty.
- **Docker start:** `up -d searxng` runs without `--wait`, before `up -d --wait app`. `app` has no `depends_on: searxng`, so a slow or broken SearXNG never fails a start. The `searxng` service has a `wget /healthz` healthcheck.
- **Sidecar client (`connectors/sidecar.py`):** the only HTTP outside `PoliteClient`.
  - It is pinned to the configured origin, and callers pass relative paths only.
  - No redirects, no environment proxy, a 2 MB body cap, a 2 s connect timeout and a 15 s total.
  - Pacing: 1 request per second, 2 in flight.
  - It sends `X-Real-IP: 127.0.0.1` to quiet SearXNG's bot-detection log.
  - It is **not** loopback-only. In Docker, `searxng` resolves to a private compose address, which is why it cannot go through the SSRF-guarded `PoliteClient`. Pinning the origin gives the same protection: no caller, page or model can choose the target.
- **Import guard:** only `connectors/http.py`, `connectors/sidecar.py` and `connectors/ssrf.py` import httpx or httpcore. `test_only_the_http_modules_import_httpx` enforces it.
- **Capability:** an app lifespan task polls `/healthz` for up to 60 s and upserts `capability('web_search')` as `ready` or `off` with a reason. SearXNG becomes healthy after the app (native 12–15 s, Docker 5–7 s), so a check at startup would always say off.

## Consequences
- Web search works keyless in both runtimes, and only the connectors spend the scholarly quotas.
- If an upstream engine changes its pages, it shows up in `unresponsive_engines` and in the nightly check, which runs the pinned image with the shipped settings. It never fails a run.
- Some users may not want scraping engines. They can set `RESEARCHLY_WEB_SEARCH=off`. A Settings toggle and keyed engines (Brave API, Tavily, Exa) are step 9.
- GROBID (step 4) will reuse the sidecar client.
- A SearXNG pin bump must re-check the engine list (ADR 0004, checklist step 7).

## Evidence
- `docs/research/2026-11-06-step2c-sidecars.md` §A2 (settings and engine behaviour), §A3 (JSON shape, `unresponsive_engines`), §A4 (plumbing, healthcheck), §A5 (start latency).
- `cli/tests/test_searxng.py::test_real_settings_enable_exactly_the_four_engines` (the pinned SearXNG's `/config`).
