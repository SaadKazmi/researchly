# Step 2a: PoliteClient, Tier-1 connectors and identity resolution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Researchly can search and fetch paper metadata from the eight keyless Tier-1 sources through one polite, SSRF-safe HTTP client, dedupe the results into one record per paper, and store them in Postgres with per-field provenance.

**Architecture:** `connectors/http.py` holds `PoliteClient` (per-host limits from `limits.yaml`, retry with Retry-After, a per-source circuit breaker, a streamed size cap, an SSRF-guarded httpx transport, and a Postgres `http_cache`). Each source is one module in `connectors/` implementing the `Connector` protocol and returning `PaperRecord`s. `services/identity.py` dedupes and merges records in memory, `services/paper_store.py` upserts them into `paper`/`paper_ids`/`author`/`venue`, and `services/search.py` fans a query out to the chosen connectors.

**Tech Stack:** httpx 0.28.1 + httpcore 1.0.9 (custom network backend), stdlib `ipaddress`/`email.utils`, defusedxml, SQLAlchemy 2 async + Alembic, pg_trgm, vcrpy + pytest-recording (cassettes), `httpx.MockTransport` (unit tests).

**Spec:** `docs/superpowers/specs/2026-10-28-researchly-design.md` (§8.1, §8.1.1, §8.2, §8.3, §16, §19.3, §21.2). Research: `docs/research/2026-11-03-step2a-connectors.md` (live API facts and field maps, cited below as **R-conn §N**) and `docs/research/2026-11-03-step2a-http-infra.md` (**R-http §N**).

## Global Constraints

- All outbound HTTP goes through `PoliteClient` in `backend/src/researchly/connectors/http.py`; no module except `connectors/http.py` imports `httpx` or `httpcore` (tests excepted).
- Keyless: no connector requires a key, an account or an email. User-Agent: `Researchly/<version> (+https://github.com/SaadKazmi/researchly)`; `; mailto:<addr>` is appended only when the caller passes the user's own address. There is no project email.
- Per-host limits (`limits.yaml`), as interval seconds / concurrency: OpenAlex 0.2/2; Crossref single 0.2/2; Crossref list 1.0/1; OpenCitations 0.5/1; arXiv API 3.0/1; DataCite 1.0/1; Europe PMC 0.2/2; PubMed 0.5/1; HF api 1.0/1; HF search 7.5/1; any other host 1.0/1.
- Retry: GET only; on transport errors and 429/500/502/503/504; at most 4 attempts; full jitter `uniform(0, min(60, 2**attempt))`; a `Retry-After` (seconds or HTTP-date) defers the host instead; `Retry-After` > 120 s raises `QuotaExhausted` without waiting.
- Circuit breaker per source: opens after 5 consecutive failed calls; cool-down 60 s doubling to 900 s; one half-open probe; 400/404 are answers, not failures.
- SSRF: resolve once, check every address (not `is_private` alone; unwrap IPv4-mapped, 6to4, Teredo, NAT64), connect to the checked IP with URL/Host/SNI untouched; ports 80/443 only; `trust_env=False`; manual redirects, at most 5; response body cap 5 MB by default.
- Cache TTLs: metadata and references 60 days; citation counts 7 days; search results 24 h. `http_cache` is an UNLOGGED table keyed by a normalized ID.
- OpenAlex budget guard: read `X-RateLimit-Remaining-USD` / `X-RateLimit-Limit-USD`; at ≤20% remaining, or after 50 searches in the current UTC day, paid calls (search, list filter, `cites:`) raise `QuotaExhausted`; singletons ($0) continue.
- Identity: exact match on a normalized ID (DOI lowercased, arXiv without version, arXiv DataCite DOI `10.48550/arxiv.<id>` derived locally, OpenAlex `W…`, PMID, PMCID); otherwise normalized-title trigram similarity ≥ 0.92, year ± 1 and the same first-author surname. Merges keep provenance per field (`field_sources`).
- Untrusted XML is parsed with `defusedxml.ElementTree`, never `xml.etree` directly.
- Tests: connector tests replay vcrpy cassettes (record mode `none` in CI; a missing cassette fails). Unit tests never touch the network. Cassettes must contain no key, cookie or email.
- Python 3.12, ruff (line length 120), pyright standard; `uv run --directory backend …` for every command.
- Never edit an applied Alembic revision; add `0002`.

## Rulings made while planning

- **Scope split.** Step 2 is three plans: 2a (this), 2b (full-text ingestion, robots.txt via Protego, content-type allowlist, chunking, embeddings, hybrid search + rerank, SPECTER2), 2c (Tier-2/3 connectors, PwC archive, SearXNG wiring). The `citation` table arrives with the graph step (5); 2a connectors return citation IDs without storing edges.
- **Routing input.** `default_sources()` takes the query's domains (`"biomedical"`, `"ml"`) from the caller; the LLM query classifier arrives with the agents (step 3).
- **Python patch level.** `requires-python` stays `>=3.12,<3.13`. The SSRF check unwraps embedded IPv4 itself, so it does not depend on the CVE-2024-4032 fix (3.12.4); its tests run on the dev box's 3.12.0 and CI's latest 3.12.
- **pytest-recording `--block-network`** is not used until 0.14.1 clears the release-age gate (Windows asyncio fix); record mode `none` already fails unrecorded requests.
- **OpenAlex search parameter.** `search.title_and_abstract=` (title + abstract; live-verified), not `search=` (now full text, noisier at the same cost).
- **Author identity.** Authors match by ORCID, then OpenAlex ID; otherwise a new row. Full author disambiguation is a step 5 concern.
- **Federated search errors.** Only `ConnectorError`s are collected per source; any other exception is a bug and propagates.

---

## File structure

| File | Responsibility |
|---|---|
| `backend/src/researchly/connectors/__init__.py` | `build_connectors(client)` registry |
| `backend/src/researchly/connectors/base.py` | `ExternalId`, `Author`, `PaperRecord`, `SearchQuery`, `ConnectorCaps`, `Connector` protocol, errors |
| `backend/src/researchly/connectors/ids.py` | Identifier normalization |
| `backend/src/researchly/connectors/http.py` | `PoliteClient`, `Fetched`, `HostLimit`, `Breaker`, `retry_after`, TTL constants |
| `backend/src/researchly/connectors/ssrf.py` | `check`, `GuardedBackend`, `GuardedTransport` |
| `backend/src/researchly/connectors/cache.py` | `HttpCache` over the `http_cache` table |
| `backend/src/researchly/connectors/limits.yaml` | Per-host interval and concurrency |
| `backend/src/researchly/connectors/{openalex,arxiv,crossref,opencitations,datacite,europepmc,pubmed,hf}.py` | One connector each |
| `backend/src/researchly/services/__init__.py` | empty |
| `backend/src/researchly/services/identity.py` | Title normalization, trigram similarity, `same_paper`, `merge`, `dedupe` |
| `backend/src/researchly/services/paper_store.py` | `upsert(session, record) -> UUID` |
| `backend/src/researchly/services/search.py` | `default_sources`, `federated_search` |
| `backend/src/researchly/db/models.py` | + `Paper`, `PaperId`, `Author`, `PaperAuthor`, `Venue`, `HttpCacheRow` |
| `backend/src/researchly/db/migrations/versions/0002_papers_and_http_cache.py` | New tables |
| `backend/tests/connectors/…` | Unit + cassette tests; `cassettes/` per module |
| `backend/tests/services/…` | Identity, store, search tests |
| `.github/workflows/nightly.yml` | Live keyless connector checks (`-m nightly`) |
| `.claude/skills/add-scholarly-connector/SKILL.md` | Project skill (spec §23.2) |

---

### Task 1: Connector contract, identifier normalization and test harness

**Files:**
- Create: `backend/src/researchly/connectors/__init__.py` (empty for now), `backend/src/researchly/connectors/base.py`, `backend/src/researchly/connectors/ids.py`
- Create: `backend/tests/connectors/__init__.py`, `backend/tests/connectors/conftest.py`, `backend/tests/connectors/test_ids.py`
- Modify: `backend/pyproject.toml` (deps, marker, addopts)

**Interfaces:**
- Produces: `ExternalId(scheme, value)` NamedTuple; `Scheme` literal; `ids.doi/arxiv/openalex/pmid/pmcid/arxiv_doi/normalize`; `Author`, `PaperRecord`, `SearchQuery`, `ConnectorCaps`, `Connector`; `ConnectorError`, `QuotaExhausted`, `SourceUnavailable`, `TooManyResults`, `ResponseTooLarge`; the `vcr_config` fixture.

- [ ] **Step 1: Dependencies and markers.** In `backend/pyproject.toml`:
  - `dependencies`: add `"httpx==0.28.1",  # PoliteClient's guarded transport replaces a private attribute (connectors/ssrf.py)`, `"httpcore==1.0.9",` and `"defusedxml>=0.7.1",  # untrusted XML (arXiv, PubMed)`.
  - `dev`: remove `"httpx>=0.28"`; add `"vcrpy>=9.2",` and `"pytest-recording>=0.14",`.
  - `addopts = "-m 'not live and not platform and not nightly'"`; add marker `"nightly: calls real keyless scholarly APIs; runs in the nightly workflow"`.
  - Run `uv lock --directory backend` then `uv sync --directory backend`. Expected: lock resolves vcrpy 9.2.x and pytest-recording 0.14.0 (the 7-day gate).

- [ ] **Step 2: Write the failing tests** `backend/tests/connectors/test_ids.py`:

```python
import pytest

from researchly.connectors import ids
from researchly.connectors.base import ExternalId


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.1038/nature14539", "10.1038/nature14539"),
        ("https://doi.org/10.48550/arXiv.1706.03762", "10.48550/arxiv.1706.03762"),
        ("doi:10.1000/ABC", "10.1000/abc"),
        ("not a doi", None),
    ],
)
def test_doi(raw, expected):
    assert ids.doi(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1706.03762v7", "1706.03762"),
        ("http://arxiv.org/abs/2610.41932v1", "2610.41932"),  # 5-digit new-style IDs exist now
        ("arXiv:hep-th/9901001", "hep-th/9901001"),
        ("math.AG/0101001v2", "math.AG/0101001"),
        ("abc", None),
    ],
)
def test_arxiv(raw, expected):
    assert ids.arxiv(raw) == expected


def test_arxiv_doi_is_derived_locally_and_lowercased():
    assert ids.arxiv_doi("1706.03762") == "10.48550/arxiv.1706.03762"
    assert ids.arxiv_doi("hep-th/9901001") == "10.48550/arxiv.hep-th/9901001"


@pytest.mark.parametrize(
    ("eid", "expected"),
    [
        (ExternalId("openalex", "https://openalex.org/W2626778328"), ExternalId("openalex", "W2626778328")),
        (ExternalId("pmid", "https://pubmed.ncbi.nlm.nih.gov/26017442"), ExternalId("pmid", "26017442")),
        (ExternalId("pmcid", "pmc4561234"), ExternalId("pmcid", "PMC4561234")),
        (ExternalId("doi", "garbage"), None),
    ],
)
def test_normalize(eid, expected):
    assert ids.normalize(eid) == expected


def test_record_exposes_ids_by_scheme():
    from researchly.connectors.base import PaperRecord

    rec = PaperRecord(source="arxiv", ids=[ExternalId("arxiv", "1706.03762")], title="Attention")
    assert rec.id("arxiv") == "1706.03762"
    assert rec.id("doi") is None
```

- [ ] **Step 3: Run to confirm failure.** `uv run --directory backend pytest tests/connectors/test_ids.py -q` → FAIL (import error).

- [ ] **Step 4: Implement** `connectors/base.py`:

```python
"""The connector contract (spec §8.2): every source returns PaperRecords keyed by normalized external IDs."""

from dataclasses import dataclass, field
from typing import Any, Literal, NamedTuple, Protocol

Scheme = Literal["doi", "arxiv", "openalex", "pmid", "pmcid", "s2", "dblp", "acl"]


class ExternalId(NamedTuple):
    scheme: Scheme
    value: str


@dataclass(frozen=True)
class Author:
    name: str
    orcid: str | None = None  # bare 0000-0000-0000-0000, no URL
    openalex_id: str | None = None  # bare A…


@dataclass
class PaperRecord:
    source: str
    ids: list[ExternalId]
    title: str
    abstract: str | None = None
    year: int | None = None
    authors: list[Author] = field(default_factory=list)
    venue: str | None = None
    venue_type: str | None = None
    cited_by: int | None = None
    fwci: float | None = None
    pct: float | None = None  # citation percentile, 0..1
    oa_url: str | None = None
    is_oa: bool | None = None
    license: str | None = None
    is_retracted: bool | None = None
    references: list[ExternalId] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)  # source-specific: topics, upvotes, code links
    field_sources: dict[str, str] = field(default_factory=dict)  # filled by identity.merge

    def id(self, scheme: Scheme) -> str | None:
        return next((i.value for i in self.ids if i.scheme == scheme), None)


@dataclass(frozen=True)
class SearchQuery:
    text: str
    limit: int = 25
    year_from: int | None = None
    year_to: int | None = None


@dataclass(frozen=True)
class ConnectorCaps:
    search: bool
    get: bool
    citations_in: bool
    citations_out: bool


class Connector(Protocol):
    name: str

    async def search(self, query: SearchQuery) -> list[PaperRecord]: ...
    async def get(self, ids: list[ExternalId]) -> list[PaperRecord]: ...
    async def citations(self, id: ExternalId, direction: Literal["in", "out"]) -> list[ExternalId]: ...
    def capabilities(self) -> ConnectorCaps: ...


class ConnectorError(Exception):
    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source


class QuotaExhausted(ConnectorError):
    """A budget or rate quota is spent; other sources continue (spec §8.2)."""


class SourceUnavailable(ConnectorError):
    """Retries are exhausted or the circuit is open."""


class TooManyResults(ConnectorError):
    """An unpaged answer would be too large (e.g. OpenCitations citations of a famous paper)."""


class ResponseTooLarge(ConnectorError):
    """The body exceeded PoliteClient's size cap."""
```

`connectors/ids.py`:

```python
"""Identifier normalization shared by connectors and identity resolution (spec §8.3)."""

import re

from researchly.connectors.base import ExternalId

_DOI = re.compile(r"^10\.\d{4,9}/\S+$")
_ARXIV_NEW = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
_ARXIV_OLD = re.compile(r"^([a-z-]+(?:\.[A-Za-z]{2})?/\d{7})(v\d+)?$")
_OPENALEX = re.compile(r"^W\d+$")
_PMID = re.compile(r"^\d{1,9}$")
_PMCID = re.compile(r"^PMC\d+$")


def _strip(raw: str, *prefixes: str) -> str:
    value = raw.strip()
    for prefix in prefixes:
        if value.lower().startswith(prefix):
            return value[len(prefix) :]
    return value


def doi(raw: str) -> str | None:
    value = _strip(raw, "https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi:")
    value = value.lower()  # DOIs are case-insensitive; OpenAlex and DataCite store arXiv DOIs lowercased
    return value if _DOI.match(value) else None


def arxiv(raw: str) -> str | None:
    value = _strip(raw, "arxiv:", "https://arxiv.org/abs/", "http://arxiv.org/abs/")
    match = _ARXIV_NEW.match(value) or _ARXIV_OLD.match(value)
    return match.group(1) if match else None  # versionless: versioned DataCite DOIs do not exist


def arxiv_doi(arxiv_id: str) -> str:
    return f"10.48550/arxiv.{arxiv_id}".lower()


def openalex(raw: str) -> str | None:
    value = _strip(raw, "https://openalex.org/", "http://openalex.org/", "openalex:").upper()
    return value if _OPENALEX.match(value) else None


def pmid(raw: str) -> str | None:
    value = _strip(raw, "https://pubmed.ncbi.nlm.nih.gov/", "pmid:").rstrip("/")
    return value if _PMID.match(value) else None


def pmcid(raw: str) -> str | None:
    value = _strip(raw, "pmcid:").upper()
    return value if _PMCID.match(value) else None


_NORMALIZERS = {"doi": doi, "arxiv": arxiv, "openalex": openalex, "pmid": pmid, "pmcid": pmcid}


def normalize(eid: ExternalId) -> ExternalId | None:
    fn = _NORMALIZERS.get(eid.scheme)
    if fn is None:
        return eid
    value = fn(eid.value)
    return ExternalId(eid.scheme, value) if value else None
```

`backend/tests/connectors/conftest.py` (cassette policy, spec §21.2; R-http §7):

```python
import pytest


@pytest.fixture(scope="module")
def vcr_config():
    return {
        "filter_headers": ["authorization", "x-api-key", "cookie", "set-cookie"],
        "filter_query_parameters": ["api_key", "mailto", "email"],  # a mailto is the user's own address
        "decode_compressed_response": True,
        "match_on": ("method", "scheme", "host", "port", "path", "query", "body"),
    }
```

- [ ] **Step 5: Run the tests.** `uv run --directory backend pytest tests/connectors -q` → PASS. Then `ruff check`, `ruff format`, `pyright`.

- [ ] **Step 6: Commit.** `git add backend/pyproject.toml backend/uv.lock backend/src/researchly/connectors backend/tests/connectors` → `feat: connector contract and identifier normalization`.

---

### Task 2: PoliteClient — limits, retry, breaker, size cap

**Files:**
- Create: `backend/src/researchly/connectors/http.py`, `backend/src/researchly/connectors/limits.yaml`
- Test: `backend/tests/connectors/test_http.py`

**Interfaces:**
- Consumes: errors from Task 1.
- Produces:
  - `Fetched(status: int, headers: dict[str, str], body: bytes, url: str)` with `.json() -> Any` and `.text() -> str` (UTF-8, `errors="replace"`); header names lower-case.
  - `PoliteClient(*, mailto: str | None = None, cache: HttpCache | None = None, transport: httpx.AsyncBaseTransport | None = None, limits: dict[str, tuple[float, int]] | None = None, sleep=asyncio.sleep, rand=random.random)`; `async get(url, *, source: str, params: Mapping[str, str | int] | None = None, bucket: str | None = None, cache_key: str | None = None, ttl: timedelta | None = None, max_bytes: int = 5_000_000) -> Fetched`; `async aclose()`; async context manager.
  - `TTL_METADATA = timedelta(days=60)`, `TTL_COUNTS = timedelta(days=7)`, `TTL_SEARCH = timedelta(hours=24)`.
  - `retry_after(value: str | None) -> float | None`; `HostLimit`; `Breaker`.
  - `bucket` names a limits.yaml key other than the URL's host (Crossref `api.crossref.org/list`, HF `huggingface.co/search`).

- [ ] **Step 1: `limits.yaml`** (interval seconds, concurrency; spec §8.2 table with the R-conn §13 DataCite delta):

```yaml
# Per-host pacing for PoliteClient: [interval_seconds, concurrency]. Set just below the observed limits (spec §8.2).
api.openalex.org: [0.2, 2]
api.crossref.org: [0.2, 2]          # single pool, 5 rps
api.crossref.org/list: [1.0, 1]     # list pool, 1 rps, concurrency 1
api.opencitations.net: [0.5, 1]     # 180/min/IP
export.arxiv.org: [3.0, 1]          # one request every 3 s, single connection
api.datacite.org: [1.0, 1]          # 500 per 5 min per IP, unidentified
www.ebi.ac.uk: [0.2, 2]             # Europe PMC, no published limit
eutils.ncbi.nlm.nih.gov: [0.5, 1]   # 3 rps keyless
huggingface.co: [1.0, 1]            # api pool, 500 per 5 min
huggingface.co/search: [7.5, 1]     # search pool, 50 per 5 min
default: [1.0, 1]
```

- [ ] **Step 2: Write the failing tests** `backend/tests/connectors/test_http.py`. All use `httpx.MockTransport`, a fake `sleep` that records delays, and `rand=lambda: 0.5`:

```python
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from researchly.connectors.base import QuotaExhausted, ResponseTooLarge, SourceUnavailable
from researchly.connectors.http import Breaker, PoliteClient, retry_after

pytestmark = pytest.mark.anyio


def client_for(handler, slept):
    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    return PoliteClient(transport=httpx.MockTransport(handler), sleep=sleep, rand=lambda: 0.5, limits={"default": (0.0, 1)})


def test_retry_after_parses_seconds_and_http_dates():
    assert retry_after("7") == 7.0
    soon = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
    assert 25 <= (retry_after(soon) or 0) <= 30
    assert retry_after(None) is None and retry_after("soon") is None


async def test_user_agent_and_optional_mailto():
    seen = []

    def handler(request):
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, json={})

    async with client_for(handler, []) as client:
        await client.get("https://api.example.org/x", source="t")
    async with PoliteClient(transport=httpx.MockTransport(handler), mailto="me@example.org") as client:
        await client.get("https://api.example.org/x", source="t")
    assert seen[0].startswith("Researchly/") and "github.com/SaadKazmi/researchly" in seen[0]
    assert "mailto" not in seen[0]
    assert seen[1].endswith("; mailto:me@example.org)")


async def test_retries_5xx_with_jitter_then_succeeds():
    calls = iter([503, 502, 200])
    slept: list[float] = []
    async with client_for(lambda r: httpx.Response(next(calls), json={"ok": 1}), slept) as client:
        fetched = await client.get("https://api.example.org/x", source="t")
    assert fetched.status == 200 and fetched.json() == {"ok": 1}
    assert slept == [0.5, 1.0]  # rand 0.5 * min(60, 2**attempt)


async def test_404_and_400_are_answers_not_retries():
    slept: list[float] = []
    async with client_for(lambda r: httpx.Response(404, text="<html>"), slept) as client:
        fetched = await client.get("https://api.example.org/x", source="t")
    assert fetched.status == 404 and slept == []


async def test_gives_up_after_four_attempts():
    slept: list[float] = []
    async with client_for(lambda r: httpx.Response(500), slept) as client:
        with pytest.raises(SourceUnavailable):
            await client.get("https://api.example.org/x", source="t")
    assert len(slept) == 3


async def test_long_retry_after_raises_quota_exhausted_without_waiting():
    slept: list[float] = []
    async with client_for(lambda r: httpx.Response(429, headers={"retry-after": "3600"}), slept) as client:
        with pytest.raises(QuotaExhausted):
            await client.get("https://api.example.org/x", source="t")
    assert slept == []


async def test_transport_errors_are_retried():
    calls = iter([httpx.ConnectError("boom"), httpx.Response(200, json={})])

    def handler(request):
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    async with client_for(handler, []) as client:
        assert (await client.get("https://api.example.org/x", source="t")).status == 200


async def test_body_over_the_cap_is_refused():
    async with client_for(lambda r: httpx.Response(200, content=b"x" * 2048), []) as client:
        with pytest.raises(ResponseTooLarge):
            await client.get("https://api.example.org/x", source="t", max_bytes=1024)


async def test_redirects_are_followed_manually_and_capped():
    def handler(request):
        n = int(request.url.path.strip("/") or 0)
        return httpx.Response(302, headers={"location": f"/{n + 1}"})

    async with client_for(handler, []) as client:
        with pytest.raises(SourceUnavailable, match="redirects"):
            await client.get("https://api.example.org/0", source="t")


async def test_redirect_to_a_non_http_scheme_is_refused():
    async with client_for(lambda r: httpx.Response(302, headers={"location": "file:///etc/passwd"}), []) as client:
        with pytest.raises(SourceUnavailable, match="scheme"):
            await client.get("https://api.example.org/x", source="t")


def test_breaker_opens_after_five_failures_and_allows_one_probe():
    now = [0.0]
    breaker = Breaker(clock=lambda: now[0])
    for _ in range(5):
        breaker.before("t")
        breaker.failure()
    with pytest.raises(SourceUnavailable):
        breaker.before("t")
    now[0] = 61.0
    breaker.before("t")  # the half-open probe
    with pytest.raises(SourceUnavailable):
        breaker.before("t")  # only one probe at a time
    breaker.failure()
    now[0] = 61.0 + 119.0
    with pytest.raises(SourceUnavailable):
        breaker.before("t")  # cool-down doubled to 120 s
    now[0] = 61.0 + 121.0
    breaker.before("t")
    breaker.success()
    breaker.before("t")  # closed again


async def test_buckets_and_hosts_are_paced_separately(monkeypatch):
    async with PoliteClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
        limits={"api.example.org": (10.0, 1), "api.example.org/list": (0.0, 1), "default": (0.0, 1)},
    ) as client:
        assert client.limit_for("https://api.example.org/x", None).interval == 10.0
        assert client.limit_for("https://api.example.org/x", "api.example.org/list").interval == 0.0
        assert client.limit_for("https://other.example.org/x", None).interval == 0.0
```

- [ ] **Step 3: Run to confirm failure.** `uv run --directory backend pytest tests/connectors/test_http.py -q` → FAIL.

- [ ] **Step 4: Implement** `connectors/http.py`:

```python
"""The only way Researchly talks to the internet (AGENTS.md; spec §8.2, §19.3).

Per-host pacing, retries with Retry-After, a circuit breaker per source, a body size cap, manual redirects and an
SSRF-guarded transport. Limits are in-process: one app process does all outbound HTTP (spec §4.2).
"""

import asyncio
import email.utils
import json
import random
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx
import yaml

from researchly.connectors.base import QuotaExhausted, ResponseTooLarge, SourceUnavailable

if TYPE_CHECKING:
    from researchly.connectors.cache import HttpCache

TTL_METADATA = timedelta(days=60)
TTL_COUNTS = timedelta(days=7)
TTL_SEARCH = timedelta(hours=24)

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
LONG_WAIT = 120.0  # a longer Retry-After is a quota, not a hiccup
MAX_REDIRECTS = 5
LIMITS_FILE = Path(__file__).with_name("limits.yaml")


@dataclass(frozen=True)
class Fetched:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str

    def json(self) -> Any:
        return json.loads(self.body)

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")  # explicit: Windows defaults to cp1252 (HF emoji)


def retry_after(value: str | None) -> float | None:
    """RFC 9110 §10.2.3: delay-seconds or an HTTP-date."""
    if not value:
        return None
    if value.strip().isdigit():
        return float(value)
    try:
        return max(0.0, (email.utils.parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError):
        return None


class HostLimit:
    """One request per `interval` seconds, at most `concurrency` in flight; `defer` pushes the next slot."""

    def __init__(self, interval: float, concurrency: int) -> None:
        self.interval = interval
        self._sem = asyncio.Semaphore(concurrency)
        self._next_at = 0.0

    def defer(self, seconds: float) -> None:
        self._next_at = max(self._next_at, time.monotonic() + seconds)

    async def __aenter__(self) -> None:
        await self._sem.acquire()
        now = time.monotonic()
        wait = self._next_at - now
        self._next_at = max(now, self._next_at) + self.interval  # reserve the slot before sleeping
        if wait > 0:
            await asyncio.sleep(wait)

    async def __aexit__(self, *exc: object) -> None:
        self._sem.release()


class Breaker:
    """Opens after `threshold` consecutive failed calls; one half-open probe after the cool-down."""

    def __init__(
        self, threshold: int = 5, cooldown: float = 60.0, max_cooldown: float = 900.0, clock=time.monotonic
    ) -> None:
        self._threshold, self._base, self._max, self._clock = threshold, cooldown, max_cooldown, clock
        self._cooldown = cooldown
        self._failures = 0
        self._open_until = 0.0
        self._probing = False

    def before(self, source: str) -> None:
        if self._failures < self._threshold:
            return
        if self._clock() < self._open_until or self._probing:
            raise SourceUnavailable(source, "circuit open after repeated failures")
        self._probing = True

    def success(self) -> None:
        self._failures, self._cooldown, self._probing = 0, self._base, False

    def failure(self, until: float | None = None) -> None:
        self._failures += 1
        self._probing = False
        if self._failures >= self._threshold:
            self._open_until = max(until or 0.0, self._clock() + self._cooldown)
            self._cooldown = min(self._cooldown * 2, self._max)


def _load_limits() -> dict[str, tuple[float, int]]:
    raw = yaml.safe_load(LIMITS_FILE.read_text(encoding="utf-8"))
    return {key: (float(v[0]), int(v[1])) for key, v in raw.items()}


class PoliteClient:
    def __init__(
        self,
        *,
        mailto: str | None = None,
        cache: "HttpCache | None" = None,
        transport: httpx.AsyncBaseTransport | None = None,
        limits: dict[str, tuple[float, int]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        from researchly.connectors.ssrf import GuardedTransport

        agent = f"Researchly/{version('researchly')} (+https://github.com/SaadKazmi/researchly"
        agent += f"; mailto:{mailto})" if mailto else ")"  # only the user's own address; never a project email
        self._http = httpx.AsyncClient(
            transport=transport or GuardedTransport(),
            headers={"User-Agent": agent},
            follow_redirects=False,  # every hop is checked below
            trust_env=False,  # an env proxy would bypass the SSRF check
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        self._cache = cache
        self._limit_specs = limits or _load_limits()
        self._limits: dict[str, HostLimit] = {}
        self._breakers: defaultdict[str, Breaker] = defaultdict(Breaker)
        self._sleep, self._rand = sleep, rand

    async def __aenter__(self) -> "PoliteClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    def limit_for(self, url: str, bucket: str | None) -> HostLimit:
        key = bucket or urlsplit(url).hostname or ""
        if key not in self._limit_specs:
            key = "default"
        if key not in self._limits:
            self._limits[key] = HostLimit(*self._limit_specs[key])
        return self._limits[key]

    async def get(
        self,
        url: str,
        *,
        source: str,
        params: Mapping[str, str | int] | None = None,
        bucket: str | None = None,
        cache_key: str | None = None,
        ttl: timedelta | None = None,
        max_bytes: int = 5_000_000,
    ) -> Fetched:
        cached = await self._cache.get(cache_key) if self._cache and cache_key else None
        if cached and cached.fresh:
            return cached.fetched
        headers = {"If-None-Match": cached.etag} if cached and cached.etag else {}
        breaker = self._breakers[source]
        breaker.before(source)
        limit = self.limit_for(url, bucket)
        for attempt in range(MAX_ATTEMPTS):
            wait: float | None = None
            try:
                async with limit:
                    fetched = await self._send(url, params, headers, max_bytes, source)
            except httpx.TransportError:
                fetched = None
            if fetched is not None and fetched.status not in RETRY_STATUS:
                breaker.success()
                if cached and fetched.status == 304 and self._cache and cache_key and ttl:
                    await self._cache.refresh(cache_key, ttl)
                    return cached.fetched
                if self._cache and cache_key and ttl and fetched.status == 200:
                    await self._cache.put(cache_key, fetched, ttl)
                return fetched
            if fetched is not None:
                wait = retry_after(fetched.headers.get("retry-after"))
                if wait is not None and wait > LONG_WAIT:
                    limit.defer(wait)
                    breaker.failure(until=time.monotonic() + wait)
                    raise QuotaExhausted(source, f"rate limited; retry after {wait:.0f} s")
            if attempt == MAX_ATTEMPTS - 1:
                break
            if wait is not None:
                limit.defer(wait)  # the next `async with limit` waits it out
            else:
                await self._sleep(self._rand() * min(60.0, 2.0**attempt))
        breaker.failure()
        raise SourceUnavailable(source, f"no answer from {urlsplit(url).hostname} after {MAX_ATTEMPTS} attempts")

    async def _send(
        self, url: str, params: Mapping[str, str | int] | None, headers: dict[str, str], max_bytes: int, source: str
    ) -> Fetched:
        request = self._http.build_request("GET", url, params=params, headers=headers)
        for _ in range(MAX_REDIRECTS + 1):
            try:
                response = await self._http.send(request, stream=True)
            except httpx.UnsupportedProtocol as exc:  # httpx refuses a redirect to file://, ftp:// … while building it
                raise SourceUnavailable(source, f"redirect to a disallowed scheme: {exc}") from exc
            try:
                if response.next_request is not None:  # set for every redirect because follow_redirects=False
                    request = response.next_request
                    continue
                if int(response.headers.get("content-length") or 0) > max_bytes:
                    raise ResponseTooLarge(source, f"{response.headers['content-length']} bytes > {max_bytes}")
                body = bytearray()
                async for chunk in response.aiter_bytes():  # decoded bytes, so a gzip bomb is capped too
                    body += chunk
                    if len(body) > max_bytes:
                        raise ResponseTooLarge(source, f"body > {max_bytes} bytes")
                return Fetched(
                    response.status_code, {k.lower(): v for k, v in response.headers.items()}, bytes(body), str(response.url)
                )
            finally:
                await response.aclose()
        raise SourceUnavailable(source, f"more than {MAX_REDIRECTS} redirects")
```

