import httpx
import pytest
from sqlalchemy import create_engine, text

from researchly.app import create_app
from researchly.config import Settings
from researchly.connectors.http import HostLimit
from researchly.connectors.sidecar import SidecarClient
from researchly.db import migrate
from researchly.services.capabilities import probe_web_search, record_web_search, write_capability

pytestmark = pytest.mark.anyio

TOKEN = "t" * 43


def sidecar(handler) -> SidecarClient:
    return SidecarClient(
        "http://127.0.0.1:8766", source="web", transport=httpx.MockTransport(handler), limit=HostLimit(0.0, 1)
    )


def read_capability(url: str, key: str):
    engine = create_engine(url)
    with engine.connect() as conn:
        value = conn.scalar(text("SELECT value FROM capability WHERE key = :k"), {"k": key})
    engine.dispose()
    return value


async def test_no_url_means_off_and_not_configured():
    assert await probe_web_search(None) == {"state": "off", "reason": "not configured"}


async def test_ready_once_healthz_answers():
    answers = iter([502, 502, 200])
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = sidecar(lambda r: httpx.Response(next(answers)))
    assert await probe_web_search(client, sleep=sleep) == {"state": "ready"}
    await client.aclose()
    assert sleeps == [2.0, 2.0]


@pytest.mark.parametrize(
    ("answer", "reason"),
    [(None, "unreachable: ConnectError"), (503, "unreachable: HTTP 503")],
)
async def test_gives_up_with_the_last_reason(answer, reason):
    def handler(request: httpx.Request) -> httpx.Response:
        if answer is None:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(answer)

    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = sidecar(handler)
    assert await probe_web_search(client, attempts=3, sleep=sleep) == {"state": "off", "reason": reason}
    await client.aclose()
    assert len(sleeps) == 2  # no sleep after the last attempt


async def test_test_apps_build_the_client_and_skip_the_probe():
    app = create_app(Settings(env="test", searxng_url="http://127.0.0.1:9"), token=TOKEN)
    async with app.router.lifespan_context(app):
        assert isinstance(app.state.searxng, SidecarClient)
        assert app.state.capability_probe is None


async def test_an_empty_url_builds_no_client():
    app = create_app(Settings(env="test", searxng_url=""), token=TOKEN)
    async with app.router.lifespan_context(app):
        assert app.state.searxng is None


async def test_a_database_failure_is_logged_not_raised(caplog):
    await record_web_search("postgresql+psycopg://nobody@127.0.0.1:1/none", None)
    assert "could not record the web search capability" in caplog.text


@pytest.mark.db
def test_write_capability_upserts(db_url):
    migrate.upgrade(db_url)
    write_capability(db_url, "web_search", {"state": "off", "reason": "unreachable: ConnectError"})
    write_capability(db_url, "web_search", {"state": "ready"})
    assert read_capability(db_url, "web_search") == {"state": "ready"}


@pytest.mark.db
async def test_record_web_search_writes_ready(db_url):
    migrate.upgrade(db_url)
    client = sidecar(lambda r: httpx.Response(200, text="OK"))
    await record_web_search(db_url, client)
    await client.aclose()
    assert read_capability(db_url, "web_search") == {"state": "ready"}


@pytest.mark.db
async def test_the_app_records_web_search_in_the_background(db_url):
    migrate.upgrade(db_url)
    app = create_app(Settings(env="production", database_url=db_url, searxng_url=None), token=TOKEN)
    async with app.router.lifespan_context(app):
        assert app.state.capability_probe is not None
        await app.state.capability_probe
    assert read_capability(db_url, "web_search") == {"state": "off", "reason": "not configured"}
