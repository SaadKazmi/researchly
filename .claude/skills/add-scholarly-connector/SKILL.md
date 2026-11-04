---
name: add-scholarly-connector
description: Use when adding or changing a scholarly data-source connector in backend/src/researchly/connectors/ (a new API such as OpenReview, DBLP or CORE, or a fix to an existing one). A checklist covering the PoliteClient, limits, cache keys, ID normalization, error mapping, cassettes and the registry.
---

# Add a scholarly connector

Read ADR 0008 (`docs/adr/0008-scholarly-http.md`) and spec §8 first. Copy the closest existing connector:
- `crossref.py` for JSON singletons;
- `arxiv.py` or `pubmed.py` for XML;
- `opencitations.py` for count-first lists;
- `openalex.py` for budgets and paging.

## Research before code
- [ ] Probe the API live, keyless and with no email. Record the following in `docs/research/`:
  - limits and their headers;
  - error shapes (404, 400, errors returned as 200);
  - ETag support;
  - ID forms;
  - response size for a popular paper.
- [ ] Confirm it needs no key, account or email (spec D14). If it does, it is an optional connector, never a default.

## Module (`connectors/<name>.py`)
- [ ] Define `class <Name>Connector` with `name = "<name>"`, `__init__(self, client: PoliteClient)`, the four `Connector` methods (`get(self, ids_, …)`; the protocol's parameter is positional-only) and `capabilities()`. Put `BASE = "https://…"` at module level.
- [ ] All HTTP goes through `client.get(url, source=self.name, cache_key=…, ttl=…)`. Never import httpx or httpcore.
- [ ] Add a `limits.yaml` entry (interval seconds, concurrency) set just below the observed limit. Use a `bucket=` when one host has pools with different limits (Crossref list, HF search).
- [ ] Cache keys start `"<name>:v1:"`. Bump the version when the field selection changes.
  - Singletons and references: `TTL_METADATA`.
  - Counts and growing citer lists: `TTL_COUNTS`.
  - Searches: `TTL_SEARCH`, keyed by `sha256(json.dumps(params, sort_keys=True))`.
  - Reuse one cached request rather than fetching a record twice.
- [ ] Never put an identifier raw into a path or query expression:
  - Path values go through `quote(value, safe="/:")`.
  - Values containing filter separators (`,`, `|`, quotes) are escaped or sent one by one.
  - Unit-test DOI `10.1234/a#b`. `ids.doi` rejects dot segments (`10.1234/../x`), so a crafted DOI cannot reach another API path.
- [ ] Normalize every identifier through `connectors/ids.py`:
  - A record carrying an arXiv ID also carries `ids.arxiv_doi(...)`.
  - ORCIDs are bare `0000-…`, else None.
- [ ] Error mapping:
  - 404 or an empty result → `[]`.
  - Other non-2xx → `SourceUnavailable(self.name, "HTTP n")`.
  - A 2xx body that does not parse, or has the wrong shape (HTML, truncated JSON or XML, an entity bomb, a non-object envelope) → `SourceUnavailable(self.name, "unreadable response")`. `ValueError`, `ParseError`, `AttributeError` and `TypeError` never escape.
  - Quota → `QuotaExhausted`.
  - Oversized lists → `TooManyResults`, after a count check where the API offers one.
- [ ] Parse XML only with `defusedxml.ElementTree`. Import `xml.etree` for types or `ParseError` only.
- [ ] `get()` accepts mixed schemes, skips the ones it cannot look up, dedupes, and makes no request when nothing is left. Use a singleton for one ID, and batch only groups of two or more.
- [ ] `citations()` returns `[]` with no request when the capability is absent.
- [ ] Paging loops stop on a short page and respect any budget on every page.

## Tests (`tests/connectors/test_<name>.py`)
- [ ] Cassette tests use `@pytest.mark.vcr`, the `polite` fixture and `anyio`. Cover:
  - search;
  - get by the probe IDs (arXiv `1706.03762`, DOI `10.1038/nature14539`, PMID `26017442`);
  - each citation direction;
  - a not-found case.

  Assert concrete values.
- [ ] Unit tests use inline payloads and `httpx.MockTransport`. Cover:
  - parsing edge cases;
  - quoting;
  - exact request params;
  - cache keys and TTLs (a spy on `client.get`);
  - every unreadable body.
- [ ] Record once, live and keyless: `uv run --directory backend pytest tests/connectors/test_<name>.py --record-mode=once -q`. Then re-run without the flag; record mode `none` makes a missing cassette fail.
- [ ] Check the cassettes before committing:
  - Keep them small (`limit` 3–5, `select=`).
  - Pick targets with short citation lists.
  - Grep them for `mailto`, `cookie`, `authorization`, `api_key`, `token` and email addresses. There must be none; `conftest.py` strips `set-cookie`.
  - Every response is 200 or a deliberate 4xx. Never commit a 429 or 5xx: re-record when the API is healthy.

## Wiring
- [ ] Add the connector to `build_connectors` in `connectors/__init__.py` and to the capability table in `tests/connectors/test_registry.py`.
- [ ] Add it to `SOURCE_PRIORITY` in `services/identity.py` (field-merge priority).
- [ ] Add it to routing in `services/search.py` if it should be searched by default.
- [ ] Add a probe to `tests/connectors/test_live.py`, which the nightly workflow runs.
- [ ] Update spec §8.1 and §8.2 (limits table) and the research doc.
- [ ] Verify with `uv run --directory backend pytest -q`, `ruff check`, `ruff format --check` and `pyright`. Commit as `feat: <name> connector`.
