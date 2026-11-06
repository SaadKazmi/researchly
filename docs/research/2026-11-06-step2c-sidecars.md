# Step 2c research: local sidecars, SearXNG and GROBID (2026-11-06)

All probes ran on 2026-11-06 on the Windows 10 dev box (i7-8700) with Docker Desktop 29.6.1 (VM: 12 CPUs, 7.7 GiB). They were keyless, with no email.
- **SearXNG:** the pinned image `searxng/searxng:2026.10.25-54ca1d9cf`, the same commit as native mode (ADR 0004), run on `127.0.0.1:18766`.
- **GROBID:** `grobid/grobid:0.9.1-crf`, pulled for the test and removed afterwards.
- Scratch scripts and outputs are outside the repo. Every container was removed; the pre-existing SearXNG images were kept.

## Recommendations

| Decision | Evidence |
|---|---|
| **2c = SearXNG wiring only.** Add the URL plumbing, a sidecar client, a web-search service, the `web_search` capability and a nightly live check. | §A |
| **SearXNG does general web and code only.** Put `keep_only: [brave, mojeek, yahoo, github]` in settings.yml; scholarly engines stay off. Our connectors already query those sources politely, and SearXNG's copies of them hit the same per-IP quotas with no limiter, robots or budget logic. | §A2 |
| **Switch settings.yml to `formats: [json]` and `outgoing.request_timeout: 5.0`.** At the default 3 s, a cold arXiv query and one Brave query in 10 timed out; at 5 s, Brave had 0 timeouts in 10 queries. | §A2, §A5 |
| **Give the backend the URL through `RESEARCHLY_SEARXNG_URL`.** In native mode it is `http://127.0.0.1:<port>`, so the CLI must pick the port *before* spawning the app. In Docker mode it is `http://searxng:8080`. | §A4 |
| **Pin the sidecar client to its configured origins; do not make it "loopback-only".** In Docker mode the service names resolve to private 172.x addresses. The client follows no redirects, ignores env proxies and caps the response size. | §A4, §B10 |
| **Probe for `web_search` in the background, not once at startup.** The app starts before SearXNG is healthy (native mode takes 12–15 s). | §A4 |
| **Defer GROBID to step 4.** Wire it there together with `paper_refs` and the PDF reader. | §B9 |
| **Drop native GROBID and keep it Docker-only.** Windows has no `pdfalto` binary. GROBID ships no runnable release; it needs a Gradle build or reassembly, and it takes about 1 GB of disk and 3–4 GB of RAM. | §B7 |
| **The largest reference gap is arXiv preprints, and arXiv HTML closes it without GROBID.** OpenAlex, Crossref and OpenCitations hold **0** references for 3 of 3 arXiv-only preprints. The arXiv HTML has all of them, split into author, title and venue blocks. | §B9 |
| **When GROBID is wired, put it on the `internal` network only, send `consolidate*=0` and set `mem_limit: 4g`.** Consolidation calls Crossref directly, bypassing PoliteClient. With no network it fails gracefully and parsing still returns 200. GROBID was OOM-killed under a 2.5 GB cap. | §B8, §B10 |

---

## Part A: SearXNG

### A1. JSON API
- `GET /search?q=…&format=json` returns 403 unless `json` is listed in `search.formats`; the upstream default is `[html]`. Our `infra/searxng/settings.yml`, and `settings_text()` in `cli/src/researchly_cli/searxng.py`, set `formats: [html, json]`, `limiter: false`, `public_instance: false` and `image_proxy: false`. A test keeps the two files equal.
- **Recommendation: `formats: [json]`.** I tested it: `format=json` works, a plain HTML search returns 403, `/healthz` still returns 200, and `GET /` (the static index) still returns 200. Nothing in Researchly needs the HTML UI.
- **Useful parameters:**
  - `engines=a,b` or `categories=general|it|science`;
  - `pageno`, `language` and `time_range` (`day|month|year`, only for engines with `time_range_support`);
  - `timeout_limit`. It can only *lower* the overall timeout (`searx/search/__init__.py`: `min(default_timeout, query_timeout)` when `max_request_timeout` is unset). Each engine's own HTTP timeout is still `engine.timeout` (default `outgoing.request_timeout`, 3.0 s), so set it in settings.yml, not per request.
- **`GET /config`** lists the loaded engines with their `enabled`, `categories`, `timeout`, `paging` and `time_range_support`. It is useful for `doctor` and the capability row.
- **Limiter and bot detection:** with `limiter: false`, nothing is blocked and no Valkey is needed. Bot detection still logs `ERROR … X-Forwarded-For nor X-Real-IP header is set!` on every request, which is noise only. Sending `X-Real-IP: 127.0.0.1` from our client silences it. A missing `limiter.toml` logs a warning, which is harmless.

