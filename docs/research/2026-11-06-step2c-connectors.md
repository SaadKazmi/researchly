# Step 2c research: Tier-2 scholarly connectors and the Opt sources (live-checked 2026-11-06 13:37-13:55 UTC)

Method: official docs plus 2-6 keyless requests per source, User-Agent `Researchly/0.1 (+https://github.com/SaadKazmi/researchly)`, no key, no account, no email. Probe papers: "Attention Is All You Need" (arXiv 1706.03762, DOI `10.48550/arXiv.1706.03762`) and "Deep learning" (Nature, DOI 10.1038/nature14539) where they apply. Source-specific probes are named in each section. Only needed fields quoted. "Spec" = design spec section 8.1/8.1.1/8.2. Field maps target `connectors/base.py` (`PaperRecord`, `ExternalId`; `Scheme` already has `dblp` and `acl`).

**Result: the Tier-2 picture changed a lot since 2026-10-28.** Three sources now sit behind bot walls for part or all of their surface:
- OpenReview record lookups (`/notes?id=`, `/notes?forum=`) return 403 `ChallengeRequiredError` on both API hosts. Only `/notes/search` still works anonymously.
- The DBLP search API and website return an Anubis proof-of-work page (HTTP 202 HTML). The DBLP SPARQL endpoint and the XML dump still work.
- `www.biorxiv.org` and `core.ac.uk/download` return a Cloudflare challenge (403). The JSON APIs on `api.biorxiv.org` and `api.core.ac.uk` still work.

CORE gives no full text without a key. OpenAIRE, the PwC archive and the ACL Anthology exports are fully keyless.

## 1. OpenReview API v2 (`https://api2.openreview.net`, JSON)

| Need | Endpoint | Keyless today? |
|---|---|---|
| Search | `/notes/search?term=<q>&content=title&source=forum&limit=5` (other params: `group`, `type`, `offset`) | **yes**, 200 |
| Note by id | `/notes?id=YicbFdNTTy` | **no**: 403 `ChallengeRequiredError` |
| Forum (reviews, decision) | `/notes?forum=YicbFdNTTy` (with or without `details=replyCount`) | **no**: 403 |
| API v1 forum | `https://api.openreview.net/notes?forum=YicbFdNTTy&limit=50` | **no**: 403 |

