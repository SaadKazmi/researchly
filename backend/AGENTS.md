# Backend guide

- Layout: `src/researchly/{api,runs,agents,services,connectors,llm,memory,security,db}`; tests mirror it under `tests/`.
- Python 3.12, async-first (FastAPI, SQLAlchemy 2 async, httpx via `PoliteClient`).
- Types: pyright strict on `src/`; no `Any` in public signatures.
- Errors: raise domain exceptions from `researchly.errors`; API layer maps them to HTTP problems (RFC 9457 JSON).
- DB: SQLAlchemy 2 models in `db/models/`; Alembic in `db/migrations/`; every migration has an upgrade→downgrade→upgrade test.
- Tests: pytest + pytest-asyncio; unit tests never touch the network; integration tests use testcontainers Postgres and replay fixtures.
- LLM in tests: `GenericFakeChatModel` for unit tests; `RecordReplay` in replay mode for integration.