### A2. Engines, measured live (keyless, one home IP)
**Per engine:** 3 queries each, 2 s apart, using "transformer attention mechanism", "retrieval augmented generation evaluation" and "CRISPR off-target effects". "Default" is the state in upstream `settings.yml` at the pin.

| Engine | Default | Result | Verdict |
|---|---|---|---|
| **brave** | on | 25–26 results, 0.7–1.3 s. 1 timeout in 10 at 3 s; 0 in 10 at 5 s | **Keep** (general) |
| **mojeek** | `inactive` | 10 results, 0.6–2.8 s, 0 failures in 13 calls | **Keep** (needs `inactive: false`) |
| **yahoo** | disabled | 7 results, 0.8–1.4 s, 0 failures in 13 calls | **Keep** (needs `disabled: false`) |
| **github** | on | 26–30 results, 1.4–1.8 s. Uses the keyless REST search API (GitHub documents 10 req/min unauthenticated) | **Keep** (code, `categories=it`) |
| duckduckgo | on | **CAPTCHA on the first query**, then suspended for 3600 s | Drop |
| startpage | `inactive` | CAPTCHA on the first query | Drop |
| google, qwant | disabled | `access denied`, then suspended | Drop |
| bing | disabled | 10, 0 and 10 results; the top hit for "CRISPR off-target effects" was an adult site | Drop |
| wikipedia | on | 0 results for all three queries (it returns only an exact article match) | Drop |
| stackoverflow | on | 10, 5 and 0 results; 2 `HTTP error` in a 10-query burst (the keyless StackExchange quota is per IP) | Optional; drop for now |
| huggingface | disabled | 0 results | Drop |
| arxiv | on | Timed out at 3 s on the first (cold) query, then suspended; afterwards 10 results in 0.4–0.8 s | **Off**: our connector covers it |
| crossref | disabled | 17–19 results, 0.5–1.6 s; `content` carries `<jats:p>` markup; 1 crash in 10 | **Off**: our connector covers it |
| openalex | disabled | 10, 10, then **429** (`too many requests`, suspended 180 s). Called directly, anonymous `/works?search=` returned 429 "Anonymous search is temporarily rate-limited while the search cluster is under elevated load" (`Retry-After: 10`), while singletons were fine (`X-RateLimit-Remaining-USD: 0.098`) | **Off**: it spends the per-IP OpenAlex budget (spec §8.1.1) |
| pubmed | on | 20 results, 1.8–2.4 s; 1 crash in 10 | **Off**: our connector covers it |
| semantic scholar | on | Timed out cold; then 10 results in 1.4–1.9 s three times; then **`parsing error` in 8 of 10** burst queries. It calls `www.semanticscholar.org/api/1/search`, the website's internal API, not the Graph API | **Off** (spec §8.1: keyless S2 is unreliable) |
| google scholar | on | `unexpected crash`: `TooManyRedirects` to Google's block page, then suspended | **Off** (spec §8.1 excludes it) |
| europepmc, openairepublications | on | 20 / 10 results, 0.4–1.5 s | **Off**: our connectors (Europe PMC; OpenAIRE at tier 2) cover them |

**Why scholarly engines stay off in SearXNG.**
- They hit export.arxiv.org, api.crossref.org, api.openalex.org and eutils from the same IP, with no 3 s arXiv spacing, no Crossref concurrency of 1, no OpenAlex budget guard, no robots check and no shared cache.
- They would compete with our connectors for per-IP quotas (spec §8.1).
- Their results are a subset of what the connectors already return with richer metadata.
- The web engines still surface papers: in the general fan-out (§A5), **65 of 369 results (18%) were arxiv.org URLs and 19 were PubMed or PMC**. We map those to IDs (§A3).

**Suspension times** (upstream `search.suspended_times`, in seconds):

| Error | Suspension |
|---|---|
| CAPTCHA | 3600 |
| Access denied | 180 |
| Too many requests | 180 |
| Cloudflare CAPTCHA | 1,296,000 (15 days) |
| reCAPTCHA | 604,800 (7 days) |

A ban hurts for a long time, so our client must pace itself (one query per second, at most 2 in flight). Parallel Scouts (spec §6) must not fan out unthrottled.

**Proposed settings.yml** (and `settings_text()`):
```yaml
use_default_settings:
  engines:
    keep_only: [brave, mojeek, yahoo, github]
server:
  limiter: false
  public_instance: false
  image_proxy: false
search:
  formats: [json]
outgoing:
  request_timeout: 5.0
engines:
  - name: mojeek
    inactive: false
  - name: yahoo
    disabled: false
```
- **What was tested:** this shape with `wikipedia` and `stackoverflow` also in `keep_only`. `/config` then listed exactly those six engines. The `ahmia`/`torch` "can't register engine" errors went away, and `/healthz` returned 200 after 7.4 s.
- **CLI test:** the mock-engine test (`cli/tests/test_searxng.py`) overwrites both `use_default_settings` and `engines`, so it keeps working.
- **Pin-bump checklist (ADR 0004):** add a check that `keep_only` names still exist.

