# Step 2b: Full-Text Ingestion and Hybrid Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a stored paper into sections, ~350-token chunks and local vectors, and search papers and chunks with BM25 + dense retrieval fused by RRF, with a cross-encoder rerank hook.

**Architecture:**
- **Fetching:** `FullTextFetcher` tries arXiv HTML, then Europe PMC JATS, then a PDF (Docling). Every fetch goes through `PoliteClient`, which gains a content-type gate, per-hop pacing and a robots.txt check.
- **Parsing:** each parser yields `FullText(sections)`.
- **Ingestion:** `ingest_paper` splits sections with semchunk using the Qwen3 tokenizer, embeds the chunks with the existing `Embedder`, and stores them. The caller commits; step 3 wraps it in a procrastinate job.
- **Search:** `services/retrieval.py` runs one SQL statement per query. The lexical side uses pg_textsearch BM25 (Docker) or a tsvector fallback (native). The dense side uses pgvector HNSW on `halfvec(1024)`. The two lists are fused with RRF k = 60.

**Tech Stack:**
- Python 3.12, SQLAlchemy 2 async on psycopg 3, Alembic.
- pgvector 0.8.x with `pgvector==0.5.1` (Python).
- pg_textsearch 1.5.1 (Docker image only).
- `protego==0.7.0`, `semchunk==4.1.1`, lxml (HTML), defusedxml (XML).
- fastembed 0.9.1 (Qwen3-Embedding-0.6B-Q, bge-reranker-v2-m3-int8), docling-slim (PDF).

**Spec:** `docs/superpowers/specs/2026-10-28-researchly-design.md` (§9.1, §9.2, §9.3, §16, §19.1, §19.3).
- Research: `docs/research/2026-11-04-step2b-fulltext.md` (**R-ft §N**) and `docs/research/2026-11-04-step2b-search.md` (**R-search §N**).
- ADRs: 0005 (Docling), 0007 (embedder and reranker), 0008 (scholarly HTTP).

## Global Constraints

- **HTTP:**
  - All outbound HTTP goes through `PoliteClient` (`backend/src/researchly/connectors/http.py`). Only `connectors/http.py` and `connectors/ssrf.py` import `httpx` or `httpcore` (tests excepted).
  - Keyless: no key, account or email anywhere. Never put a `mailto` in tests or cassettes.
- **Untrusted input:**
  - Parse XML only with `defusedxml.ElementTree`. Parse HTML with `lxml.html` (no network access, no external entities).
  - Parsed text is untrusted (spec §19.1). It is stored and embedded, and never sent to a planner or writer prompt in this step.
- **Caps and gates** (spec §19.3):
  - Body caps: 50 MB for a PDF, 5 MB for HTML and XML.
  - Allowed content types: `text/html` for arXiv HTML; `application/xml` and `text/xml` for JATS; `application/pdf` and `application/octet-stream` for PDFs. A PDF must also contain `%PDF-` in its first 1,024 bytes.
- **robots.txt:** crawled URLs (arxiv.org HTML and PDF, `oa_url` PDFs) check robots.txt with Protego under the product token `Researchly`. A Crawl-delay above 60 s means "skip this site". API endpoints (Europe PMC REST) are not crawled and skip the check.
- **Limits:** `arxiv.org: [15.0, 1]` (robots Crawl-delay 15, R-ft §1). `export.arxiv.org` stays `[3.0, 1]`.
- **Embeddings:** 1024 dims, `halfvec(1024)`, HNSW with `halfvec_cosine_ops` (m = 16, ef_construction = 64). Queries use `hnsw.iterative_scan = relaxed_order` and `hnsw.ef_search = 100`. Rows carry `model_id`.
- **BM25:**
  - The engine is read from `capability.bm25_engine` (`'pg_textsearch'` or `'tsvector'`, set by migration 0001).
  - Both engines use the `'english'` text config.
  - Engine-specific database objects have `bm25` in their names (`ix_paper_bm25`, `ix_chunk_bm25`, column `bm25_tsv`). They live in the migration only, not in `models.py`.
- **Fusion and chunking:** RRF k = 60. Chunks are made by semchunk with `chunk_size=512` and `overlap=0.15`, which gives a mean of about 350 tokens (R-ft §6). Tokens are counted with the Qwen3 tokenizer, without truncation.
- **No torch:** never add a dependency that pulls torch (`backend/tests/test_lock.py` guards it).
- **Dependencies:** pin new ones exactly: `pgvector==0.5.1`, `semchunk==4.1.1`, `protego==0.7.0`, `lxml==6.1.3` (already locked through docling, now direct). Every dependency change runs `uv lock --directory backend` and `uv lock --directory cli`.
- **Tests:**
  - DB tests are marked `pytest.mark.db` and run under `anyio` with a fresh database per test (`db_url`).
  - HTTP tests replay vcrpy cassettes (record mode `none`) or use `httpx.MockTransport`.
  - Alembic: never edit 0001 or 0002. Add 0003.
- **Verify** with `uv run --directory backend pytest -q`, `pytest -m db -q`, `ruff check`, `ruff format --check` and `pyright` (0 errors).
- **Commits:** Conventional Commits, ending with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Rulings made while planning

1. **LaTeX source tier dropped:** arxiv.org robots.txt disallows `/e-print` and `/src` (R-ft §2). The tiers are arXiv HTML, then JATS, then PDF.
2. **Europe PMC JATS added as a tier before PDF:** it gives clean sections and a licence, while the PDF path is lossy (R-ft §3, ADR 0005).
3. **SPECTER2 deferred to step 5:** nothing in 2b uses paper-to-paper similarity. Correct SPECTER2 needs base plus the proximity adapter, and the only public ONNX export is untrustworthy (R-search §1). The export needs hosting under the project's control, which is a decision for step 5.
4. **Only sections and chunks are stored:** `paper_figure`, `paper_table`, `paper_equation`, `paper_refs` and raw PDF storage arrive with the step 4 reader. Figure and table captions and equation LaTeX are kept as text inside their section.
5. **PDF section paths are flat** (one heading each): Docling's Markdown heading levels are not reliable.
6. **Ingestion is an async service:** Docling and embedding run in `asyncio.to_thread`. The procrastinate job and progress events are step 3 (the run manager).
7. **Chunk size:** `chunk_size=512` with semchunk's overlap gives a mean of about 330 tokens and a maximum of about 400 (research measured a mean of 223 at 350). That fits the spec's "~350" and stays inside the reranker's 512-token pair window with a short query.
8. **Chunks are embedded with their section path prepended** (`"3 Model Architecture > 3.2 Attention\n<text>"`). This is cheap context; contextual-retrieval prefixes stay opt-in for step 4.

---

### Task 1: PoliteClient for full text (content-type gate, per-hop pacing, crawl delay)

**Files:**
- Modify: `backend/src/researchly/connectors/base.py` (new `UnwantedContent`)
- Modify: `backend/src/researchly/connectors/http.py` (`get`/`_send` `accept`, hop pacing, `set_crawl_delay`, `_limit_key`)
- Modify: `backend/src/researchly/connectors/limits.yaml` (add `arxiv.org`)
- Test: `backend/tests/connectors/test_http.py`

**Interfaces:**
- Produces:
  - `class UnwantedContent(ConnectorError)` in `connectors/base.py`.
  - `PoliteClient.get(url, *, source, params=None, bucket=None, cache_key=None, ttl=None, max_bytes=5_000_000, accept: frozenset[str] | None = None) -> Fetched`. With `accept` set, a 200 response whose media type (before `;`, lowercased) is not in `accept` raises `UnwantedContent` before the body is read.
  - `PoliteClient.set_crawl_delay(host: str, seconds: float) -> None`. It raises that host's interval to `seconds` with concurrency 1 and never lowers it.

- [ ] **Step 1: Write the failing tests** (append to `backend/tests/connectors/test_http.py`; extend the import of `researchly.connectors.base` with `UnwantedContent`)

```python
async def test_accept_rejects_other_content_types_before_reading_the_body():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=b"<html>landing</html>")

    async with client_for(handler, []) as client:
        with pytest.raises(UnwantedContent, match="text/html"):
            await client.get("https://pub.example.org/paper.pdf", source="t", accept=frozenset({"application/pdf"}))


async def test_accept_matches_the_media_type_ignoring_parameters():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "Application/PDF; qs=0.9"}, content=b"%PDF-1.7")

    async with client_for(handler, []) as client:
        fetched = await client.get("https://pub.example.org/p.pdf", source="t", accept=frozenset({"application/pdf"}))
    assert fetched.body == b"%PDF-1.7"


async def test_accept_ignores_non_200_answers():
    async with client_for(lambda r: httpx.Response(404, headers={"content-type": "text/html"}), []) as client:
        fetched = await client.get("https://pub.example.org/p.pdf", source="t", accept=frozenset({"application/pdf"}))
    assert fetched.status == 404


def redirecting(hops: dict[str, str]):
    def handler(request):
        target = hops.get(str(request.url))
        if target:
            return httpx.Response(302, headers={"location": target})
        return httpx.Response(200, content=b"ok")

    return handler


async def test_a_cross_host_hop_waits_for_its_own_host():
    slept: list[float] = []
    now = [0.0]

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    hops = {"https://doi.example.org/1": "https://pub.example.org/1", "https://doi.example.org/2": "https://pub.example.org/2"}
    client = PoliteClient(
        transport=httpx.MockTransport(redirecting(hops)),
        sleep=sleep,
        clock=lambda: now[0],
        limits={"doi.example.org": (0.0, 1), "pub.example.org": (5.0, 1), "default": (0.0, 1)},
    )
    async with client:
        await client.get("https://doi.example.org/1", source="t")
        await client.get("https://doi.example.org/2", source="t")
    assert slept == [5.0]  # the second hop to pub.example.org waited for its 5 s slot


async def test_a_redirect_back_to_a_held_host_does_not_deadlock():
    hops = {"https://a.example.org/1": "https://b.example.org/1", "https://b.example.org/1": "https://a.example.org/2"}
    client = PoliteClient(
        transport=httpx.MockTransport(redirecting(hops)),
        limits={"a.example.org": (0.0, 1), "b.example.org": (0.0, 1), "default": (0.0, 1)},
    )
    async with client:
        fetched = await asyncio.wait_for(client.get("https://a.example.org/1", source="t"), timeout=2)
    assert fetched.body == b"ok"


async def test_unknown_hosts_sharing_the_default_limit_do_not_deadlock_on_a_hop():
    hops = {"https://x.example.org/1": "https://y.example.org/1"}
    client = PoliteClient(transport=httpx.MockTransport(redirecting(hops)), limits={"default": (0.0, 1)})
    async with client:
        fetched = await asyncio.wait_for(client.get("https://x.example.org/1", source="t"), timeout=2)
    assert fetched.body == b"ok"


def test_crawl_delay_raises_but_never_lowers_a_host_interval():
    client = PoliteClient(limits={"arxiv.org": (15.0, 1), "default": (1.0, 1)})
    client.set_crawl_delay("pub.example.org", 10.0)
    assert client.limit_for("https://pub.example.org/x", None).interval == 10.0
    client.set_crawl_delay("arxiv.org", 2.0)
    assert client.limit_for("https://arxiv.org/html/1", None).interval == 15.0
    client.set_crawl_delay("quick.example.org", 0.5)  # below the default: the default still applies
    assert client.limit_for("https://quick.example.org/x", None).interval == 1.0


def test_limits_file_paces_arxiv_org_per_robots_crawl_delay():
    assert http._load_limits()["arxiv.org"] == (15.0, 1)
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `uv run --directory backend pytest tests/connectors/test_http.py -q`
Expected: FAIL. `UnwantedContent` cannot be imported, `accept` is an unexpected keyword, `set_crawl_delay` is missing, the `arxiv.org` key is missing, and the hop-pacing test records `[]`.

- [ ] **Step 3: Implement**

`connectors/base.py`, after `ResponseTooLarge`:

```python
class UnwantedContent(ConnectorError):
    """The server answered with a content type the caller did not ask for (a landing page instead of a PDF)."""
