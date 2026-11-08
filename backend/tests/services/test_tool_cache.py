import pytest

from researchly.db import migrate
from researchly.db.engine import make_engine, make_sessionmaker
from researchly.services import tool_cache


def test_cache_keys_ignore_argument_order():
    a = tool_cache.cache_key("gather_evidence", {"paper": "p", "question": "q"})
    assert a == tool_cache.cache_key("gather_evidence", {"question": "q", "paper": "p"})
    assert a != tool_cache.cache_key("gather_evidence", {"paper": "p", "question": "other"})
    assert len(a) == 64


@pytest.mark.db
@pytest.mark.anyio
async def test_put_get_overwrite_and_expiry(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    sessions = make_sessionmaker(engine)
    async with sessions() as session:
        await tool_cache.put(session, "k", "t", {"v": 1})
        await tool_cache.put(session, "k", "t", {"v": 2})
        await tool_cache.put(session, "old", "t", {"v": 3}, ttl=0)
        await session.commit()
        assert await tool_cache.get(session, "k") == {"v": 2}
        assert await tool_cache.get(session, "old") is None  # expired
        assert await tool_cache.get(session, "missing") is None
    await engine.dispose()