**ToS note.** SearXNG's `brave`, `mojeek` and `yahoo` engines scrape HTML result pages, the same way `ddgs` does. Spec F2.1 makes `ddgs` opt-in because "it scrapes and carries ToS risk", and the same reasoning applies to SearXNG's general engines. This needs an explicit decision, recorded as an ADR 0004 addendum (spec delta 6).

### A3. Response shape and mapping
- **Top level:** `query`, `results`, `answers`, `corrections`, `infoboxes`, `suggestions`, `unresponsive_engines`. `unresponsive_engines` is a list of `[engine, reason]` pairs, for example `["brave", "timeout"]`, `["duckduckgo", "CAPTCHA"]`, `["brave", "Suspended: timeout"]`. `number_of_results` was `null`.
- **Fields on every result:** `url`, `title`, `content` (the snippet; it can contain HTML, so strip it), `engine`, `engines` (all engines that returned it, after merging), `score`, `category`, `template`, `positions`, `publishedDate` (ISO string or `null`) and `pubdate`.
- **Extra fields by template:**
  - **`paper.html`** (scholarly engines): `doi`, `authors`, `journal`, `publisher`, `pdf_url`, `tags`, `type`.
  - **`packages.html`** (github): `package_name`, `maintainer`, `popularity` (stars), `license_name`, `source_code_url`, `tags`.
  - **Brave:** `publishedDate` is the page's crawl date, not a publication date. Never treat it as a paper's year.
- **Mapping to a paper.** If `url`, or a `doi` field, parses to an `ExternalId` through `connectors/ids.py`, emit a stub `PaperRecord(source="web", ids=[…], title=…)` and let identity resolution enrich it ($0 OpenAlex singleton). Otherwise emit a `WebResult(url, title, snippet, engines, published)`.
  - `ids.py` already handles `arxiv.org/abs|pdf/<id>[vN][.pdf]` over http or https, `doi.org` / `dx.doi.org`, `pubmed.ncbi.nlm.nih.gov/<pmid>` and `pmc.ncbi.nlm.nih.gov/articles/PMC…`.
  - **Gaps seen in live results:**
    - `oadoi.org/<doi>`, used by the crossref and openalex engines;
    - `www.ncbi.nlm.nih.gov/pubmed/<pmid>`;
    - `europepmc.org/article/MED/<pmid>` (and `PPR…` preprint IDs, which have no scheme);
    - `arxiv.org/html/<id>`;
    - `dl.acm.org/citation.cfm?id=…`, which carries no DOI and stays a WebResult.

  A small URL-to-ID mapper, plus unit tests on these strings, covers the gaps.
- **Latency:** see §A5. A web-search tool should allow about 10 s end to end.

### A4. Plumbing: URL discovery, health and capability
**Current state:**
- **Native:** `native.start` spawns uvicorn *first*, then picks `sx_port = free_port(8766)` and spawns granian on `127.0.0.1`. The port is recorded in `state.json` (`searxng_port`), but the app's environment never receives it.
- **Docker:** `compose.yaml` runs `searxng` with no published port, on `[internal, default]`. The `app` service has no SearXNG variable, and `searxng` has no healthcheck, so `up --wait` waits only until the container is running.
- **Backend:** `Settings` has no SearXNG field.

**Proposal:**
- **Settings:** add `searxng_url: str | None = None` (read from `RESEARCHLY_SEARXNG_URL`). `None` means web search is off.
- **Native:** pick `sx_port` before spawning uvicorn. Add `RESEARCHLY_SEARXNG_URL=http://127.0.0.1:{sx_port}` to the app env only when `searxng_home` is not `None`. Keep `state.json` as it is.
- **Docker:** set `RESEARCHLY_SEARXNG_URL: http://searxng:8080` on `app`. Add a SearXNG healthcheck; the image has busybox `wget` and `python3`:
  ```yaml
  healthcheck:
    test: ["CMD", "wget", "-q", "--spider", "http://127.0.0.1:8080/healthz"]
  ```
  Do not add `depends_on: searxng` to `app`, because web search is optional.
