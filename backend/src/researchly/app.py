"""FastAPI application factory."""

import logging
from importlib.metadata import version

from fastapi import FastAPI

from researchly.api import system
from researchly.config import Settings
from researchly.security.keychain import ensure_local_token
from researchly.security.localhost_guard import LocalhostGuard
from researchly.security.redact import RedactingArgsFilter, RedactingFilter, register_secret


def _redact_server_logs() -> None:
    """uvicorn logs request paths with their query string, so a `?token=` login link would reach the access log."""
    for name, kind in (("uvicorn.access", RedactingArgsFilter), ("uvicorn.error", RedactingFilter)):
        target = logging.getLogger(name)
        if not any(isinstance(f, kind) for f in target.filters):
            target.addFilter(kind())


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
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.add_middleware(LocalhostGuard, token=token)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(system.router)
    return app
