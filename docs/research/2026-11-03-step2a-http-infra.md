# Research 2a: HTTP infrastructure for `PoliteClient` (rate limits, retries, breaker, cache, SSRF, robots, replay)

Date: 2026-11-03. Scope: spec §8.2, §19.3, §21.2, AGENTS.md conventions. Evidence: PyPI JSON (dates, licences),
GitHub source/changelogs, live header checks against the APIs, and a working SSRF + vcrpy prototype (scratchpad only).

## TL;DR
Build `PoliteClient` on **httpx 0.28.1 (already locked)** with small in-house pieces for rate limit, retry and breaker
(each 10-25 lines, all coupled to the same per-host state), a **custom UNLOGGED `http_cache` table**, an SSRF guard
implemented as an **httpcore network backend** (pins the checked IP, keeps URL/Host/SNI untouched, vcrpy still
intercepts), **Protego** for robots.txt (stdlib parser gets wildcards and precedence wrong), **vcrpy + pytest-recording**
for cassettes, and `httpx.MockTransport` (built in) for unit tests. Only two new deps: `protego` (runtime) and
`vcrpy`/`pytest-recording` (dev). `httpx` must move from the dev group to runtime dependencies.

## Current state of the repo
- `httpx 0.28.1` / `httpcore 1.0.9` are locked but `httpx` is only in the **dev** group (runtime gets it transitively via
  litellm/langchain-core/openai). `PoliteClient` imports `httpx` and `httpcore` directly, so both become direct runtime deps.
