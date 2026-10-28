"""FastAPI application factory."""

from fastapi import FastAPI

from researchly.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Researchly", version="0.0.0")
    app.state.settings = settings or Settings()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
