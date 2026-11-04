# Step 2a research: Tier-1 scholarly connectors (live-checked 2026-11-02 23:07-23:12 UTC)

Method: official docs plus 2-6 keyless requests per source, User-Agent `Researchly/0.1 (+https://github.com/SaadKazmi/researchly)`, no key, no email. Probe papers: "Attention Is All You Need" (arXiv 1706.03762) and "Deep learning" (Nature, DOI 10.1038/nature14539, PMID 26017442). Only needed fields quoted. "Spec" = design spec section 8.1/8.2/8.3.

**Result: all eight Tier-1 sources still work keyless, with no key or email.** Changes since 2026-10-28 are limited to OpenAlex search semantics and cost units, a newly documented DataCite limit, and a few corrections (see "Spec deltas").

## 1. OpenAlex (`https://api.openalex.org`, JSON)

| Need | Endpoint | Cost |
|---|---|---|
| Singleton | `/works/{W-id}`, `/works/doi:10.x/y`, `/works/pmid:26017442` (also `/works/https://doi.org/...`) | **0** (header `X-RateLimit-Cost-USD: 0`) |
| Batch get | `/works?filter=doi:a\|b\|...&per-page=100&select=...` (`pmid:a\|b`, `openalex:W1\|W2` work too; max 100 OR values) | 0.0001 (1 credit); count=2 for 2 DOIs |
| Search | `/works?search=q&per-page=N&sort=cited_by_count:desc` | **0.001 (10 credits)** |
| Search, abstract/title only | `filter=title_and_abstract.search:q` or `search.title_and_abstract=q` | 0.001 (confirmed a search) |
| Citations in | `/works?filter=cites:W2626778328&per-page=200&select=id` (count 120129) | 0.0001 per page |
| References out | singleton field `referenced_works` (28 URLs for Attention), `related_works` (10) | 0 |
| Filter by arXiv landing page | `filter=locations.landing_page_url:http://arxiv.org/abs/1706.03762` | 0.0001 (not needed, DOI works) |
| Budget probe | `/rate-limit` requires a key (`403`-style JSON `Authentication required`), so use response headers instead | - |

**Search is now full-text and keyword-augmented.** Today `search=` covers title, abstract, fulltext and keywords. The response `meta.x_query.oql` read "works with (graph neural networks) in the full text". Docs: `search.title_abstract_keywords=` is what openalex.org itself uses, and `search.title_and_abstract=` is title and abstract only. Default `sort` for a plain filter is "most cited (age-adjusted)"; `search` sorts by relevance. `rerank=true` adds 10 credits (not needed). `per-page` max 100; basic paging stops at 10,000 results. Cursor paging **requires a key**, so keyless callers get page 1 and basic `page=` up to 10k.

**Rate limits observed:**
- `X-RateLimit-Limit-USD: 0.1` and `X-RateLimit-Limit: 1000` (credits; 1 credit = $0.0001).
- `X-RateLimit-Remaining` (credits) and `X-RateLimit-Remaining-USD` fell 0.0965, 0.0955 (search), 0.0954, 0.0953 (filters) and held at 0 cost for singletons and 404s.
- `X-RateLimit-Cost-USD`, `X-RateLimit-Credits-Used`, `X-RateLimit-Reset` (seconds until midnight UTC; 3160 s at 23:07 UTC).
- `X-RateLimit-Onetime-Remaining` and `X-RateLimit-Prepaid-Remaining-USD` are present (0).
- The `meta` block carries `count`, `cost_usd`, `x_query` and a long `notice` string ("No API key, so this request shares one free daily budget ($0.10) with everyone on your network's IP address") on every keyless response.
- **Per-second limit (docs):** 10 rps keyless (30 with a key); a per-second 429 costs nothing; queries with more than 5 `OR/AND/NOT` operators are limited to 1 rps keyless. Our 5 rps / concurrency 2 is safe. The budget was already 3.5% spent before my first call (shared IP).
- **429:** exceeding the daily budget or the per-second rate gives 429. I did not provoke one. Docs show no `Retry-After` contract, so treat `X-RateLimit-Reset` as the daily reset hint and use backoff for per-second 429.
- 404: HTML body (`<title>404 Not Found</title>`), not JSON; `/works/W0` costs 0.
- 400 bad filter: JSON `{"error":"Invalid query parameters error.","message":"foo is not a valid field. Valid fields are ..."}`.
- Merged entity IDs return the surviving record (docs, 301 followed server-side).
- No `ETag` / `Last-Modified` on responses.