```

`connectors/limits.yaml`, after the `export.arxiv.org` line:

```yaml
arxiv.org: [15.0, 1]                # robots.txt Crawl-delay: 15 (HTML and PDF pages)
```

`connectors/http.py`:
1. Import `contextlib` and `UnwantedContent`.
2. Split the key lookup out of `limit_for`, and add `set_crawl_delay`:

```python
    def _limit_key(self, url: str, bucket: str | None) -> str:
        key = bucket or urlsplit(url).hostname or ""
        return key if key in self._limit_specs else "default"

    def limit_for(self, url: str, bucket: str | None) -> HostLimit:
        key = self._limit_key(url, bucket)
        if key not in self._limits:
            self._limits[key] = HostLimit(*self._limit_specs[key], sleep=self._sleep, clock=self._clock)
        return self._limits[key]

    def set_crawl_delay(self, host: str, seconds: float) -> None:
        """Honour a robots.txt Crawl-delay: never faster than the site asks, never slower than we already are."""
        interval, _ = self._limit_specs.get(host, self._limit_specs["default"])
        if seconds > interval:
            self._limit_specs[host] = (seconds, 1)
            self._limits.pop(host, None)  # the next request builds the slower limit
```

3. `get(...)` gains `accept: frozenset[str] | None = None` after `max_bytes` and passes `accept` and `self._limit_key(url, bucket)` to `_send`.
4. `_send(self, url, params, headers, max_bytes, source, accept, first_key)`:
   - Remove the `ponytail:` comment about first-host pacing.
   - Wrap the hop loop in `async with contextlib.AsyncExitStack() as hops:` with `held = {first_key}`.
   - At the top of each iteration, before `self._http.send`:

```python
                key = self._limit_key(str(request.url), None)
                if key not in held:  # a cross-host hop waits its own host's turn; a held host is not re-entered
                    held.add(key)
                    await hops.enter_async_context(self.limit_for(str(request.url), None))
```

   - After the redirect branch, before the `content-length` check:

```python
                if accept is not None and response.status_code == 200:
                    media = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    if media not in accept:
                        raise UnwantedContent(source, f"content type {media or 'missing'}")
```

`UnwantedContent` escapes `get` through the existing `except Exception: breaker.success(); raise` branch: the server answered, so this is not an outage.

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `uv run --directory backend pytest tests/connectors -q`
Expected: PASS (all connector tests, old and new).

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run --directory backend ruff check && uv run --directory backend ruff format --check && uv run --directory backend pyright
git add backend/src/researchly/connectors backend/tests/connectors/test_http.py
git commit -m "feat: content-type gate, per-hop pacing and crawl delay in PoliteClient"
```

---

### Task 2: robots.txt with Protego

**Files:**
- Create: `backend/src/researchly/connectors/robots.py`
- Modify: `backend/pyproject.toml` (add `"protego==0.7.0",  # robots.txt (RFC 9309) for full-text fetches`), `backend/uv.lock`, `cli/uv.lock`
- Test: `backend/tests/connectors/test_robots.py`

**Interfaces:**
- Consumes: `PoliteClient.get`, `PoliteClient.set_crawl_delay` (Task 1).
- Produces: `class Robots`:
  - Constructor: `Robots(client: PoliteClient, *, clock: Callable[[], float] = time.monotonic)`.
  - `async def allowed(self, url: str) -> bool`.
  - Module constants: `AGENT = "Researchly"` and `MAX_CRAWL_DELAY = 60.0`.

- [ ] **Step 1: Write the failing tests** (`backend/tests/connectors/test_robots.py`)

```python
import httpx
import pytest

from researchly.connectors.http import PoliteClient
from researchly.connectors.robots import Robots

pytestmark = pytest.mark.anyio

ARXIV_ROBOTS = "User-agent: *\nCrawl-delay: 15\nAllow: /html\nAllow: /pdf\nDisallow: /e-print\nDisallow: /src\n"


def make(handler, seen=None, now=None):
    async def sleep(seconds: float) -> None:
        return None

    def logged(request):
        if seen is not None:
            seen.append(str(request.url))
        return handler(request)

    client = PoliteClient(transport=httpx.MockTransport(logged), sleep=sleep, rand=lambda: 0.0, limits={"default": (0.0, 1)})
    clock = (lambda: now[0]) if now is not None else (lambda: 0.0)
    return client, Robots(client, clock=clock)


async def test_rules_and_crawl_delay_are_applied():
    client, robots = make(lambda r: httpx.Response(200, text=ARXIV_ROBOTS))
    async with client:
        assert await robots.allowed("https://arxiv.org/html/1706.03762")
        assert not await robots.allowed("https://arxiv.org/e-print/1706.03762")
        assert client.limit_for("https://arxiv.org/html/1", None).interval == 15.0


async def test_robots_is_fetched_once_per_origin_and_refreshed_after_24_hours():
    seen: list[str] = []
    now = [0.0]
    client, robots = make(lambda r: httpx.Response(200, text="User-agent: *\nAllow: /\n"), seen, now)
    async with client:
        await robots.allowed("https://pub.example.org/a.pdf")
        await robots.allowed("https://pub.example.org/b.pdf")
        assert seen == ["https://pub.example.org/robots.txt"]
        now[0] = 24 * 3600 + 1
        await robots.allowed("https://pub.example.org/c.pdf")
    assert len(seen) == 2


async def test_missing_robots_allows_everything():
    client, robots = make(lambda r: httpx.Response(404))
    async with client:
        assert await robots.allowed("https://pub.example.org/a.pdf")


async def test_unreachable_robots_disallows():
    client, robots = make(lambda r: httpx.Response(503))
    async with client:
        assert not await robots.allowed("https://pub.example.org/a.pdf")


async def test_a_crawl_delay_over_a_minute_skips_the_site():
    client, robots = make(lambda r: httpx.Response(200, text="User-agent: *\nCrawl-delay: 3600\nAllow: /\n"))
    async with client:
        assert not await robots.allowed("https://slow.example.org/a.pdf")


async def test_rules_for_our_agent_win_over_the_wildcard():
    text = "User-agent: Researchly\nDisallow: /private\n\nUser-agent: *\nDisallow: /\n"
    client, robots = make(lambda r: httpx.Response(200, text=text))
    async with client:
        assert await robots.allowed("https://pub.example.org/paper.pdf")
        assert not await robots.allowed("https://pub.example.org/private/x.pdf")
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --directory backend pytest tests/connectors/test_robots.py -q`
Expected: FAIL with `ModuleNotFoundError: researchly.connectors.robots`.

- [ ] **Step 3: Add the dependency and implement**

Add `"protego==0.7.0",  # robots.txt (RFC 9309) for full-text fetches` to `[project] dependencies`, then run `uv lock --directory backend`, `uv lock --directory cli` and `uv sync --directory backend`.

`backend/src/researchly/connectors/robots.py`:

```python
"""robots.txt for crawled full-text pages (spec §19.3, RFC 9309). API endpoints are not crawled and skip it."""

import time
from collections.abc import Callable
from urllib.parse import urlsplit

from protego import Protego

from researchly.connectors.base import ConnectorError
from researchly.connectors.http import PoliteClient

AGENT = "Researchly"  # the product token of our User-Agent
TTL = 24 * 3600.0  # RFC 9309 §2.4: a cached copy is good for 24 h
RETRY_UNREACHABLE = 600.0  # an unreachable file disallows for a while, then we ask again
MAX_CRAWL_DELAY = 60.0  # a slower site is skipped rather than stalling a run


class Robots:
    def __init__(self, client: PoliteClient, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._client, self._clock = client, clock
        self._rules: dict[str, tuple[float, Protego | None]] = {}  # origin -> (expires at, rules or None)

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        cached = self._rules.get(origin)
        if cached is None or cached[0] <= self._clock():
            rules = await self._fetch(origin, parts.hostname or "")
            cached = (self._clock() + (TTL if rules is not None else RETRY_UNREACHABLE), rules)
            self._rules[origin] = cached
        rules = cached[1]
        if rules is None:
            return False
        delay = rules.crawl_delay(AGENT)
        if delay is not None:
            if delay > MAX_CRAWL_DELAY:
                return False
            self._client.set_crawl_delay(parts.hostname or "", float(delay))
        return rules.can_fetch(url, AGENT)

    async def _fetch(self, origin: str, host: str) -> Protego | None:
        try:
            fetched = await self._client.get(f"{origin}/robots.txt", source=host, max_bytes=500_000)
        except ConnectorError:
            return None  # unreachable (5xx after retries, network): disallow, RFC 9309 §2.3.1.4
        if fetched.status >= 500:
            return None
        if fetched.status >= 400:
            return Protego.parse("")  # unavailable: everything is allowed, RFC 9309 §2.3.1.3
        return Protego.parse(fetched.text())
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `uv run --directory backend pytest tests/connectors -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run --directory backend ruff check && uv run --directory backend ruff format --check && uv run --directory backend pyright
git add backend/pyproject.toml backend/uv.lock cli/uv.lock backend/src/researchly/connectors/robots.py backend/tests/connectors/test_robots.py
git commit -m "feat: robots.txt checks for full-text fetches"
```

---

### Task 3: Schema for sections, chunks, vectors and BM25 (migration 0003)

**Files:**
- Modify: `backend/pyproject.toml` (add `"pgvector==0.5.1",  # halfvec columns and binds (MIT)`), `backend/uv.lock`, `cli/uv.lock`
- Modify: `backend/src/researchly/db/models.py`
- Create: `backend/src/researchly/db/migrations/versions/0003_fulltext_and_vectors.py`
- Modify: `backend/tests/conftest.py` (`db_url` creates `pg_textsearch` when the server offers it)
- Modify: `backend/tests/db/test_migrations.py` (`test_models_match_migrations` ignores `bm25` objects)
- Modify: `.github/workflows/ci.yml` (new `backend-bm25` job)
- Test: `backend/tests/db/test_fulltext_schema.py`

**Interfaces:**
- Produces (ORM, `researchly.db.models`):
  - `EMBED_DIM = 1024`.
  - `Paper.fulltext_source: str | None` (`"arxiv_html" | "jats" | "pdf" | "none"`) and `Paper.fulltext_at: datetime | None`.
  - `PaperSection(id: UUID, paper_id: UUID, ord: int, path: list[str], text: str)`, table `paper_section`.
  - `Chunk(id: UUID, paper_id: UUID, section_id: UUID, ord: int, text: str, context_prefix: str | None, tokens: int)`, table `chunk`.
  - `PaperEmbedding(paper_id: UUID, model_id: str, vec)`, table `paper_embedding`, primary key `(paper_id, model_id)`.
  - `ChunkEmbedding(chunk_id: UUID, model_id: str, vec)`, table `chunk_embedding`, primary key `(chunk_id, model_id)`.
- Produces (DB only, by engine):
  - pg_textsearch: indexes `ix_paper_bm25` on `(title || ' ' || coalesce(abstract, ''))` and `ix_chunk_bm25` on `text`, both `USING bm25 ... WITH (text_config = 'english')`.
  - tsvector: stored generated columns `paper.bm25_tsv` and `chunk.bm25_tsv` (`to_tsvector('english', …)`), with GIN indexes `ix_paper_bm25` and `ix_chunk_bm25`.

- [ ] **Step 1: Write the failing tests** (`backend/tests/db/test_fulltext_schema.py`)

