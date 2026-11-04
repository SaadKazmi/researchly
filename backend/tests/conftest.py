import asyncio
import os
import uuid
from collections.abc import Iterator

# Before any test module imports langchain_litellm: litellm fetches its cost map over the network at import.
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

import keyring
import pytest
from keyring.backend import KeyringBackend
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


class MemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        from keyring.errors import PasswordDeleteError

        if self.store.pop((service, username), None) is None:
            raise PasswordDeleteError(username)


@pytest.fixture(autouse=True)
def _memory_keyring(tmp_path, monkeypatch):
    """Tests never touch the real OS keychain or the real data dir."""
    monkeypatch.setenv("RESEARCHLY_DATA_DIR", str(tmp_path / "data"))
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture(autouse=True)
def _no_built_frontend(tmp_path, monkeypatch):
    """Tests never depend on whether `pnpm -C frontend build` ran in this checkout."""
    monkeypatch.setattr("researchly.app.WEB_DIR", tmp_path / "no-web")


@pytest.fixture
def anyio_backend():
    # psycopg's async driver cannot run on Windows' default Proactor loop.
    return ("asyncio", {"loop_factory": asyncio.SelectorEventLoop})


@pytest.fixture(scope="session")
def pg_admin_url() -> Iterator[str]:
    url = os.environ.get("RESEARCHLY_TEST_DATABASE_URL")
    if url:
        yield url
        return
    try:
        from testcontainers.community.postgres import PostgresContainer

        container = PostgresContainer("pgvector/pgvector:pg18", driver="psycopg")
        container.start()
    except Exception as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"no Postgres: set RESEARCHLY_TEST_DATABASE_URL or start Docker ({exc})")
    try:
        yield container.get_connection_url()
    finally:
        container.stop()


@pytest.fixture
def db_url(pg_admin_url: str) -> Iterator[str]:
    """A fresh, empty database per test."""
    name = f"t_{uuid.uuid4().hex[:12]}"
    admin = create_engine(pg_admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(pg_admin_url).set(database=name)
    fresh = create_engine(url, isolation_level="AUTOCOMMIT")
    with fresh.connect() as conn:
        if conn.scalar(text("SELECT 1 FROM pg_available_extensions WHERE name = 'pg_textsearch'")):
            conn.execute(text("CREATE EXTENSION pg_textsearch"))  # provisioning does this in the product (spec §16.1)
    fresh.dispose()
    yield url.render_as_string(hide_password=False)
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


def _drop_set_cookie(response):
    """NCBI sets an `ncbi_sid` session cookie on every answer: never keep one in a cassette."""
    response["headers"] = {k: v for k, v in response["headers"].items() if k.lower() != "set-cookie"}
    return response


@pytest.fixture(scope="module")
def vcr_config():
    return {
        "before_record_response": _drop_set_cookie,
        # no record_mode here: pytest-recording defaults to "none" (a missing cassette fails the test) and a
        # pinned value would override --record-mode, so recording could never be asked for
        "filter_headers": ["authorization", "x-api-key", "cookie", "set-cookie", "user-agent"],
        "filter_query_parameters": ["api_key", "mailto", "email"],  # a mailto is the user's own address
        "decode_compressed_response": True,
        "match_on": ("method", "scheme", "host", "port", "path", "query", "body"),
    }


@pytest.fixture
async def polite(request):
    """A PoliteClient for cassette tests: instant in replay, real rate limits when recording live."""
    from researchly.connectors.http import PoliteClient

    recording = request.config.getoption("--record-mode") not in (None, "none")

    async def no_sleep(_: float) -> None:
        pass  # a recorded 429/5xx must never stall replay

    client = PoliteClient() if recording else PoliteClient(limits={"default": (0.0, 1)}, sleep=no_sleep)
    async with client:
        yield client