**Field map (work object):**

| Target | Path |
|---|---|
| title | `title` (= `display_name`) |
| abstract | `abstract_inverted_index` (`{"The":[0,18],"dominant":[1],...}`): rebuild by placing each word at each index. Null for many works |
| year / date | `publication_year`, `publication_date` (`2017-06-12`) |
| authors | `authorships[].author.{id,display_name,orcid}` (orcid is a full URL `https://orcid.org/0000-...`), `authorships[].author_position` (`first`/`middle`/`last`), `raw_author_name`, `institutions[]`, `is_corresponding` |
| venue | `primary_location.source.{id,display_name,issn_l,type}` (type `journal`/`repository`), `primary_location.version` |
| DOI | `doi` (URL form; **arXiv DOIs are lowercased**: `https://doi.org/10.48550/arxiv.1706.03762`) |
| PMID | `ids.pmid` (URL `https://pubmed.ncbi.nlm.nih.gov/26017442`), `ids.pmcid` when present |
| arXiv ID | no field. Parse from `locations[].landing_page_url` (`http://arxiv.org/abs/1706.03762`) or the arXiv DOI |
| OpenAlex ID | `id` (`https://openalex.org/W2626778328`), `ids.openalex`, `ids.mag` |
| cited_by | `cited_by_count` (120404 for Attention) |
| fwci / percentile | `fwci` (121.69 for Nature paper; **null for the arXiv preprint**), `citation_normalized_percentile.{value,is_in_top_1_percent,is_in_top_10_percent}`, `cited_by_percentile_year.{min,max}` (null for the preprint) |
| OA | `open_access.{is_oa,oa_status,oa_url}`, `best_oa_location.{pdf_url,landing_page_url,license,version,source}`. `pdf_url` can be null even when `oa_url` is set (HAL example) |
| license | `best_oa_location.license` / `primary_location.license` (often null) |
| retracted | `is_retracted` (bool) |
| graph | `referenced_works` (list of URLs), `referenced_works_count`, `related_works` |
| topics | `primary_topic`, `topics[].{id,display_name,score,subfield,field,domain}`, `keywords[].{id,display_name,score}`, `concepts[]` (22 for Attention; legacy, still returned) |
| other | `type` (`preprint`, `article`), `language`, `biblio.{volume,issue,first_page,last_page}`, `has_content`, `content_urls`, `counts_by_year`, `updated_date` |

**Identity gotchas:** OpenAlex can hold duplicate Work IDs for the same arXiv paper. OpenCitations returned `openalex:W4385245566` for the arXiv DOI while OpenAlex's own DOI lookup returned `W2626778328`. Merge via DOI first and keep both OpenAlex IDs in `paper_ids`.

## 2. arXiv API (`https://export.arxiv.org/api/query`, Atom XML)

- Search: `?search_query=all:"graph neural network"&start=0&max_results=2&sortBy=submittedDate|relevance|lastUpdatedDate&sortOrder=descending`. Fields prefixes `ti: au: abs: cat: all:`. `max_results` up to 2000 per call.
- Get / batch: `?id_list=1706.03762v7,2005.14165&max_results=2` (comma list; a versionless ID returns the latest version). One call, so batching is free of extra cost.
- No citations or references endpoint.
- HTTPS works. Limit per the ToU page: **one request every 3 seconds, single connection** (the whole machine/IP).
- No rate-limit headers, no `ETag`, no `Last-Modified`; a conditional `If-Modified-Since` returned a normal 200. Responses are fronted by Fastly (`X-Cache`). `Strict-Transport-Security: max-age=300`.
- **Error shapes (all HTTP 200):**
  - Unknown field, e.g. `search_query=foo:bar`, is treated as `all:foo:bar` and returns `totalResults 0`.
  - Nonexistent ID `9999.99999` returns an empty feed.
  - Malformed ID `abc` returns one `<entry>` with `<id>https://arxiv.org/api/errors#incorrect_id_format_for_abc</id>`, `<title>Error</title>`, `<summary>incorrect id format for abc</summary>`. **Connector must detect `id` containing `/api/errors`.**
  - Over-limit gives HTTP 429 (docs; not provoked).
