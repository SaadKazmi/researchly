"""FastAPI application factory."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from researchly.api import system
from researchly.config import Settings
from researchly.connectors.sidecar import SidecarClient
from researchly.db import provision
from researchly.db.engine import make_engine, make_sessionmaker
from researchly.runs.live import LiveRuntime
from researchly.runs.manager import RunManager
from researchly.security.keychain import ensure_local_token
from researchly.security.localhost_guard import LocalhostGuard
from researchly.security.redact import RedactingArgsFilter, RedactingFilter, register_secret
from researchly.services.capabilities import record_web_search
from researchly.spa import BUILD_HINT, WEB_DIR, SPAStaticFiles

logger = logging.getLogger(__name__)


def _redact_server_logs() -> None:
    """uvicorn logs request paths with their query string, so a `?token=` login link would reach the access log."""
    for name, kind in (("uvicorn.access", RedactingArgsFilter), ("uvicorn.error", RedactingFilter)):
        target = logging.getLogger(name)
        if not any(isinstance(f, kind) for f in target.filters):
            target.addFilter(kind())


def require_selector_loop(loop: asyncio.AbstractEventLoop) -> None:
    """psycopg's async driver (runs, checkpoints) cannot run on Windows' default Proactor loop."""
    proactor = getattr(asyncio, "ProactorEventLoop", None)  # Windows only
    if proactor is not None and isinstance(loop, proactor):
        raise RuntimeError(
            "Researchly needs a selector event loop: start uvicorn with --loop asyncio:SelectorEventLoop"
        )


@asynccontextmanager
async def run_manager(settings: Settings, searxng: SidecarClient | None) -> AsyncIterator[RunManager]:
    """The run manager on one psycopg pool shared by LangGraph's checkpointer and store (ADR 0011)."""
    engine = make_engine(settings.database_url)
    sessions = make_sessionmaker(engine)
    pool = AsyncConnectionPool(
        provision.conninfo(settings.database_url),
        connection_class=AsyncConnection[DictRow],
        max_size=10,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},  # what LangGraph's savers need
    )
    await pool.open()
    runtime = LiveRuntime(
        settings, sessions, checkpointer=AsyncPostgresSaver(pool), store=AsyncPostgresStore(pool), searxng=searxng
    )
    manager = RunManager(sessions, runtime.build)
    try:
        await manager.resume_running()
        yield manager
    finally:
        await manager.shutdown()
        await runtime.aclose()
        await pool.close()
        await engine.dispose()


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    if settings.env != "test":  # TestClient apps run on the default loop and have no database
        require_selector_loop(asyncio.get_running_loop())
    searxng = None
    if settings.searxng_url:
        try:
            searxng = SidecarClient(settings.searxng_url, source="web")
        except ValueError as exc:  # optional feature: stay up; the type only, the value may hold credentials
            logger.warning(
                "RESEARCHLY_SEARXNG_URL is not a valid sidecar origin, web search is off: %s", type(exc).__name__
            )
    app.state.searxng = searxng
    probe = None
    if settings.env != "test":  # unit-test apps have no database
        probe = asyncio.create_task(record_web_search(settings.database_url, searxng))
    app.state.capability_probe = probe
    app.state.runs = None
    stack = contextlib.AsyncExitStack()
    try:
        if settings.env != "test":  # unit-test apps have no database
            app.state.runs = await stack.enter_async_context(run_manager(settings, searxng))
        yield
    finally:
        await stack.aclose()
        if probe is not None:
            probe.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await probe
        if searxng is not None:
            await searxng.aclose()


def create_app(settings: Settings | None = None, *, token: str | None = None) -> FastAPI:
    settings = settings or Settings()
    if token is None:
        token = settings.local_token.get_secret_value() if settings.local_token is not None else ensure_local_token()
    register_secret(token)
    _redact_server_logs()
    app = FastAPI(
        title="Researchly",
        version=version("researchly"),
        openapi_url="/api/openapi.json",
        docs_url=None,  # Swagger UI loads CDN script into the cookie-authenticated origin (ADR 0003)
        redoc_url=None,
        lifespan=_lifespan,
    )
    app.state.settings = settings
    app.add_middleware(LocalhostGuard, token=token)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(system.router)
    if WEB_DIR.is_dir():
        app.mount("/", SPAStaticFiles(directory=WEB_DIR, html=True), name="spa")  # last: the routes above win
    else:
        logger.warning("frontend not built (%s is missing): %s", WEB_DIR, BUILD_HINT)

        @app.get("/", include_in_schema=False, response_class=PlainTextResponse)
        async def not_built() -> str:  # the login link lands here, so say why there is no UI
            return f"The Researchly web UI is not built: {BUILD_HINT}, then reload this page.\n"

    return app