- **Sidecar client** (for example `connectors/sidecar.py`; AGENTS.md already reserves this exception to "all HTTP through PoliteClient"):
  - one `httpx.AsyncClient(base_url=<configured>, trust_env=False, follow_redirects=False)`;
  - callers pass relative paths only, so the target can never come from user or LLM input;
  - short timeouts (connect 2 s; total about 15 s for SearXNG);
  - a response cap (2 MB JSON);
  - a `HostLimit(1.0, 2)` for pacing (the class already exists in `connectors/http.py`).

  It must **not** use `GuardedTransport`, which refuses loopback and private addresses. It also must not be "loopback-only": `searxng` resolves to a private 172.x address on the compose network. The guard is "only the origin the CLI configured".
- **Health:** `GET /healthz` returns 200 `OK`, as the CLI's `searxng.healthy()` already uses. Measured time to healthy: Docker 5–7 s after `run`; native 12–15 s after spawn (ADR 0004).
- **Capability:** the app starts before SearXNG is healthy, so a one-shot check at startup would record "off".
  - Run a lifespan background task that polls `/healthz` for up to 60 s, then upserts `capability('web_search', {"state": "ready"|"off", "reason": …})`. Reasons: "not configured" when there is no URL; "unreachable: …" otherwise.
  - Per call, a connection error or 5xx becomes `SourceUnavailable("web", …)`, so a run degrades and never crashes.
  - Report `unresponsive_engines` per engine the way `federated_search` reports sources.
- **Caching:** cache results 24 h, matching `TTL_SEARCH` in `http.py`. Optional; key on the query plus category.

### A5. Measurements: fan-out latency and failures
Each run sent 10 different research queries one at a time, 1.5–2 s apart.

| Engine set | Median (s) | p90 (s) | Max (s) | Results per query | Failures |
|---|---|---|---|---|---|
| **General** (proposed settings, `categories` default: brave + mojeek + yahoo + wikipedia) | 0.96 | 1.34 | 2.44 | about 37 | **0** |
| **`categories=it`** (github + stackoverflow) | 1.89 | 2.24 | 2.90 | about 28 | stackoverflow `HTTP error` ×2 (and suspended ×2) |
| **Science** (6 scholarly engines, 3 s timeout; reference only) | 1.70 | — | 3.40 | 60–80 | semantic scholar `parsing error` 8/10; crossref and pubmed 1 crash each |
| **General, defaults** (brave + duckduckgo + startpage + …) | — | — | 1.95 | 40–45 | duckduckgo `CAPTCHA` and startpage `Suspended: CAPTCHA` on every query |

In the general run, results per engine were brave 244, mojeek 100 and yahoo 70.

---

## Part B: GROBID

### B6. Docker images (Docker Hub, read 2026-11-06)
Latest release: **0.9.1** (2026-08-04, Apache-2.0, repo `grobidOrg/grobid`).

| Tag | Index digest | Per-arch manifests | Compressed size |
|---|---|---|---|
| **`0.9.1-crf`** (CRF only, CPU) | `sha256:223957791ac2bbe48609dcc58a689b16b60baeae13a8734ef440ae6bfb38f4cd` | amd64 `sha256:eb306e6d494f6f7e89b35bbaf3b4925afd58c6a5638c775f2a1c35bfd3c5db0d`; arm64 `sha256:0897f025b24969badc6766dd3b1a785d906dacae17ceee50a11e8974b0347ded` | 510 MB / 506 MB |
| `0.9.1-full` (DeLFT deep-learning models + CRF) | `sha256:35c83afedde0469ab7aef01870a26f6c402ae96cba535db5269f223fadc534ce` | amd64 only, `sha256:cf3e24bba3a3e8492488bd07e0c3beb2cd681724a760815488c7e44cb7f47cf3` | **14.8 GB** |
| `0.9.0-crf` / `0.9.0-full` | `sha256:24ba90eb…` / `sha256:52774a83…` | crf amd64+arm64; full amd64 | 501 MB / 10.3 GB |