- New-style IDs are now 5 digits (e.g. `2610.41932`), so ID regex must allow `\d{4}\.\d{4,5}(v\d+)?` and old style `[a-z-]+(\.[A-Z]{2})?/\d{7}`.

**Field map (`<entry>`, namespace `http://www.w3.org/2005/Atom`; `arxiv:` = `http://arxiv.org/schemas/atom`):**

| Target | Path |
|---|---|
| title | `title` (whitespace-wrapped, normalise) |
| abstract | `summary` |
| dates | `published` (v1), `updated` (latest version) |
| authors | `author/name` only. No ORCID, affiliation only if `arxiv:affiliation` present |
| venue | `arxiv:journal_ref` (optional, author-supplied); `arxiv:primary_category@term`, `category@term` |
| DOI | `arxiv:doi` (optional, the **journal** DOI, not the DataCite one) |
| arXiv ID | `id` = `http://arxiv.org/abs/1706.03762v7`: strip prefix, split `v7` |
| PDF | `link[@title='pdf']@href` (`https://arxiv.org/pdf/1706.03762v7`) |
| other | `comment` (`arxiv:comment`), `opensearch:totalResults`, `opensearch:startIndex` |

Not available: cited_by, license, retraction flag, references.

## 3. Crossref (`https://api.crossref.org`, JSON)

- Singleton: `/works/10.1038/nature14539` (pool `x-api-pool: public-single`, `x-rate-limit-limit: 5`, `x-rate-limit-interval: 1s`).
- Search: `/works?query.bibliographic=attention+is+all+you+need&rows=2&select=DOI,title,issued,author,is-referenced-by-count&sort=relevance` (pool `public-array`, limit 1 per 1s). Also `query=`, `query.title`, `query.author`.
- Batch: `/works?filter=doi:A,doi:B&rows=5&select=DOI,title` returned both; it is a **list** call, so 1 rps. There is no singleton-pool batch.
- References: singleton `message.reference[]` with `{key, DOI?, unstructured, author, year, volume-title, first-page}`. Note `select=` can omit `reference`; fetch the singleton to get it. `reference-count`/`references-count` = 103.
- Citations in: only the count `is-referenced-by-count` (79000). No citing list (use OpenCitations).
- 404: `text/plain` empty body. 400 filter error: JSON `{"status":"failed","message-type":"validation-failure","message":[{"type":"filter-not-available","value":"foo","message":"Filter 'foo' specified but there is no such filter for this route. Valid filters ..."}]}`.
- 429 per docs; headers `x-rate-limit-*` are the only limit signals seen. No `Retry-After` observed. No `ETag` or `Last-Modified`. **No `mailto` needed**; polite pool only with the user's own.
- Envelope: `status`, `message-type`, `message-version`, `message` (singleton) or `message.{items,total-results,items-per-page,query}` (list).

| Target | Path |
|---|---|
| title | `title[0]` (array) |
| abstract | `abstract` (JATS XML string; often absent, null for Nature) |
| date | `issued.date-parts[0]` = `[2015,5,27]` (parts may be shorter), `created.date-time` |
| authors | `author[].{given,family,sequence,ORCID?,affiliation[]}`; ORCID appears as `ORCID` URL when deposited |
| venue | `container-title[0]`, `ISSN[]`, `publisher`, `volume`, `issue`, `page` |
| DOI | `DOI` |
| cited_by | `is-referenced-by-count` |
| license | `license[].{URL,content-version,start}` (Nature: TDM licence URL, not an open licence) |
| funders | `funder[].{name,DOI,award}` (null for probe) |
| retraction | `update-to[]`, `updated-by[]` entries with `type` such as `retraction` (docs: Retraction Watch data is merged in; not live-verified), `relation` |
| PDF | `link[].{URL,content-type,intended-application}` (TDM links, publisher-gated) |
| type | `type` (`journal-article`, `posted-content`) |