```python
import os
import uuid

import numpy as np
import pytest
from sqlalchemy import create_engine, delete, func, select, text

from researchly.db import migrate
from researchly.db.engine import make_engine, make_sessionmaker
from researchly.db.models import EMBED_DIM, Chunk, ChunkEmbedding, Paper, PaperEmbedding, PaperSection

pytestmark = [pytest.mark.db, pytest.mark.anyio]


@pytest.fixture
async def sm(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


def unit(i: int) -> np.ndarray:
    v = np.zeros(EMBED_DIM, dtype=np.float32)
    v[i] = 1.0
    return v


async def test_rows_round_trip_and_cascade_from_the_paper(sm):
    pid, sid, cid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with sm() as s:
        s.add(Paper(id=pid, title="Attention Is All You Need", title_norm="attention is all you need"))
        s.add(PaperSection(id=sid, paper_id=pid, ord=0, path=["3 Model Architecture", "3.2 Attention"], text="Scaled."))
        s.add(Chunk(id=cid, paper_id=pid, section_id=sid, ord=0, text="Scaled dot-product attention.", tokens=5))
        s.add(PaperEmbedding(paper_id=pid, model_id="m", vec=unit(0)))
        s.add(ChunkEmbedding(chunk_id=cid, model_id="m", vec=unit(1)))
        await s.commit()
        nearest = await s.scalar(
            select(ChunkEmbedding.chunk_id).order_by(ChunkEmbedding.vec.cosine_distance(unit(1))).limit(1)
        )
        assert nearest == cid
        assert (await s.get(PaperSection, sid)).path == ["3 Model Architecture", "3.2 Attention"]
        await s.execute(delete(Paper).where(Paper.id == pid))
        await s.commit()
        for model in (PaperSection, Chunk, PaperEmbedding, ChunkEmbedding):
            assert await s.scalar(select(func.count()).select_from(model)) == 0


async def test_bm25_objects_match_the_recorded_engine(db_url, sm):
    expected = os.environ.get("RESEARCHLY_EXPECT_BM25", "tsvector")  # the backend-bm25 CI job sets pg_textsearch
    engine = create_engine(db_url)
    with engine.connect() as conn:
        recorded = conn.scalar(text("SELECT value #>> '{}' FROM capability WHERE key = 'bm25_engine'"))
        indexes = dict(conn.execute(text("SELECT indexname, indexdef FROM pg_indexes WHERE indexname LIKE '%bm25'")).all())
        tsv_columns = set(conn.scalars(text("SELECT table_name FROM information_schema.columns WHERE column_name = 'bm25_tsv'")))
    engine.dispose()
    assert recorded == expected
    assert set(indexes) == {"ix_paper_bm25", "ix_chunk_bm25"}
    if expected == "pg_textsearch":
        assert all("USING bm25" in d for d in indexes.values()) and tsv_columns == set()
    else:
        assert all("USING gin" in d for d in indexes.values()) and tsv_columns == {"paper", "chunk"}


async def test_vector_indexes_are_hnsw_cosine(db_url, sm):
    engine = create_engine(db_url)
    with engine.connect() as conn:
        defs = list(conn.scalars(text("SELECT indexdef FROM pg_indexes WHERE indexname LIKE '%hnsw'")))
    engine.dispose()
    assert len(defs) == 2 and all("hnsw" in d and "halfvec_cosine_ops" in d for d in defs)
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --directory backend pytest tests/db/test_fulltext_schema.py -m db -q`
Expected: FAIL with `ImportError` (no `EMBED_DIM` or the new models).

- [ ] **Step 3: Add the dependency, models, migration and test plumbing**

Add `"pgvector==0.5.1",  # halfvec columns and binds (MIT)` to dependencies, then run `uv lock --directory backend`, `uv lock --directory cli` and `uv sync --directory backend`.

`db/models.py`:
- Imports: add `from pgvector.sqlalchemy import HALFVEC` and `from sqlalchemy.dialects.postgresql import ARRAY`.
- Add `EMBED_DIM = 1024  # Qwen3-Embedding-0.6B, ADR 0007`.
- Add to `Paper`:

```python
    fulltext_source: Mapped[str | None] = mapped_column(Text)  # arxiv_html | jats | pdf | none (tried, nothing usable)
    fulltext_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

- Add after `PaperAuthor`:

```python
class PaperSection(Base):
    __tablename__ = "paper_section"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper.id", ondelete="CASCADE"), index=True)
    ord: Mapped[int] = mapped_column(Integer)
    path: Mapped[list[str]] = mapped_column(ARRAY(Text))
    text: Mapped[str] = mapped_column(Text)


# BM25 objects (ix_*_bm25, bm25_tsv) depend on the engine (spec §9.3), so they live in migration 0003 only.
class Chunk(Base):
    __tablename__ = "chunk"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper.id", ondelete="CASCADE"), index=True)
    section_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper_section.id", ondelete="CASCADE"), index=True)
    ord: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    context_prefix: Mapped[str | None] = mapped_column(Text)
    tokens: Mapped[int] = mapped_column(Integer)


def _hnsw(name: str) -> Index:
    return Index(
        name,
        "vec",
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"vec": "halfvec_cosine_ops"},
    )


class PaperEmbedding(Base):
    """Title + abstract (spec §9.2)."""

    __tablename__ = "paper_embedding"
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper.id", ondelete="CASCADE"), primary_key=True)
    model_id: Mapped[str] = mapped_column(Text, primary_key=True)
    vec: Mapped[Any] = mapped_column(HALFVEC(EMBED_DIM))
    __table_args__ = (_hnsw("ix_paper_embedding_hnsw"),)


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embedding"
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chunk.id", ondelete="CASCADE"), primary_key=True)
    model_id: Mapped[str] = mapped_column(Text, primary_key=True)
    vec: Mapped[Any] = mapped_column(HALFVEC(EMBED_DIM))
    __table_args__ = (_hnsw("ix_chunk_embedding_hnsw"),)
```

`db/migrations/versions/0003_fulltext_and_vectors.py`:

```python
"""full text: sections, chunks, title/abstract and chunk vectors, BM25 by engine

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

PAPER_DOC = "(title || ' ' || coalesce(abstract, ''))"  # retrieval.py repeats this exactly: pg_textsearch matches the expression


def upgrade() -> None:
    op.add_column("paper", sa.Column("fulltext_source", sa.Text()))
    op.add_column("paper", sa.Column("fulltext_at", sa.DateTime(timezone=True)))
    op.create_table(
        "paper_section",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("paper_id", UUID(), sa.ForeignKey("paper.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.Column("path", ARRAY(sa.Text()), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
    )
    op.create_index("ix_paper_section_paper_id", "paper_section", ["paper_id"])
    op.create_table(
        "chunk",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("paper_id", UUID(), sa.ForeignKey("paper.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_id", UUID(), sa.ForeignKey("paper_section.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("context_prefix", sa.Text()),
        sa.Column("tokens", sa.Integer(), nullable=False),
    )
    op.create_index("ix_chunk_paper_id", "chunk", ["paper_id"])
    op.create_index("ix_chunk_section_id", "chunk", ["section_id"])
    for table, key, ref in (("paper_embedding", "paper_id", "paper.id"), ("chunk_embedding", "chunk_id", "chunk.id")):
        op.create_table(
            table,
            sa.Column(key, UUID(), sa.ForeignKey(ref, ondelete="CASCADE"), primary_key=True),
            sa.Column("model_id", sa.Text(), primary_key=True),
            sa.Column("vec", HALFVEC(1024), nullable=False),
        )
        op.execute(
            f"CREATE INDEX ix_{table}_hnsw ON {table} USING hnsw (vec halfvec_cosine_ops) WITH (m = 16, ef_construction = 64)"
        )
    engine = op.get_bind().scalar(sa.text("SELECT value #>> '{}' FROM capability WHERE key = 'bm25_engine'"))
    if engine == "pg_textsearch":
        op.execute(f"CREATE INDEX ix_paper_bm25 ON paper USING bm25 ({PAPER_DOC}) WITH (text_config = 'english')")
        op.execute("CREATE INDEX ix_chunk_bm25 ON chunk USING bm25 (text) WITH (text_config = 'english')")
    else:
        op.execute(
            f"ALTER TABLE paper ADD COLUMN bm25_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', {PAPER_DOC})) STORED"
        )
        op.execute("CREATE INDEX ix_paper_bm25 ON paper USING gin (bm25_tsv)")
        op.execute("ALTER TABLE chunk ADD COLUMN bm25_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED")
        op.execute("CREATE INDEX ix_chunk_bm25 ON chunk USING gin (bm25_tsv)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_paper_bm25")
    op.execute("ALTER TABLE paper DROP COLUMN IF EXISTS bm25_tsv")
    op.drop_table("chunk_embedding")
    op.drop_table("paper_embedding")
    op.drop_table("chunk")
    op.drop_table("paper_section")
    op.drop_column("paper", "fulltext_at")
    op.drop_column("paper", "fulltext_source")
```

`tests/db/test_migrations.py`, `test_models_match_migrations`: configure the comparison so it skips the engine-specific objects:

```python
def _not_bm25(obj, name, type_, reflected, compare_to):
    return not (name and "bm25" in name)  # engine-specific BM25 objects live in migration 0003 only (spec §9.3)


# inside test_models_match_migrations:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"include_object": _not_bm25}), Base.metadata)
```

`tests/conftest.py`, in `db_url` after `CREATE DATABASE`. This mirrors provisioning, which creates the untrusted extension as superuser:

```python
    url = make_url(pg_admin_url).set(database=name)
    fresh = create_engine(url, isolation_level="AUTOCOMMIT")
    with fresh.connect() as conn:
        if conn.scalar(text("SELECT 1 FROM pg_available_extensions WHERE name = 'pg_textsearch'")):
            conn.execute(text("CREATE EXTENSION pg_textsearch"))  # provisioning does this in the product (spec §16.1)
    fresh.dispose()
    yield url.render_as_string(hide_password=False)
```

`.github/workflows/ci.yml`: add a job after `backend`. It uses the same pinned actions as `backend`. The leg runs the DB tests against the repo's own Postgres image, which has pg_textsearch. First check `infra/postgres/Dockerfile` for its build context (it may expect the repo root) and adjust the `docker build` line to match:

```yaml
  backend-bm25:
    runs-on: ubuntu-latest
    timeout-minutes: 25
    env:
      RESEARCHLY_TEST_DATABASE_URL: postgresql+psycopg://postgres:postgres@localhost:5432/postgres
      RESEARCHLY_EXPECT_BM25: pg_textsearch
    defaults:
      run:
        working-directory: backend
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@07a047e608db84b3d8c06cd9a7c7dcc9c355c18e # v10.3.2
        with:
          version: "0.12.10"
          enable-cache: true
          cache-dependency-glob: backend/uv.lock
      - name: Postgres with pg_textsearch (the Docker-mode image)
        run: |
          docker build -t researchly-postgres-test ../infra/postgres
          docker run -d --name pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 researchly-postgres-test
          for i in $(seq 1 30); do docker exec pg pg_isready -U postgres && break; sleep 2; done
      - run: uv sync --locked
      - run: uv run pytest -m db -q
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `uv run --directory backend pytest -m db -q` and `uv run --directory backend pytest -q`
Expected: PASS. Locally, the testcontainers `pgvector/pgvector:pg18` image gives the `tsvector` engine. The repeated upgrade, downgrade and upgrade in `test_migrations.py` must pass with 0003. If Docker allows it, also build `infra/postgres` locally and run `-m db` against it with `RESEARCHLY_TEST_DATABASE_URL` and `RESEARCHLY_EXPECT_BM25=pg_textsearch`.

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run --directory backend ruff check && uv run --directory backend ruff format --check && uv run --directory backend pyright
git add backend/pyproject.toml backend/uv.lock cli/uv.lock backend/src/researchly/db backend/tests/conftest.py backend/tests/db .github/workflows/ci.yml
git commit -m "feat: sections, chunks, halfvec embeddings and BM25 schema"
```

---

### Task 4: Full-text types and the arXiv HTML parser

**Files:**
- Create: `backend/src/researchly/ingest/document.py`
- Create: `backend/src/researchly/ingest/arxiv_html.py`
- Modify: `backend/pyproject.toml` (add `"lxml==6.1.3",  # arXiv LaTeXML HTML (already locked via docling)`), `backend/uv.lock`, `cli/uv.lock`
- Test: `backend/tests/ingest/__init__.py` (empty), `backend/tests/ingest/test_arxiv_html.py`

**Interfaces:**
- Produces (`researchly.ingest.document`):

```python
@dataclass(frozen=True)
class Section:
    path: tuple[str, ...]
    text: str  # paragraphs joined by a blank line


@dataclass(frozen=True)
class FullText:
    source: str  # "arxiv_html" | "jats" | "pdf"
    sections: list[Section]
    license: str | None = None
```

- Produces: `parse_arxiv_html(body: bytes) -> FullText | None` (`researchly.ingest.arxiv_html`). It returns None when the page has no `article` element or no section text.

- [ ] **Step 1: Write the failing tests** (`backend/tests/ingest/test_arxiv_html.py`)

```python
from researchly.ingest.arxiv_html import parse_arxiv_html
from researchly.ingest.document import FullText, Section

PAGE = b"""<!DOCTYPE html><html><head><title>Attention</title></head><body>
<nav>chrome</nav>
<article class="ltx_document">
  <h1 class="ltx_title ltx_title_document">Attention Is All You Need</h1>
  <div class="ltx_authors">Ashish Vaswani</div>
  <div class="ltx_abstract"><h6 class="ltx_title ltx_title_abstract">Abstract</h6>
    <p class="ltx_p">The dominant sequence transduction models.</p></div>
  <section class="ltx_section" id="S1"><h2 class="ltx_title ltx_title_section"><span class="ltx_tag ltx_tag_section">1 </span>Introduction</h2>
    <div class="ltx_para"><p class="ltx_p">Recurrent networks<span class="ltx_note ltx_role_footnote">footnote text</span> are slow
      <cite class="ltx_cite">[<a class="ltx_ref" href="#bib.bib1">1</a>]</cite>.</p></div>
    <section class="ltx_subsection" id="S1.SS1"><h3 class="ltx_title ltx_title_subsection">1.1 Attention</h3>
      <div class="ltx_para"><p class="ltx_p">We use <math alttext="QK^{T}" class="ltx_Math"><mi>Q</mi></math> scores.</p></div>
      <table class="ltx_equation ltx_eqn_table"><tr class="ltx_eqn_row"><td>
        <math alttext="\\mathrm{softmax}(x)" class="ltx_Math" display="block"><mi>s</mi></math></td></tr></table>
      <figure class="ltx_figure"><img class="ltx_graphics" src="x1.png"/>
        <figcaption class="ltx_caption"><span class="ltx_tag ltx_tag_figure">Figure 1: </span>The Transformer.</figcaption></figure>
    </section>
  </section>
  <section class="ltx_bibliography" id="bib"><h2 class="ltx_title">References</h2>
    <ul class="ltx_biblist"><li class="ltx_bibitem" id="bib.bib1">Bahdanau et al.</li></ul></section>