- **403 body (both hosts):** `{"name":"ChallengeRequiredError","message":"Challenge verification required (2026-11-06-18013728)","status":403,"details":{"challengeUrl":"https://openreview.net/challenge?redirect=...","reqId":"..."}}`. The challenge is a Cloudflare Turnstile page. OpenReview publishes no notice about it. A third-party report (conflens PR #24, github.com/picaultj/conflens/pull/24) saw the same 403 on ICLR 2023/2024 notes on both hosts, for both the `content.venueid` and `invitation` query forms. Its conclusion: no anonymous workaround, only login works. The likely trigger is the November 2025 OpenReview identity-leak incident. Treat **reviews and the decision note as unreachable keyless**.
- **Limits observed.** Two policies, per endpoint group, sent as both IETF and `x-` headers:
  - search: `ratelimit-policy: 20;w=60`, `x-ratelimit-limit: 20`, `x-ratelimit-remaining: 19`, `ratelimit-reset: 60` (seconds), `x-ratelimit-reset: 1793972335` (epoch);
  - `/notes`: `180;w=60`, sent even on the 403.

  Spec's "20 / min" matches search. No `Retry-After` was seen (I made no 429).
- Weak `etag` on search, but `cache-control: no-store`. A `set-cookie: GCILB=...` load-balancer cookie is set; `conftest.py` already strips `set-cookie`.
- **Search quality.** `count` was capped at 10000 for a title query. The term search is loose.
  - "Attention Is All You Need" returned "Dataflow Is All You Need" (MLSys 2026) first, then DBLP-imported records.
  - "An Image is Worth 16x16 Words" (ViT, ICLR 2021) did **not** return the ICLR 2021 note in the top 5. It returned a 2024 CoRR record, an ICLR 2025 paper with a similar title, and `OpenReview.net/Archive` copies. ICLR 2021 is an API-v1 venue and appears to be absent from v2 search (inferred; v1 search was not probed).

  So a lookup must match on the normalized title (spec §8.3 rules) and drop rows whose `domain` is `DBLP.org` or starts with `OpenReview.net/`. Those are imports, not venue decisions.
- **Decision without the forum.** In v2 the submission note's `content.venue.value` already encodes the outcome: `"ICLR 2025 Poster"`, `"MLSys 2026"`. Per the OpenReview v2 conventions, also `"... Oral"`, `"... Spotlight"`, and `"Submitted to ICLR 2025"` for rejected or withdrawn papers; those forms were not live-seen today. `content.venueid.value` is the group, e.g. `ICLR.cc/2025/Conference`. This is enough for `venue_prior` (accept tier and oral). It is not enough for review text or scores.
- **Privacy gotcha for cassettes.** `content.authorids.value` holds **raw author email addresses** for many venues, e.g. `darshan.gandhi@sambanovasystems.com`. A recorded cassette fails the skill's "no email addresses" grep. Either filter `authorids` out in a vcr `before_record_response` hook or pick probes whose rows carry only `~Profile_Id` values; the hook is safer. Never store `authorids`.
- `content.pdf.value` is a relative path (`/pdf/95f2...pdf`) for venue notes, or an arXiv URL for imports. Whether `openreview.net/pdf` is also challenged was not probed. Do not rely on it; the arXiv copy is the full-text route.

**Field map (search row, v2 note):**

| Target | Path |
|---|---|
| title | `content.title.value` |
| abstract | `content.abstract.value` |
| authors | `content.authors.value[]` (names only; **ignore `authorids`**, emails) |
| venue | `content.venue.value` (`"ICLR 2025 Poster"`), `content.venueid.value`, `domain` |
| year | parse from `venue`; `pdate` / `odate` (ms epoch) as fallback |
| decision | parse `venue`: `Oral`/`Spotlight`/`Poster`/`Submitted to` → `extra.openreview_decision` |
| IDs | `id` / `forum` (OpenReview id, e.g. `tjNf0L8QjR`) → `extra.openreview_id`. No `Scheme` exists for it. arXiv only via `content.html.value` (`https://doi.org/10.48550/arXiv.2407.04708`) or `content.pdf.value` on imported rows |
| license | `license` (`"CC BY 4.0"`, `"CC BY-SA 4.0"`) |
| other | `content._bibtex.value`, `content.topics.value`, `content.paperhash.value`, `invitations[]` |

- Licence and terms: notes carry per-note `license` (CC BY 4.0 for the venue notes seen, CC BY-SA 4.0 for imports). The spec's "Public" is fine.
- **`limits.yaml`:** `api2.openreview.net: [3.5, 1]   # /notes/search 20 per 60 s (ratelimit-policy: 20;w=60)`. Use only search; `/notes` is challenged.
- **Spec deltas:** the role "decisions and reviews" becomes **"decisions only, through search, for API-v2 venues"**. Reviews need a logged-in account; at most that is a future optional-credential feature, never a default. Coverage is v2 venues only (ICLR 2024+, NeurIPS 2023+ and newer venues, per OpenReview's migration; not live-verified per venue).

## 2. bioRxiv / medRxiv API (`https://api.biorxiv.org`, JSON)

Probe: bioRxiv `10.1101/2020.03.22.002386` (SARS-CoV-2 interactome, published in Nature as `10.1038/s41586-020-2286-9`).

| Need | Endpoint (from the API page, live-checked where marked) | Page size |
|---|---|---|
| Get by DOI (all versions) | `/details/{biorxiv\|medrxiv}/{doi}` (also `/details/{server}/{doi}/na/json`) **(live)** | all versions |
| Preprint → published DOI | `/pubs/{server}/{doi}` **(live)**, or the `published` field of `/details` | - |
| Date range | `/details/{server}/{from}/{to}/{cursor}` **(live: 30 rows per page)** ; `/pubs/{server}/{interval}/{cursor}` (100) | 30 / 100 |
| Published-article feed | `/pub/{interval}/{cursor}` (bioRxiv only), `/publisher/{prefix}/{interval}/{cursor}`, `/funder/{server}/{interval}/{ror}/{cursor}` | 100 |
| Stats | `/sum/{m\|y}`, `/usage/{m\|y}/{server}` | - |

- **No search endpoint** (confirmed on the API page). The only filters are `category=` on details/funder and the ROR id on funder. bioRxiv cannot be a search source. For keyword search, bioRxiv preprints are reachable through Europe PMC (`SRC:PPR`), Crossref (`posted-content`) and OpenAlex.
- `interval` forms: `YYYY-MM-DD/YYYY-MM-DD`, `N` (most recent N posts) or `Nd` (last N days). The cursor is a row offset. Response `messages[0]` = `{status, category, interval, cursor, count, count_new_papers, total}`. Live: medRxiv 2026-11-01..05 returned `count 30`, `total "339"`. The counts are strings.
- **Envelope / errors: always HTTP 200**:
  - unknown DOI → `{"messages":[{"status":"no posts found"}],"collection":[]}`;
  - malformed path → `{"messages":[{"status":"Both dates must be in yyyy-mm-dd format"}],"collection":[]}`.

  Treat any `status != "ok"` with an empty collection as `[]`.
- No rate-limit headers, no `ETag`, no `Last-Modified`, `access-control-allow-origin: *`. Server: Apache + PHP. **No documented limit or polite-use text** on the API page. Keep the spec's 1 rps.
- **The server must be known.** bioRxiv and medRxiv share the `10.1101/` prefix. Call `biorxiv` first; on "no posts found", try `medrxiv`, or take the server from the Crossref/OpenAlex source. Two calls at worst.
- **Full text is not keyless from bioRxiv itself.** The `jatsxml` URL (`https://www.biorxiv.org/content/early/.../...source.xml`) and even `www.biorxiv.org/robots.txt` returned **403 Cloudflare challenge** (`Cf-Mitigated: challenge`). Use Europe PMC (PPR full text) or the published version. Keep `jatsxml` only as `extra`.

**Field map (`collection[]` row from `/details`; one row per version, take the highest `version`):**

| Target | Path |
|---|---|
| DOI | `doi` (`10.1101/2020.03.22.002386`) → `ExternalId("doi", ...)` |
| published DOI | `published` (`10.1038/s41586-020-2286-9`, or `"NA"`) → second `ExternalId("doi")` only after the identity merge rules; `/pubs` adds `published_journal`, `published_date` |
| title / abstract | `title`, `abstract` (plain text) |
| authors | `authors` (one string `"Gordon, D. E.; Jang, G. M.; ..."`, split on `"; "`); `author_corresponding`, `author_corresponding_institution` |
| date / year | `date` (version date, `2020-03-22` for v1), `version` (`"3"`) |
| venue | `server` (`bioRxiv` / `medRxiv`), `venue_type="repository"`; `category` (`systems biology`), `type` (`new results`) |
| license | `license` (`cc_by`, `cc_no`, `cc_by_nc_nd`, ...) → map to SPDX-ish |
| other | `jatsxml` (Cloudflare-gated), `funder` (when present) |

- Licence and terms: per-paper licences (the `license` field). The API page states no terms.
- **`limits.yaml`:** `api.biorxiv.org: [1.0, 1]   # no published limit; spec polite 1 rps`.
- **Spec deltas:**
  - "Preprints ↔ published DOIs" is right, but it is a get-only connector (no search).
  - It is two endpoints, and `details` already carries `published`, so one call suffices.
  - bioRxiv full text is Cloudflare-gated.

## 3. CORE v3 (`https://api.core.ac.uk/v3`, JSON)

- **Search works keyless:** `/search/works/?q=title:"attention is all you need"&limit=2` → 200, `totalHits 3298001`.
  - **The trailing slash matters.** Without it the API returns **301** to the slashed URL, and the 301 **consumed a token** (`x-ratelimit-remaining` 9 → 8).
  - DOI lookup: `/search/works/?q=doi:"10.1038/nature14539"` → 3 hits, including a mis-assigned Spanish thesis carrying the Nature DOI. Filter on title.
  - Docs also list `/works/{id}`, `/outputs/{id}`, `/discover`, `/recommend`, `/search/outputs/`, `/data-providers/...`. Not probed.
- **Limits (docs, `api.core.ac.uk/docs/v3` "Rate limits"):** "Access to the CORE API is free and requires no authentication". Table:
  - Unauthenticated: "**100 tokens per day, maximum 10 per minute**", with the footnote "Full-text is not available for unauthenticated API users";
  - registered personal: 1,000/day, 25/min;
  - registered academic: 5,000/day, 10/min.

  "A simple query will cost you 1 token while more complex queries will cost you between 3 to 5 tokens."
- **Headers observed:** `x-ratelimit-limit: 10`, `x-ratelimit-remaining: 9 → 8 → 7` across about 30 s (a per-minute window), and `x-ratelimit-retry-after: 2026-11-06T13:40:32+0000`. That is an **ISO timestamp in a non-standard header**; `PoliteClient.retry_after()` reads only `Retry-After`, so the connector must map it. Served through Cloudflare. No daily-budget header was seen. A 429 was not provoked.
- **Full text is out keyless, three ways:**
  1. the `fullText` field returns the literal string `"Not available for public API users."`;
  2. `downloadUrl` / `links[type=download]` = `https://core.ac.uk/download/83868954.pdf` → **403 Cloudflare challenge**;
  3. the docs' `GET /v3/outputs/{id}/download` counts against the 100 tokens and is "generally more expensive". Not probed.

  The docs also forbid systematic PDF harvesting that bypasses the API. `sourceFulltextUrls` points to the original repository, and that URL is what our full-text chain could use. In the probe it was CORE's own download URL, so it is not reliable.
- **Field map (`results[]`):**
  - title → `title`; abstract → `abstract`;
  - authors → `authors[].name` (`"Uszkoreit, Jakob"`);
  - year → `yearPublished` (2023 for the arXiv probe, wrong: it is the harvest date);
  - DOI → `doi` (null for the arXiv probe);
  - arXiv → `identifiers[type=arxiv_id]` (`1706.03762`), `oaiIds[]` (`oai:arxiv.org:1706.03762`). The top-level `arxivId` was null;
  - venue → `publisher`, `journals[]`, `dataProviders[].name`;
  - IDs: `id` (work), `identifiers[type=core_id]` (output);
  - `citationCount` (0 for Attention: unusable);
  - `magId`, `pubmedId`, `language.code`, `documentType`, `links[]`;
  - license → `license` (output field per docs).
- Licence and terms: per-item licences. The docs show a `license` field with CC URIs.
- **Verdict:** CORE metadata is keyless but capped at **100 tokens/day per IP** and adds little over OpenAlex. Its data quality on the probes was poor: wrong year, no DOI, citation count 0, a mis-assigned DOI. **Keyless full text from CORE is out.** CORE becomes an **optional-key connector** (Settings → Sources; a registered key gives full text and 1,000-5,000/day), not a default.
- **`limits.yaml` (only if the optional-key connector is built):** `api.core.ac.uk: [7.5, 1]   # keyless 10/min and 100 tokens/day; keyed 10-25/min`. The daily cap needs a budget guard like OpenAlex's, since `x-ratelimit-*` shows only the minute window.
- **Spec deltas:**
  - §8.1 "~10 / min" is correct for the minute, but misses the 100/day cap and the "no full text keyless" rule.
  - §8.1.1 Full text "... then CORE" must drop CORE from the keyless chain.
  - F3.2 lists CORE as an OA full-text source; it applies only with the optional key.

## 4. OpenAIRE Graph API v1 (`https://api.openaire.eu/graph/v1`, JSON)

| Need | Endpoint | Live |
|---|---|---|
| Search | `/researchProducts?search="attention is all you need"&type=publication&pageSize=2` | 200, `numFound 182` |
| Title only | `mainTitle=` (docs) | - |
| By PID | `/researchProducts?pid=10.48550/arXiv.1706.03762&pageSize=3` | 200, `numFound 1` |
| Not found | `pid=10.1234/does-not-exist-xyz` | **200** `{"header":{"numFound":0,...},"results":[]}` |
| Bad param | `foo=bar` | **400** `{"message":"Unknown parameter: foo; valid parameters are: [cursor, relCommunityId, ... pid, ... search, sortBy, ...]"}` |

- Other filters (docs): `fromPublicationDate`/`toPublicationDate` (`YYYY` or `YYYY-MM-DD`), `type` (`publication|dataset|software|other`), `bestOpenAccessRightLabel` (`OPEN`, ...), `authorOrcid`, `authorFullName`, `isPeerReviewed`, `openAccessColor`, `countryCode`, `instanceType`, `fos`, `sdg`.
  - Paging: `page` or `cursor=*`; `pageSize` 1-100.
  - `sortBy=<field> ASC|DESC` with `relevance`, `publicationDate`, `citationCount`, `popularity`, `influence`, `impulse`.
- **Limits:**
  - Docs (`graph.openaire.eu/docs/apis/terms`): authenticated "up to 7200 requests per hour", anonymous "**up to 60 requests per hour**".
  - Live headers on keyless calls: `x-ratelimit-limit: 7199` and `x-ratelimit-used: 1, 2, 3, 4`. The counter rises per call, including the 400. There is no remaining or reset header.

  The header still disagrees with the docs, as it did on 2026-10-28. Whether anonymous callers really get 7,199/h cannot be tested without passing 60 calls. **Plan on the documented 60/h.** Log `x-ratelimit-used` so a later check can relax it.
- No `ETag`/`Last-Modified`. No `Retry-After` seen.
- **Data-quality gotcha: dedup merges.** The arXiv-DOI lookup returned one merged record, `doi_dedup___::5e53...`, with 11 `pids`:
  - the arXiv DOI, the bare arXiv id, `10.5283/epub.*`, and seven `10.65215/*` DOIs that look like spam reposts of the paper;
  - a top-level `publicationDate` of `2025-08-23` and `publisher` "Shenzhen Medical Academy of Research and Translation";
  - a subject string about medieval ordeals.

  So **never take top-level date, publisher or subjects from a merged record**. Take the matching `instances[]` entry (the one whose `pids` or `alternateIdentifiers` hold our DOI or arXiv id) for date, licence, URL and refereed status.
- **Field map (`results[]`):**

| Target | Path |
|---|---|
| title | `mainTitle` (`subTitle`) |
| abstract | `descriptions[0]` (may be JATS: `<jats:p>...`; strip tags) |
| authors | `authors[].{fullName,rank,pid.id.{scheme,value}}`. ORCID when `scheme` is `orcid` (also `orcid_pending`: ignore) |
| DOI / arXiv | `pids[].{scheme:"doi",value}` (lowercased in this sample), `pids[scheme=arXiv]`, `instances[].pids` / `alternateIdentifiers` |
| year | `instances[match].publicationDate`; top-level `publicationDate` only for unmerged records |
| venue | `container.{name,issnPrinted,issnOnline,vol,iss,sp,ep}` (e.g. Neurocomputing), `publisher` |
| OA | `bestAccessRight.label` (`OPEN`), `openAccessColor`, `isGreen`, `isInDiamondJournal`, `instances[].{urls[],accessRight,license,type,refereed,hostedBy}` |
| license | `instances[match].license` (e.g. `http://arxiv.org/licenses/nonexclusive-distrib/1.0/`) |
| cited_by | `indicators.citationImpact.citationCount` (16770 for Attention), plus `influence`/`popularity`/`impulse` and their classes (`C1`..`C5`) |
| other | `id` (OpenAIRE id) → `extra`, `subjects[]`, `sources[]`, `language`, `publiclyFunded`, project links (`relProject*` filters) |

- Licence and terms: "the records returned by the service can be freely re-used ... under CC-BY license ... as long as OpenAIRE is acknowledged as a data source". The spec's attribution line already covers this.
- **`limits.yaml`:** `api.openaire.eu: [60.0, 1]   # anonymous 60 per hour (docs); header claims 7199`.
- **Spec deltas:**
  - The limit is still "60/h docs vs header", and now the exact headers are known: `x-ratelimit-limit: 7199`, `x-ratelimit-used`.
  - Add the merged-record rule.
  - The optional-key row ("7,200 / hour") is right, but OpenAIRE's "key" is a personal access token from a registered account that **expires after one hour**; it is renewed with a refresh token. That is more work than a static key.

## 5. DBLP (`https://dblp.org`, `https://sparql.dblp.org`)

| Surface | Request | Result |
|---|---|---|
| Search API | `https://dblp.org/search/publ/api?q=attention is all you need vaswani&format=json&h=3` | **HTTP 202, HTML "Making sure you're not a bot!"**: Anubis v1.27.0, `metarefresh` challenge with difficulty 1, plus a `dblp_org-cookie-verification-*` cookie |
| Mirror | same path on `dblp.uni-trier.de` | **same 202 Anubis page** |
| FAQ / docs pages | `dblp.org/faq/...` (WebFetch) | "Access Denied ... Protected by Anubis" |
| SPARQL (QLever) | `GET https://sparql.dblp.org/sparql?query=...` with `Accept: application/sparql-results+json` | **200 JSON**, 15-19 ms query time, about 1 s wall time |
| XML dump | `HEAD https://dblp.org/xml/dblp.xml.gz` | **200**, `content-length: 1116868681` (1.1 GB), `last-modified: Thu, 05 Nov 2026 22:36:01 GMT` (nightly) |
| robots.txt | `dblp.org/robots.txt` | 200, `Crawl-delay: 4` |

- **The JSON search API is not usable keyless today.** The Anubis `metarefresh` challenge is a deliberate bot gate. Passing it by script would circumvent the site's protection, so we must not. Note the 202 status: a naive client treats 202 as success, so the connector must check `content-type`. The `UnwantedContent` error type fits.
- **The SPARQL endpoint works.** The sparql.dblp.org page states "all dblp metadata released as open data under CC0 1.0 license" and "Default maximum 100 rows". It lists text-search support (`contains-word`, `contains-entity`, `textlimit`) but documents **no rate limit, timeout or fair-use rule**. Probes:
  - Title literal: `SELECT ?p ?venue ?doi WHERE { ?p dblp:title "Attention is All you Need." ; dblp:publishedIn ?venue . OPTIONAL { ?p dblp:doi ?doi } }` returned `https://dblp.org/rec/conf/nips/VaswaniSPUJGKP17`, venue `NIPS`, and no DOI.
  - The NIPS record's predicates:
    - `title` "Attention is All you Need." (**trailing period, DBLP casing**);
    - `publishedIn` "NIPS", `publishedInStream` `https://dblp.org/streams/conf/nips`, `publishedAsPartOf` `.../rec/conf/nips/2017`;
    - `yearOfPublication` 2017, `pagination` 5998-6008;
    - `authoredBy` → `https://dblp.org/pid/...`;
    - `primaryDocumentPage` / `documentPage` (proceedings.neurips.cc, the "ee" links);
    - `bibtexType` Inproceedings, `listedOnTocPage`, `numberOfCreators` 8.
  - The CoRR record `rec/journals/corr/VaswaniSPUJGKP17`:
    - `doi` = **`https://doi.org/10.48550/ARXIV.1706.03762` (URI, upper-case)**, `sameAs` the same, `primaryDocumentPage` the DOI;
    - `publishedIn` "CoRR", `publishedInJournalVolume` "abs/1706.03762";
    - `omid` `https://w3id.org/oc/meta/br/061703597415`; type `Informal`.
  - **The CoRR and NIPS records are separate and not linked** (no `sameAs` between them). Mapping an arXiv paper to its conference record needs a title match.
  - **DOI lookup is case-sensitive on the URI.** A `VALUES ?doi { <https://doi.org/10.48550/arXiv.1706.03762> }` query returned 0 rows. The stored form is upper-case `ARXIV`. Query with `FILTER(LCASE(STR(?doi)) = "...")` or try both cases. Note our `ids.doi` lowercases.
- **TLS gotcha (Windows dev box):** `dblp.org` and `sparql.dblp.org` use a GEANT TLS RSA 1 certificate chained to HARICA. **Windows schannel curl rejected it** (`SEC_E_UNTRUSTED_ROOT`); curl with certifi's bundle succeeded. PoliteClient uses `httpx.create_ssl_context(trust_env=True)`, which uses certifi, so it is unaffected. Manual curl checks on Windows need `--cacert`.
- **Field map (SPARQL bindings):**
  - DBLP key `https://dblp.org/rec/...` → `ExternalId("dblp", "conf/nips/VaswaniSPUJGKP17")`;
  - title → `dblp:title` (strip the trailing period);
  - venue → `dblp:publishedIn`, with `publishedInStream` for normalisation; `venue_type` from `rdf:type` (`Inproceedings`→conference, `Article`→journal, `Informal`→preprint);
  - year → `dblp:yearOfPublication`;
  - DOI → `dblp:doi` (lower-case it);
  - "ee" links → `dblp:primaryDocumentPage`, `dblp:documentPage` → `extra.ee`;
  - authors → `dblp:authoredBy` (names need a second hop, `dblp:primaryCreatorName`; or use `rdfs:label` "Ashish Vaswani et al.");
  - `dblp:omid` gives an OpenCitations bridge.
  - No abstract, no citations, no OA flag.
- Licence and terms: CC0 1.0 (SPARQL page). Polite use: robots `Crawl-delay: 4` for dblp.org. No `Retry-After` was observed anywhere, and the search API cannot be tested.
- **`limits.yaml`:** `sparql.dblp.org: [2.0, 1]   # no published limit; QLever, keep it gentle`. Do not add dblp.org: the search API is Anubis-gated.
- **Spec deltas:**
  - "honor Retry-After (live check pending)" is moot: the search API is behind Anubis.
  - DBLP's role becomes **venue enrichment via SPARQL**: get by DOI, or exact/near-title lookup of a known paper. It is no longer a search fan-out source.
  - The offline alternative is the 1.1 GB nightly XML dump (see Opt).

## 6. ACL Anthology

- **No REST API.** The data-access FAQ (`aclanthology.org/faq/api/`) says the metadata lives in the GitHub repo (`acl-org/acl-anthology`, `data/xml`, `data/json/people.json`) and is "accessible via the ACL Anthology Python module on PyPI".
- **`acl-anthology` package (PyPI 1.3.4, 2026-10-19, Apache-2.0, Python >3.11,<3.15):**
  - `Anthology.from_repo()` runs `git clone --single-branch --depth 1` of the **whole** repo into the platformdirs user-data dir, and on later calls a `git pull`. Checked in the wheel source, `utils/git.py`. It **requires a git binary** plus `gitpython`.
  - Its other dependencies: `lxml<7`, `numpy`, `citeproc-py`, `texsoup`, `omegaconf`, `rich`, `langcodes[data]`, `pylatexenc`, `stop-words`, `msgspec`, `rnc2rng`, `docopt-ng`, `python-slugify`, `requests`.
  - The `Paper` object has `bibkey`, `title`, `authors`, `abstract`, `doi`, `pdf`, and venue via the volume.
- **Size:**
  - the repo is 911 MB on GitHub (with history);
  - `data/xml` is **1,773 files, 206 MB uncompressed** (GitHub tree API). The shallow clone is smaller than the repo, but it still includes the site code.
- **Lighter keyless exports**, served from aclanthology.org (Apache, not Cloudflare), with byte ranges supported (206), and rebuilt daily:
  - `https://aclanthology.org/anthology+abstracts.bib.gz`: **47.2 MB** (`Last-Modified: Fri, 06 Nov 2026 11:34:59 GMT`);
  - `https://aclanthology.org/anthology.bib.gz`: 13.6 MB, without abstracts.

  Entries are BibTeX with LaTeX escapes (`{\v{Z}}elezn{\'y}`, `EMNLP{~}2026`) and carry `title, author, editor, booktitle, month, year, address, publisher, url, doi, pages, ISBN, abstract`. Example DOI `10.18653/2026.wslp-1.1`; the URL `https://aclanthology.org/2026.wslp-1.1/` is also the ACL id. **No arXiv id** appears in the bib.
- Licence: papers "Prior to 2016 ... Creative Commons 3.0 BY-NC-SA", 2016 onward "Creative Commons 4.0 BY". The **metadata licence is not stated**. The package and repo are Apache-2.0.
- **Is a local index practical?** Yes, but its marginal value is low:
  - ACL papers carry Crossref DOIs (`10.18653/...`) that OpenAlex and Crossref already resolve, with venue;
  - abstracts are in OpenAlex for most;
  - the unique bits are the ACL id, the canonical PDF URL (`https://aclanthology.org/<id>.pdf`, OA, CC BY for 2016+), and clean venue/volume names.
  - If built, the cheapest path is a daily-cached download of `anthology+abstracts.bib.gz` (47 MB) parsed into a Postgres table. Not the package: git binary, numpy and gitpython for a 206 MB clone. The parse needs a BibTeX reader plus LaTeX-to-Unicode; `pylatexenc` would be a new dependency, while a ~30-line splitter over the regular export format is enough.
- **Field map (bib entry):**
  - `ExternalId("acl", "2026.wslp-1.1")` from `url`, plus `ExternalId("doi", doi)`;
  - title, abstract (de-LaTeX both); authors (split `" and "`, "Last, First");
  - venue → `booktitle`/`journal`, `venue_type` conference;
  - year; `oa_url` = `url` + `.pdf` (not verified per entry); `is_oa=True`;
  - license by year (≥2016 `CC-BY-4.0`, else `CC-BY-NC-SA-3.0`).
- **`limits.yaml`:** none needed (one daily download through `default`, `[1.0, 1]`).
- **Spec delta:** "local package data" should become "**daily `anthology+abstracts.bib.gz` (47 MB), not the Python package**", and the licence should read "papers CC BY 4.0 (2016+) / CC BY-NC-SA 3.0 (earlier); metadata licence unstated".

## 7. Papers with Code archive (Hugging Face `pwc-archive`)

- **The org has 7 datasets.** All are public, not gated, and keyless; listed via `GET https://huggingface.co/api/datasets?author=pwc-archive&full=true`, api pool `RateLimit-Policy: "fixed window";"api";q=500;w=300`.

  | Dataset | Licence tag | Rows / size | Last modified |
  |---|---|---|---|
  | `links-between-paper-and-code` | **cc-by-sa-4.0** | 300,161 rows, parquet 41.4 MB | 2025-09-10 |
  | `papers-with-abstracts` | cc-by-sa-4.0 | 576,261 rows, parquet 580 MB | 2026-08-17 |
  | `evaluation-tables` | cc-by-sa-4.0 | 1K-10K | 2025-09-13 |
  | `methods`, `datasets` | cc-by-sa-4.0 | - | 2025-09-10 |
  | `files` (original JSON dumps) | cc-by-sa-4.0 | see below | 2025-09-14 |
  | `pwc-paper-redirects` | `cc` | - | 2025-12-12 |

  The README states: "This dataset will not be updated. It corresponds to the last available public snapshot of the data, retrieved on July 28th, 2025." The spec's "frozen July 2025" is confirmed.
- **Recommended file:** `pwc-archive/files` → `jul-28-links-between-papers-and-code.json.gz`, **26.3 MB**. It is one JSON array, readable with stdlib `gzip` + `json`, so **no pyarrow**: the backend lock has no parquet reader, only pandas transitively. Record shape (range-read live):

  ```json
  {"paper_url": "https://paperswithcode.com/paper/...", "paper_title": "...", "paper_arxiv_id": "1810.10559",
   "paper_url_abs": "https://arxiv.org/abs/1810.10559v5", "paper_url_pdf": "...", "repo_url": "https://github.com/paul-buerkner/psis-non-factorized-paper",
   "is_official": true, "mentioned_in_paper": true, "mentioned_in_github": false, "framework": "none"}
  ```

  Other files in `files`:
  - `jul-28-datasets.json.gz` 8.4 MB;
  - `jul-28-evaluation-tables.json.gz` 21.8 MB;
  - `jul-28-methods.json.gz` 5.4 MB;
  - `jul-29-papers-with-abstracts.json.gz` 564 MB. Its parquet twin carries `arxiv_id`, `openreview_id`, `nips_id`, `conference`, `proceeding`, `tasks`, `methods`, and could give a PwC conference label, but it is far too big for the value.
- **Download is keyless:**
  - `https://huggingface.co/datasets/pwc-archive/links-between-paper-and-code/resolve/<commit>/data/train-00000-of-00001.parquet` returns **302** to `us.aws.cdn.hf.co` (Xet bridge), then 200, with `accept-ranges: bytes`;
  - `X-Repo-Commit: 56cc5c1938678c33dedebf5f74fc4e62e2c35381`;
  - `X-Linked-Size: 41364239` and `X-Linked-ETag` (the sha256), so the downloader can **pin the commit and verify sha256**;
  - a range request on the `files` JSON.gz returned 206.

  The redirect crosses hosts, so PoliteClient's manual-redirect and SSRF guard must allow `*.cdn.hf.co`; it is public, so this should pass. Use a byte cap above 30 MB for this one call.
- The HF dataset-viewer `filter` API (`datasets-server.huggingface.co/filter?...where="paper_arxiv_id"='1706.03762'`) returned `{"error":"Authentication check on the Hugging Face Hub failed or timed out..."}`. It is unreliable keyless; do not use it, download the file.
- **Licence: CC BY-SA 4.0 (verified on the dataset card).** Using it locally to show "code: github.com/..." is fine with attribution ("Papers with Code archive, CC BY-SA 4.0"). ShareAlike bites only if we redistribute an adapted table, so **do not bundle it and do not export it**; that matches the spec.
- **Field map → `PaperRecord.extra`** (no new record; enrich papers by arXiv id):
  - `ids.arxiv(paper_arxiv_id)` is the join key. Some rows have null `paper_arxiv_id`; use `paper_title` only through the §8.3 fuzzy rules, or drop those rows;
  - `extra.code = [{repo_url, is_official, framework, mentioned_in_paper}]`;
  - the `practical` ranking signal uses `is_official`.
- **Spec delta:** confirm the licence (CC BY-SA 4.0). Name the file: `pwc-archive/files/jul-28-links-between-papers-and-code.json.gz` at a pinned commit. Note HF Papers' `githubRepo` overlaps it for newer papers.

## 8. Opt sources

### 8a. arXiv OAI-PMH local harvest
- **Endpoint:** `https://oaipmh.arxiv.org/oai`. `Identify` → 200, but with `<responseDate>2026-10-14T11:08:16Z</responseDate>`: a 3-week-old cached copy served by Varnish. Its contents:
  - `earliestDatestamp 2005-09-16`, granularity `YYYY-MM-DD`, `deletedRecord persistent`;
  - metadataPolicy "Metadata harvesting permitted through OAI interface", dataPolicy "Full-content harvesting not permitted".
- Formats (docs): `oai_dc`, `arXiv` (with licence and split authors), `arXivRaw` (with versions). Docs: metadata is updated nightly; resumption tokens "expire daily"; page size is not stated.
- **Live harvest probe failed:**
  - `ListRecords&metadataPrefix=arXiv&set=cs:cs:CL&from=2026-11-04&until=2026-11-04` hung for about 3 min, then returned **503 "first byte timeout"** (Varnish HTML, Error 54113);
  - a retry at 13:52 UTC (`metadataPrefix=oai_dc&set=cs&from=2026-11-05&until=2026-11-05`) also returned **503 first byte timeout** after 185 s, with `Server: Varnish` and `Retry-After: 0`.

  ListRecords was unusable during this window. `Retry-After: 0` on a 503 would make PoliteClient retry at once, so a harvester needs its own backoff floor.
- Licence: "may be reused in non-commercial and commercial systems". The arXiv metadata itself is CC0; per-paper licences cover the content.
- **Assessment:**
  - What it would add: offline title/abstract search for chosen categories, and freedom from the 1 req/3 s API for bulk work.
  - What it costs: an incremental harvester with resumption tokens and flow control, a few GB of Postgres rows for the big categories, and a nightly job.
  - Why it is not needed now: the step-2b hybrid search plus the arXiv API already covers interactive search, and the endpoint was not reliably responsive today.
  - **Defer.** Revisit when users ask for offline/airplane mode or bulk category monitoring ("alert me on new cs.CL papers"). That is a per-day `ListRecords from=yesterday`, which is small, and would be the first slice to build.

### 8b. OpenCitations Meta dump
- `download.opencitations.net` (2026-11-06): Meta release **2026-06-27**, hosted on Zenodo.
  - CSV: **8.4 GB 7z → 56 GB uncompressed**;
  - RDF: 45.7 GB 7z;
  - Virtuoso DB: 31.2 GB 7z → 187 GB.
  - 147M+ bibliographic entities.
- The licence is not stated on the download page (OpenCitations data is CC0 per the spec). The Index dump (2026-07-28, 2.56 B citations) is 36 GB zipped CSV → 250 GB.
- **Assessment:**
  - The archive is 7z, so it needs `py7zr` or a 7-Zip binary: a new dependency.
  - 56 GB of CSV is beyond the "a laptop app" footprint (NFR), on top of Postgres.
  - It duplicates what free OpenAlex/Crossref singletons already give at $0. The step-2a research found Meta has "marginal value beyond Crossref".
  - **Defer indefinitely.** Reconsider only for a "fully offline" edition, and then prefer a filtered subset (one discipline) built server-side by a script, not the full dump.

## 9. Identifiers each source returns (for §8.3 merging)

| Source | DOI | arXiv | PMID | Other |
|---|---|---|---|---|
| OpenReview (search) | via `content.html` on imports only | via `content.html`/`pdf` on imports | no | OpenReview id → `extra`; never `authorids` |
| bioRxiv/medRxiv | `doi` (10.1101/...) + `published` | no | no | `server`, `version` |
| CORE (optional key) | `doi` (often null) | `identifiers[type=arxiv_id]` | `pubmedId` | CORE work/output id, `magId` |
| OpenAIRE | `pids[scheme=doi]` (several on merged records) | `pids[scheme=arXiv]` | `pids[scheme=pmid]` (per schema; not seen) | OpenAIRE id |
| DBLP SPARQL | `dblp:doi` (URI, upper-case) | via the CoRR record DOI | no | `dblp` key, `omid` |
| ACL bib | `doi` | no | no | `acl` id |
| PwC archive | no | `paper_arxiv_id` | no | PwC paper URL |

## 10. Summary table

| Source | Keyless today | Search | Get | Format | Limit (docs / observed) | Errors | Licence | Verdict |
|---|---|---|---|---|---|---|---|---|
| OpenReview v2 | **search only**; `/notes` 403 challenge | `/notes/search` | no (challenged) | JSON | search 20/60 s, notes 180/60 s (`ratelimit-*`, `x-ratelimit-*`) | 403 JSON `ChallengeRequiredError` | per-note CC BY / CC BY-SA | build: decision-only enrichment |
| bioRxiv/medRxiv | yes | **none** | `/details/{server}/{doi}`, `/pubs/...` | JSON | none published; no headers | always 200; `messages[0].status` | per-paper | build: get-only |
| CORE v3 | metadata yes (100 tokens/day, 10/min); **full text no** | `/search/works/` (slash!) | `/works/{id}` | JSON | `x-ratelimit-limit: 10`, ISO `x-ratelimit-retry-after` | 301 without slash (costs a token) | per-item | defer; optional-key only |
| OpenAIRE Graph v1 | yes | `/researchProducts?search=` | `?pid=` | JSON | docs 60/h anon; header `x-ratelimit-limit: 7199`, `x-ratelimit-used` | 400 JSON for bad param; 200 empty for not found | CC BY (acknowledge) | build: low-rate Deep extra and OA links |
| DBLP | **search API no (Anubis 202)**; SPARQL yes; dump yes | SPARQL text (unprobed) | SPARQL by key/DOI/title | SPARQL JSON | none published; robots crawl-delay 4 | 202 HTML challenge | CC0 | build: SPARQL venue enrichment |
| ACL Anthology | yes (bib.gz exports, repo) | local | local | BibTeX | n/a | n/a | papers CC BY 4.0 / BY-NC-SA 3.0; metadata unstated | defer |
| PwC archive | yes (HF, 302 to CDN) | local | local | JSON.gz (26 MB) / parquet | HF api 500/5 min | n/a | **CC BY-SA 4.0** | build: local paper→code table |
| arXiv OAI-PMH | yes, but 503 timeouts today | local | local | OAI XML | none published | 503 Varnish HTML | metadata reusable | defer |
| OpenCitations Meta dump | yes | local | local | CSV in 7z, 8.4 GB → 56 GB | n/a | n/a | CC0 | defer |

## 11. Spec deltas (§8.1, §8.1.1, §8.2, F3.2, F9.3)

1. **OpenReview:**
   - Role: "decisions and reviews" → "**decisions only** (from the `venue` string via `/notes/search`; API-v2 venues)".
   - Limit "20 / min" is correct for search. `/notes` lookups are 403 `ChallengeRequiredError` keyless on both hosts.
   - The §10 `venue_prior` text "OpenReview acceptance/oral" still works from the `venue` string.
   - Cassettes must strip `authorids` (emails).
2. **bioRxiv/medRxiv:** get-only (no search endpoint). `details` includes `published`. Full-text JATS on www.biorxiv.org is Cloudflare-gated. Keep 1 rps.
3. **CORE:**
   - Keyless is 100 tokens/day plus 10/min, and **no full text**.
   - Move CORE out of the keyless Tier-2 list into the optional-key table.
   - Remove "then CORE" from §8.1.1 Full text, and qualify F3.2's "CORE" as "with the optional CORE key".
   - The optional-key row should say "full text + 1,000-5,000 tokens/day".
3a. **CORE headers:** `X-RateLimit-Retry-After` is an ISO timestamp, not `Retry-After`. PoliteClient needs a per-connector mapping if CORE is ever built.
4. **OpenAIRE:**
   - The limit row stays "60 / hour (docs)". The live header is `x-ratelimit-limit: 7199` with an `x-ratelimit-used` counter.
   - Add the dedup-merge caveat: use the matching `instances[]` entry.
   - The optional token expires hourly (refresh-token flow).
5. **DBLP:**
   - "Polite; honor Retry-After (live check pending)" → "**search API Anubis-gated; use `sparql.dblp.org` (CC0, no published limit)** for venue enrichment".
   - Remove DBLP from the §8.1.1 search fan-out ("OpenAIRE and DBLP as low-rate extras in Deep runs" → "OpenAIRE as a low-rate extra in Deep runs").
   - DBLP joins Enrichment for CS papers.
6. **ACL Anthology:** "local package data" → "daily `anthology+abstracts.bib.gz` (47 MB); the Python package needs git and a 206 MB+ clone". Licence: papers CC BY 4.0 (2016+) / CC BY-NC-SA 3.0, metadata unstated. Deferred.
7. **PwC archive:** licence verified **CC BY-SA 4.0**. The source file is `pwc-archive/files/jul-28-links-between-papers-and-code.json.gz` (26.3 MB, 300,161 links) at a pinned commit.
8. **Opt:**
   - OpenCitations Meta is now the 2026-06-27 release (8.4 GB 7z / **56 GB** CSV).
   - arXiv OAI-PMH is at `oaipmh.arxiv.org/oai`; ListRecords returned 503 first-byte timeouts today.
   - Both deferred.
9. **§8.2 limits table:**
   - OpenReview "1 per 3 s" → 3.5 s on `api2.openreview.net`;
   - add `api.biorxiv.org` 1 rps and `sparql.dblp.org` 1 per 2 s;
   - OpenAIRE stays 1 per min;
   - drop the CORE row unless the optional key is set.
10. **Excluded list (§8.1)** gains: DBLP search API (Anubis), OpenReview anonymous note lookups (challenge), bioRxiv/CORE web downloads (Cloudflare).

## 12. Recommendations for plan 2c

**Build now, in this order:**
1. **PwC archive table.** It has the highest value per line of code, is fully keyless and offline after one download, and feeds the `practical` ranking signal.
   - A one-shot downloader of `jul-28-links-between-papers-and-code.json.gz`, commit-pinned and sha256-checked through PoliteClient (byte cap of about 30 MB; allow the `cdn.hf.co` redirect).
   - Load it into a `paper_code` table (arXiv id, repo URL, official flag, framework). It is an enrichment lookup, not a `Connector`.
   - Attribution "Papers with Code archive (CC BY-SA 4.0)". Never exported or bundled.
2. **bioRxiv/medRxiv connector**, get-only.
   - `get()` for `10.1101/` DOIs: `details/biorxiv`, then `medrxiv` on "no posts found". It yields `published` DOI, licence, version and category; `search()` returns `[]`; no citations.
   - Routing: run it in Enrichment when a DOI starts with `10.1101/`. Emit the published DOI as a second id under §8.3 merge rules.
3. **OpenReview connector**, search-backed `get()` only.
   - `search()` is not wired into the fan-out: its relevance is weak and it costs 1 of 20 per minute.
   - `get()` by title: title-search with `content=title&source=forum`, drop imports (`DBLP.org`, `OpenReview.net/*` domains), match the title per §8.3, and return `venue` / decision / licence in `extra`.
   - Use it only for ML papers that HF/arXiv surfaced, with top-k seeds capped per run.
   - Strip `authorids` in the cassette hook.
4. **DBLP SPARQL connector**, get-only.
   - Look up by DOI (case-insensitive filter) or exact normalized title. Return the conference record's venue, year, `ee` links and dblp key; prefer non-`Informal` records over CoRR.
   - Use it for CS papers whose OpenAlex `primary_location` is arXiv/CoRR, to give `venue_prior` its real venue.
   - Keep queries simple (no full-text scans) at 1 per 2 s.
5. **OpenAIRE connector**, last.
   - `search()` at 1/min, used only as a Deep-run extra; `get()` by `pid=` for OA instance URLs as a full-text locator after Europe PMC.
   - Apply the merged-record rule. Log `x-ratelimit-used`.

**Defer:**
- **CORE**, until an optional-keys step: keyless gives no full text and 100 tokens/day.
- **ACL Anthology**: OpenAlex/Crossref/DBLP already cover ACL DOIs and venues. If wanted later, use the 47 MB bib.gz, not the package.
- **arXiv OAI-PMH**: build when offline mode or category alerts are scheduled, starting with a daily incremental `ListRecords`.
- **OpenCitations Meta dump**: indefinitely; 56 GB, 7z, duplicates free singletons.

**Routing changes to §8.1.1:**
- *Search fan-out:* replace "OpenAIRE and DBLP as low-rate extras in Deep runs" with "OpenAIRE as a low-rate extra in Deep runs (≤1/min)". OpenReview and bioRxiv are never searched.
- *Enrichment:* after the OpenAlex then Crossref singletons, add:
  - bioRxiv `details` for `10.1101/` DOIs (published DOI, licence);
  - DBLP SPARQL for CS papers whose venue is arXiv/CoRR (real venue);
  - OpenReview title lookup for ML papers (decision tier);
  - the PwC table for any arXiv id (code links).
- *Full text:* "OpenAlex `best_oa_location.pdf_url`, then arXiv HTML/LaTeX/PDF, then Europe PMC OA XML, then OpenAIRE OA instance URLs (repository copies; robots-checked)". CORE is removed; it returns only with the optional key.

**Open checks for the plan:**
- The OpenReview v2 `venue` strings for Oral/Spotlight/rejected (`Submitted to`) need one recorded example each before parsing is finalised.
- OpenAIRE's real anonymous limit (60 vs 7199/h) should be checked from a throwaway IP, or watched in production via `x-ratelimit-used`.
- DBLP SPARQL text search (`ql:contains-word`) syntax was not probed.

## Pre-flight probes (2026-11-06 15:16 UTC)
- **PwC pins:** `HEAD https://huggingface.co/datasets/pwc-archive/files/resolve/1af3a546770f9b98665523ccba4b22b4f3e18c1f/jul-28-links-between-papers-and-code.json.gz` answered 302 to `us.aws.cdn.hf.co`, with `X-Repo-Commit: 1af3a546770f9b98665523ccba4b22b4f3e18c1f`, `X-Linked-Size: 26261205` and `X-Linked-ETag: "e7c60f2170880af3bd5a5b9469a951341435e4ccfd21d3d18393c52cfec24694"` (the file's sha256).
- **DBLP SPARQL without an Accept header:** httpx's default `Accept: */*` gets `200 application/sparql-results+json`, so no header pass-through is needed.