## 4. OpenCitations Index v2 (`https://api.opencitations.net/index/v2`, JSON) and Meta v1

- `/references/doi:10.1038/nature14539` (40 refs), `/citations/doi:...`, `/citation-count/doi:...` (`[{"count":"24956"}]` for the arXiv DOI), `/reference-count/doi:...`, `/citation/{oci}`.
- Accepted ID prefixes: `doi:`, `pmid:` (count 73495 for PMID 26017442), `omid:`. **`openalex:` is rejected** with 400 text `The value 'openalex:W...' is not valid for parameter 'id'`.
- **No pagination.** `/citations/doi:10.48550/arXiv.1706.03762` streamed **8.6 MB+** (curl aborted mid-stream, error 18). **Always call `citation-count` first and refuse or cap above a threshold; stream with a byte cap.** Use OpenAlex `cites:` for paging top-k seeds.
- Row: `{oci, citing, cited, creation, timespan, journal_sc, author_sc}`; `citing`/`cited` are space-separated multi-ID strings, e.g. `"omid:br/0650349701 pmid:26017442 doi:10.1038/nature14539 openalex:W2919115771"`. **Useful for identity merging** (gives DOI, PMID, OpenAlex ID together; for books `isbn:`).
- Unknown DOI returns 200 with count `0`.
- Meta: `/meta/v1/metadata/doi:10.1038/nature14539` returns `[{id, title, author "LeCun, Yann [omid:ra/...]; ...", pub_date, issue, volume, venue "Nature [issn:... omid:...]", page, type, publisher}]`. No abstract, no ORCID in this sample. Marginal value beyond Crossref. Skip it unless needed as OMID bridge.
- Limit: **180 req/min/IP** (docs). An optional access token goes in an `authorization` header and is not required. No limit headers returned (CORS headers only). Response header `X-Cache: HIT/MISS` shows server-side cache; no `ETag`.
- Counts differ strongly from OpenAlex (24,956 DOI-DOI citations for the arXiv DOI vs 120,404 in OpenAlex) because only DOI-to-DOI links are indexed.

## 5. DataCite (`https://api.datacite.org`, JSON:API)

- Singleton: `/dois/10.48550/arxiv.1706.03762` (case-insensitive; arXiv suffix is stored lowercase). Old-style IDs work: `/dois/10.48550/arXiv.hep-th/9901001` returned 200.
- **Versioned DOIs do not exist**: `10.48550/arXiv.1706.03762v7` is 404. Strip `vN` before constructing the DOI.
- Batch/search: `/dois?query=doi:(A OR B)&page[size]=5&fields[dois]=doi,titles,publicationYear` returned both. Other queries: `titles.title:"..."`, `resource-type-id=dataset`, `page[size]`, `page[number]`. Response `meta.{total,totalPages,page}`.
- 404: `{"errors":[{"status":"404","title":"The resource you are looking for doesn't exist."}]}`.
- **ETag works**: weak `etag: W/"..."` plus `cache-control: max-age=0, private, must-revalidate`; `If-None-Match` returned **304**.
- Limit (new in docs, `support.datacite.org/docs/rate-limit`): **unidentified 500 requests per 5 min per IP**; 1000 per 5 min if the UA or `mailto=` contains an email address; 3000 with credentials. 429 on excess. No limit headers seen. Researchly (no email) is therefore at 500 / 5 min, about 1.6 rps.
- Fields: `data.attributes.{doi, titles[].title, publicationYear, publisher ("arXiv"), creators[].{name,givenName,familyName,nameIdentifiers[],affiliation[]}, descriptions[].description (abstract), types.resourceTypeGeneral ("Preprint"), identifiers[] ({"identifier":"1706.03762","identifierType":"arXiv"}), relatedIdentifiers[], rightsList[], url (https://arxiv.org/abs/1706.03762), version ("7"), dates[], citationCount (69), referenceCount (2), updated}`.
- arXiv DOI coverage is immediate: a brand-new ID `10.48550/arxiv.2610.41932` (submitted 2026-10-30) already resolved (HTTP 200).

## 6. Europe PMC (`https://www.ebi.ac.uk/europepmc/webservices/rest`, JSON/XML)