- **The `-crf` image is the right one.** It is about 1.1 GB on disk: `/opt/grobid` 819 MB (models 439 MB, the service's jar `lib/` 247 MB, `pdfalto` 49 MB, lexicon 78 MB) plus the Temurin 21.0.11 JRE (159 MB) and the base OS.
- **Upstream on `-full`:** it adds "2–4 points F1 for bibliographical reference parsing" (doc/Grobid-docker.md) but recommends a GPU and is amd64-only. It does not fit a keyless, CPU-only default.
- **Already in compose:** `infra/compose.yaml` has the service, `grobid/grobid:0.9.1-crf`, under `profiles: [grobid]`. **The CLI never starts it:** `docker.start` runs `up -d --wait app searxng`, and only `down` uses `--profile *`.
- **Pin by digest** when it is wired (spec §19.7, release step).

### B7. Native GROBID
- **JVM, no admin and keyless — both routes work:**
  - **`jdk4py==21.0.8.2`** (PyPI, GPLv2 with the Classpath Exception; a jlinked `java.se` runtime, so pdfbox's AWT is present). There are wheels for all five targets (`win_amd64`, `macosx_11_0_arm64`, `macosx_10_9_x86_64`, `manylinux_2_17_x86_64` and `aarch64`), 30–34 MB each. They are hash-lockable in an app-private venv, the same way SearXNG is installed. The latest is 25.0.2.1 (JDK 25); GROBID builds on JDK 21.
  - **Adoptium API:** `GET https://api.adoptium.net/v3/assets/latest/21/hotspot?image_type=jre&os=<os>&architecture=<arch>&vendor=eclipse` returns `jdk-21.0.13+9` with a `checksum` (sha256) and a GitHub release link. Sizes: windows x64 zip 49 MB; mac aarch64 48 MB; mac x64 42 MB; linux x64 52 MB; linux aarch64 51 MB.
- **GROBID itself is the blocker:**
  - The 0.9.1 GitHub release ships only three thin jars (`grobid-core` 17 MB, `grobid-service` 3.3 MB, `grobid-trainer` 16 MB, each with a sha256 asset digest). It has no runnable distribution and no `grobid-home`.
  - The documented route is `./gradlew clean assemble` from source, which downloads Gradle plus about 250 MB of Maven dependencies.
  - The alternative is to reassemble the runtime: `grobid-home/` from the tag's source tree (models 492 MB in git, `pdfalto` 38–40 MB per platform), plus the 247 MB `lib/` taken from Maven or from the image's layers.
  - Either route is a brittle installer to maintain on every bump.
- **Windows: not possible natively.**
  - `grobid-home/pdfalto/` at 0.9.1 has only `lin-64`, `lin_arm-64`, `mac-64` and `mac_arm-64`.
  - The `pdfalto` releases (v0.6.1–v0.6.3, 2026-07/09) publish only linux-64, linux-arm64, mac-64 and mac-arm64.
  - Wapiti has a `win-64` DLL, but without `pdfalto` there is no PDF parsing.
  - The spec's "not Windows native" therefore holds and is still current.
- **macOS arm64 and Linux:** the binaries exist (`mac_arm-64` `pdfalto` and `libwapiti.dylib`). Not run here.
- **Footprint, measured from the image:**
  - **Disk:** about 1.0–1.1 GB for JRE + `grobid-home` (one platform) + `lib/`. The spec estimates "optional JDK + GROBID ~0.6 GB".
  - **RAM:** 3.1 GiB RSS idle (no `-Xmx`). Peak 4.2 GB under a 4 GiB cap with default heap. **OOM-killed under a 2.5 GiB cap** even with `-Xmx1536m`; most of the memory is native (Wapiti models), not heap. Upstream asks for 4 GB for fulltext (doc/Grobid-docker.md).
- **Native binding is unsafe by default.** Jetty listens on **`0.0.0.0:8070` and the admin connector on `0.0.0.0:8071`**, and `grobid.yaml` sets `corsAllowedOrigins: "*"`. A native install would need a `server.applicationConnectors[].bindHost: 127.0.0.1` override (and the same for admin), and CORS turned off.
- **Verdict:** native GROBID costs about 1 GB of disk and 4 GB of RAM, plus a custom installer for two OS families, for a feature whose largest gap is closed without it (§B9). **Drop it; keep GROBID Docker-only.**

### B8. API, throughput and consolidation
**Endpoints**, all `POST` multipart `input=<pdf>` unless noted, returning TEI XML:
- `/api/processHeaderDocument`;
- `/api/processReferences`; `includeRawCitations=1` adds `<note type="raw_reference">`;
- `/api/processFulltextDocument`, which returns header + body + figures/tables + `listBibl` and in-text `<ref type="bibr" target="#bN">`;
- `/api/processCitationList`, form-encoded, `citations=<raw string>` repeated;
- health: `GET /api/isalive` returns `true`; `GET /api/version` returns `{"version":"0.9.1"}`.

**Run on arXiv 1706.03762** (2.2 MB, 15 pages, fetched keyless):

| Step | Time |
|---|---|
| Container start to `isalive` | 25–30 s |
| First request (model load) | **16–25 s** |
| Warm `processHeaderDocument` | 1.1–1.2 s |
| Warm `processReferences` | 1.4–1.6 s |
| Warm `processFulltextDocument` | 2.1–2.6 s |
| Warm `processCitationList`, 40 strings | **0.14–0.17 s** |

For comparison, Docling takes 0.7 s per page warm, about 10 s for this paper (ADR 0005).

**Quality on this paper:**
- **Header:**
  - Title correct; abstract correct; `arXiv:1706.03762v7` found; date 2023-08-02 (v7).
  - **10 authors for 8**: "Google Brain" and "Google Research" were parsed as people.
  - **Affiliations wrong**: every author got "University of Toronto". 4 of 8 emails were found.
  - The CRF header model is weak on this messy byline.
- **References:** 40 of 40 found; titles 39/40 ("Layer normalization" was missed); years 40/40; 16 typed `arXiv` idnos plus 6 untyped `CoRR, abs/…`; DOIs 0 (the paper prints none). `processCitationList` on the 40 raw strings gave the same quality.
- **Body:** 22 numbered sections correct. All 58 in-text citations link to a `biblStruct`; these citation contexts are valuable for step 5.
- **Tables:** 3 of 4 found, with **columns misaligned** (for example `'2048 8'` merged in one cell, header cells collapsed). Do not count on GROBID TEI tables as the table source (an ADR 0005 delta).

**Consolidation.** The `consolidate*` parameters default to 0 per the service docs. I checked this only for the header endpoint, where the output is byte-identical with and without `consolidateHeader=0`. Researchly should always send 0 explicitly.
- With `consolidateCitations=1`, GROBID itself calls Crossref (log: `CrossRef API tier: Public (no mailto, no token) - concurrency: 1, rate: 5 req/sec`) or biblio-glutton. That traffic bypasses PoliteClient, robots and our cache, so **never enable it**. Match the references through our connectors instead.
- I ran the image with `--network none` and `consolidateCitations=1`. It logged `UnknownHostException` per reference, **still returned 200 with all 40 references parsed**, and took 12 s instead of about 1.5 s. So a no-egress network is a safe backstop.

### B9. Value against what we already have
**Reference counts from the existing sources** (OpenAlex singleton, Crossref `/works/{doi}`, OpenCitations index v2 `/references`; live, keyless). "n/a" means Crossref returned 404 because the paper has a DataCite DOI.

| Paper | OpenAlex `referenced_works` | Crossref (open) | OpenCitations |
|---|---|---|---|
| arXiv 1706.03762 (Transformer) | 28 (paper has 40) | n/a | 0 |
| **arXiv 2310.06825 (Mistral 7B, preprint only)** | **0** | n/a | **0** |
| **arXiv 2405.04434 (DeepSeek-V2, preprint only)** | **0** | n/a | **0** |
| **arXiv 2611.02500 (Nov 2026 preprint)** | **0** | n/a | **0** |
| ACL N19-1423 (BERT) | 52 | **0 deposited** | 0 |
| IEEE CVPR 2016 (ResNet) | 64 | 49 | 26 |
| Nature 2015 (deep learning) | 62 | 103 | 40 |
| Elsevier Neural Networks 2015 | 1029 | 888 | 456 |
| ACM KDD 2016 (XGBoost) | 21 | 25 | 12 |
| bioRxiv 2021 | 39 | 35 | 29 |
| Science 2006 | 9 | 17 | 8 |
| PLOS ONE 2016 | 67 | 70 | 43 |
| MDPI Sensors 2021 | 115 | 115 | 94 |

- **Published papers with a Crossref DOI** are covered: 9 of 9 have references in OpenAlex and/or Crossref. GROBID adds little there.
- **arXiv-only preprints are the hole.** All three API sources returned 0 for 3 of 3, and those are much of ML and CS. **The arXiv HTML tier (2b) already fetches the fix:**
  - 1706.03762 has 40 `li.ltx_bibitem`, 39 split into ≥3 `ltx_bibblock`s (authors / title / venue). 22 carry an arXiv ID or DOI that a regex can read.
  - 2310.06825 has 29 bibitems, all split, 21 with IDs.
  - The HTML got "Layer normalization" right where GROBID missed it.
  - Today `ingest/arxiv_html.py` drops `ltx_bibliography` (`_DROP`), `ingest/pdf.py` drops the back matter, and nothing stores references from text; there is no `paper_refs` table yet.
- **GROBID's unique value** is limited to:
  - PDF-only papers outside arXiv and PMC whose publisher deposits no references (small OA journals, proceedings without DOIs, theses, reports). I could not size this from a keyless sample.
  - User-uploaded PDFs with unknown metadata (header extraction).
  - In-text citation contexts from PDFs (step 5).

  For JATS papers, `ref-list` already carries pub-ids.
- **Recommendation: wire GROBID at step 4** (the PDF reader, F3.3 output `paper_refs`), not in 2c. Nothing consumes references or headers yet. The cheap, high-value step is to keep the bibliography from arXiv HTML and JATS `ref-list` and map IDs via `ids.py`, which needs no sidecar. It belongs with `paper_refs` at step 4, or earlier if the step-3 Reader needs it. GROBID then adds `processFulltextDocument` (or `processCitationList` on Docling's back-matter strings) for PDF-only papers in Docker mode.
- **Native mode with no GROBID** gets the same IDs-first path. Raw strings without an ID are matched by title through the connectors; an LLM parse is the last resort.

### B10. Security
- **TEI parsing:** use `defusedxml.ElementTree.fromstring`, already a dependency and used in `ingest/jats.py` and the arXiv and PubMed connectors. The TEI is derived from an untrusted PDF, so its text is untrusted (spec §19.1 quarantine). Cap the response size in the sidecar client (for example 20 MB).
- **Binding and exposure:**
  - Docker mode publishes no port for `searxng` or `grobid`; only `app` publishes `127.0.0.1:${PORT}`.
  - Inside the containers, SearXNG listens on `[::]:8080`, and GROBID on `0.0.0.0:8070` with admin on `0.0.0.0:8071`. Both are reachable only from the compose networks.
  - Native SearXNG binds `127.0.0.1` through granian's `--host`.
- **GROBID's network:** compose puts it on `[internal, default]`, so it has **egress**. Make it `networks: [internal]` only. Parsing needs no network (verified), and `app` is on `internal` already. Also add:
  ```yaml
  mem_limit: 4g
  healthcheck:
    test: ["CMD", "curl", "-fs", "http://127.0.0.1:8070/api/isalive"]
  ```
  and send one warm-up request after it is healthy. The image runs as root; `cap_drop: [ALL]` and `no-new-privileges` are cheap hardening, but untested here.
- **Authentication:** neither sidecar has any. In Docker mode only containers on the compose networks can reach them.
  - In native mode, any local process, or a web page through CSRF-style GETs, can query SearXNG on loopback. The impact is limited to spending the user's IP on search engines; there are no secrets or writes. Accepted, as for any loopback service; `formats: [json]` keeps the HTML UI's search off.
  - GROBID's `corsAllowedOrigins: "*"` would matter only if its port were published or run natively, which this proposal never does.
- **Identity:** neither sidecar ever receives keys. The SearXNG secret travels only in its environment (ADR 0004).

---

## Spec deltas
1. **§4.1 runtime table, PDF parsing row.** Remove "**GROBID** native via `jdk4py` (JDK 21+ wheel) on Linux/macOS". GROBID becomes **Docker-only** (compose profile `grobid`, `0.9.1-crf`, about 1.1 GB on disk, 4 GB RAM). Native mode on every OS uses the IDs-first path: arXiv HTML bibitems, JATS `ref-list`, PDF back-matter strings, then ID regex, then connector matching, then LLM parsing last.
2. **§4.2.** Drop "the optional GROBID" from the native supervisor's children.
3. **§9.1.**
   - Item 2: drop "GROBID's TEI tables" as a table source; the measured column alignment was broken.
   - Item 3: add "arXiv HTML `ltx_bibitem` and JATS `ref-list`" as reference sources before parsed references. Note that the API sources hold **no** references for arXiv-only preprints.
   - Mirror both in ADR 0005, "Tables".
4. **§22 footprint.** Replace "optional JDK + GROBID ~0.6 GB" with "optional GROBID image ~0.5 GB download / ~1.1 GB on disk, 3–4 GB RAM; Docker mode only".
5. **§19.**
   - Add sidecar rules: reached only at the CLI-configured origins through the sidecar client (relative paths, no redirects, no env proxy, size cap). GROBID on the internal network with no egress, `consolidate*=0` always. TEI through defusedxml and treated as untrusted.
   - AGENTS.md "loopback-only client" becomes "client pinned to configured sidecar origins", because Docker service names resolve to private IPs.
6. **F2.1 and ADR 0004.**
   - SearXNG serves general web and code: `brave`, `mojeek`, `yahoo`, `github`.
   - Its scholarly engines are off, because the connectors own those sources and their quotas.
   - Settings become `keep_only`, `formats: [json]` and `request_timeout: 5`.
   - Record that these engines scrape result pages as `ddgs` does. Explain why SearXNG is acceptable as the default while `ddgs` stays opt-in: it is local, ships no vendored scraper and is suspended on CAPTCHA. Or revisit the `ddgs` wording.
7. **§4.1 capability detection.** Change "at startup the app records" to "the app probes sidecars in the background after startup and records state and reason". The sidecars become healthy after the app does.

## Recommendations for plan 2c
**Scope** (in dependency order):
1. **Settings and plumbing.**
   - Add `Settings.searxng_url` (`RESEARCHLY_SEARXNG_URL`).
   - Native: pick the SearXNG port before spawning the app and pass the URL in the app env only when SearXNG is installed.
   - Docker: set `RESEARCHLY_SEARXNG_URL: http://searxng:8080` on `app`, and add a `wget` healthcheck to `searxng` (no `depends_on`).
   - *Done when* CLI tests assert the env in both modes.
2. **SearXNG settings.**
   - Apply `keep_only: [brave, mojeek, yahoo, github]`, `formats: [json]`, `request_timeout: 5.0` and the mojeek/yahoo overrides to `infra/searxng/settings.yml` and `settings_text()` together.
   - *Done when* the existing equality test and the `searxng`-marked install test pass on five OS.
   - Add a pin-bump check that `keep_only` names exist.
3. **Sidecar client** (`connectors/sidecar.py`): origin-pinned httpx client, relative paths only, no redirects, `trust_env=False`, timeouts, a 2 MB cap and `HostLimit(1.0, 2)`. *Done when* unit tests with `httpx.MockTransport` cover: an absolute URL is refused, a redirect is not followed, an oversize body is refused, and pacing holds.
4. **Web-search service and tool.**
   - `web_search(query, category="general"|"code")` calls `GET /search?format=json&categories=…`.
   - Parse into `WebResult` (strip HTML from `content`; `publishedDate` is not a year), or into a `PaperRecord` stub when the URL or `doi` maps to an ID. Extend the URL mapper for `oadoi.org`, `ncbi.nlm.nih.gov/pubmed/`, `europepmc.org/article/MED/` and `arxiv.org/html/`.
   - Surface `unresponsive_engines` per engine. Optionally cache 24 h.
   - *Done when* tests run on recorded SearXNG JSON fixtures, never live engines (connector replay rule).
5. **Capability.**
   - A lifespan background probe of `/healthz` (up to 60 s) upserts `capability('web_search', {state, reason})`.
   - `/api/system` and `doctor` show it; `doctor` in Docker mode also reports the probe.
   - *Done when* tests cover "not configured", "unreachable" and "ready".
6. **Nightly live check** (`-m nightly`): one query per kept engine, plus one fan-out, asserting non-empty results. It warns only, like the connector live checks.

**Defer:**
- **GROBID wiring → step 4**, with `paper_refs` and the PDF reader.
  - **Why:** no consumer exists yet; the main reference gap (arXiv preprints) is closed better by arXiv HTML; GROBID is Docker-only, opt-in and 4 GB; published DOIs are already covered by APIs.
  - **What to do then:**
    - Make the CLI actually start the `grobid` profile when the user opts in.
    - Compose: `networks: [internal]`, `mem_limit: 4g`, an `isalive` healthcheck, a warm-up request and a digest pin.
    - `RESEARCHLY_GROBID_URL=http://grobid:8070`; the `grobid` capability.
    - `processFulltextDocument` with `consolidate*=0` and `includeRawCitations=1`; `processCitationList` for Docling back-matter strings; defusedxml.
- **Reference extraction from arXiv HTML and JATS → step 4**, or step 3 if the Reader needs it. Today both parsers drop the bibliography; it needs `paper_refs` to land in.
- **Native GROBID: drop.** See §B7: no Windows `pdfalto`, no runnable release, about 1 GB of disk and 4 GB of RAM.
- **Keyed web search** (Tavily, Exa, Brave API) → step 9 settings. `ddgs` stays opt-in.
- **Stack Overflow / Wikipedia engines:** not now. Stack Overflow had keyless quota errors and Wikipedia returned 0 results for research queries. Revisit if Scouts need them.

## Sources
- SearXNG at the pin, read from inside the image:
  - `searx/settings.yml`: `formats`, `request_timeout: 3.0`, `suspended_times`, engine defaults;
  - `searx/search/__init__.py`: the `timeout_limit` logic;
  - `searx/engines/{semantic_scholar,arxiv,brave,github,crossref,pubmed}.py`: endpoints.
- Earlier research and decisions: `docs/research/2026-11-02-step1e-searxng.md`, ADR 0004.
- GROBID:
  - Docker Hub tags API: https://hub.docker.com/v2/repositories/grobid/grobid/tags
  - `gh api repos/grobidOrg/grobid/releases` and the tree at tag 0.9.1 (`grobid-home/pdfalto`, `grobid-home/lib`);
  - `doc/Grobid-docker.md` and `doc/Frequently-asked-questions.md` at 0.9.1: image variants, memory, consolidation;
  - `grobid-home/config/grobid.yaml` in the image.
- pdfalto releases: `gh api repos/kermitt2/pdfalto/releases` (v0.6.1–0.6.3).
- JVM: jdk4py at https://pypi.org/pypi/jdk4py/json and https://github.com/activeviam/jdk4py (`_added_modules.py`); the Adoptium API at https://api.adoptium.net/v3/assets/latest/21/hotspot.
- Reference sources: OpenAlex `/works/doi:…`, Crossref `/works/{doi}`, OpenCitations `/index/v2/references/doi:…`, and arXiv HTML for 1706.03762 and 2310.06825. All keyless, 1–1.5 s apart, arxiv.org 15 s apart.