Note for the implementer: `GuardedTransport` arrives in Task 3. Until then, create `connectors/ssrf.py` with `class GuardedTransport(httpx.AsyncHTTPTransport): pass` so imports resolve; Task 3 replaces it. `ruff format` may rewrap long lines; keep behaviour identical.

- [ ] **Step 5: Run the tests.** `uv run --directory backend pytest tests/connectors -q` → PASS; ruff, format, pyright clean.

- [ ] **Step 6: Commit.** `feat: PoliteClient with per-host pacing, retries and a circuit breaker`.

---

### Task 3: SSRF-guarded transport

**Files:**
- Modify: `backend/src/researchly/connectors/ssrf.py`
- Test: `backend/tests/connectors/test_ssrf.py`

**Interfaces:**
- Produces: `Blocked(httpcore.ConnectError)`; `check(ip: IPv4Address | IPv6Address) -> None`; `GuardedBackend(resolve: Callable[[str, int], Awaitable[list[str]]] | None = None)`; `GuardedTransport(resolve=None)` (subclass of `httpx.AsyncHTTPTransport`, so vcrpy still intercepts it, R-http §5).

- [ ] **Step 1: Write the failing tests** `backend/tests/connectors/test_ssrf.py` (vectors from spec §19.3 and R-http §5):

```python
from ipaddress import ip_address

import httpcore
import httpx
import pytest

from researchly.connectors.http import PoliteClient
from researchly.connectors.ssrf import Blocked, GuardedBackend, GuardedTransport, check

pytestmark = pytest.mark.anyio

BLOCKED = [
    "127.0.0.1", "0.0.0.0", "10.0.0.1", "172.16.0.1", "192.168.0.1", "100.64.0.1", "169.254.169.254",
    "198.18.0.1", "224.0.0.1", "::", "::1", "::ffff:127.0.0.1", "::ffff:7f00:1", "64:ff9b::a9fe:a9fe",
    "2002:a9fe:a9fe::", "2001:0:4136:e378:8000:63bf:3fff:fdd2", "fc00::1", "fe80::1", "fe80::1%eth0", "ff02::1",
]


@pytest.mark.parametrize("raw", BLOCKED)
def test_non_global_addresses_are_blocked(raw):
    with pytest.raises(Blocked):
        check(ip_address(raw))


@pytest.mark.parametrize("raw", ["93.184.215.14", "2606:2800:21f:cb07:6820:80da:af6b:8b2c"])
def test_global_addresses_pass(raw):
    check(ip_address(raw))


def resolver(*answers):
    calls = iter(answers)

    async def resolve(host, port):
        return next(calls)

    return resolve


async def test_any_private_answer_blocks_the_connection():
    backend = GuardedBackend(resolver(["93.184.215.14", "10.0.0.1"]))
    with pytest.raises(Blocked):
        await backend.connect_tcp("mixed.example", 443)


async def test_dns_rebinding_cannot_swap_the_address():
    # first answer public, a second lookup would be private: the backend must connect to the checked IP only
    connected = []

    class Inner:
        async def connect_tcp(self, host, port, **kwargs):
            connected.append(host)
            raise httpcore.ConnectError("stop here")

    backend = GuardedBackend(resolver(["93.184.215.14"], ["127.0.0.1"]))
    backend._inner = Inner()  # type: ignore[assignment]
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("rebind.example", 443)
    assert connected == ["93.184.215.14"]


async def test_only_ports_80_and_443():
    with pytest.raises(Blocked):
        await GuardedBackend(resolver(["93.184.215.14"])).connect_tcp("example.com", 5432)


async def test_unix_sockets_are_refused():
    with pytest.raises(Blocked):
        await GuardedBackend().connect_unix_socket("/var/run/docker.sock")


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost/", "http://[::1]/", "http://169.254.169.254/"])
async def test_client_refuses_internal_targets(url):
    async with PoliteClient(transport=GuardedTransport(), limits={"default": (0.0, 1)}) as client:
        with pytest.raises(Exception) as info:  # Blocked surfaces as a transport error, then SourceUnavailable
            await client.get(url, source="t")
    assert "Blocked" in repr(info.value.__cause__ or info.value) or "no answer" in str(info.value)


def test_the_default_client_uses_the_guarded_backend():
    client = PoliteClient()
    transport = client._http._transport  # contract test: httpx upgrades must keep the private hook working
    assert isinstance(transport, GuardedTransport)
    assert isinstance(transport._pool._network_backend, GuardedBackend)
```

Note: `test_client_refuses_internal_targets` must make no real connection: `Blocked` is raised before any socket opens. If the retry loop makes it slow, pass `sleep` and `rand` fakes as in Task 2.

- [ ] **Step 2: Run to confirm failure.**

- [ ] **Step 3: Implement** `connectors/ssrf.py`:

```python
"""SSRF-safe connections (spec §19.3): resolve once, check every address, connect to the checked IP.

URL, Host header, SNI and certificate checks stay those of the original host, because httpcore passes the origin host to
`start_tls`. `GuardedTransport` subclasses `httpx.AsyncHTTPTransport` so vcrpy cassettes still intercept it.
"""

import socket
from collections.abc import Awaitable, Callable, Iterable
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network

import anyio
import httpcore
import httpx

NAT64 = ip_network("64:ff9b::/96")
Resolver = Callable[[str, int], Awaitable[list[str]]]


class Blocked(httpcore.ConnectError):
    pass


def check(ip: IPv4Address | IPv6Address) -> None:
    """`is_private` alone misses CGNAT and NAT64, and 3.12.0's `ipaddress` misreads 6to4, so unwrap explicitly."""
    if isinstance(ip, IPv6Address):
        if ip.scope_id:
            raise Blocked(f"zoned address {ip}")
        inner = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if inner is None and ip in NAT64:
            inner = IPv4Address(int(ip) & 0xFFFFFFFF)
        if inner is not None:
            check(inner)
    if not ip.is_global or ip.is_multicast:
        raise Blocked(f"non-public address {ip}")


async def _resolve(host: str, port: int) -> list[str]:
    try:
        infos = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise httpcore.ConnectError(f"cannot resolve {host}: {exc}") from exc
    return [str(info[4][0]) for info in infos]


class GuardedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, resolve: Resolver | None = None) -> None:
        self._resolve = resolve or _resolve
        self._inner: httpcore.AsyncNetworkBackend = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if port not in (80, 443):
            raise Blocked(f"port {port} is not allowed")
        addresses = [ip_address(a) for a in await self._resolve(host, port)]
        if not addresses:
            raise httpcore.ConnectError(f"no address for {host}")
        for address in addresses:
            check(address)
        return await self._inner.connect_tcp(
            str(addresses[0]), port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None
    ) -> httpcore.AsyncNetworkStream:
        raise Blocked("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class GuardedTransport(httpx.AsyncHTTPTransport):
    def __init__(self, resolve: Resolver | None = None) -> None:
        super().__init__(trust_env=False)
        # ponytail: replaces httpx's private pool to inject the backend; httpx is pinned and a test checks the hook
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(), network_backend=GuardedBackend(resolve)
        )
```

Verify `httpcore.SOCKET_OPTION`, `httpx.create_ssl_context` and the `AsyncHTTPTransport.__init__` signature in the installed httpx 0.28.1/httpcore 1.0.9 (read the installed source); adjust names if they differ, keeping the behaviour.