- Search: `/search?query="graph neural network"&format=json&resultType=core&pageSize=25&sort=CITED desc` (also `lite` for lighter rows; cursor via `cursorMark`, response `nextCursorMark`). `pageSize` max 1000.
- Singleton by ID: `/search?query=EXT_ID:26017442 AND SRC:MED&format=json&resultType=core` (`SRC:PMC`, `SRC:PPR`; also `DOI:10.1038/nature14539`, `PMCID:PMC...`).
- Batch: no multi-ID endpoint. Use an OR query (`DOI:a OR DOI:b`), which is unverified for size caps; keep batches at about 25.
- Citations in: `/MED/26017442/citations?format=json&pageSize=2&page=1` → `hitCount 19073`, `citationList.citation[].{source,id,title,authorString,pubYear,citedByCount}`. References out: `/MED/26017442/references?format=json` → `hitCount 22`, `referenceList.reference[]` with `citedOrder`, `match`. Page param is `page` (1-based).
- **No rate-limit headers, no `ETag`, no `Last-Modified`.** Documented etiquette only. Keep 5 rps.
- Errors are lenient: a malformed query `((` returned **HTTP 200 `hitCount 0`**; unknown ID `/MED/1/citations` returned 200 with data. No 404 shape observed.

| Target | Path |
|---|---|
| title | `title` (ends with a period) |
| abstract | `abstractText` (HTML-ish markup) |
| date | `pubYear`, `firstPublicationDate` (`2015-05-01`), `journalInfo.dateOfPublication` |
| authors | `authorList.author[].{fullName,firstName,lastName,authorId.{type:"ORCID",value},authorAffiliationDetailsList}`; also `authorString` |
| venue | `journalInfo.journal.{title,medlineAbbreviation,issn,essn}`, `volume`, `issue`, `pageInfo` |
| IDs | `id`+`source`, `pmid`, `pmcid`, `doi` (all top-level) |
| cited_by | `citedByCount` (19318) |
| OA | `isOpenAccess` ("Y"/"N"), `inPMC`, `fullTextUrlList.fullTextUrl[].{availabilityCode,documentStyle,site,url}` (includes Unpaywall rows), `license` (null here) |
| other | `pubTypeList.pubType[]` (look for "Retracted Publication"), `meshHeadingList`, `hasReferences` |

## 7. PubMed E-utilities (`https://eutils.ncbi.nlm.nih.gov/entrez/eutils`)

- Search: `esearch.fcgi?db=pubmed&term=...&retmode=json&retmax=3&sort=relevance` returns `esearchresult.{count,idlist[],translationset[],querytranslation}`; `retstart` for paging (cap 10,000 via esearch, use `usehistory=y` + `WebEnv` beyond that).
- Batch get: `efetch.fcgi?db=pubmed&id=26017442,25719670&retmode=xml` (comma IDs; use POST for >200). `esummary.fcgi?...&retmode=json` gives lighter JSON (`result.<pmid>.{pubdate,source,authors[].name,...}`; no abstract).
- Cited-by: `elink.fcgi?dbfrom=pubmed&db=pubmed&id=26017442&linkname=pubmed_pubmed_citedin&retmode=json` (PMC-based, partial). References out: `pubmed_pubmed_refs` or `PubmedData/ReferenceList` in efetch.
- Limit headers present on every response: `X-Ratelimit-Limit: 3`, `X-Ratelimit-Remaining: 2` (also exposed via CORS). 3 rps keyless (10 with key); no email required (NCBI asks for `tool`/`email` but does not enforce). Our 2 rps is fine. No `ETag` or `Last-Modified`; `Cache-Control: private`.
- **Errors:** `efetch` with a bad ID returns **HTTP 400** with XML `<eFetchResult><ERROR>ID list is empty! Possibly it has no correct IDs.</ERROR></eFetchResult>`. 429 on rate violation (docs). The PMC ID converter works keyless but warns about missing `email`/`tool`.
- XML field paths (`PubmedArticleSet/PubmedArticle`):

