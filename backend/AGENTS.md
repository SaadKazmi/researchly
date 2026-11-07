# Backend guide

- Layout: `src/researchly/{api,runs,agents,services,connectors,llm,memory,security,db}`; tests mirror it under `tests/`.
- Python 3.12, async-first (FastAPI, SQLAlchemy 2 async + psycopg 3; outbound HTTP only via `PoliteClient` in `connectors/http.py` (sidecars: the origin-pinned `connectors/sidecar.py`), from step 2).
- Types: pyright `standard` on `src/` and `tests/` (litellm/langchain are not strict-clean); no `Any` in public signatures where a real type exists.
- Settings: `researchly.config.Settings` (`RESEARCHLY_*` env vars). Secrets only via `researchly.security.keychain`.
- DB: models in `db/models.py`; Alembic revisions in `db/migrations/versions/` (never edit an applied one); every revision is covered by the upgrade → downgrade → upgrade test.
- Tests: pytest + anyio (`@pytest.mark.anyio`); `@pytest.mark.db` needs Postgres (`RESEARCHLY_TEST_DATABASE_URL` or Docker via testcontainers); `@pytest.mark.live` is opt-in only. Unit tests never touch the network or the OS keychain.
- LLM in tests: `GenericFakeChatModel` for unit tests; `RecordReplayChatModel` in replay mode for integration.