- [ ] **Step 4: Run the tests** (Task 2 and 3 files) → PASS; ruff, pyright.

- [ ] **Step 5: Commit.** `feat: SSRF-guarded transport for PoliteClient`.

---

### Task 4: Paper tables, `http_cache` and `HttpCache`

**Files:**
- Modify: `backend/src/researchly/db/models.py`
- Create: `backend/src/researchly/db/migrations/versions/0002_papers_and_http_cache.py`, `backend/src/researchly/connectors/cache.py`
- Modify: `backend/tests/db/test_migrations.py` (`TABLES`)
- Test: `backend/tests/connectors/test_cache.py`

**Interfaces:**
- Produces: models `Paper`, `PaperId`, `Author`, `PaperAuthor`, `Venue`, `HttpCacheRow`; `HttpCache(sessions: async_sessionmaker[AsyncSession])` with `async get(key) -> Cached | None`, `async put(key, fetched, ttl)`, `async refresh(key, ttl)`; `Cached(fetched: Fetched, etag: str | None, fresh: bool)`.

Tables (spec §16, plus `title_norm` for trigram matching and `is_retracted` for verification):

| Table | Columns |
|---|---|
| `venue` | `id uuid pk`, `name text not null`, `type text`, `tier text`; unique index on `lower(name)` |
| `paper` | `id uuid pk`, `title text not null`, `title_norm text not null`, `abstract text`, `year int`, `venue_id uuid fk venue`, `doi text`, `license text`, `license_source text`, `oa_url text`, `is_oa bool`, `cited_by int`, `fwci double`, `pct double`, `is_retracted bool`, `field_sources jsonb not null default '{}'`, `updated_at timestamptz not null default now()`; GIN index `ix_paper_title_norm_trgm` on `title_norm gin_trgm_ops`; index on `doi` |
| `paper_ids` | `paper_id uuid fk paper on delete cascade`, `scheme text not null`, `value text not null`; pk `(scheme, value)`; index on `paper_id` |
| `author` | `id uuid pk`, `name text not null`, `orcid text unique`, `openalex_id text unique` |
| `paper_author` | `paper_id uuid fk paper cascade`, `author_id uuid fk author`, `position int`; pk `(paper_id, position)` |
| `http_cache` (UNLOGGED) | `key text pk`, `url text not null`, `status int not null`, `headers jsonb not null`, `body bytea not null`, `etag text`, `expires_at timestamptz not null` |

- [ ] **Step 1: Write the failing tests.** In `test_migrations.py` add the six tables to `TABLES`. Add `backend/tests/connectors/test_cache.py`:

```python
from datetime import timedelta

import pytest
from sqlalchemy import text

from researchly.connectors.cache import HttpCache
from researchly.connectors.http import Fetched
from researchly.db import migrate
from researchly.db.engine import make_engine, make_sessionmaker

pytestmark = [pytest.mark.db, pytest.mark.anyio]


@pytest.fixture
async def cache(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    yield HttpCache(make_sessionmaker(engine)), engine
    await engine.dispose()


async def test_put_get_refresh(cache):
    store, _ = cache
    fetched = Fetched(200, {"etag": 'W/"1"', "content-type": "application/json"}, b'{"a":1}', "https://x/y")
    await store.put("datacite:doi:10.1/a", fetched, timedelta(days=60))
    hit = await store.get("datacite:doi:10.1/a")
    assert hit and hit.fresh and hit.etag == 'W/"1"' and hit.fetched.json() == {"a": 1}
    await store.put("datacite:doi:10.1/a", fetched, timedelta(seconds=-1))  # upsert, already stale
    stale = await store.get("datacite:doi:10.1/a")
    assert stale and not stale.fresh
    await store.refresh("datacite:doi:10.1/a", timedelta(days=1))
    assert (await store.get("datacite:doi:10.1/a")).fresh  # type: ignore[union-attr]
    assert await store.get("missing") is None


async def test_http_cache_is_unlogged(cache):
    _, engine = cache
    async with engine.connect() as conn:
        persistence = await conn.scalar(text("SELECT relpersistence FROM pg_class WHERE relname = 'http_cache'"))
    assert persistence == "u"


async def test_politeclient_serves_fresh_hits_and_revalidates_with_etag(cache):
    import httpx

    from researchly.connectors.http import PoliteClient

    store, _ = cache
    calls = []

    def handler(request):
        calls.append(request.headers.get("if-none-match"))
        if request.headers.get("if-none-match") == 'W/"1"':
            return httpx.Response(304)
        return httpx.Response(200, headers={"etag": 'W/"1"'}, json={"v": 1})

    async with PoliteClient(transport=httpx.MockTransport(handler), cache=store, limits={"default": (0.0, 1)}) as c:
        first = await c.get("https://api.example.org/a", source="t", cache_key="k", ttl=timedelta(days=1))
        again = await c.get("https://api.example.org/a", source="t", cache_key="k", ttl=timedelta(days=1))
        await store.put("k", first, timedelta(seconds=-1))
        revalidated = await c.get("https://api.example.org/a", source="t", cache_key="k", ttl=timedelta(days=1))
    assert calls == [None, 'W/"1"']  # the fresh hit made no request; the stale one sent If-None-Match
    assert again.json() == revalidated.json() == {"v": 1}
```

- [ ] **Step 2: Run to confirm failure** (needs Postgres: Docker running or `RESEARCHLY_TEST_DATABASE_URL`). `uv run --directory backend pytest tests/db tests/connectors/test_cache.py -q`.

- [ ] **Step 3: Implement.**
  - Models in `db/models.py` following the existing style (`Mapped[...]`, `mapped_column`, `UUID`, `JSONB`); `HttpCacheRow.__table_args__ = {"prefixes": ["UNLOGGED"]}`; the trigram index as `Index("ix_paper_title_norm_trgm", "title_norm", postgresql_using="gin", postgresql_ops={"title_norm": "gin_trgm_ops"})`; the venue index `Index("uq_venue_lower_name", func.lower(Venue.name), unique=True)`. `test_models_match_migrations` must pass: if autogenerate compare reports a spurious diff for the functional or GIN index, keep the index in the migration and model both, and confirm with the test.
  - Revision `0002` (`down_revision = "0001"`) creating the tables in the order venue, paper, paper_ids, author, paper_author, http_cache (`prefixes=["UNLOGGED"]`), and dropping them in reverse.
  - `connectors/cache.py`:

```python
"""Postgres-backed response cache keyed by a normalized ID (spec §8.2). UNLOGGED: lost after a crash, by design."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from researchly.connectors.http import Fetched
from researchly.db.models import HttpCacheRow


@dataclass(frozen=True)
class Cached:
    fetched: Fetched
    etag: str | None
    fresh: bool


class HttpCache:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get(self, key: str) -> Cached | None:
        async with self._sessions() as session:
            row = await session.scalar(select(HttpCacheRow).where(HttpCacheRow.key == key))
        if row is None:
            return None
        fetched = Fetched(row.status, dict(row.headers), row.body, row.url)
        return Cached(fetched, row.etag, row.expires_at > datetime.now(UTC))

    async def put(self, key: str, fetched: Fetched, ttl: timedelta) -> None:
        values = {
            "url": fetched.url,
            "status": fetched.status,
            "headers": fetched.headers,
            "body": fetched.body,
            "etag": fetched.headers.get("etag"),
            "expires_at": datetime.now(UTC) + ttl,
        }
        stmt = insert(HttpCacheRow).values(key=key, **values)
        async with self._sessions() as session, session.begin():
            await session.execute(stmt.on_conflict_do_update(index_elements=["key"], set_=values))

    async def refresh(self, key: str, ttl: timedelta) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(HttpCacheRow).where(HttpCacheRow.key == key).values(expires_at=datetime.now(UTC) + ttl)
            )
```

- [ ] **Step 4: Run** `uv run --directory backend pytest -q` (whole suite) → PASS; ruff, pyright.

- [ ] **Step 5: Commit.** `feat: paper tables and the Postgres http_cache`.

---

### Connector tasks (5–10): shared rules