| Target | Path |
|---|---|
| PMID | `MedlineCitation/PMID` |
| title | `MedlineCitation/Article/ArticleTitle` (can contain inline markup: use `"".join(itertext())`) |
| abstract | `Article/Abstract/AbstractText[@Label]` (multiple labelled sections; join) |
| date | `Article/Journal/JournalIssue/PubDate/{Year,Month,Day|MedlineDate}`, `Article/ArticleDate` (e-pub) |
| authors | `Article/AuthorList/Author/{LastName,ForeName,Initials,Identifier[@Source='ORCID'],AffiliationInfo/Affiliation}` or `CollectiveName` |
| venue | `Article/Journal/{Title,ISOAbbreviation,ISSN}` |
| DOI / PMCID | **`PubmedData/ArticleIdList/ArticleId[@IdType='doi'|'pmc'|'pubmed']`** only. `.//ArticleId` also matches the ~30 `ReferenceList` entries (observed), so scope the path. `Article/ELocationID[@EIdType='doi']` is a second DOI source |
| type / retraction | `Article/PublicationTypeList/PublicationType` ("Retracted Publication"), `MedlineCitation/CommentsCorrectionsList/CommentsCorrections[@RefType='RetractionIn']` |
| MeSH | `MeshHeadingList/MeshHeading/DescriptorName` |

## 8. Hugging Face Papers (`https://huggingface.co/api`, JSON)

- Search: `/papers/search?q=graph+neural+network&limit=2` returns an array of `{paper:{id,authors[],publishedAt,title,summary,upvotes,githubRepo,projectPage,githubStars,ai_summary,ai_keywords,organization,discussionId}, numComments, ...}`. Header `RateLimit: "search";r=49;t=173` and `RateLimit-Policy: "fixed window";"search";q=50;w=300` (50 per 5 min).
- Singleton: `/papers/{arxiv-id}` (`1706.03762`) returns `{id, authors, publishedAt, title, summary, upvotes, ai_summary, linkedModels, numTotalModels, linkedDatasets, linkedSpaces,...}`. Header `RateLimit-Policy: "api";q=500;w=300`. `githubRepo` appeared in search rows, not in this singleton (field presence varies).
- Linked artefacts: `/models?filter=arxiv:<id>&limit=n`, `/datasets?filter=arxiv:<id>`. Both `api` pool.
- Daily: `/daily_papers?limit=1` (api pool).
- **ETag works** (weak): `If-None-Match` returned 304 on `/papers/{id}`.
- 404: `{"error":"Paper not found. For an arXiv paper to appear on Hugging Face, its arxiv.org URL needs to be mentioned in at least one model, dataset or Space's README.md..."}`. Only papers referenced by a repo exist, so 404 is normal.
- No DOI, venue, citations, license in the paper object. IDs are arXiv IDs only. Not a metadata hub: use for upvotes, code, models.
- Windows gotcha: responses contain emoji. Decode as UTF-8 explicitly (`open(..., encoding="utf-8")`); cp1252 default crashed my parse.

## 9. arXiv ID to DataCite DOI mapping

- **Derivable without a call:** `doi = "10.48550/arXiv." + id_without_version` (old style keeps the slash: `10.48550/arXiv.hep-th/9901001`). Compare case-insensitively (OpenAlex, OpenCitations and DataCite store `arxiv` lowercased). Strip `vN` first; versioned DOIs 404.
- DataCite is needed only when we want metadata (it mirrors title, authors, abstract, license, `version`) or to confirm existence. Routing should use the derived DOI as the identity key, with no DataCite call.
- The DataCite DOI is not the journal DOI. A published paper has a separate publisher DOI. Sources for the link: arXiv `arxiv:doi`, Crossref `relation.is-preprint-of`, DataCite `relatedIdentifiers` (empty for the probe), OpenAlex (may keep two Works, see section 1). Treat link as a second `paper_ids` row, merged only on DOI/title match.

## 10. Identifiers each source returns (for section 8.3 merging)