- `tenacity 9.2.1` is locked transitively (langchain-core). `anyio 4.15.1` locked. No limiter/breaker/cache/VCR deps.
- `httpx2` 2.16.0 (Pydantic's maintained fork, BSD-3, since 2026-05) is already locked via langsmith/mcp. httpx's last
  release is 0.28.1 (2024-12-06; only 1.0.dev pre-releases since). vcrpy and hishel support httpx2 too. Stay on httpx
  0.28.1 (litellm/openai/langchain-core use it); revisit in an ADR if httpx stalls on a security fix.
- **Dev venv runs CPython 3.12.0** (`backend/.venv`, `sys.version` 3.12.0, Oct 2023). Its `ipaddress` predates the
  CVE-2024-4032 fix (3.12.4): `2002:7f00:1::` (6to4 wrapping 127.0.0.1) reports `is_global=True` on 3.12.0 and `False`
  on 3.12.14 (both verified locally). The SSRF check must not rely on the interpreter being patched (see §5).

## 1. Rate limiting (per-host rate + concurrency)
| Option | Version / date | Licence | Fit |
|---|---|---|---|
| aiolimiter | 1.3.0, 2026-09-07; 789★, 6 open issues | MIT | Leaky bucket `AsyncLimiter(max_rate, period)`; asyncio only; no concurrency cap; no API to "pause until T" (needed for Retry-After/Crawl-delay); open #825 starvation/FIFO |
| pyrate-limiter | 4.5.1, 2026-10-20; 531★ | MIT | Rich (sliding log/GCRA, per-key buckets, Redis/SQLite/sync-only Postgres buckets, httpx transport); no concurrency cap; much more surface than needed |
| custom bucket + `asyncio.Semaphore` | stdlib | — | ~15 lines; one `next_at` per host also absorbs Retry-After and Crawl-delay |

**Recommendation: custom.** Every row in the §8.2 table is "N per period, optional concurrency"; a per-host
"next allowed time" (GCRA with burst 1) plus a `Semaphore(concurrency)` is exact and lets the same state be pushed
forward by `Retry-After` and robots `Crawl-delay`, which neither library exposes. Sketch:
```python
class HostLimit:  # ponytail: in-process only; single app process (spec §4.2)
    def __init__(self, interval: float, concurrency: int) -> None:
        self.interval, self.sem, self.next_at = interval, asyncio.Semaphore(concurrency), 0.0
    def defer(self, seconds: float) -> None:  # Retry-After / Crawl-delay
        self.next_at = max(self.next_at, time.monotonic() + seconds)
    async def __aenter__(self) -> None:
        await self.sem.acquire()
        now = time.monotonic(); wait = self.next_at - now
        self.next_at = max(now, self.next_at) + self.interval   # reserve the slot before sleeping
        if wait > 0: await asyncio.sleep(wait)
    async def __aexit__(self, *exc: object) -> None: self.sem.release()
```
"— " concurrency in the spec table → default 1 for 1-per-≥1 s hosts, 2 otherwise. No lock needed: the reservation has
no `await` between read and write. **In-process is enough:** spec §4.2 runs API + async workers + procrastinate in one
process; OpenAlex's budget is counted server-side per IP anyway. Caveats: asyncio primitives bind to one loop, so the
dev `claude-code` provider thread must not call `PoliteClient`; if procrastinate is ever run as a separate worker
process, limits double (then move state to Postgres advisory locks, not before).
Live check 2026-11-03: arxiv.org robots.txt has `Crawl-delay: 15`, so PDF/HTML fetches from `arxiv.org` need a 15 s
interval (separate host from the 3 s `export.arxiv.org` API). Rate key = host; source-specific splits (Crossref single
vs list) = host + path class.

## 2. Retry / backoff and Retry-After
| Option | Version / date | Licence | Notes |
|---|---|---|---|
| tenacity | 9.2.1, 2026-10-07 (already locked) | Apache-2.0 | `wait_exponential_jitter`; 9.1.3 added `wait_exception` (wait derived from the exception, usable for Retry-After) |
| stamina | 26.1.0, 2026-04-13 | MIT | Opinionated wrapper **on tenacity**; an `on` hook may return float/timedelta to override backoff (25.2.0) — designed for Retry-After; unreleased `TryAgain(after=...)` |
| hand-rolled loop | stdlib | — | ~15 lines inside `PoliteClient.get` |

**Recommendation: hand-rolled loop** in `PoliteClient`. Each attempt must re-enter the host limiter, feed the
breaker and possibly `defer()` the host — a decorator wraps the whole call and hides those interactions. Policy
(AWS "Exponential Backoff and Jitter", full jitter): GET only; retry on `ConnectError`/`ReadTimeout`/`RemoteProtocolError`,
429, 500, 502, 503, 504; max 4 attempts; `delay = Retry-After if present else uniform(0, min(60, 1 * 2**attempt))`.
Retry-After per RFC 9110 §10.2.3 is delay-seconds **or** HTTP-date; stdlib handles both:
```python
def retry_after(v: str | None) -> float | None:
    if not v: return None
    if v.strip().isdigit(): return float(v)
    try: return max(0.0, (email.utils.parsedate_to_datetime(v) - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError): return None
```
If Retry-After > 120 s, do not sleep: `defer()` the host, open the breaker until then, raise `QuotaExhausted`/`RateLimited`
(OpenAlex exposes `Retry-After` in `access-control-expose-headers`; its daily budget resets at midnight UTC). Fallback if
the team prefers a library: tenacity (already installed, `wait_exception`), not stamina (extra dep on top of tenacity).

## 3. Circuit breaker per source
| Library | Last release | Async | Verdict |
|---|---|---|---|
| pybreaker 1.4.1 (BSD) | 2025-09-21 | **Tornado only** (`call_async` needs `tornado.gen`, README) | no |
| aiobreaker 1.2.0 (BSD) | 2021-05-17 | yes | unmaintained |
| purgatory 3.0.1 (MIT) | 2024-11-02; 5★, last push 2025-07 | yes | tiny, stale |
| circuitbreaker 2.1.3 (BSD-3) | 2025-03-31 | decorates async funcs | per-function decorator, exception-only; can't express "open until Retry-After" |

**Recommendation: custom (~20 lines)**: `failures`, `open_until`, half-open = one probe. Thresholds, aligned with
library defaults (pybreaker `fail_max=5, reset_timeout=60`; circuitbreaker `FAILURE_THRESHOLD=5, RECOVERY_TIMEOUT=30`;
Resilience4j `waitDurationInOpenState=60s`): **open after 5 consecutive failed calls** (after retries are exhausted;
failures = transport errors, 5xx, 429), **cool-down 60 s doubling to 15 min**, one half-open probe, success resets.
404/400 are answers, not failures. A long Retry-After sets `open_until` directly. Breaker key = connector `name`
(source), while limits are per host. Open breaker → raise `SourceUnavailable`; the Lead continues with other sources (§8.2).

## 4. HTTP caching: hishel vs custom `http_cache`
hishel 1.4.0 (2026-09-16, BSD-3, 417★, active): storages are **SQLite and Redis only**; a Postgres store means
implementing `AsyncBaseStorage` (create/get/update/remove entry, `refresh_entry_ttl`, soft-delete, streamed bodies).
Cache key is `sha256(url)` (or body), not a normalized ID. Per-request TTL exists (`extensions={"hishel_ttl": s}`) but
is an eviction TTL; freshness follows RFC 9111 under `SpecificationPolicy`, while `FilterPolicy` "skips all RFC 9111
specification checks" (so no ETag revalidation).
**Live headers (2026-11-03): OpenAlex, Crossref, arXiv API and Europe PMC send no `Cache-Control`, no `ETag`, no
`Last-Modified`.** RFC caching would therefore store little and revalidate nothing; the spec's 60 d / 7 d / 24 h TTLs
are app policy, not HTTP semantics. Only full-text hosts send validators (arxiv.org/pdf: `etag` + `last-modified`;
`If-None-Match` → **304** verified live).
**Recommendation: custom table, no hishel.** `http_cache(key text pk, url, status, headers jsonb, body bytea, etag,
expires_at)` UNLOGGED; key = normalized ID (`openalex:work:W…`, `crossref:doi:10.…`, `search:openalex:<sha256 of
normalized params>`, `robots:https://host`). Flow: fresh row → return without network; stale row with `etag` →
conditional GET, 304 → bump `expires_at`; else upsert (`INSERT … ON CONFLICT (key) DO UPDATE`). Housekeeping:
`DELETE … WHERE expires_at < now() - interval '30 days'` at startup. PDFs stay in `<data_dir>/files` (§9.1); store only
their `etag`/`last-modified` for revalidation. DOI dedupe across agents happens before the call (§8.2), not in HTTP.

## 5. SSRF-safe fetching with httpx
Options checked:
- `advocate` 1.0.0 (2020-07-14): requests-only, unmaintained. No.
- `safehttpx` 0.2.0 (Gradio, Apache-2.0): GET wrapper over **httpx2**, validates via **Google DNS** — a third-party
  DNS dependency and a different HTTP stack. No.
- `drawbridge` 0.1.2 (MIT): single release 2026-02-27, 7★, wrapper API. No.
- `httpx-secure` 2.0.0 (0BSD, 2026-10-27; 3★, single maintainer): patches an existing `AsyncClient` with a guarded
  httpcore network backend; pins `httpx<0.29`, `httpcore<1.1`; private-API integration. Good reference design; too thin a
  maintenance base to depend on for a security boundary.
- `extensions={"sni_hostname": …}` + rewriting the URL to the IP + `Host` header: works for TLS (httpcore passes
  `sni_hostname` as `server_hostname`, verified in httpcore 1.0.9 `_async/connection.py::_connect`), but the URL,
  pool key, cookies, redirects and **vcrpy cassettes** then carry IPs, so replay matching breaks whenever DNS changes. No.

**Recommendation: custom guarded network backend (~40 lines)**, same mechanism as httpx-secure. httpcore calls
`network_backend.connect_tcp(host=<origin host>, port, …)` and afterwards `start_tls(server_hostname=<origin host>)`, so a
backend that resolves once, validates **every** returned address and connects to the validated IP literal gives pinning
with untouched URL, `Host`, SNI and certificate verification. `httpx.AsyncHTTPTransport` has no `network_backend`
parameter, so subclass it and replace `_pool` (`httpcore.AsyncConnectionPool(..., network_backend=Guarded())`;
`# ponytail: private _pool, re-check on httpx bumps`). Keep it a **subclass of `AsyncHTTPTransport`**: vcrpy patches
`httpx.AsyncHTTPTransport.handle_async_request`, so a non-subclass transport would escape cassettes.
Prototype (2026-11-03, httpx 0.28.1, httpcore 1.0.9, vcrpy 9.2.0, Windows): `https://example.com` → 200 over the pinned
IP with normal cert verification; blocked: `127.0.0.1`, `localhost` (→ `::1`), `[::ffff:127.0.0.1]`,
`127.0.0.1.nip.io`, `169.254.169.254`, `[64:ff9b::7f00:1]`; `2130706433` → DNS error on Windows (glibc resolves it to
127.0.0.1 via `inet_aton`, which the post-resolution check then blocks). Cassette stored `uri: https://example.com/`;
replay worked; unrecorded URL raised `CannotOverwriteExistingCassetteException`.
Address rule (do not trust `is_private` alone: CGNAT `100.64/10` is `is_private=False`):
```python
def check(ip: IPv4Address | IPv6Address) -> None:
    if isinstance(ip, IPv6Address):
        if ip.scope_id: raise Blocked(ip)                     # zone ids (fe80::1%eth0)
        inner = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if inner is None and ip in NAT64: inner = IPv4Address(int(ip) & 0xFFFFFFFF)   # 64:ff9b::/96, is_global=True!
        if inner is not None: check(inner)
    if not ip.is_global or ip.is_multicast: raise Blocked(ip)
```
Other rules: schemes `http`/`https` only, ports 80/443 only, `trust_env=False` on this client (an env proxy would
bypass the check or fail it on a private proxy IP), reject `connect_unix_socket`; wrap `socket.gaierror` as
`httpcore.ConnectError`. Redirects: `follow_redirects=False` and a manual loop (max 5) so every hop gets scheme/port,
robots and host-limiter checks; the backend re-validates every new connection anyway. Size caps: reject on
`Content-Length` > cap, then count `aiter_bytes()` (decoded bytes, so gzip bombs are capped) and abort at 50 MB PDF /
5 MB HTML. Content-type allowlist checked before reading the body (`application/pdf`, `text/html`,
`application/xhtml+xml`, `application/xml`, `text/xml`, `application/json`, `application/atom+xml`, `text/plain`);
also accept `application/octet-stream` only if the first bytes are `%PDF-`. One guarded client can serve API hosts too
(they resolve to public IPs; cost is one check per new connection), so there is no API/non-API transport split.
Local sidecars (SearXNG, GROBID on 127.0.0.1) cannot use it — they need a plain loopback-only client;
AGENTS.md should name that exception explicitly.
**Unit test vectors** (§19.3), run against `check()` and `Guarded.connect_tcp` with a fake resolver: `127.0.0.1`,
`127.1`, `2130706433`, `0x7f.0.0.1`, `017700000001`, `0177.0.0.1` (httpx rejects as InvalidURL), `0.0.0.0`, `[::]`,
`[::1]`, `[::ffff:127.0.0.1]`, `[::ffff:7f00:1]`, `[64:ff9b::a9fe:a9fe]`, `[2002:a9fe:a9fe::]`, `[fe80::1%25eth0]`,
`[fc00::1]`, `100.64.0.1`, `169.254.169.254`, `192.168.0.1`, `198.18.0.1`, `localhost.`, `*.nip.io`; DNS rebinding
(fake resolver returns public then private: connection must use the first validated IP, never re-resolve); mixed
answers (one public + one private → block); redirect 302 → `http://169.254.169.254/` and → `file:///`;
6th redirect; oversize body without `Content-Length`; disallowed content-type.

## 6. robots.txt
stdlib `urllib.robotparser` (verified 3.12.14): has `crawl_delay()`/`request_rate()`, but **ignores `*`/`$`
wildcards** (`Disallow: /*.pdf$` did not block `/x/file.pdf`) and uses first-match, not RFC 9309 longest-match
(`Allow: /src/ok` after `Disallow: /src` still blocked). Its `read()` uses `urllib` directly, bypassing `PoliteClient`.
**Protego** 0.7.0 (2026-09-21, BSD-3, Scrapy project, zero deps, pure Python) got all four cases right and returns
`crawl_delay` 15.0. **Recommendation: Protego**, fed by `PoliteClient`: fetch `/robots.txt` per scheme+host,
cache 24 h in `http_cache` (RFC 9309 §2.4: SHOULD NOT use a cached copy > 24 h), 2xx → parse, 4xx → allow all,
5xx/unreachable → disallow all (RFC 9309 §2.3.1), follow ≤5 redirects; `Crawl-delay` → `HostLimit.defer`/interval.
"Non-API" = any fetch not addressed to a connector's documented API endpoint: OA full-text URLs (`best_oa_location`,
publisher landing pages, CORE download links), `arxiv.org/pdf|html`, Europe PMC rendered pages, SearXNG result pages.
API endpoints (`api.openalex.org`, `api.crossref.org`, `export.arxiv.org/api`, `api.opencitations.net`, eutils, …) are
governed by their API terms, not robots: `export.arxiv.org/robots.txt` is `Disallow: /` for `*` (live check), so
applying robots to APIs would disable arXiv entirely. arxiv.org disallows `/src` and `/e-print` (LaTeX source).