Every connector:
- Lives in `connectors/<name>.py` as `class <Name>Connector` with `name = "<name>"`, `__init__(self, client: PoliteClient)`, the four `Connector` methods, and module-level `BASE = "https://…"`.
- Uses `client.get(..., source=self.name, cache_key=..., ttl=...)`. Cache keys: `f"{name}:{scheme}:{value}"` for singletons (`TTL_METADATA`), `f"{name}:refs:{scheme}:{value}"` for references (`TTL_METADATA`), `f"{name}:count:{…}"` for counts (`TTL_COUNTS`), `f"{name}:search:" + sha256(json.dumps(params, sort_keys=True))` for searches (`TTL_SEARCH`).
- Normalizes every identifier through `ids.*` before putting it in `PaperRecord.ids`; a record carrying an arXiv ID also carries `ExternalId("doi", ids.arxiv_doi(arxiv_id))`; ORCIDs are bare (`0000-…`).
- Returns `[]` for a 404 or an empty result; raises `SourceUnavailable(self.name, …)` for other non-2xx answers; returns `[]` from `citations()` when the capability is absent.
- `get()` accepts mixed schemes and silently skips ones the source cannot look up.
- Has tests in `backend/tests/connectors/test_<name>.py`:
  - **cassette tests** (`@pytest.mark.vcr`, `@pytest.mark.anyio`) for `search` (query `"attention is all you need"` or the source's natural equivalent), `get` by the probe IDs from R-conn (arXiv `1706.03762`, DOI `10.1038/nature14539`, PMID `26017442`), each citation direction the source supports, and one not-found case. Each asserts concrete field values from R-conn (e.g. title contains "Attention", year 2017, the expected IDs present, authors non-empty).
  - **pure unit tests** on parsing edge cases using small inline payloads (no cassette).
  - The client in cassette tests is `PoliteClient(limits={"default": (0.0, 1)})` so replay is instant.
- Records cassettes once, live and keyless: `uv run --directory backend pytest tests/connectors/test_<name>.py --record-mode=once -q`, then re-runs without the flag (record mode `none`). Before committing, grep the cassettes for `mailto`, `cookie`, `authorization` and any email address: there must be none. Keep cassettes small (`limit` 3–5; `select=` where the API supports it).
- Commits `feat: <name> connector`.

### Task 5: OpenAlex connector and budget guard

**Files:** `connectors/openalex.py`; `tests/connectors/test_openalex.py`.

**Interfaces:** `OpenAlexConnector(client)`; `budget_low: bool` property; constants `SEARCHES_PER_DAY = 50`, `LOW_BUDGET = 0.2`, `CITERS_CAP = 1000`.

Behaviour (R-conn §1):
- `search`: `GET /works` with `search.title_and_abstract=<text>`, `per-page=min(limit, 100)`, `select=` the fields of the field map, `filter=publication_year:<from>-<to>` when a year bound is set (open bounds: `>`/`<` forms, e.g. `publication_year:>2014`). Paid: guarded.
- `get`: one ID → singleton (`/works/doi:<doi>`, `/works/pmid:<pmid>`, `/works/<W…>`; arXiv → its DataCite DOI) at $0; several IDs → one `filter=doi:a|b|…` (≤100 per call; same for `pmid:`, `openalex:`) unless `budget_low`, then singletons.
- `citations(id, "out")`: the singleton's `referenced_works` → `ExternalId("openalex", W…)`. `citations(id, "in")`: `filter=cites:<W…>&per-page=200&select=id,doi`, pages until `CITERS_CAP`; paid, guarded. Non-OpenAlex IDs are resolved to a W-ID via a singleton first.
- Parsing: rebuild the abstract from `abstract_inverted_index`; `pct` from `citation_normalized_percentile.value`; `oa_url` from `best_oa_location.pdf_url` or `open_access.oa_url`; `license` from `best_oa_location.license` or `primary_location.license`; venue from `primary_location.source.display_name` and `.type`; arXiv ID from any `locations[].landing_page_url` matching `arxiv.org/abs/`; ids also from `ids.pmid`, `ids.pmcid`, `id`; authors from `authorships` (ORCID URL stripped, OpenAlex author ID stripped to `A…`); `extra["topics"]` = `[t["display_name"] for t in topics]`; `references` from `referenced_works`.
- Budget guard: after every response read `x-ratelimit-remaining-usd` and `x-ratelimit-limit-usd`; `budget_low` when remaining/limit ≤ `LOW_BUDGET`. A paid call raises `QuotaExhausted(self.name, "OpenAlex daily budget low")` when `budget_low`, or when this process made `SEARCHES_PER_DAY` searches in the current UTC day (`# ponytail: in-memory counter, resets on restart; the server-side budget is the hard cap`).

Unit tests (inline payloads): abstract rebuild (`{"b": [1], "a": [0]}` → `"a b"`); budget guard flips at 20% (`remaining 0.02 / limit 0.1`) and blocks search but not a singleton (use `httpx.MockTransport`); the 51st search in a day raises; a 404 HTML body returns `[]`.

### Task 6: arXiv connector

**Files:** `connectors/arxiv.py`; `tests/connectors/test_arxiv.py`.

Behaviour (R-conn §2, §11):
- `search`: `GET https://export.arxiv.org/api/query` with `search_query` built as `" AND ".join(f"all:{w}" for w in words)` (words = alphanumeric tokens of the text), plus ` AND submittedDate:[<from>01010000 TO <to>12312359]` when year bounds are set (missing bound → `1900`/`2100`), `sortBy=relevance`, `max_results=limit`.
- `get`: arXiv IDs only (and `doi` values of the form `10.48550/arxiv.<id>`, mapped back); `id_list` comma-joined, ≤ 50 per call.
- `citations`: none (`[]`).
- Parse with `defusedxml.ElementTree.fromstring`; namespaces Atom `http://www.w3.org/2005/Atom`, `arxiv` `http://arxiv.org/schemas/atom`. An `<entry>` whose `<id>` contains `/api/errors` raises `SourceUnavailable` with the `<summary>` text. Title and abstract whitespace collapsed; year from `published`; authors from `author/name`; `venue` from `arxiv:journal_ref`; ids: arXiv (versionless), its DataCite DOI, and `arxiv:doi` when present; `oa_url` = the `link[@title='pdf']` href; `is_oa=True`; `extra["categories"]`.

Unit tests: an error entry raises; whitespace collapsing; an XML bomb payload (`<!DOCTYPE x [<!ENTITY a "aaaa"> …]>`) raises `defusedxml` `EntitiesForbidden` (or is wrapped as `SourceUnavailable`); the search query builder.

### Task 7: Crossref connector

**Files:** `connectors/crossref.py`; `tests/connectors/test_crossref.py`.

Behaviour (R-conn §3):
- `search`: `GET https://api.crossref.org/works` with `query.bibliographic`, `rows=limit`, `select=DOI,title,abstract,issued,author,container-title,type,is-referenced-by-count,license,updated-by,link`, `filter=from-pub-date:<y>,until-pub-date:<y>` when bounded; `bucket="api.crossref.org/list"`.
- `get`: DOIs only; singletons `GET /works/<doi>` (single pool).
- `citations(id, "out")`: the singleton's `reference[].DOI` (DOIs only); `"in"`: unsupported (`[]`; counts only).
- Parsing: `title[0]`; abstract JATS → plain text by removing tags (`re.sub(r"<[^>]+>", " ", …)` then collapse whitespace); year from `issued.date-parts[0][0]`; authors from `given`+`family` (or `name`), ORCID URL stripped; venue `container-title[0]`; `license` = first `license[].URL`; `is_retracted` = any `updated-by[].type == "retraction"`; `cited_by` = `is-referenced-by-count`.
- Unit tests: JATS stripping; retraction detection from an inline `updated-by` payload; a 404 (empty `text/plain`) returns `[]`.

### Task 8: OpenCitations and DataCite connectors

**Files:** `connectors/opencitations.py`, `connectors/datacite.py`; tests for each.

OpenCitations (R-conn §4): `name = "opencitations"`; no search, no get (`capabilities` says so; both return `[]`).
- `citations(id, direction)` accepts `doi` and `pmid` only. First `GET /index/v2/citation-count/<scheme>:<value>` (or `reference-count` for `"out"`), cached with `TTL_COUNTS`; when the count exceeds `MAX_ROWS = 5000`, raise `TooManyResults(self.name, f"{count} citations")`. Otherwise `GET /index/v2/citations/…` (or `references/…`) with `max_bytes=20_000_000`, and parse each row's `citing` (or `cited`) multi-ID string (`"omid:br/… pmid:… doi:… openalex:W…"`) into normalized `ExternalId`s, one per row: prefer DOI, then PMID, then OpenAlex.
- Unit test: the multi-ID string parser; `TooManyResults` above the threshold (MockTransport).

DataCite (R-conn §5): `name = "datacite"`; `get` only (DOIs with prefix `10.48550/` and other DataCite DOIs; arXiv IDs mapped to their DataCite DOI); singleton `GET https://api.datacite.org/dois/<doi>`, cached with `TTL_METADATA` (ETag revalidation comes from `PoliteClient`). Parse `data.attributes`: `titles[0].title`, `descriptions[descriptionType=Abstract].description`, `publicationYear`, `creators[].name` (ORCID from `nameIdentifiers[nameIdentifierScheme=ORCID]`), `identifiers[identifierType=arXiv]`, `rightsList[0].rightsUri` as license, `publisher` as venue. Search and citations are `[]`.

### Task 9: Europe PMC and PubMed connectors

**Files:** `connectors/europepmc.py`, `connectors/pubmed.py`; tests for each.

Europe PMC (R-conn §6): `name = "europepmc"`; `BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"`.
- `search`: `GET /search` with `query=<text>` (+ ` AND PUB_YEAR:[<from> TO <to>]`), `format=json`, `resultType=core`, `pageSize=limit`.
- `get`: one OR query per ≤25 IDs (`DOI:"…"`, `EXT_ID:<pmid> AND SRC:MED`, `PMCID:…`).
- `citations`: needs a PMID; `"in"` → `GET /MED/<pmid>/citations?format=json&pageSize=1000` (one page, `# ponytail: first 1000 citers`), `"out"` → `/MED/<pmid>/references?format=json&pageSize=1000`; rows with `source == "MED"` give PMIDs.
- Parse: `title` (trailing period stripped), `abstractText` with tags removed, `pubYear`, `authorList.author[]` (`fullName`, ORCID from `authorId`), `journalInfo.journal.title`, ids `pmid`/`pmcid`/`doi`, `citedByCount`, `isOpenAccess == "Y"`, `license`, `is_retracted` when `pubTypeList.pubType` contains `"Retracted Publication"`.

PubMed (R-conn §7, §11): `name = "pubmed"`; `BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"`.
- `search`: `esearch.fcgi?db=pubmed&term=<text>&retmode=json&retmax=<limit>&sort=relevance` (+ `datetype=pdat&mindate=<from>&maxdate=<to>`), then `efetch` for the IDs.
- `get`: PMIDs only; `efetch.fcgi?db=pubmed&id=<comma list ≤200>&retmode=xml`.
- `citations`: `elink.fcgi?dbfrom=pubmed&db=pubmed&id=<pmid>&linkname=pubmed_pubmed_citedin|pubmed_pubmed_refs&retmode=json`.
- Parse efetch XML with `defusedxml.ElementTree`; follow R-conn §7's table exactly, including: titles and abstracts via `"".join(el.itertext())`, labelled `AbstractText` sections joined, DOI/PMCID **only** from `PubmedData/ArticleIdList/ArticleId` (never `.//ArticleId`), `CollectiveName` authors, ORCID from `Identifier[@Source='ORCID']`, year from `PubDate/Year` or the first 4 digits of `MedlineDate`, retraction from `PublicationType == "Retracted Publication"`.
- An HTTP 400 `<ERROR>` body returns `[]`.
- Unit test: a reference-list `ArticleId` must not leak into the record's ids (inline XML with a `ReferenceList`).

### Task 10: Hugging Face Papers connector and the registry

**Files:** `connectors/hf.py`, `connectors/__init__.py`; `tests/connectors/test_hf.py`, `tests/connectors/test_registry.py`.

HF (R-conn §8): `name = "hf"`.
- `search`: `GET https://huggingface.co/api/papers/search?q=<text>&limit=<limit>` with `bucket="huggingface.co/search"`; rows are `{paper: {...}}`.
- `get`: arXiv IDs only; `GET /api/papers/<id>` (404 is normal: not every paper is on HF).
- `citations`: none.
- Parse: `id` → arXiv ID (+ DataCite DOI), `title`, `summary`, `publishedAt` year, `authors[].name`; `extra` = `upvotes`, `githubRepo`, `githubStars`, `ai_keywords` when present. Decode with `Fetched.json()` (bytes → UTF-8; emoji-safe on Windows).

Registry:

```python
"""Connector registry (spec §8.1): every keyless Tier-1 source."""

from researchly.connectors.arxiv import ArxivConnector
from researchly.connectors.base import Connector
from researchly.connectors.crossref import CrossrefConnector
from researchly.connectors.datacite import DataCiteConnector
from researchly.connectors.europepmc import EuropePmcConnector
from researchly.connectors.hf import HfConnector
from researchly.connectors.http import PoliteClient
from researchly.connectors.openalex import OpenAlexConnector
from researchly.connectors.opencitations import OpenCitationsConnector
from researchly.connectors.pubmed import PubMedConnector


def build_connectors(client: PoliteClient) -> dict[str, Connector]:
    connectors: list[Connector] = [
        OpenAlexConnector(client),
        ArxivConnector(client),
        CrossrefConnector(client),
        OpenCitationsConnector(client),
        DataCiteConnector(client),
        EuropePmcConnector(client),
        PubMedConnector(client),
        HfConnector(client),
    ]
    return {c.name: c for c in connectors}
```

`test_registry.py`: the eight names; each object satisfies the protocol (`isinstance` against a `runtime_checkable` copy is not needed — assert `callable(getattr(c, m))` for the four methods); `capabilities()` matches the table: search for openalex/arxiv/crossref/europepmc/pubmed/hf; citations in for openalex/opencitations/europepmc/pubmed; citations out for openalex/crossref/opencitations/europepmc/pubmed.

---

### Task 11: Identity resolution (in memory) and the paper store

**Files:**
- Create: `backend/src/researchly/services/__init__.py`, `services/identity.py`, `services/paper_store.py`
- Test: `backend/tests/services/__init__.py`, `tests/services/test_identity.py`, `tests/services/test_paper_store.py`

**Interfaces:**
- Produces: `normalize_title(str) -> str`; `trigram_similarity(a, b) -> float` (pg_trgm-compatible); `first_surname(record) -> str | None`; `same_paper(a, b) -> bool`; `merge(records: list[PaperRecord]) -> PaperRecord`; `dedupe(records) -> list[PaperRecord]`; `SOURCE_PRIORITY`; `async upsert(session: AsyncSession, record: PaperRecord) -> uuid.UUID`.

- [ ] **Step 1: Write the failing tests** `test_identity.py`:

```python
from researchly.connectors.base import Author, ExternalId, PaperRecord
from researchly.services.identity import dedupe, merge, normalize_title, same_paper, trigram_similarity


def rec(source, title, *, ids=(), year=2017, author="Ashish Vaswani", **kw):
    return PaperRecord(source=source, ids=list(ids), title=title, year=year, authors=[Author(author)], **kw)


def test_normalize_title_folds_case_accents_and_punctuation():
    assert normalize_title("Attention Is All You Need!") == "attention is all you need"
    assert normalize_title("Réseaux  de neurones—profonds") == "reseaux de neurones profonds"


def test_trigram_similarity_matches_pg_trgm():
    assert trigram_similarity("word", "word") == 1.0
    assert round(trigram_similarity("word", "two words"), 6) == 0.363636  # SELECT similarity('word', 'two words')
    assert trigram_similarity("", "x") == 0.0


def test_shared_identifier_means_same_paper():
    a = rec("arxiv", "Attention", ids=[ExternalId("doi", "10.48550/arxiv.1706.03762")])
    b = rec("openalex", "Something else", ids=[ExternalId("doi", "10.48550/arxiv.1706.03762")], year=1990)
    assert same_paper(a, b)


def test_fuzzy_match_needs_title_year_and_first_author():
    a = rec("arxiv", "Attention Is All You Need")
    assert same_paper(a, rec("crossref", "Attention is all you need.", year=2018))
    assert not same_paper(a, rec("crossref", "Attention is all you need", year=2019))
    assert not same_paper(a, rec("crossref", "Attention is all you need", author="Noam Shazeer"))
    assert not same_paper(a, rec("crossref", "Attention is not all you need"))


def test_surname_handles_comma_form():
    a = rec("pubmed", "Deep learning", author="LeCun, Yann", year=2015)
    b = rec("crossref", "Deep learning", author="Yann LeCun", year=2015)
    assert same_paper(a, b)


def test_merge_takes_fields_by_source_priority_and_records_provenance():
    arxiv = rec("arxiv", "Attention Is All You Need", ids=[ExternalId("arxiv", "1706.03762")], abstract="arXiv text")
    openalex = rec(
        "openalex", "Attention is All you Need", ids=[ExternalId("openalex", "W2626778328")], cited_by=120404
    )
    merged = merge([arxiv, openalex])
    assert merged.title == "Attention is All you Need"  # openalex outranks arxiv
    assert merged.abstract == "arXiv text"  # only arxiv had one
    assert merged.cited_by == 120404
    assert merged.field_sources["title"] == "openalex" and merged.field_sources["abstract"] == "arxiv"
    assert {i.scheme for i in merged.ids} == {"arxiv", "openalex"}


def test_dedupe_clusters_transitively():
    a = rec("arxiv", "Attention Is All You Need", ids=[ExternalId("arxiv", "1706.03762")])
    b = rec("hf", "Attention is all you need", ids=[ExternalId("arxiv", "1706.03762")])
    c = rec("crossref", "Deep learning", author="LeCun, Yann", year=2015)
    assert len(dedupe([a, b, c])) == 2
```

`test_paper_store.py` (`@pytest.mark.db`, `anyio`; migrate the per-test DB, open an `AsyncSession`):
- upserting a new record creates `paper`, `paper_ids` (normalized), `author`, `paper_author` and `venue` rows and returns the id;
- upserting a record with a shared ID returns the same id and adds new IDs (e.g. a second OpenAlex W-ID is kept, R-conn §13 item 11);
- a record with no shared ID but the same normalized title, year ± 1 and first-author surname returns the same id;
- a lower-priority source does not overwrite a field set by a higher-priority one, but fills a null field; `field_sources` and `license_source` reflect it;
- an author with the same ORCID on two papers is one `author` row.

- [ ] **Step 2: Run to confirm failure.**

- [ ] **Step 3: Implement** `services/identity.py`:

```python
"""Identity resolution (spec §8.3): exact IDs first, then title trigram ≥ 0.92 + year ± 1 + first-author surname."""

import re
import unicodedata
from dataclasses import fields, replace

from researchly.connectors import ids as idn
from researchly.connectors.base import ExternalId, PaperRecord

TITLE_SIMILARITY = 0.92
# spec §8.1.1: OpenAlex enriches first, then Crossref; arXiv and HF have the thinnest metadata
SOURCE_PRIORITY = ("openalex", "crossref", "pubmed", "europepmc", "datacite", "arxiv", "hf", "opencitations")
MERGED_FIELDS = ("title", "abstract", "year", "authors", "venue", "venue_type", "cited_by", "fwci", "pct", "oa_url",
                 "is_oa", "license", "is_retracted")


def normalize_title(title: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_.lower()).split())


def _trigrams(text: str) -> set[str]:
    grams: set[str] = set()
    for word in re.findall(r"[a-z0-9]+", text.lower()):  # pg_trgm: words padded with two spaces before, one after
        padded = f"  {word} "
        grams.update(padded[i : i + 3] for i in range(len(padded) - 2))
    return grams


def trigram_similarity(a: str, b: str) -> float:
    ga, gb = _trigrams(a), _trigrams(b)
    return len(ga & gb) / len(ga | gb) if ga and gb else 0.0


def first_surname(record: PaperRecord) -> str | None:
    if not record.authors:
        return None
    name = record.authors[0].name.strip()
    surname = name.split(",")[0] if "," in name else name.split()[-1] if name.split() else ""
    return normalize_title(surname) or None


def _ids(record: PaperRecord) -> set[ExternalId]:
    return {n for i in record.ids if (n := idn.normalize(i))}


def same_paper(a: PaperRecord, b: PaperRecord) -> bool:
    if _ids(a) & _ids(b):
        return True
    if a.year is None or b.year is None or abs(a.year - b.year) > 1:
        return False
    sa, sb = first_surname(a), first_surname(b)
    if not sa or sa != sb:
        return False
    return trigram_similarity(normalize_title(a.title), normalize_title(b.title)) >= TITLE_SIMILARITY


def _rank(source: str) -> int:
    return SOURCE_PRIORITY.index(source) if source in SOURCE_PRIORITY else len(SOURCE_PRIORITY)


def merge(records: list[PaperRecord]) -> PaperRecord:
    ordered = sorted(records, key=lambda r: _rank(r.source))
    merged = replace(ordered[0], ids=[], references=[], extra={}, field_sources={})
    for name in MERGED_FIELDS:
        for record in ordered:
            value = getattr(record, name)
            if value not in (None, [], ""):
                setattr(merged, name, value)
                merged.field_sources[name] = record.source
                break
    seen_ids: set[ExternalId] = set()
    for record in ordered:
        merged.ids += [i for i in _ids(record) if i not in seen_ids and not seen_ids.add(i)]
        merged.references += [r for r in record.references if r not in merged.references]
        merged.extra = {**record.extra, **merged.extra}  # the higher-priority source wins on clashes
    merged.source = ordered[0].source
    return merged


def dedupe(records: list[PaperRecord]) -> list[PaperRecord]:
    """# ponytail: O(n²) clustering; fine for one fan-out (a few hundred records)."""
    clusters: list[list[PaperRecord]] = []
    for record in records:
        hits = [c for c in clusters if any(same_paper(record, other) for other in c)]
        joined = [record] + [r for c in hits for r in c]
        clusters = [c for c in clusters if c not in hits] + [joined]
    return [merge(c) for c in clusters]
```

(`fields` import is unused if not needed; remove it. The walrus-in-comprehension for `seen_ids` may be rewritten as a plain loop for readability; behaviour must match the tests.)

`services/paper_store.py` — `async def upsert(session, record) -> uuid.UUID`:
1. `found = select(PaperId.paper_id).where(tuple_(PaperId.scheme, PaperId.value).in_(normalized ids))` → first match (`# ponytail: two stored papers sharing an ID are not merged here; a later merge job can`).
2. Otherwise fuzzy: `SET LOCAL pg_trgm.similarity_threshold = 0.92`; `select Paper where Paper.title_norm.op("%")(normalize_title(record.title)) and year between y-1 and y+1`, then keep the first candidate whose first author (via `paper_author` position 0 → `author.name`) has the same `first_surname`.
3. Otherwise insert a new `Paper`.
4. For each field in `MERGED_FIELDS` except `authors`: set it when the stored value is null, or when `_rank(record.source) < _rank(stored field_sources[field])`; update `field_sources`; `license_source = field_sources.get("license")`; `doi` column = the record's DOI that is not an arXiv DataCite DOI, else the DataCite one; `title_norm` follows `title`; `updated_at = now()`.
5. Venue: get-or-create by `lower(name)` (`INSERT … ON CONFLICT DO NOTHING` then select).
6. `paper_ids`: `INSERT … ON CONFLICT DO NOTHING` for every normalized ID.
7. Authors: only when the paper has no `paper_author` rows yet; each author matched by ORCID, then OpenAlex ID, else a new row; `paper_author(position=i)`.
8. `await session.flush()`; return the id. The caller commits.

Import `_rank` and `MERGED_FIELDS` from `identity` (rename `_rank` to `source_rank` and export it).

- [ ] **Step 4: Run** `uv run --directory backend pytest tests/services -q` → PASS (db tests need Postgres); ruff, pyright.

- [ ] **Step 5: Commit.** `feat: identity resolution and the paper store`.

---

### Task 12: Federated search, nightly live checks

**Files:**
- Create: `backend/src/researchly/services/search.py`, `backend/tests/services/test_search.py`, `backend/tests/connectors/test_live.py`, `.github/workflows/nightly.yml`

**Interfaces:**
- Produces: `SearchOutcome(papers: list[PaperRecord], errors: dict[str, str])`; `default_sources(connectors: Mapping[str, Connector], domains: frozenset[str] = frozenset()) -> list[Connector]`; `async federated_search(query: SearchQuery, connectors: Sequence[Connector]) -> SearchOutcome`.

- [ ] **Step 1: Write the failing tests** `test_search.py` with fake connectors (plain classes returning canned records or raising):

```python
import pytest

from researchly.connectors.base import Author, ExternalId, PaperRecord, QuotaExhausted, SearchQuery
from researchly.services.search import default_sources, federated_search

pytestmark = pytest.mark.anyio


class Fake:
    def __init__(self, name, result):
        self.name, self._result = name, result

    async def search(self, query):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def paper(source, arxiv_id):
    return PaperRecord(source, [ExternalId("arxiv", arxiv_id)], "Attention Is All You Need", year=2017,
                       authors=[Author("Ashish Vaswani")])


async def test_results_are_deduped_and_quota_errors_reported():
    outcome = await federated_search(
        SearchQuery("attention"),
        [Fake("arxiv", [paper("arxiv", "1706.03762")]), Fake("hf", [paper("hf", "1706.03762")]),
         Fake("openalex", QuotaExhausted("openalex", "OpenAlex daily budget low"))],
    )
    assert len(outcome.papers) == 1
    assert outcome.errors == {"openalex": "openalex: OpenAlex daily budget low"}


async def test_unexpected_errors_propagate():
    with pytest.raises(KeyError):
        await federated_search(SearchQuery("x"), [Fake("arxiv", KeyError("bug"))])


def test_default_sources_follow_the_routing_rules():
    names = {n: object() for n in ("arxiv", "crossref", "openalex", "europepmc", "pubmed", "hf", "datacite")}
    pick = lambda d: [n for n, c in names.items() if c in default_sources(names, frozenset(d))]  # noqa: E731
    assert pick([]) == ["arxiv", "crossref", "openalex"]
    assert pick(["biomedical"]) == ["arxiv", "crossref", "openalex", "europepmc", "pubmed"]
    assert pick(["ml"]) == ["arxiv", "crossref", "openalex", "hf"]
```

- [ ] **Step 2: Implement** `services/search.py`:

```python
"""Search fan-out (spec §8.1.1): query the chosen sources concurrently, then dedupe locally."""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from researchly.connectors.base import Connector, ConnectorError, PaperRecord, SearchQuery
from researchly.services.identity import dedupe

ALWAYS = ("arxiv", "crossref", "openalex")
BY_DOMAIN = {"biomedical": ("europepmc", "pubmed"), "ml": ("hf",)}


@dataclass
class SearchOutcome:
    papers: list[PaperRecord]
    errors: dict[str, str] = field(default_factory=dict)  # source -> why it gave nothing (quota, outage)


def default_sources(connectors: Mapping[str, Connector], domains: frozenset[str] = frozenset()) -> list[Connector]:
    names = list(ALWAYS) + [n for d in sorted(domains) for n in BY_DOMAIN.get(d, ())]
    return [connectors[n] for n in names if n in connectors]


async def federated_search(query: SearchQuery, connectors: Sequence[Connector]) -> SearchOutcome:
    results = await asyncio.gather(*(c.search(query) for c in connectors), return_exceptions=True)
    papers: list[PaperRecord] = []
    errors: dict[str, str] = {}
    for connector, result in zip(connectors, results, strict=True):
        if isinstance(result, ConnectorError):
            errors[connector.name] = str(result)
        elif isinstance(result, BaseException):
            raise result
        else:
            papers += result
    return SearchOutcome(dedupe(papers), errors)
```

- [ ] **Step 3: Nightly live checks.** `backend/tests/connectors/test_live.py`, `pytestmark = [pytest.mark.nightly, pytest.mark.anyio]`, a real `PoliteClient()` (default limits). One parametrized test per searchable connector: `search(SearchQuery("attention is all you need", limit=3))` returns ≥1 record with a non-empty title; one test per `get`-capable connector with the R-conn probe ID returns one record; OpenCitations `citations(ExternalId("doi", "10.1038/nature14539"), "out")` returns ≥10 IDs. `.github/workflows/nightly.yml`: `on: schedule: - cron: "17 3 * * *"` and `workflow_dispatch`; one ubuntu-24.04 job: checkout, `astral-sh/setup-uv` (same pinned version as `ci.yml`), `uv sync --locked` in `backend`, `uv run pytest -m nightly -q`. Copy the action pins from `ci.yml`.

- [ ] **Step 4: Run** `uv run --directory backend pytest -q` → PASS; `uv run --directory backend pytest -m nightly -q` once locally → PASS (real network, keyless); ruff, pyright.

- [ ] **Step 5: Commit.** `feat: federated search and nightly live connector checks`.

---

### Task 13 (controller): docs

- [ ] `docs/adr/0008-scholarly-http.md`: the R-http recommended stack (custom limiter/retry/breaker, custom UNLOGGED cache, guarded httpcore backend, vcrpy), the risks (private `_pool`, in-process limits, vcrpy churn), and the CI evidence.
- [ ] Spec edits (§8.1 DataCite limit; §8.1.1 OpenAlex `search.title_and_abstract`, arXiv DOI derived locally, OpenCitations count-first cap; §8.2 limits table rows for DataCite and HF, ETag only where sent, 304 handling; §16 `title_norm`, `is_retracted`; `citation` table at step 5).
- [ ] `.claude/skills/add-scholarly-connector/SKILL.md`: the "Connector tasks: shared rules" section above as a checklist.
- [ ] `AGENTS.md`: connectors gotchas (record cassettes with `--record-mode=once`, keyless, grep for emails; limits in `limits.yaml`; local sidecars such as SearXNG and GROBID are the only HTTP outside `PoliteClient`, through a loopback-only client, from step 2b/2c).
- [ ] Plan Deferred list below; `docs/progress.md` entry.

## Deferred (recorded, not built here)

- robots.txt (Protego), full-text fetch, content-type allowlist, arXiv `Crawl-delay: 15` on arxiv.org: step 2b.
- `researchly backup` passes `--no-unlogged-table-data`: step 9.
- `citation` table and edge storage; author disambiguation: step 5.
- Query domain classifier (LLM): step 3.
- Merging two stored papers that turn out to share an ID: a maintenance job when it shows up in practice.
- `--block-network` in pytest once pytest-recording 0.14.1 clears the release-age gate (after 2026-11-09).
- User's own `mailto` setting in Settings → Sources: step 9 (PoliteClient already accepts it).
- Added during execution:
  - Pruning expired `http_cache` rows: step 9 maintenance.
  - `paper_author.author_id` index, author N+1 batching in `upsert`, race-safe author inserts, upgrading authors after the first write (e.g. ORCIDs from a later OpenAlex record): step 5 author work.
  - Venue type by source priority (today the first source that supplies one wins): step 5.
  - A 7-day path for OpenAlex `cited_by` (today it sits in the 60-day singleton cache): step 5 graph work.
  - Per-hop pacing for cross-host redirects (hops are paced by the first host): step 2b, with full-text fetches.
  - arXiv search tuning: AND of `all:` words today; a title-phrase pass for known-title lookups if federated search misses them (OpenAlex and Crossref cover them now).
  - Non-Latin fuzzy matching on a C-locale Postgres: pg_trgm may return no candidates, so a duplicate row is possible (never a false merge); revisit if native pg0 runs with a C locale.