| Source | DOI | arXiv | PMID | PMCID | OpenAlex | Other |
|---|---|---|---|---|---|---|
| OpenAlex | yes (lowercased arXiv DOI) | derive from landing page/DOI | `ids.pmid` | `ids.pmcid` (sometimes) | `id` | MAG |
| arXiv | `arxiv:doi` (journal, optional) | `id` | no | no | no | - |
| Crossref | `DOI` | no (`relation`) | no | no | no | ISSN, ORCID |
| OpenCitations | yes | arXiv via DOI | `pmid:` | no | `openalex:` (may differ from OpenAlex canonical) | OMID, ISBN |
| DataCite | `doi` | `identifiers[type=arXiv]` | no | no | no | - |
| Europe PMC | `doi` | PPR preprints only | `pmid` | `pmcid` | no | `id`+`source` |
| PubMed | `ArticleId doi` | no | `PMID` | `ArticleId pmc` | no | pii |
| HF Papers | no | `id` | no | no | no | github repo |

## 11. XML parsing

- **arXiv Atom: stdlib `xml.etree.ElementTree` is adequate.** The feed is regular (one namespace map: Atom, `arxiv`, `opensearch`). feedparser adds a dependency, normalises away `arxiv:*` extension fields and reorders authors poorly, so skip it.
- **Security:** both feeds are third-party data. ElementTree does not fetch external entities, but it is not hardened against entity expansion ("billion laughs", quadratic blowup); Python 3.12 bundles expat 2.8.3 (checked) which caps amplification, but the Python docs still call it unsafe for untrusted input. Neither `defusedxml` nor `lxml` is a backend dependency today. Recommended: add `defusedxml` (pure Python, tiny) and use `defusedxml.ElementTree.fromstring` for arXiv and PubMed. It keeps the stdlib API; `forbid_entities=True` is the default and the PubMed `<!DOCTYPE ... efetch.dtd>` line parses fine (`forbid_dtd` default False). Also cap response size (e.g. 5 MB, matches `PoliteClient` streaming) before parsing. Adding the dependency is a decision for the plan; if not wanted, stdlib ET plus a size cap plus rejecting any body containing `<!ENTITY` is the minimum.
- **PubMed:** parse `efetch` XML, not `esummary` (no abstract). Pitfalls: `ArticleTitle` and `AbstractText` carry inline `<i>`/`<sub>` (use `itertext()`), `PubDate` may be `MedlineDate` ("2015 Jan-Feb"), authors may be `CollectiveName`, ORCID is under `Identifier[@Source='ORCID']`, and article IDs must be scoped to `PubmedData/ArticleIdList` (not `ReferenceList`). Use `esearch` JSON for search.

## 12. Summary table

| Source | Search endpoint | Get endpoint | Batch? | Citations? | Format | Keyless limit observed today | Notes |
|---|---|---|---|---|---|---|---|
| OpenAlex | `/works?search=` ($0.001) | `/works/doi:..`, `/works/W..` ($0) | yes, `filter=doi:a\|b` x100 ($0.0001) | out: `referenced_works` ($0); in: `filter=cites:` ($0.0001/page) | JSON | $0.10/day = 1000 credits; 10 rps; headers `X-RateLimit-*` | search is full-text; no ETag; cursor paging needs key |
| arXiv | `/api/query?search_query=` | `/api/query?id_list=` | yes (comma list) | no | Atom XML | 1 req/3 s, 1 conn; no headers | errors are HTTP 200; no ETag |
| Crossref | `/works?query.bibliographic=` | `/works/{doi}` | filter `doi:a,doi:b` (list pool) | out: `reference[]`; in: count only | JSON | single 5 rps, list 1 rps; `x-rate-limit-*` | no ETag; no email needed |
| OpenCitations | n/a | `/index/v2/citation-count/doi:` | no | yes, both ways; **unpaged** | JSON | 180/min/IP (docs); no headers | cap payload; `openalex:` ID rejected |
| DataCite | `/dois?query=` | `/dois/{doi}` | `query=doi:(a OR b)` | counts only | JSON:API | **500 / 5 min / IP unidentified** (docs); no headers | ETag 304 works; versioned DOI 404 |
| Europe PMC | `/search?query=` | `/search?query=EXT_ID:..` | OR query | yes both ways (`/citations`, `/references`) | JSON/XML | no headers; keep 5 rps | lenient errors (200) |
| PubMed | `esearch.fcgi` | `efetch.fcgi?id=` | yes (comma list) | `elink` (partial) | XML/JSON | 3 rps (`X-Ratelimit-Limit: 3`) | 400 XML `<ERROR>`; scope ArticleId |
| HF Papers | `/papers/search?q=` | `/papers/{arxiv-id}` | no | no | JSON | search 50/5 min, api 500/5 min (`RateLimit` headers) | ETag 304 works; 404 for unlisted papers |