## 7. Test replay
- **vcrpy 9.4.2** (MIT, py ≥3.11): released 2026-11-02 after a burst of 10 releases since 2026-10-25 (8.5.0 → 9.4.2).
  httpx support: patches `httpx.(Async)HTTPTransport.handle_(async_)request` and the httpx2 equivalents
  (`vcr/stubs/httpx_stubs.py`); no open httpx bugs (GitHub search 2026-11-03: 2 open issues mention httpx, neither
  relevant). 9.4.0 fixed requests **escaping a write-protected cassette** (#979, #1051) — matters for "missing
  cassette must fail". Lock ≥9.4.2 once it passes the 7-day `exclude-newer` gate (2026-11-09); 9.2.0 until then.
- `record_mode="none"`: a request with no matching interaction (missing or empty cassette) raises
  `CannotOverwriteExistingCassetteException` (verified). Config: `match_on=("method","scheme","host","port","path","query","body")`,
  `filter_headers=["authorization","x-api-key","cookie"]`, `filter_query_parameters=["api_key","mailto","email"]`
  (`mailto` is the user's email = PII), `before_record_response` dropping `set-cookie`, `decode_compressed_response=True`.
  vcrpy reads whole bodies while recording; replay returns a `ByteStream`, so streaming size-cap code still runs.
- **pytest-recording 0.14.1** (MIT): default record mode is `none`; `--block-network` (with
  `--allowed-hosts=127.0.0.1,::1,localhost` for testcontainers Postgres) catches anything that bypasses vcrpy.
  0.14.1 (2026-11-02) fixes `--block-network` breaking asyncio loops on **Windows** (#212); 0.14.0 until the age gate.
- **Unit tests** (retry, Retry-After, breaker, size caps, redirects): `httpx.MockTransport` (built into httpx) — no
  respx needed (respx 0.23.1, BSD-3, is fine but redundant). SSRF tests target `check()`/`Guarded` with a fake resolver;
  cassettes never exercise pinning, because vcrpy short-circuits above the pool.
- Coexistence: no API/full-text split needed, as long as the guarded transport subclasses `AsyncHTTPTransport`.

## 8. Postgres UNLOGGED `http_cache`
SQLAlchemy 2.1.5 (locked) renders `Table(..., prefixes=["UNLOGGED"])` as `CREATE UNLOGGED TABLE http_cache (...)`
(verified); Alembic `op.create_table(..., prefixes=["UNLOGGED"])` passes it through; ORM: `__table_args__ = {"prefixes": ["UNLOGGED"]}`.
Autogenerate does not diff logged/unlogged, so write it by hand in the revision. PG 18 docs: unlogged data skips WAL,
"is automatically truncated after a crash or unclean shutdown", is not replicated, and its indexes are unlogged too.
`pg_dump` dumps unlogged **data** unless `--no-unlogged-table-data` is given (schema always). For a cache that is fine;
recommend `researchly backup` pass `--no-unlogged-table-data` (smaller backups; cache rebuilds). Native mode: a killed
pg0 (Job Object close, `taskkill /F`) empties the cache — harmless, but `researchly stop` should stop Postgres cleanly.

## Recommended stack
| Concern | Choice | Pin | Licence | Why |
|---|---|---|---|---|
| HTTP client | httpx (+ httpcore) | `httpx==0.28.1`, `httpcore==1.0.9` (move to runtime deps) | BSD-3 | Already locked and used by litellm/openai; network-backend hook enables pinning |
| Rate limit | custom `HostLimit` (next-at + `asyncio.Semaphore`) | stdlib | — | Exact for §8.2 table; same state absorbs Retry-After and Crawl-delay; in-process suffices |
| Retry | hand-rolled loop, full jitter, RFC 9110 Retry-After | stdlib (`email.utils`) | — | Must interleave limiter + breaker per attempt; ~15 lines |
| Circuit breaker | custom, 5 fails → open 60 s (×2 to 15 min), 1 probe | stdlib | — | No maintained asyncio library; needs open-until-Retry-After |
| Cache | custom UNLOGGED `http_cache`, key = normalized ID | SQLAlchemy/Alembic (locked) | MIT | APIs send no cache validators; hishel has no Postgres store and keys by URL |
| SSRF | guarded `httpcore.AsyncNetworkBackend` + `AsyncHTTPTransport` subclass | stdlib `ipaddress` | — | Pins IP, keeps URL/Host/SNI; vcrpy-compatible; prototype passed |
| robots.txt | Protego | `protego==0.7.0` | BSD-3 | RFC 9309 wildcards + longest match; stdlib gets both wrong |
| Cassettes | vcrpy + pytest-recording | `vcrpy==9.4.2`, `pytest-recording==0.14.1` (after 2026-11-09; 9.2.0/0.14.0 until then) | MIT | httpx + httpx2 stubs; cassette-escape and Windows block-network fixes |
| Unit mocks | `httpx.MockTransport` | (httpx) | BSD-3 | Built in; respx redundant |

## Risks
1. **Private httpx API**: the guarded transport replaces `AsyncHTTPTransport._pool`. Pin httpx exactly; a contract test
   asserts the pool's backend is `Guarded` (catches silent regressions on upgrade). httpx is quiet (last release 2024-12);
   httpx2 is the likely successor, and switching would mean redoing this hook.
2. **Interpreter `ipaddress` drift**: dev venv is 3.12.0 (pre-CVE-2024-4032). Explicit unwrapping of mapped/6to4/
   Teredo/NAT64 avoids depending on it; still recommend `requires-python >=3.12.4` and re-creating the venv.
3. **Corporate proxies / TLS interception**: `trust_env=False` ignores `HTTPS_PROXY` and `SSL_CERT_FILE`; users behind
   mandatory proxies cannot fetch full text. Deferred: an explicit proxy setting (proxy host exempt from the IP check).
4. **arXiv source and pacing**: robots disallows `/src` and `/e-print` and sets `Crawl-delay: 15`, which conflicts with
   the spec's "arXiv HTML/LaTeX/PDF" path. Prefer `arxiv.org/html` + PDF; LaTeX via API terms needs a spec decision.
5. **vcrpy churn**: 10 releases in 9 days, including behaviour changes (`filter_post_data_parameters` now applies to every
   method; 3.11+). Pin exactly, re-record once on upgrade.
6. **In-process limits**: correct only while one process does outbound HTTP; a separate procrastinate worker would
   double effective rates.
7. **UNLOGGED truncation** after unclean shutdown wipes the cache (by design); first run after a crash re-spends the
   OpenAlex search budget (≤50 searches/day guard still applies).
8. **Local sidecars** (SearXNG/GROBID on loopback) need a documented exception to "all HTTP via PoliteClient".

Sources: PyPI JSON for every package above (fetched 2026-11-03); github.com/mjpieters/aiolimiter (releases, issues);
github.com/vutran1710/PyrateLimiter; github.com/jd/tenacity/releases (9.1.3 `wait_exception`);
github.com/hynek/stamina/blob/main/CHANGELOG.md; aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter;
rfc-editor.org/rfc/rfc9110#section-10.2.3; github.com/danielfm/pybreaker (README, defaults);
github.com/fabfuel/circuitbreaker (defaults); resilience4j.readme.io/docs/circuitbreaker;
github.com/karpetrosyan/hishel (docs/storages.md, docs/metadata.md, `_policies.py`, `_async_cache.py`);
github.com/encode/httpcore/blob/1.0.9/httpcore/_async/connection.py; github.com/Zaczero/pkgs/tree/main/httpx-secure;
github.com/tachyon-oss/drawbridge; github.com/gradio-app/safehttpx; github.com/pydantic/httpx2;
nvd.nist.gov/vuln/detail/CVE-2024-4032; rfc-editor.org/rfc/rfc9309; github.com/scrapy/protego;
arxiv.org/robots.txt, export.arxiv.org/robots.txt (2026-11-03); github.com/kevin1024/vcrpy (releases, `vcr/patch.py`,
`vcr/stubs/httpx_stubs.py`); github.com/kiwicom/pytest-recording (README, changelog);
postgresql.org/docs/18/sql-createtable.html, postgresql.org/docs/18/app-pgdump.html.
