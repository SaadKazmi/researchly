"""FastAPI application factory."""

from fastapi import FastAPI

from researchly.config import Settings
from researchly.security.keychain import ensure_local_token
from researchly.security.localhost_guard import LocalhostGuard


def create_app(settings: Settings | None = None, *, token: str | None = None) -> FastAPI:
    app = FastAPI(title="Researchly", version="0.0.0")
    app.state.settings = settings or Settings()
    app.add_middleware(LocalhostGuard, token=token or ensure_local_token())

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