</article></body></html>"""


def test_sections_carry_their_path_and_clean_text():
    full = parse_arxiv_html(PAGE)
    assert isinstance(full, FullText) and full.source == "arxiv_html" and full.license is None
    assert full.sections[0] == Section(("Abstract",), "The dominant sequence transduction models.")
    intro, sub = full.sections[1], full.sections[2]
    assert intro.path == ("1 Introduction",)
    assert intro.text == "Recurrent networks are slow [1]."  # footnote dropped, citation kept as text
    assert sub.path == ("1 Introduction", "1.1 Attention")
    assert sub.text == "We use $QK^{T}$ scores.\n\n$\\mathrm{softmax}(x)$\n\nFigure 1: The Transformer."


def test_bibliography_authors_and_chrome_are_not_sections():
    texts = " ".join(s.text for s in parse_arxiv_html(PAGE).sections)
    assert "Bahdanau" not in texts and "Vaswani" not in texts and "chrome" not in texts


def test_pages_without_an_article_or_text_give_none():
    assert parse_arxiv_html(b"<html><body><p>404 not found</p></body></html>") is None
    assert parse_arxiv_html(b"<html><body><article class='ltx_document'></article></body></html>") is None
    assert parse_arxiv_html(b"") is None
    assert parse_arxiv_html(b"\x00\xff garbage") is None
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --directory backend pytest tests/ingest/test_arxiv_html.py -q`
Expected: FAIL with `ModuleNotFoundError: researchly.ingest.arxiv_html`.

- [ ] **Step 3: Implement**

Add the lxml pin, then run `uv lock --directory backend`, `uv lock --directory cli` and `uv sync --directory backend`.

`backend/src/researchly/ingest/document.py`:

```python
"""What every full-text parser yields (spec §9.1): sections with their heading path, as untrusted text."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    path: tuple[str, ...]
    text: str  # paragraphs joined by a blank line


@dataclass(frozen=True)
class FullText:
    source: str  # "arxiv_html" | "jats" | "pdf"
    sections: list[Section]
    license: str | None = None


def clean(text: str) -> str:
    return " ".join(text.split())
```

`backend/src/researchly/ingest/arxiv_html.py`:

```python
"""arXiv's LaTeXML HTML (arxiv.org/html/<id>) → sections (research 2026-11-04-step2b-fulltext §1)."""

import lxml.html
from lxml import etree

from researchly.ingest.document import FullText, Section, clean

_SECTIONS = {"ltx_section", "ltx_subsection", "ltx_subsubsection", "ltx_paragraph", "ltx_appendix"}
_DROP = {"ltx_note", "ltx_authors", "ltx_bibliography", "ltx_page_footer"}  # footnotes, bylines, references


def _classes(el: etree._Element) -> set[str]:
    return set((el.get("class") or "").split())


def _is_section(el: etree._Element) -> bool:
    return el.tag == "section" and bool(_classes(el) & _SECTIONS)


def _title(section: etree._Element) -> str:
    for child in section:
        if child.tag in ("h2", "h3", "h4", "h5", "h6") and "ltx_title" in _classes(child):
            return clean(child.text_content())
    return ""


def _blocks(container: etree._Element, owner: etree._Element | None) -> list[str]:
    """Paragraphs, display equations and captions whose nearest section is `owner`."""
    out = []
    for el in container.iter("p", "figcaption", "table"):
        if el.tag == "table" and not _classes(el) & {"ltx_equation", "ltx_equationgroup"}:
            continue
        if el.tag == "p" and next(el.iterancestors("figcaption"), None) is not None:
            continue  # a caption's own <p> is read with the caption
        if next((a for a in el.iterancestors() if _is_section(a)), None) is not owner:
            continue
        if text := clean(el.text_content()):
            out.append(text)
    return out


def parse_arxiv_html(body: bytes) -> FullText | None:
    try:
        root = lxml.html.document_fromstring(body)
    except (etree.ParserError, ValueError):
        return None
    article = next(root.iter("article"), None)
    if article is None:
        return None
    for el in list(article.iter()):
        if isinstance(el.tag, str) and _classes(el) & _DROP and el.getparent() is not None:
            el.drop_tree()  # keeps the tail text
    for math in list(article.iter("math")):
        span = etree.Element("span")
        span.text, span.tail = f"${math.get('alttext', '')}$", math.tail
        math.getparent().replace(math, span)  # pyright: ignore[reportOptionalMemberAccess]
    sections: list[Section] = []
    abstract = next((d for d in article.iter("div") if "ltx_abstract" in _classes(d)), None)
    if abstract is not None and (paras := [clean(p.text_content()) for p in abstract.iter("p")]):
        sections.append(Section(("Abstract",), "\n\n".join(p for p in paras if p)))
    for sec in (el for el in article.iter("section") if _is_section(el)):
        path = tuple(_title(a) for a in reversed(list(sec.iterancestors())) if _is_section(a)) + (_title(sec),)
        if blocks := _blocks(sec, sec):
            sections.append(Section(tuple(p for p in path if p), "\n\n".join(blocks)))
    return FullText("arxiv_html", sections) if sections else None
```

If `lxml.html.document_fromstring(b"")` raises a different exception type, add it to the `except` tuple (the test pins this). Keep `etree._Element` annotations, or use `lxml.html.HtmlElement` if pyright prefers it.

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `uv run --directory backend pytest tests/ingest -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run --directory backend ruff check && uv run --directory backend ruff format --check && uv run --directory backend pyright
git add backend/pyproject.toml backend/uv.lock cli/uv.lock backend/src/researchly/ingest backend/tests/ingest
git commit -m "feat: arXiv HTML full-text parser"
```

---

### Task 5: JATS parser and PDF Markdown sections

**Files:**
- Create: `backend/src/researchly/ingest/jats.py`
- Modify: `backend/src/researchly/ingest/pdf.py` (add `markdown_sections`)
- Test: `backend/tests/ingest/test_jats.py`, `backend/tests/ingest/test_pdf_sections.py`

**Interfaces:**
- Consumes: `Section`, `FullText` and `clean` from `researchly.ingest.document` (Task 4).
- Produces:
  - `parse_jats(body: bytes) -> FullText | None` (`researchly.ingest.jats`), with `source="jats"` and `license` taken from `permissions/license`. It uses the `xlink:href` when present, else the licence text.
  - `markdown_sections(markdown: str) -> list[Section]` (`researchly.ingest.pdf`): flat paths, without References or Bibliography sections and without Docling's `<!-- image -->` lines.

- [ ] **Step 1: Write the failing tests**

`backend/tests/ingest/test_jats.py`:

```python
from researchly.ingest.document import Section
from researchly.ingest.jats import parse_jats

ARTICLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink" article-type="research-article">
<front><article-meta>
  <article-id pub-id-type="pmcid">PMC5334499</article-id>
  <permissions><license license-type="cc-by" xlink:href="https://creativecommons.org/licenses/by/4.0/">
    <license-p>Open access.</license-p></license></permissions>
  <abstract><p>Deep learning <italic>works</italic>.</p></abstract>
</article-meta></front>
<body>
  <p>Lead paragraph.</p>
  <sec><title>Introduction</title><p>First.</p>
    <fig id="f1"><label>Figure 1</label><caption><p>A network.</p></caption></fig>
    <sec><title>Background</title><p>Second.</p></sec>
  </sec>
  <sec><p>Untitled section text.</p></sec>
</body>
<back><ref-list><ref id="r1"><mixed-citation>LeCun 2015</mixed-citation></ref></ref-list></back>
</article>"""


def test_sections_paths_and_licence():
    full = parse_jats(ARTICLE)
    assert full is not None and full.source == "jats"
    assert full.license == "https://creativecommons.org/licenses/by/4.0/"
    assert full.sections == [
        Section(("Abstract",), "Deep learning works."),
        Section(("Body",), "Lead paragraph."),
        Section(("Introduction",), "First.\n\nA network."),
        Section(("Introduction", "Background"), "Second."),
        Section(("Body",), "Untitled section text."),
    ]


def test_licence_text_when_there_is_no_link():
    body = ARTICLE.replace(b' xlink:href="https://creativecommons.org/licenses/by/4.0/"', b"")
    assert parse_jats(body).license == "Open access."


def test_references_are_not_sections():
    assert all("LeCun" not in s.text for s in parse_jats(ARTICLE).sections)


def test_unreadable_or_foreign_xml_gives_none():
    assert parse_jats(b"<html><body>error</body></html>") is None
    assert parse_jats(b"<article><front/></article>") is None
    assert parse_jats(b"not xml") is None
    bomb = b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "xx">]><article>&x;</article>'
    assert parse_jats(bomb) is None
```

`backend/tests/ingest/test_pdf_sections.py`:

```python
from researchly.ingest.document import Section
from researchly.ingest.pdf import markdown_sections

MARKDOWN = """Attention Is All You Need

## Abstract

The dominant models.

## 1 Introduction

Recurrent networks.

<!-- image -->

Second paragraph.

## References

[1] Bahdanau.

## A Appendix

