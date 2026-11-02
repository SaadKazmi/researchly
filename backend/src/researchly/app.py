"""FastAPI application factory."""

from fastapi import FastAPI

from researchly.config import Settings
from researchly.security.keychain import ensure_local_token
from researchly.security.localhost_guard import LocalhostGuard
from researchly.security.redact import register_secret


def create_app(settings: Settings | None = None, *, token: str | None = None) -> FastAPI:
    settings = settings or Settings()
    if token is None and settings.local_token is not None:
        token = settings.local_token.get_secret_value()
        register_secret(token)
    app = FastAPI(title="Researchly", version="0.0.0")
    app.state.settings = settings
    app.add_middleware(LocalhostGuard, token=token or ensure_local_token())

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