## 13. Spec deltas (section 8.1 / 8.2 / 8.3 / 8.1.1)

1. **OpenAlex units and headers.** Spec cites only `X-RateLimit-Limit-USD`. Today also: `X-RateLimit-Limit: 1000` credits, `Remaining` (credits), `Cost-USD`, `Credits-Used`, `Reset` (seconds to midnight UTC), `meta.cost_usd`. 1 credit = $0.0001; search = 10 credits; filter/list = 1; singleton = 0. Budget-guard can read `X-RateLimit-Remaining-USD` or `X-RateLimit-Remaining`/`Limit` (fine either way).
2. **OpenAlex `search` now matches full text and keywords** (and costs 0.001). Prefer `search.title_and_abstract=` for precision; `title_and_abstract.search` filter also costs a search. Cursor paging needs a key; keyless is page 1 plus `page=` up to 10k.
3. **OpenAlex per-second limit** is 10 rps keyless (docs); the spec's 5 rps / concurrency 2 stays valid. A per-second 429 is free. 404 is HTML, not JSON.
4. **DataCite does have a published limit:** 500 / 5 min / IP unidentified (1000 with an email in UA or `mailto=`). Spec says "No published limit". Add a `limits.yaml` entry (about 1 rps, burst 10). Researchly will not add an email, so use 500.
5. **OpenCitations citations are unpaged** and huge for popular papers (Attention: 8.6 MB+, truncated by curl). Spec 8.1.1 "OpenCitations `/citations` first" needs a `citation-count` pre-check and a byte or row cap, with OpenAlex `cites:` for top-k seeds. OpenCitations does not accept `openalex:` IDs on the Index API. `/citations` and `/references` rows carry `omid`, `pmid`, `openalex` IDs usable for identity merging.
6. **arXiv DOI derivable without a call** (section 8.3 and 8.1.1 "arXiv ID to DataCite DOI"): construct `10.48550/arXiv.<id>` locally (strip `vN`, compare lowercase). Remove DataCite from the routing path for identity; keep it only for metadata. Versioned DOIs do not exist.
7. **arXiv API error behaviour:** all failures return HTTP 200 (including malformed IDs as an `<entry>` with `id` under `/api/errors`). Connector must detect it; no `ETag`/`Last-Modified` (spec 8.2 "ETag/If-None-Match" applies only to DataCite and HF; OpenAlex, arXiv, Crossref, OpenCitations, Europe PMC and PubMed return no validators).
8. **HF Papers limits are exposed** as `RateLimit` / `RateLimit-Policy` headers (search 50 / 5 min, api 500 / 5 min) and the host supports ETag. Spec table "HF search 8 per min" is consistent (50 per 5 min = 10 per min).
9. **PubMed and Europe PMC:** PubMed header confirmed (`X-Ratelimit-Limit: 3`, `Remaining`). Europe PMC returns no limit headers. Bad query/ID shape differs: PubMed 400 XML, Europe PMC 200 with `hitCount 0`.
10. **Crossref:** limits confirmed (single 5/s, list 1/s) via `x-rate-limit-limit`, `x-api-pool` headers. Batch DOI via `filter=doi:a,doi:b` is a list-pool call (1 rps); there is no singleton batch.
11. **Identity (8.3):** OpenAlex DOIs for arXiv are lowercased, so DOI normalisation must lowercase. OpenAlex may hold duplicate Work IDs for one arXiv paper, so `paper_ids` must allow multiple OpenAlex IDs per paper. PubMed `ArticleId` scoping pitfall (section 7).
12. **Keys/emails:** **no Tier-1 source now requires a key or email for keyless use.** OpenAlex's agent key flow (`POST /api-keys`) needs the user's own email and is optional. DataCite and NCBI raise limits with an email, and neither is required.