Extra.
"""


def test_headings_become_flat_sections_without_references_or_image_markers():
    assert markdown_sections(MARKDOWN) == [
        Section(("Body",), "Attention Is All You Need"),
        Section(("Abstract",), "The dominant models."),
        Section(("1 Introduction",), "Recurrent networks.\n\nSecond paragraph."),
        Section(("A Appendix",), "Extra."),
    ]


def test_numbered_reference_headings_are_dropped_too():
    assert markdown_sections("## 7 References\n\n[1] x\n\n## Bibliography\n\ny\n") == []


def test_empty_markdown_has_no_sections():
    assert markdown_sections("") == [] and markdown_sections("<!-- image -->\n") == []
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --directory backend pytest tests/ingest -q`
Expected: FAIL with `ModuleNotFoundError: researchly.ingest.jats` and `ImportError: markdown_sections`.

- [ ] **Step 3: Implement**

`backend/src/researchly/ingest/jats.py`:

```python
"""Europe PMC JATS full text → sections (research 2026-11-04-step2b-fulltext §3)."""

from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from researchly.ingest.document import FullText, Section, clean

_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_BLOCKS = ("p", "disp-formula", "list", "def-list")


def _text(el: Element | None) -> str:
    return clean("".join(el.itertext())) if el is not None else ""


def _own_blocks(sec: Element) -> list[str]:
    out = []
    for child in sec:
        if child.tag in _BLOCKS:
            out.append(_text(child))
        elif child.tag in ("fig", "table-wrap"):
            out.append(_text(child.find("caption")))
    return [b for b in out if b]


def _walk(sec: Element, parent: tuple[str, ...], out: list[Section]) -> None:
    title = _text(sec.find("title"))
    path = (*parent, title) if title else parent
    if blocks := _own_blocks(sec):
        out.append(Section(path or ("Body",), "\n\n".join(blocks)))
    for child in sec.findall("sec"):
        _walk(child, path, out)


def parse_jats(body: bytes) -> FullText | None:
    try:
        root = fromstring(body)
    except (DefusedXmlException, ParseError, ValueError):  # entity bombs, DTDs, HTML error pages, truncation
        return None
    if root.tag != "article":
        return None
    sections: list[Section] = []
    if abstract := _text(root.find("front/article-meta/abstract")):
        sections.append(Section(("Abstract",), abstract))
    content = root.find("body")
    if content is not None:
        if lead := _own_blocks(content):
            sections.append(Section(("Body",), "\n\n".join(lead)))
        for sec in content.findall("sec"):
            _walk(sec, (), sections)
    if len(sections) <= (1 if abstract else 0):
        return None  # an abstract alone is not full text
    lic = root.find("front/article-meta/permissions/license")
    license_ = (lic.get(_XLINK_HREF) or _text(lic) or None) if lic is not None else None
    return FullText("jats", sections, license_)
```

Note: `test_unreadable_or_foreign_xml_gives_none` expects `<article><front/></article>` to give None. The "abstract alone" rule also returns None for a JATS record with only an abstract, which is what the PDF fallback needs.

`backend/src/researchly/ingest/pdf.py`: add `import re` and `from researchly.ingest.document import Section, clean`, then:

```python
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$")
_BACK_MATTER = re.compile(r"^(?:[\dIVX]+\.?\s+)?(?:references|bibliography|works cited)$", re.I)


def markdown_sections(markdown: str) -> list[Section]:
    """Docling Markdown → flat sections: its heading levels are not reliable, so each heading is its own path."""
    sections: list[Section] = []
    title, lines = "Body", []

    def flush() -> None:
        paras = [clean(p) for p in "\n".join(lines).split("\n\n")]
        if (text := "\n\n".join(p for p in paras if p)) and not _BACK_MATTER.match(title):
            sections.append(Section((title,), text))

    for line in markdown.splitlines():
        if line.strip().startswith("<!--"):
            continue
        if heading := _HEADING.match(line):
            flush()
            title, lines = clean(heading.group(1)), []
        else:
            lines.append(line)
    flush()
    return sections
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `uv run --directory backend pytest tests/ingest -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run --directory backend ruff check && uv run --directory backend ruff format --check && uv run --directory backend pyright
git add backend/src/researchly/ingest backend/tests/ingest
git commit -m "feat: JATS parser and PDF markdown sections"
```

---

### Task 6: Chunking and token counting

**Files:**
- Create: `backend/src/researchly/ingest/chunking.py`
- Modify: `backend/src/researchly/embeddings.py` (`Embedder.model_id`, `Embedder.count_tokens`)
- Modify: `backend/pyproject.toml` (add `"semchunk==4.1.1",  # sentence-aware chunking with any token counter (MIT)`), `backend/uv.lock`, `cli/uv.lock`
- Test: `backend/tests/ingest/test_chunking.py`, and a platform test in `backend/tests/platform/` next to the existing embedder test (use the same fixtures and markers that file uses for the cached models dir)

**Interfaces:**
- Produces:
  - `CHUNK_TOKENS = 512` and `OVERLAP = 0.15` (`researchly.ingest.chunking`).
  - `@dataclass(frozen=True) class Piece: text: str; tokens: int`.
  - `chunk_text(text: str, count_tokens: Callable[[str], int]) -> list[Piece]`.
  - `Embedder.model_id: str` (equals `EMBED_MODEL`).
  - `Embedder.count_tokens(text: str) -> int`: Qwen3 tokens with no truncation and no special tokens.

- [ ] **Step 1: Write the failing tests**

`backend/tests/ingest/test_chunking.py`:

```python
from researchly.ingest.chunking import CHUNK_TOKENS, Piece, chunk_text


def words(text: str) -> int:
    return len(text.split())


def test_short_text_is_one_chunk():
    assert chunk_text("Attention is all you need.", words) == [Piece("Attention is all you need.", 5)]


def test_blank_text_has_no_chunks():
    assert chunk_text("  \n\n ", words) == []


def test_long_text_is_split_within_the_budget_with_overlap():
    sentences = [f"Sentence number {i} talks about attention heads." for i in range(400)]  # 7 words each
    text = " ".join(sentences)
    pieces = chunk_text(text, words)
    assert len(pieces) > 4
    assert all(p.tokens == words(p.text) <= CHUNK_TOKENS for p in pieces)
    assert all(p.text in text for p in pieces)  # chunks are verbatim spans
    first_tail = pieces[0].text.split()[-5:]
    assert " ".join(first_tail) in pieces[1].text  # consecutive chunks overlap
```

Platform test (`backend/tests/platform/test_chunking_tokens.py`; copy the embedder fixture and marker pattern from the existing embedder platform test):

```python
def test_qwen3_token_counts_are_untruncated_and_chunks_fit(embedder):
    assert 3 <= embedder.count_tokens("Attention is all you need") <= 12
    long = "Self-attention relates positions of a single sequence. " * 200
    assert embedder.count_tokens(long) > 1000  # fastembed's own tokenizer truncates at 512; ours must not
    pieces = chunk_text(long, embedder.count_tokens)
    assert all(p.tokens <= CHUNK_TOKENS for p in pieces)
    mean = sum(p.tokens for p in pieces) / len(pieces)
    assert 250 <= mean <= 512
    assert embedder.model_id == "Qwen/Qwen3-Embedding-0.6B-Q"
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --directory backend pytest tests/ingest/test_chunking.py -q`
Expected: FAIL with `ModuleNotFoundError: researchly.ingest.chunking`.

- [ ] **Step 3: Add the dependency and implement**

Add the semchunk pin, then run `uv lock --directory backend`, `uv lock --directory cli` and `uv sync --directory backend`. Check `backend/tests/test_lock.py` still passes: semchunk needs only `tqdm`, plus `mpire`/`dill` on some platforms, and no torch.

`backend/src/researchly/ingest/chunking.py`:

```python
"""Section text → overlapping, sentence-aware chunks (spec §9.2) with semchunk and the embedder's tokenizer."""

from collections.abc import Callable
from dataclasses import dataclass

import semchunk

# semchunk keeps chunk + overlap within chunk_size: 512 gives ~330-token means and ~400 maxima (research §6),
# the spec's "~350", and leaves room for a short query in the reranker's 512-token pair window.
CHUNK_TOKENS = 512
OVERLAP = 0.15


@dataclass(frozen=True)
class Piece:
    text: str
    tokens: int


def chunk_text(text: str, count_tokens: Callable[[str], int]) -> list[Piece]:
    if not text.strip():
        return []
    chunks = semchunk.chunk(text, chunk_size=CHUNK_TOKENS, token_counter=count_tokens, overlap=OVERLAP)
    return [Piece(c, count_tokens(c)) for c in chunks if c.strip()]
```

If semchunk's `chunk()` returns a different shape with `overlap` set (check its 4.1.1 signature), adapt the call so the tests' "verbatim spans" and "overlap" properties hold. Do not hand-roll a splitter.

`backend/src/researchly/embeddings.py`: add `from tokenizers import Tokenizer`, then:

```python
class Embedder:
    """1024-dim, L2-normalised vectors: the model's full size, stored as halfvec(1024)."""

    model_id = EMBED_MODEL

    def __init__(self, cache_dir: Path) -> None:
        self._model = TextEmbedding(EMBED_MODEL, cache_dir=str(cache_dir))
        loaded: Tokenizer = self._model.model.tokenizer  # pyright: ignore[reportAttributeAccessIssue]
        # fastembed's copy truncates at 512 and pads; counting needs the whole text
        self._counter = Tokenizer.from_str(loaded.to_str())
        self._counter.no_truncation()
        self._counter.no_padding()

    def count_tokens(self, text: str) -> int:
        return len(self._counter.encode(text, add_special_tokens=False).ids)
```

Keep the rest of the existing `Embedder` and `Reranker` methods unchanged. If the attribute path to fastembed's tokenizer differs in 0.9.1, find it in `fastembed/text/onnx_text_model.py` (`self.tokenizer`), and keep the platform test as the guard.

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `uv run --directory backend pytest tests/ingest -q`, then the platform test: `uv run --directory backend pytest -m platform tests/platform/test_chunking_tokens.py -q`. The platform test downloads the model into `backend/.models` the first time.
Expected: PASS.

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run --directory backend ruff check && uv run --directory backend ruff format --check && uv run --directory backend pyright
git add backend/pyproject.toml backend/uv.lock cli/uv.lock backend/src/researchly/ingest/chunking.py backend/src/researchly/embeddings.py backend/tests/ingest/test_chunking.py backend/tests/platform
git commit -m "feat: sentence-aware chunking with Qwen3 token counts"
```

---

### Task 7: Full-text fetcher (arXiv HTML, then JATS, then PDF)

**Files:**
- Create: `backend/src/researchly/services/fulltext.py`
- Test: `backend/tests/services/test_fulltext.py`, plus cassettes under `backend/tests/services/cassettes/test_fulltext/` (recorded live and keyless)

**Interfaces:**
- Consumes:
  - `PoliteClient.get(..., accept=, max_bytes=)` and `UnwantedContent` (Task 1).
  - `Robots.allowed` (Task 2).
  - `parse_arxiv_html` (Task 4), and `parse_jats` and `markdown_sections` (Task 5).
  - `convert_pdf(path) -> ConvertedPdf` (existing `ingest/pdf.py`).
- Produces (`researchly.services.fulltext`):
  - `@dataclass(frozen=True) class Target: arxiv: str | None = None; pmcid: str | None = None; pdf_urls: tuple[str, ...] = ()`.
  - `class FullTextFetcher`, constructed as `FullTextFetcher(client: PoliteClient, robots: Robots, convert: Callable[[Path], str] = lambda p: convert_pdf(p).markdown)`.
  - `async def fetch(self, target: Target) -> FullText | None`.
  - Constants `HTML_TYPES`, `XML_TYPES`, `PDF_TYPES`, `MAX_HTML = 5_000_000`, `MAX_PDF = 50_000_000`.

- [ ] **Step 1: Write the failing tests** (`backend/tests/services/test_fulltext.py`)

```python
import httpx
import pytest

from researchly.connectors.http import PoliteClient
from researchly.connectors.robots import Robots
from researchly.services.fulltext import FullTextFetcher, Target

pytestmark = pytest.mark.anyio

ARXIV_PAGE = (
    b"<html><body><article class='ltx_document'><section class='ltx_section'><h2 class='ltx_title'>1 Intro</h2>"
    b"<div class='ltx_para'><p class='ltx_p'>From HTML.</p></div></section></article></body></html>"
)
JATS = b"<article><body><sec><title>Intro</title><p>From JATS.</p></sec></body></article>"


def fetcher_for(routes: dict[str, httpx.Response], seen: list[str], markdown="## Intro\n\nFrom PDF.\n"):
    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return routes.get(str(request.url), httpx.Response(404))
        return routes.get(str(request.url), httpx.Response(404, headers={"content-type": "text/html"}))

    async def sleep(seconds: float) -> None:
        return None

    client = PoliteClient(transport=httpx.MockTransport(handler), sleep=sleep, rand=lambda: 0.0, limits={"default": (0.0, 1)})
    converted: list[bytes] = []

    def convert(path):
        converted.append(path.read_bytes())
        return markdown

    return client, FullTextFetcher(client, Robots(client), convert=convert), converted


def html(body: bytes) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=body)


async def test_arxiv_html_is_the_first_choice():
    seen: list[str] = []
    client, fetcher, _ = fetcher_for({"https://arxiv.org/html/1706.03762": html(ARXIV_PAGE)}, seen)
    async with client:
        full = await fetcher.fetch(Target(arxiv="1706.03762", pmcid="PMC1", pdf_urls=("https://x.org/p.pdf",)))
    assert full.source == "arxiv_html" and full.sections[0].text == "From HTML."
    assert not any("europepmc" in u or "x.org" in u for u in seen)


async def test_old_style_arxiv_ids_keep_their_slash():
    seen: list[str] = []
    client, fetcher, _ = fetcher_for({"https://arxiv.org/html/hep-th/9901001": html(ARXIV_PAGE)}, seen)
    async with client:
        assert (await fetcher.fetch(Target(arxiv="hep-th/9901001"))).source == "arxiv_html"


async def test_missing_html_falls_back_to_jats():
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC5334499/fullTextXML"
    routes = {url: httpx.Response(200, headers={"content-type": "application/xml"}, content=JATS)}
    client, fetcher, _ = fetcher_for(routes, [])
    async with client:
        full = await fetcher.fetch(Target(arxiv="1706.03762", pmcid="PMC5334499"))
    assert full.source == "jats" and full.sections[0].text == "From JATS."


async def test_pdf_tier_needs_a_pdf_type_and_magic_bytes():
    routes = {
        "https://landing.org/paper": html(b"<html>a landing page</html>"),
        "https://fake.org/p.pdf": httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"<html>"),
        "https://real.org/p.pdf": httpx.Response(
            200, headers={"content-type": "application/octet-stream"}, content=b"%PDF-1.7 body"
        ),
    }
    client, fetcher, converted = fetcher_for(routes, [])
    async with client:
        full = await fetcher.fetch(
            Target(pdf_urls=("https://landing.org/paper", "https://fake.org/p.pdf", "https://real.org/p.pdf"))
        )
    assert full.source == "pdf" and full.sections[0].text == "From PDF."
    assert converted == [b"%PDF-1.7 body"]  # only the real PDF reached Docling


async def test_robots_disallow_skips_the_fetch():
    routes = {"https://arxiv.org/robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /html\n")}
    seen: list[str] = []
    client, fetcher, _ = fetcher_for(routes, seen)
    async with client:
        assert await fetcher.fetch(Target(arxiv="1706.03762")) is None
    assert "https://arxiv.org/html/1706.03762" not in seen


async def test_a_pdf_that_docling_cannot_read_is_no_full_text():
    routes = {"https://real.org/p.pdf": httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-")}
    client, fetcher, _ = fetcher_for(routes, [])

    def broken(path):
        raise RuntimeError("corrupt xref table")

    fetcher._convert = broken  # an untrusted PDF may crash the converter
    async with client:
        assert await fetcher.fetch(Target(pdf_urls=("https://real.org/p.pdf",))) is None


async def test_nothing_available_gives_none():
    client, fetcher, _ = fetcher_for({}, [])
    async with client:
        assert await fetcher.fetch(Target()) is None


@pytest.mark.vcr
async def test_live_arxiv_html_and_jats(polite):
    fetcher = FullTextFetcher(polite, Robots(polite))
    html_full = await fetcher.fetch(Target(arxiv="1706.03762"))
    assert html_full.source == "arxiv_html" and len(html_full.sections) >= 10
    assert any(s.path[-1].endswith("Scaled Dot-Product Attention") for s in html_full.sections)
    jats_full = await fetcher.fetch(Target(pmcid="PMC5334499"))
    assert jats_full.source == "jats" and jats_full.license and len(jats_full.sections) >= 3
```

