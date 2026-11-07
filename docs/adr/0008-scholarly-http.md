# ADR 0008: Scholarly HTTP stack: custom limiter, retry, breaker and cache on a guarded httpx

- Status: accepted
- Date: 2026-11-03
- Spec sections: §8.1, §8.1.1, §8.2, §8.3, §16, §19.3

## Context
Step 2a needs one outbound HTTP client for eight keyless scholarly APIs (OpenAlex, arXiv, Crossref, OpenCitations, DataCite, Europe PMC, PubMed, Hugging Face Papers). It must provide:
- per-host rate and concurrency limits;
- retries that honour `Retry-After`;
- a circuit breaker per source;
- a shared response cache keyed by paper ID;
- SSRF protection.

It must also stay replayable in tests. Research is in `docs/research/2026-11-03-step2a-http-infra.md` (stack) and `docs/research/2026-11-03-step2a-connectors.md` (live-checked API facts). The research found:
- **Rate limiting and breakers:** every §8.2 limit is "N per period, optional concurrency". No maintained asyncio breaker library can also hold "open until Retry-After".
- **Caching:** the APIs send few cache validators, and hishel has no Postgres store and keys by URL.
- **SSRF:** httpx has no public hook to connect to a pre-checked IP while keeping URL, Host and SNI.

## Decision
- **Client:** `httpx==0.28.1` and `httpcore==1.0.9` (runtime dependencies, pinned exactly), wrapped by `PoliteClient` in `connectors/http.py`. Only `connectors/http.py`, its SSRF guard `connectors/ssrf.py` and the sidecar client `connectors/sidecar.py` (ADR 0010) import httpx or httpcore; `test_only_the_http_modules_import_httpx` enforces it.
- **Limits:** a custom `HostLimit` (next-at time plus a semaphore) per host or bucket, read from `connectors/limits.yaml`. The same state absorbs a short `Retry-After`. A long one (over 120 s) blocks the host and raises `QuotaExhausted` without waiting.
- **Retry:** a hand-rolled loop, GET only. It retries on transport errors and on 429, 500, 502, 503 and 504, with at most 4 attempts. Backoff is full jitter, `uniform(0, min(60, 2**attempt))`. Every attempt re-enters the limiter. The breaker admits the call once; only a half-open probe holds it.
- **Breaker:** a custom breaker per source. It opens after 5 consecutive failures, cools down for 60 s doubling to 900 s, and allows one half-open probe. 400 and 404 count as answers, not failures. An SSRF refusal is neither retried nor counted as a failure.
- **Cache:** a custom UNLOGGED `http_cache` table, keyed by a versioned, normalized ID (`<source>:v1:…`):
  - It stores only the `etag`, `content-type`, `content-language` and `last-modified` headers, and the URL without its query string.
  - It uses ETag revalidation only where the source sends one (DataCite, Hugging Face). A 304 refreshes the cached entry.
  - TTLs: metadata and references 60 days; counts 7 days; search 24 h.
- **SSRF:** a guarded `httpcore` network backend. It resolves once and checks every address:
  - Mapped, NAT64, 6to4 and Teredo addresses are unwrapped and the inner IPv4 is checked.
  - IPv6 addresses must also lie in 2000::/3.
  - Only ports 80 and 443 are allowed.

  It connects to the checked IP and leaves URL, Host and SNI untouched. Redirects are followed manually, at most 5, with a scheme check on every hop. Bodies are capped at 5 MB by default, and 20 MB for OpenCitations lists.
- **Untrusted XML** is parsed only with `defusedxml.ElementTree`.
- **Tests:**
  - **Replay:** vcrpy 9.2.0 and pytest-recording 0.14.0 replay recorded cassettes in record mode `none`, so a missing cassette fails the test.
  - **Unit mocks:** `httpx.MockTransport`.
  - **Nightly:** live checks run in a separate workflow (`nightly.yml`).

## Consequences
- **No new runtime dependency beyond httpx, httpcore and defusedxml.** Every policy is a few dozen lines that the tests cover directly.
- **Private API risk:** `GuardedTransport` replaces `AsyncHTTPTransport._pool`. httpx is pinned exactly, and the end-to-end refusal tests through `GuardedTransport` (`test_ssrf.py`) fail if the hook stops applying. Any httpx or httpx2 upgrade needs this ADR revisited.
- **In-process limits:** they are correct only while one process does outbound HTTP. A separate worker process would double the effective rates, so revisit this before adding one.
- **vcrpy churn:** pin exactly and re-record once on each upgrade. `--block-network` waits for pytest-recording 0.14.1, which clears the release-age gate after 2026-11-09.
- **UNLOGGED cache:** an unclean shutdown truncates the cache by design. The OpenAlex budget guard (≤50 searches per UTC day, stop at ≤20% of the daily USD budget) still applies.
- **Exception to "all HTTP via `PoliteClient`":** local sidecars (SearXNG now, GROBID at step 4) go through the origin-pinned `SidecarClient` (ADR 0010).
- **Corporate proxies:** `trust_env=False` ignores `HTTPS_PROXY`. The TLS context still honours `SSL_CERT_FILE` and `SSL_CERT_DIR` for corporate CAs. An explicit proxy setting is deferred.

## Evidence
- **SSRF tests:** a prototype and the test suite cover every spelling in spec §19.3, including decimal, octal and hex IPv4, mapped, NAT64, 6to4, Teredo, zoned and site-local forms, and a redirect to an internal address. They pass on the dev box's Python 3.12.0, which predates the CVE-2024-4032 fix, and on CI's latest 3.12.
- **Live API probes** (2026-11-02/03, keyless, no email) confirmed limits, error shapes, ETag support and ID forms per source. Three findings shaped the design:
  - OpenCitations streams 8.6 MB or more for a single paper's citations, hence count-first with a 5,000-row cap.
  - DataCite allows 500 requests per 5 min unidentified.
  - Hugging Face search allows 50 per 5 min.
- **CI:** run 41603542298 (PR #6, Tasks 1–8) was green on every job, including the five-OS platform job. Each connector replays its own cassettes. All recorded responses are 200 or deliberate 404s, with no cookie, key or email.
