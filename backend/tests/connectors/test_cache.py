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


async def test_put_stores_only_safe_headers_and_no_query_string(cache):
    store, _ = cache
    headers = {"etag": '"e"', "content-type": "text/plain", "set-cookie": "s=1", "content-encoding": "gzip"}
    fetched = Fetched(200, headers, b"x", "https://api.example.org/works?mailto=me@example.org&q=x#f")
    await store.put("k", fetched, timedelta(days=1))
    hit = await store.get("k")
    assert hit
    assert hit.fetched.headers == {"etag": '"e"', "content-type": "text/plain"}
    assert hit.fetched.url == "https://api.example.org/works"


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