The `polite` fixture lives in `backend/tests/connectors/conftest.py`. Move it (and the `vcr_config` with the `set-cookie` filter) to `backend/tests/conftest.py`, or import it into a `backend/tests/services/conftest.py`, so the live test can use it. Check the exact section title in the recorded page and pin a real value.

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --directory backend pytest tests/services/test_fulltext.py -q`
Expected: FAIL with `ModuleNotFoundError: researchly.services.fulltext`.

- [ ] **Step 3: Implement**

`backend/src/researchly/services/fulltext.py`:

```python
"""Full text for one paper, best source first (spec §9.1): arXiv HTML, Europe PMC JATS, then a PDF through Docling."""

import asyncio
import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

from researchly.connectors.base import ConnectorError
from researchly.connectors.http import Fetched, PoliteClient
from researchly.connectors.robots import Robots
from researchly.ingest.arxiv_html import parse_arxiv_html
from researchly.ingest.document import FullText
from researchly.ingest.jats import parse_jats
from researchly.ingest.pdf import convert_pdf, markdown_sections

log = logging.getLogger(__name__)

HTML_TYPES = frozenset({"text/html"})
XML_TYPES = frozenset({"application/xml", "text/xml"})
PDF_TYPES = frozenset({"application/pdf", "application/octet-stream"})  # some repositories send octet-stream
MAX_HTML = 5_000_000  # spec §19.3
MAX_PDF = 50_000_000
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"


@dataclass(frozen=True)
class Target:
    arxiv: str | None = None
    pmcid: str | None = None
    pdf_urls: tuple[str, ...] = ()


def _markdown(path: Path) -> str:
    return convert_pdf(path).markdown


class FullTextFetcher:
    def __init__(self, client: PoliteClient, robots: Robots, convert: Callable[[Path], str] = _markdown) -> None:
        self._client, self._robots, self._convert = client, robots, convert

    async def fetch(self, target: Target) -> FullText | None:
        if target.arxiv and (full := await self._arxiv_html(target.arxiv)):
            return full
        if target.pmcid and (full := await self._jats(target.pmcid)):
            return full
        for url in target.pdf_urls:
            if full := await self._pdf(url):
                return full
        return None

    async def _get(self, url: str, **kwargs) -> Fetched | None:
        try:
            fetched = await self._client.get(url, **kwargs)
        except ConnectorError as exc:  # outage, quota, too big, a landing page instead of a paper
            log.info("full text skipped: %s", exc)
            return None
        return fetched if fetched.status == 200 else None

    async def _arxiv_html(self, arxiv_id: str) -> FullText | None:
        url = f"https://arxiv.org/html/{quote(arxiv_id, safe='/')}"  # old-style ids keep their slash
        if not await self._robots.allowed(url):
            return None
        fetched = await self._get(url, source="arxiv.org", accept=HTML_TYPES, max_bytes=MAX_HTML)
        return parse_arxiv_html(fetched.body) if fetched else None

    async def _jats(self, pmcid: str) -> FullText | None:
        url = f"{EPMC}/{quote(pmcid, safe='')}/fullTextXML"  # a REST API: not crawled, no robots.txt
        fetched = await self._get(url, source="europepmc", accept=XML_TYPES, max_bytes=MAX_HTML)
        return parse_jats(fetched.body) if fetched else None

    async def _pdf(self, url: str) -> FullText | None:
        host = urlsplit(url).hostname or ""
        if not host or not await self._robots.allowed(url):
            return None
        fetched = await self._get(url, source=host, accept=PDF_TYPES, max_bytes=MAX_PDF)
        if fetched is None or b"%PDF-" not in fetched.body[:1024]:
            return None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper.pdf"
            path.write_bytes(fetched.body)
            try:
                markdown = await asyncio.to_thread(self._convert, path)
            except Exception:  # an untrusted PDF may crash the converter: no full text, never a failed ingest
                log.warning("PDF conversion failed for %s", url, exc_info=True)
                return None
        sections = markdown_sections(markdown)
        return FullText("pdf", sections) if sections else None
```

Notes:
- **Breaker sources:** pass the host (`arxiv.org`, the PDF host) as `source`, so one publisher's outage never opens another's breaker.
- **`robots.allowed`** also feeds Crawl-delay into the client.
- **Pyright:** if it complains about `**kwargs` in `_get`, spell out the keyword parameters (`source`, `accept`, `max_bytes`).

- [ ] **Step 4: Record the live cassette, then replay**

Record once, keyless (arXiv HTML waits for the 15 s crawl delay):
`uv run --directory backend pytest tests/services/test_fulltext.py -k live --record-mode=once -q`

Then:
- Re-run without the flag: `uv run --directory backend pytest tests/services -q`. Expected: PASS.
- Check the cassette as the `add-scholarly-connector` skill describes. It must have no `mailto`, `cookie`, `authorization`, `api_key`, `token` or email address, and only 200 or deliberate 404 responses (a robots.txt 404 is fine). The arXiv page is about 190 KB and the JATS about 75 KB; that is acceptable.

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run --directory backend ruff check && uv run --directory backend ruff format --check && uv run --directory backend pyright
git add backend/src/researchly/services/fulltext.py backend/tests/services backend/tests/conftest.py backend/tests/connectors/conftest.py
git commit -m "feat: full-text fetcher with arXiv HTML, JATS and PDF tiers"
```

---

### Task 8: Ingestion service (sections, chunks, vectors)

**Files:**
- Create: `backend/src/researchly/services/ingest.py`
- Test: `backend/tests/services/test_ingest.py`

**Interfaces:**
- Consumes:
  - `Target` and `FullTextFetcher.fetch` (Task 7).
  - `chunk_text` (Task 6).
  - `Embedder.model_id`, `Embedder.documents` and `Embedder.count_tokens` (Task 6).
  - The `Paper`, `PaperId`, `PaperSection`, `Chunk`, `ChunkEmbedding` and `PaperEmbedding` models (Task 3).
- Produces (`researchly.services.ingest`):
  - `class TextEmbedder(Protocol)`, with `model_id: str`, `def documents(self, texts: Sequence[str]) -> np.ndarray` and `def count_tokens(self, text: str) -> int`.
  - `class Fetcher(Protocol)`, with `async def fetch(self, target: Target) -> FullText | None`.
  - `async def target_for(session: AsyncSession, paper_id: uuid.UUID) -> Target`.
  - `async def ingest_paper(session: AsyncSession, paper_id: uuid.UUID, *, fetcher: Fetcher, embedder: TextEmbedder) -> str`. It returns the `fulltext_source` written, or `"none"`.
  - `async def embed_papers(session: AsyncSession, paper_ids: Sequence[uuid.UUID], *, embedder: TextEmbedder) -> int`. It returns how many papers it embedded.

- [ ] **Step 1: Write the failing tests** (`backend/tests/services/test_ingest.py`)

```python
import hashlib
import uuid

import numpy as np
import pytest
from sqlalchemy import func, select

from researchly.db import migrate
from researchly.db.engine import make_engine, make_sessionmaker
from researchly.db.models import EMBED_DIM, Chunk, ChunkEmbedding, Paper, PaperEmbedding, PaperId, PaperSection
from researchly.ingest.document import FullText, Section
from researchly.services.ingest import embed_papers, ingest_paper, target_for

pytestmark = [pytest.mark.db, pytest.mark.anyio]


class FakeEmbedder:
    model_id = "fake"

    def __init__(self):
        self.seen: list[str] = []

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    def documents(self, texts):
        self.seen.extend(texts)
        out = []
        for t in texts:
            seed = int.from_bytes(hashlib.sha256(t.encode()).digest()[:4], "little")
            v = np.random.default_rng(seed).standard_normal(EMBED_DIM).astype(np.float32)
            out.append(v / np.linalg.norm(v))
        return np.array(out)


class StubFetcher:
    def __init__(self, full):
        self.full, self.targets = full, []

    async def fetch(self, target):
        self.targets.append(target)
        return self.full


FULL = FullText(
    "arxiv_html",
    [
        Section(("Abstract",), "We propose the Transformer."),
        Section(("3 Model Architecture", "3.2 Attention"), " ".join(["Attention maps queries to outputs."] * 200)),
    ],
)


@pytest.fixture
async def sm(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


async def paper(session, **kw) -> uuid.UUID:
    pid = uuid.uuid4()
    session.add(Paper(id=pid, title="Attention Is All You Need", title_norm="attention is all you need", **kw))
    await session.flush()
    return pid


async def count(session, model):
    return await session.scalar(select(func.count()).select_from(model))


async def test_target_uses_ids_oa_url_and_the_arxiv_pdf(sm):
    async with sm() as s:
        pid = await paper(s, oa_url="https://arxiv.org/pdf/1706.03762")
        s.add_all([PaperId(paper_id=pid, scheme="arxiv", value="1706.03762"), PaperId(paper_id=pid, scheme="pmcid", value="PMC1")])
        await s.flush()
        target = await target_for(s, pid)
    assert target.arxiv == "1706.03762" and target.pmcid == "PMC1"
    assert target.pdf_urls == ("https://arxiv.org/pdf/1706.03762",)  # the oa_url and the arXiv PDF deduplicated


async def test_ingest_stores_sections_chunks_and_vectors(sm):
    embedder = FakeEmbedder()
    async with sm() as s:
        pid = await paper(s)
        assert await ingest_paper(s, pid, fetcher=StubFetcher(FULL), embedder=embedder) == "arxiv_html"
        await s.commit()
        assert await count(s, PaperSection) == 2
        chunks = (await s.scalars(select(Chunk).order_by(Chunk.ord))).all()
        assert len(chunks) >= 3 and [c.ord for c in chunks] == list(range(len(chunks)))
        assert all(c.tokens <= 512 for c in chunks)
        assert await count(s, ChunkEmbedding) == len(chunks)
        assert embedder.seen[1].startswith("3 Model Architecture > 3.2 Attention\n")  # path prepended for embedding
        row = await s.get(Paper, pid)
        assert row.fulltext_source == "arxiv_html" and row.fulltext_at is not None


async def test_reingest_replaces_rather_than_duplicates(sm):
    async with sm() as s:
        pid = await paper(s)
        await ingest_paper(s, pid, fetcher=StubFetcher(FULL), embedder=FakeEmbedder())
        first = await count(s, Chunk)
        await ingest_paper(s, pid, fetcher=StubFetcher(FULL), embedder=FakeEmbedder())
        assert await count(s, Chunk) == first and await count(s, PaperSection) == 2


async def test_no_full_text_is_recorded_as_none(sm):
    async with sm() as s:
        pid = await paper(s)
        assert await ingest_paper(s, pid, fetcher=StubFetcher(None), embedder=FakeEmbedder()) == "none"
        assert (await s.get(Paper, pid)).fulltext_source == "none" and await count(s, Chunk) == 0


async def test_jats_licence_fills_an_empty_licence_only(sm):
    full = FullText("jats", FULL.sections, license="https://creativecommons.org/licenses/by/4.0/")
    async with sm() as s:
        empty = await paper(s)
        known = await paper(s, license="cc-by-nc")
        for pid in (empty, known):
            await ingest_paper(s, pid, fetcher=StubFetcher(full), embedder=FakeEmbedder())
        assert (await s.get(Paper, empty)).license == "https://creativecommons.org/licenses/by/4.0/"
        assert (await s.get(Paper, known)).license == "cc-by-nc"


async def test_embed_papers_embeds_title_and_abstract_once(sm):
    embedder = FakeEmbedder()
    async with sm() as s:
        a = await paper(s, abstract="We propose the Transformer.")
        b = await paper(s)
        assert await embed_papers(s, [a, b], embedder=embedder) == 2
        assert await embed_papers(s, [a, b], embedder=embedder) == 0
        assert await count(s, PaperEmbedding) == 2
    assert embedder.seen[0] == "Attention Is All You Need\nWe propose the Transformer."
    assert embedder.seen[1] == "Attention Is All You Need"
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --directory backend pytest tests/services/test_ingest.py -m db -q`
Expected: FAIL with `ModuleNotFoundError: researchly.services.ingest`.

- [ ] **Step 3: Implement**

`backend/src/researchly/services/ingest.py`:

```python
"""Paper → sections, chunks and vectors (spec §9.1–9.2). The caller commits; step 3 runs this as a procrastinate job."""

import asyncio
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

import numpy as np
from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from researchly.db.models import Chunk, ChunkEmbedding, Paper, PaperEmbedding, PaperId, PaperSection
from researchly.ingest.chunking import chunk_text
from researchly.ingest.document import FullText
from researchly.services.fulltext import Target


class TextEmbedder(Protocol):
    model_id: str

    def documents(self, texts: Sequence[str]) -> np.ndarray: ...

    def count_tokens(self, text: str) -> int: ...


class Fetcher(Protocol):
    async def fetch(self, target: Target) -> FullText | None: ...


async def target_for(session: AsyncSession, paper_id: uuid.UUID) -> Target:
    paper = await session.get_one(Paper, paper_id)
    ids = dict((await session.execute(select(PaperId.scheme, PaperId.value).where(PaperId.paper_id == paper_id))).all())
    arxiv_pdf = f"https://arxiv.org/pdf/{ids['arxiv']}" if "arxiv" in ids else None
    pdfs = tuple(dict.fromkeys(u for u in (paper.oa_url, arxiv_pdf) if u))
    return Target(arxiv=ids.get("arxiv"), pmcid=ids.get("pmcid"), pdf_urls=pdfs)


async def ingest_paper(
    session: AsyncSession, paper_id: uuid.UUID, *, fetcher: Fetcher, embedder: TextEmbedder
) -> str:
    full = await fetcher.fetch(await target_for(session, paper_id))
    await session.execute(delete(PaperSection).where(PaperSection.paper_id == paper_id))  # cascades to chunks, vectors
    paper = await session.get_one(Paper, paper_id)
    paper.fulltext_at = datetime.now(UTC)
    paper.fulltext_source = full.source if full else "none"
    if full is None:
        await session.flush()
        return "none"
    if full.license and paper.license is None:
        paper.license, paper.license_source = full.license, full.source
    chunks: list[Chunk] = []
    texts: list[str] = []
    for ord_, sec in enumerate(full.sections):
        section = PaperSection(id=uuid.uuid4(), paper_id=paper_id, ord=ord_, path=list(sec.path), text=sec.text)
        session.add(section)
        for piece in await asyncio.to_thread(chunk_text, sec.text, embedder.count_tokens):
            chunks.append(
                Chunk(id=uuid.uuid4(), paper_id=paper_id, section_id=section.id, ord=len(chunks), text=piece.text, tokens=piece.tokens)
            )
            texts.append(f"{' > '.join(sec.path)}\n{piece.text}")  # the heading path is cheap context for the vector
    session.add_all(chunks)
    if texts:
        vectors = await asyncio.to_thread(embedder.documents, texts)
        session.add_all(
            ChunkEmbedding(chunk_id=c.id, model_id=embedder.model_id, vec=v) for c, v in zip(chunks, vectors, strict=True)
        )
    await session.flush()
    return full.source


async def embed_papers(session: AsyncSession, paper_ids: Sequence[uuid.UUID], *, embedder: TextEmbedder) -> int:
    """Title + abstract vectors for every paper (spec §9.2); papers that already have one for this model are skipped."""
    done = exists().where(PaperEmbedding.paper_id == Paper.id, PaperEmbedding.model_id == embedder.model_id)
    rows = (await session.execute(select(Paper.id, Paper.title, Paper.abstract).where(Paper.id.in_(paper_ids), ~done))).all()
    if not rows:
        return 0
    vectors = await asyncio.to_thread(embedder.documents, [f"{t}\n{a}" if a else t for _, t, a in rows])
    session.add_all(
        PaperEmbedding(paper_id=pid, model_id=embedder.model_id, vec=v) for (pid, _, _), v in zip(rows, vectors, strict=True)
    )
    await session.flush()
    return len(rows)
```

If the same paper has two values for one scheme, `dict(...)` keeps the last. That is acceptable: any of them reaches the same paper.

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `uv run --directory backend pytest -m db -q` and `uv run --directory backend pytest -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run --directory backend ruff check && uv run --directory backend ruff format --check && uv run --directory backend pyright
git add backend/src/researchly/services/ingest.py backend/tests/services/test_ingest.py
git commit -m "feat: ingest papers into sections, chunks and vectors"
```

---

### Task 9: Hybrid retrieval (BM25 + dense, RRF, rerank)

**Files:**
- Create: `backend/src/researchly/services/retrieval.py`
- Test: `backend/tests/services/test_retrieval.py`

**Interfaces:**
- Consumes: the Task 3 schema (including the engine-specific BM25 objects) and `EMBED_DIM`.
- Produces (`researchly.services.retrieval`):
  - `RRF_K = 60` and `CANDIDATES = 100`.
  - `@dataclass(frozen=True) class Hit: id: uuid.UUID; score: float; lexical_rank: int | None; dense_rank: int | None`.
  - `async def bm25_engine(session: AsyncSession) -> str`.
  - `async def search_papers(session, query: str, vector: np.ndarray, *, model_id: str, limit: int = 50, within: Sequence[uuid.UUID] | None = None) -> list[Hit]`.
  - `async def search_chunks(session, query: str, vector: np.ndarray, *, model_id: str, limit: int = 50, paper_ids: Sequence[uuid.UUID] | None = None) -> list[Hit]`.
  - `class Scorer(Protocol)`, with `def scores(self, query: str, passages: Sequence[str]) -> list[float]`. `embeddings.Reranker` satisfies it.
  - `async def rerank(reranker: Scorer, query: str, items: Sequence[tuple[uuid.UUID, str]]) -> list[tuple[uuid.UUID, float]]`, sorted best first.

- [ ] **Step 1: Write the failing tests** (`backend/tests/services/test_retrieval.py`)

```python
import uuid

import numpy as np
import pytest

from researchly.db import migrate
from researchly.db.engine import make_engine, make_sessionmaker
from researchly.db.models import EMBED_DIM, Chunk, ChunkEmbedding, Paper, PaperEmbedding, PaperSection
from researchly.services.retrieval import bm25_engine, rerank, search_chunks, search_papers

pytestmark = [pytest.mark.db, pytest.mark.anyio]


def unit(i: int) -> np.ndarray:
    v = np.zeros(EMBED_DIM, dtype=np.float32)
    v[i] = 1.0
    return v


def near(i: int, j: int, w: float) -> np.ndarray:  # mostly axis i, a little of axis j
    v = unit(i) + w * unit(j)
    return v / np.linalg.norm(v)


@pytest.fixture
async def sm(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


PAPERS = {
    "rag": ("Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks", "We combine retrieval with generation.", unit(0)),
    "dpr": ("Dense Passage Retrieval for Open-Domain Question Answering", "Dense retrieval of passages.", near(0, 1, 0.3)),
    "cnn": ("ImageNet Classification with Deep Convolutional Neural Networks", "Convolutional nets for images.", unit(5)),
    "bio": ("CRISPR-Cas9 genome editing in human cells", "Gene editing with guide RNA.", unit(7)),
}


async def seed(s) -> dict[str, uuid.UUID]:
    ids = {}
    for key, (title, abstract, vec) in PAPERS.items():
        pid = ids[key] = uuid.uuid4()
        s.add(Paper(id=pid, title=title, title_norm=title.lower(), abstract=abstract))
        s.add(PaperEmbedding(paper_id=pid, model_id="m", vec=vec))
        s.add(PaperEmbedding(paper_id=pid, model_id="other", vec=unit(9)))
    await s.commit()
    return ids


async def test_engine_is_read_from_the_capability(sm):
    async with sm() as s:
        assert await bm25_engine(s) in {"tsvector", "pg_textsearch"}


async def test_natural_language_query_fuses_lexical_and_dense(sm):
    async with sm() as s:
        ids = await seed(s)
        hits = await search_papers(s, "how does retrieval augmented generation reduce hallucination", unit(0), model_id="m")
    by_id = {h.id: h for h in hits}
    assert hits[0].id == ids["rag"]  # first in both lists
    assert by_id[ids["rag"]].lexical_rank == 1 and by_id[ids["rag"]].dense_rank == 1
    assert ids["dpr"] in by_id  # lexical "retrieval" and second densely
    assert by_id[ids["rag"]].score == pytest.approx(2 / 61)


async def test_dense_only_and_lexical_only_matches_both_surface(sm):
    async with sm() as s:
        ids = await seed(s)
        hits = await search_papers(s, "genome editing", unit(5), model_id="m", limit=10)
    by_id = {h.id: h for h in hits}
    assert by_id[ids["bio"]].lexical_rank == 1 and by_id[ids["bio"]].dense_rank != 1
    assert by_id[ids["cnn"]].dense_rank == 1 and by_id[ids["cnn"]].lexical_rank is None


async def test_scope_and_model_filters(sm):
    async with sm() as s:
        ids = await seed(s)
        hits = await search_papers(s, "retrieval", unit(0), model_id="m", within=[ids["dpr"], ids["cnn"]])
        assert {h.id for h in hits} <= {ids["dpr"], ids["cnn"]} and hits[0].id == ids["dpr"]
        other = await search_papers(s, "zzzz", unit(9), model_id="other", limit=10)
        assert len(other) == 4 and all(h.lexical_rank is None for h in other)


async def test_chunk_search_and_paper_scope(sm):
    async with sm() as s:
        ids = await seed(s)
        chunk_ids = {}
        for n, (key, text, vec) in enumerate(
            [("rag", "The retriever fetches passages from Wikipedia.", unit(0)), ("cnn", "Dropout reduces overfitting.", unit(5))]
        ):
            sid, cid = uuid.uuid4(), uuid.uuid4()
            s.add(PaperSection(id=sid, paper_id=ids[key], ord=0, path=["Method"], text=text))
            s.add(Chunk(id=cid, paper_id=ids[key], section_id=sid, ord=n, text=text, tokens=6))
            s.add(ChunkEmbedding(chunk_id=cid, model_id="m", vec=vec))
            chunk_ids[key] = cid
        await s.commit()
        hits = await search_chunks(s, "passages from wikipedia", unit(0), model_id="m")
        assert hits[0].id == chunk_ids["rag"]
        scoped = await search_chunks(s, "passages from wikipedia", unit(0), model_id="m", paper_ids=[ids["cnn"]])
        assert [h.id for h in scoped] == [chunk_ids["cnn"]]


async def test_rerank_orders_by_cross_encoder_score():
    class Fixed:
        def scores(self, query, passages):
            return [len(p) for p in passages]

    a, b = uuid.uuid4(), uuid.uuid4()
    assert await rerank(Fixed(), "q", [(a, "short"), (b, "a longer passage")]) == [(b, 16.0), (a, 5.0)]
```

The tests must hold on both engines: the default `-m db` run uses `tsvector`, and the `backend-bm25` CI job uses `pg_textsearch`. If an exact rank differs between the engines for a reason the research documents (OR semantics, IDF), loosen only that assertion, and say why in a comment.

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --directory backend pytest tests/services/test_retrieval.py -m db -q`
Expected: FAIL with `ModuleNotFoundError: researchly.services.retrieval`.

- [ ] **Step 3: Implement**

`backend/src/researchly/services/retrieval.py`:

```python
"""Hybrid search (spec §9.3): BM25 + dense in one statement, fused by RRF (k = 60); cross-encoder rerank after.

BM25 is pg_textsearch where the server has it (Docker) and a tsvector fallback otherwise (native); migration 0003
built the matching objects. Research: docs/research/2026-11-04-step2b-search.md §3–5.
"""

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from researchly.db.models import EMBED_DIM

RRF_K = 60
CANDIDATES = 100  # per list, before fusion


@dataclass(frozen=True)
class Hit:
    id: uuid.UUID
    score: float
    lexical_rank: int | None
    dense_rank: int | None


@dataclass(frozen=True)
class _Kind:
    table: str
    doc: str  # must repeat the BM25 index expression exactly (pg_textsearch matches on it)
    index: str
    scope: str  # column the caller's id filter applies to
    vectors: str
    vector_key: str


_PAPERS = _Kind("paper", "(title || ' ' || coalesce(abstract, ''))", "ix_paper_bm25", "id", "paper_embedding", "paper_id")
_CHUNKS = _Kind("chunk", "text", "ix_chunk_bm25", "paper_id", "chunk_embedding", "chunk_id")


async def bm25_engine(session: AsyncSession) -> str:
    engine = await session.scalar(text("SELECT value #>> '{}' FROM capability WHERE key = 'bm25_engine'"))
    return engine or "tsvector"


def _lexical(kind: _Kind, engine: str, scoped: bool) -> str:
    scope = f"AND {kind.scope} = ANY(:scope)" if scoped else ""
    if engine == "pg_textsearch":
        score = f"{kind.doc} <@> to_bm25query(:q, '{kind.index}')"  # negative; lower is better; non-matches score 0
        return (
            f"SELECT id, row_number() OVER (ORDER BY s) AS r FROM ("
            f"SELECT id, {score} AS s FROM {kind.table} WHERE TRUE {scope} ORDER BY {score} LIMIT :n) b WHERE s < 0"
        )
    # websearch_to_tsquery ANDs every word (0 hits on questions); OR the stemmed lexemes instead (research §3)
    query = "to_tsquery('english', replace(plainto_tsquery('english', :q)::text, ' & ', ' | '))"
    return (
        f"SELECT id, row_number() OVER (ORDER BY s DESC) AS r FROM ("
        f"SELECT id, ts_rank_cd(bm25_tsv, query, 32) AS s FROM {kind.table}, {query} AS query "
        f"WHERE bm25_tsv @@ query {scope} ORDER BY s DESC LIMIT :n) b"
    )


def _dense(kind: _Kind, scoped: bool) -> str:
    join = scope = ""
    if scoped:
        if kind is _CHUNKS:
            join, scope = "JOIN chunk c ON c.id = e.chunk_id", "AND c.paper_id = ANY(:scope)"
        else:
            scope = "AND e.paper_id = ANY(:scope)"
    return (
        f"SELECT id, row_number() OVER (ORDER BY d) AS r FROM ("  # re-sort: relaxed_order may return slightly out of order
        f"SELECT e.{kind.vector_key} AS id, e.vec <=> :v AS d FROM {kind.vectors} e {join} "
        f"WHERE e.model_id = :model {scope} ORDER BY e.vec <=> :v LIMIT :n) x"
    )


async def _search(
    session: AsyncSession, kind: _Kind, query: str, vector: np.ndarray, model_id: str, limit: int, scope: Sequence[uuid.UUID] | None
) -> list[Hit]:
    scoped = scope is not None
    sql = (
        f"WITH lex AS MATERIALIZED ({_lexical(kind, await bm25_engine(session), scoped)}), "
        f"dense AS MATERIALIZED ({_dense(kind, scoped)}) "
        f"SELECT id, coalesce(1.0 / ({RRF_K} + lex.r), 0) + coalesce(1.0 / ({RRF_K} + dense.r), 0) AS score, "
        f"lex.r AS lexical_rank, dense.r AS dense_rank "
        f"FROM lex FULL JOIN dense USING (id) ORDER BY score DESC, id LIMIT :limit"
    )
    stmt = text(sql).bindparams(bindparam("v", type_=HALFVEC(EMBED_DIM)))
    params: dict[str, object] = {"q": query, "v": vector, "model": model_id, "n": CANDIDATES, "limit": limit}
    if scoped:
        stmt = stmt.bindparams(bindparam("scope", type_=ARRAY(UUID(as_uuid=True))))
        params["scope"] = list(scope)
    await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))  # filters would starve plain HNSW
    await session.execute(text("SET LOCAL hnsw.ef_search = 100"))
    rows = (await session.execute(stmt, params)).all()
    return [Hit(r.id, float(r.score), r.lexical_rank, r.dense_rank) for r in rows]


async def search_papers(
    session: AsyncSession,
    query: str,
    vector: np.ndarray,
    *,
    model_id: str,
    limit: int = 50,
    within: Sequence[uuid.UUID] | None = None,
) -> list[Hit]:
    """Title + abstract search over stored papers."""
    return await _search(session, _PAPERS, query, vector, model_id, limit, within)


async def search_chunks(
    session: AsyncSession,
    query: str,
    vector: np.ndarray,
    *,
    model_id: str,
    limit: int = 50,
    paper_ids: Sequence[uuid.UUID] | None = None,
) -> list[Hit]:
    """Full-text passage search, optionally inside a set of papers."""
    return await _search(session, _CHUNKS, query, vector, model_id, limit, paper_ids)


class Scorer(Protocol):
    def scores(self, query: str, passages: Sequence[str]) -> list[float]: ...


async def rerank(reranker: Scorer, query: str, items: Sequence[tuple[uuid.UUID, str]]) -> list[tuple[uuid.UUID, float]]:
    """Cross-encoder scores, best first. Depth and truncation are the caller's (set at step 4, ADR 0007)."""
    scores = await asyncio.to_thread(reranker.scores, query, [t for _, t in items])
    return sorted(((i, float(s)) for (i, _), s in zip(items, scores, strict=True)), key=lambda p: p[1], reverse=True)
```

Notes:
- **Statements:** the SQL is assembled only from module constants. User text goes in bound parameters (`:q`, `:v`, `:scope`).
- **`SET LOCAL`:** it applies to the current transaction, which the session autobegins.
- **`ORDER BY score DESC, id`:** this keeps ties deterministic.

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `uv run --directory backend pytest -m db -q` and `uv run --directory backend pytest -q`
Expected: PASS. If Docker allows it, also run `-m db` against the repo's `infra/postgres` image with `RESEARCHLY_EXPECT_BM25=pg_textsearch`, as in Task 3. CI runs it in `backend-bm25`.

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run --directory backend ruff check && uv run --directory backend ruff format --check && uv run --directory backend pyright
git add backend/src/researchly/services/retrieval.py backend/tests/services/test_retrieval.py
git commit -m "feat: hybrid BM25 and dense retrieval with RRF and rerank"
```

---

### Task 10: ADR, spec, guide and progress

**Files:**
- Create: `docs/adr/0009-fulltext-and-hybrid-retrieval.md` (from `docs/adr/0000-template.md`)
- Modify: `docs/superpowers/specs/2026-10-28-researchly-design.md` (§8.2 limits table, §9.1, §9.2, §9.3, §16, §19.3)
- Modify: `AGENTS.md` (gotchas), `docs/progress.md`, this plan's Deferred list
- Test: `uv run --no-project --python 3.12 scripts/repo_checks.py`

- [ ] **Step 1: Write ADR 0009.** Use the format of ADR 0008: Context, Decision, Consequences and Evidence. Cover:
  - **Tiers:** the order, and the LaTeX tier dropped because of robots.txt.
  - **PoliteClient:** the content-type gate with PDF magic bytes, per-hop pacing, Crawl-delay, and robots.txt rules (RFC 9309: 4xx allow, 5xx disallow, 24 h, over 60 s skip).
  - **Chunking:** semchunk at 512 with 15% overlap, using the Qwen3 tokenizer.
  - **Schema:** `halfvec(1024)` with HNSW cosine, iterative scan and `ef_search` 100.
  - **BM25:** pg_textsearch vs tsvector by `capability.bm25_engine`; the objects live in the migration only; the `backend-bm25` CI job.
  - **Fusion:** RRF in one statement.
  - **SPECTER2:** deferred, with the reason (R-search §1).
  - **Evidence:** cite both research docs, and put the CI run number in once it is green.
- [ ] **Step 2: Edit the spec:**
  - §8.2 limits table: add an `arxiv.org (HTML/PDF) | 1 per 15 s (robots Crawl-delay) | 1` row.
  - §9.1: the tiers are arXiv HTML, then Europe PMC JATS for open-access papers with a PMCID, then the PDF via Docling. LaTeX source is dropped (`Disallow: /e-print`).
  - §9.2: chunking uses semchunk at `chunk_size` 512 with 15% overlap, measured in Qwen3 tokens (mean about 350). SPECTER2 is `specter2_base` plus the proximity adapter, exported once to ONNX, and is deferred to step 5 (hosting decision).
  - §9.3: the `'english'` config on both engines; the OR-ed `plainto_tsquery` fallback; `relaxed_order` with `ef_search` 100.
  - §16:
    - `paper.fulltext_source` and `paper.fulltext_at`;
    - `paper_section.path text[]`;
    - the `paper_embedding` and `chunk_embedding` primary keys;
    - the engine-specific `bm25_tsv` and `ix_*_bm25`.
  - §19.3: the per-tier content-type allowlist and the `%PDF-` sniff.
- [ ] **Step 3: Update the plan's Deferred list and AGENTS.md:**
  - Append to this plan's Deferred list anything added while executing.
  - Add an AGENTS.md "Gotchas" bullet: BM25 objects are engine-specific, have `bm25` in their names, live only in migrations, and are skipped by `test_models_match_migrations`. Name the `backend-bm25` CI job, which runs the DB tests with pg_textsearch.
  - Add a bullet: full-text fetches pass the host as the breaker `source`.
- [ ] **Step 4: Append the step 2b entry to `docs/progress.md`**, in the format of the step 2a entry: what was done, test counts, rulings, deferred items and next steps. Then run the repo checks.
- [ ] **Step 5: Commit**

```bash
uv run --no-project --python 3.12 scripts/repo_checks.py
git add docs AGENTS.md
git commit -m "docs: step 2b ADR, spec updates and progress"
```

---

## Deferred (recorded, not built here)

- **SPECTER2** (`specter2_base` plus the proximity adapter, an ONNX export we host, `paper_specter halfvec(768)`): step 5 graph work. The user decides where the export is hosted (a GitHub release asset or a Hugging Face repo).
- **GROBID** (header, references, TEI tables) and the local sidecar client: step 2c and step 4.
- **Rich document tables:** `paper_figure`, `paper_table`, `paper_equation`, `paper_refs`, and raw PDF storage for the reader: step 4.
- **Ingestion as a background job:** procrastinate, progress events, batching and concurrency limits: step 3 (run manager).
- **Contextual-retrieval prefixes** (`chunk.context_prefix`, opt-in per collection): step 4.
- **Re-embedding:** on an embedder change, and for stale `paper_embedding` rows after a title or abstract update: step 9 maintenance, or when model switching ships.
- **Multilingual BM25** (a partial index per language): when non-English corpora matter.
- **Publisher blocks:** a 403 or 429 is retried like any other outage. Revisit with a fail-fast path if ingest latency suffers.
- **Unknown hosts** share the `default` limit (one at a time): give each host its own default if PDF batches are slow.
- **arXiv HTML `ltx_ERROR` count** as a quality signal: step 4.
- **Rerank depth and passage truncation:** step 4 benchmark (ADR 0007).
- **Carried from step 2a:** cached OpenAlex searches count against the daily budget (step 3).
- **robots.txt on redirects:** it is checked only for the first PDF host, not for redirect hops (PoliteClient follows redirects internally).
- **Ingest transaction:** `ingest_paper` fetches inside the caller's open transaction. The step 3 job wrapper should split read, fetch and write.
- **Scoped lexical search on pg_textsearch:** it filters after the index top-`CANDIDATES`, so a narrow `within`/`paper_ids` scope over a large library can starve the lexical list (the dense side uses iterative scan). Revisit with a real library at step 4.
