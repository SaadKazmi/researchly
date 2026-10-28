# Step 1b — Backend Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A tested Python backend skeleton: config, `/health`, localhost guard, OS-keychain secrets, Postgres baseline migration, and the full LLM layer (capability table, role routing, ledger, RecordReplay, dev-only `claude-code` provider), with CI running it all.

**Architecture:** One `researchly` package under `backend/src/` (uv project, hatchling build). FastAPI app factory with a pure-ASGI guard. SQLAlchemy 2 + psycopg 3 (sync for Alembic, async for the app). The LLM layer returns LangChain `BaseChatModel`s: `ChatLiteLLM` for real providers, `RecordReplayChatModel` for fixtures, `ClaudeCodeChatModel` for dev recording.

**Tech Stack:** Python 3.12, uv 0.12.10, FastAPI, SQLAlchemy 2.1, Alembic, psycopg 3, pydantic-settings, keyring, LiteLLM 1.105.1, langchain-core 1.6.12, langchain-litellm 0.11.1, claude-agent-sdk 0.2.169 (dev group only), pytest + anyio plugin, testcontainers (`pgvector/pgvector:pg18`).

**Spec:** `docs/superpowers/specs/2026-10-28-researchly-design.md` — §4.3, §7, §16, §16.1, §19.2, §19.7, §21, §22, §24 step 1.

## Global Constraints

- All commands go through `uv` (`uv run --directory backend …`); the system `python` is 3.8.
- `litellm` is imported **only** in `backend/src/researchly/llm/registry.py`; `deepagents` is not used in this plan.
- Exact pins: `litellm==1.105.1` (released 2026-10-14, ≥14 days, no OSV advisories; never 1.82.7, 1.82.8 or 1.107.0), `langchain-core==1.6.12`, `langchain-litellm==0.11.1`, `claude-agent-sdk==0.2.169`.
- `[tool.uv] exclude-newer = "7 days"`, `exclude-newer-package = { litellm = "14 days" }` (spec §19.7).
- `LITELLM_LOCAL_MODEL_COST_MAP=True` is set before `litellm` is imported; telemetry off.
- API keys never enter state, logs, fixtures or DB rows; `provider_config.key_ref` stores the keychain entry name only.
- Tests never touch the real OS keychain (autouse in-memory backend) or the network (except `@pytest.mark.live`, opt-in).
- DB tests run against Postgres 18 + pgvector: `RESEARCHLY_TEST_DATABASE_URL` if set, else testcontainers; on `CI` a missing database is a failure, never a skip.
- On Windows, psycopg async needs `asyncio.SelectorEventLoop`; the Claude Agent SDK needs subprocess support (Proactor). The dev provider therefore runs its SDK call in its own thread and loop.
- Conventional Commits; every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File map

```
.claude/hooks/_common.py               + locate(): worktree-aware (effective root, rel)
.claude/hooks/{protect,format}.py      use locate()
.claude/hooks/{format,session_context,typecheck}.py, .claude/statusline.py   hardening backlog
pyrightconfig.json                     IDE resolution for hooks/scripts/backend
backend/pyproject.toml, uv.lock, alembic.ini
backend/AGENTS.md                      ratified conventions
backend/src/researchly/__init__.py
backend/src/researchly/config.py       Settings (RESEARCHLY_*)
backend/src/researchly/app.py          create_app(): /health + LocalhostGuard
backend/src/researchly/db/{base,models,engine,migrate}.py, db/migrations/{env.py,script.py.mako,versions/0001_baseline.py}
backend/src/researchly/security/{keychain,redact,localhost_guard}.py
backend/src/researchly/llm/{capabilities.yaml,capabilities.py,registry.py,ledger.py,record_replay.py}
backend/src/researchly/llm/dev/{__init__,claude_code}.py
backend/tests/conftest.py + tests mirroring src
.github/workflows/ci.yml               backend job; uv + pytest pins
```

Tasks 0 and 9 edit harness control-plane files (`.claude/**`, `.github/workflows/**`), so the **controller** does them in the main checkout. Tasks 1–8 go to `implementer` subagents.

---

### Task 0: Worktree-aware hooks + hardening (controller)

**Files:**
- Modify: `.claude/hooks/_common.py`, `.claude/hooks/protect.py`, `.claude/hooks/format.py`, `.claude/hooks/typecheck.py`, `.claude/hooks/session_context.py`, `.claude/statusline.py`
- Test: `.claude/hooks/tests/test_common.py` (new), `test_protect.py`, `test_typecheck.py`, `test_statusline.py`

**Interfaces:**
- Produces: `_common.locate(file_path: str, root: Path) -> tuple[Path, str] | None`, returning (effective root, POSIX path relative to it). For files under `.claude/worktrees/<name>/`, the effective root is that worktree.

- [ ] **Step 1: Failing tests**

`.claude/hooks/tests/test_common.py`:
```python
from pathlib import Path

from _common import locate


def test_locate_plain_file(tmp_path):
    assert locate(str(tmp_path / "docs" / "x.md"), tmp_path) == (tmp_path, "docs/x.md")


def test_locate_strips_worktree_prefix(tmp_path):
    wt = tmp_path / ".claude" / "worktrees" / "agent-1"
    assert locate(str(wt / "docs" / "features.json"), tmp_path) == (wt, "docs/features.json")


def test_locate_worktree_dir_itself_and_outside(tmp_path):
    assert locate(str(tmp_path / ".claude" / "worktrees" / "agent-1"), tmp_path) is None
    assert locate(str(Path(tmp_path).parent / "elsewhere.txt"), tmp_path) is None
```
Append to `test_protect.py` (ensure `import json` is at its top):
```python
def test_features_structure_change_inside_worktree_asks(tmp_path):
    wt = tmp_path / ".claude" / "worktrees" / "w1"
    (wt / "docs").mkdir(parents=True)
    old = {"features": [{"id": "F1.1", "title": "t", "build_step": 3, "spec_ref": "§3", "acceptance": "a", "e2e": [], "passes": False}]}
    (wt / "docs" / "features.json").write_text(json.dumps(old), encoding="utf-8")
    new = {"features": [{**old["features"][0], "title": "changed"}]}
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(wt / "docs" / "features.json"), "content": json.dumps(new)}}
    assert protect.decide(payload, tmp_path)[0] == "ask"


def test_control_plane_inside_worktree_asks(tmp_path):
    path = tmp_path / ".claude" / "worktrees" / "w1" / ".claude" / "hooks" / "x.py"
    assert protect.decide({"tool_name": "Write", "tool_input": {"file_path": str(path)}}, tmp_path)[0] == "ask"
```
Replace `test_parse_porcelain_handles_renames_and_untracked` in `test_typecheck.py`:
```python
def test_parse_porcelain_z_handles_renames_untracked_and_spaces():
    text = " M backend/a.py\0?? frontend/src/b c.tsx\0R  backend/new.py\0old.py\0"
    assert typecheck.parse_porcelain(text) == ["backend/a.py", "frontend/src/b c.tsx", "backend/new.py"]
```
Append to `test_statusline.py`:
```python
def test_render_tolerates_null_fields():
    assert statusline.render({"model": None, "context_window": None, "cost": None, "effort": None}) == "? | ctx ?% | $0.00 | effort ?"
```

- [ ] **Step 2: Run, expect failures**

Run: `uv run --no-project --python 3.12 --with pytest pytest .claude/hooks/tests -q`
Expected: FAIL. `ImportError: cannot import name 'locate'`, the worktree protect tests return `None`, the porcelain test fails, and statusline raises `AttributeError`.

- [ ] **Step 3: Implement**

`_common.py`: add below `rel_path`:
```python
_WORKTREES = ".claude/worktrees/"


def locate(file_path: str, root: Path) -> tuple[Path, str] | None:
    """(effective root, rel path): files in .claude/worktrees/<name>/ resolve against that worktree."""
    rel = rel_path(file_path, root)
    if rel is None or not rel.casefold().startswith(_WORKTREES):
        return None if rel is None else (root, rel)
    name, _, inner = rel[len(_WORKTREES) :].partition("/")
    return (root / ".claude" / "worktrees" / name, inner) if name and inner else None
```
`protect.py`: import `locate` instead of `rel_path`. In `decide`, replace the first three lines with:
```python
    located = locate(payload.get("tool_input", {}).get("file_path", ""), root)
    if located is None:
        return None
    root, rel = located
```
`format.py`: import `locate`. The new `main`, best-effort and never failing the tool call:
```python
def main() -> int:
    try:
        located = locate(read_payload().get("tool_input", {}).get("file_path", ""), project_root())
        if located:
            root, rel = located
            for cmd in commands_for(rel, root):
                exe = shutil.which(cmd[0]) or cmd[0]
                subprocess.run([exe, *cmd[1:]], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=60)
    except Exception:  # formatting is best-effort; never block an edit
        pass
    return 0
```
`typecheck.py`:
```python
def parse_porcelain(text: str) -> list[str]:
    """Parse `git status --porcelain=v1 -z` output: NUL-separated, a rename's source path in its own field."""
    paths: list[str] = []
    fields = iter(text.split("\0"))
    for entry in fields:
        if len(entry) < 4:
            continue
        paths.append(entry[3:])
        if entry[0] in "RC":
            next(fields, None)
    return paths
```
In `main`, change the git command to `["git", "status", "--porcelain=v1", "-z", "-uall"]` and add `timeout=30`. In `_run`, add `timeout=600` and wrap it so a timeout becomes a failed check:
```python
    def run(cmd: list[str]) -> CompletedLike:
        exe = shutil.which(cmd[0]) or cmd[0]
        try:
            return subprocess.run([exe, *cmd[1:]], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=600)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(cmd, 1, "", f"timed out after 600s: {' '.join(cmd)}")
```
`session_context.py`: wrap the body of `main` in `try: … except Exception: return 0`, and add `timeout=10` to the git call.
`statusline.py` `render`: replace each `payload.get("x", {})` with `(payload.get("x") or {})`.

- [ ] **Step 4: Run, expect pass**

Run: `uv run --no-project --python 3.12 --with pytest pytest .claude/hooks/tests scripts/tests -q`
Expected: all pass (56 existing + 5 new + 1 replaced).

- [ ] **Step 5: Verify a real worktree dispatch (no code)**

Dispatch `implementer` with this prompt: "Do not edit files. Run and report verbatim: `echo $CLAUDE_PROJECT_DIR`; `pwd`; `git rev-parse --show-toplevel`; `git branch --show-current`; `git log --oneline -1`. Then report whether the worktree contains `docs/superpowers/plans/2026-10-28-step1b-backend-core.md`." Record the result in `docs/progress.md`.
- If the worktree is based on `main`, not on `feat/step1b-backend-core`, every implementer prompt in this plan must begin with: "First run `git merge --ff-only feat/step1b-backend-core` in your worktree." The controller then brings each task's commits back with `git merge --ff-only <worktree-branch>` (or `git cherry-pick`).
- If `CLAUDE_PROJECT_DIR` points at the main checkout, `locate()` covers the hooks. If it points at the worktree, the hooks need nothing more.

- [ ] **Step 6: Commit**

```bash
git add .claude/hooks .claude/statusline.py docs/progress.md
git commit -m "fix(claude): worktree-aware hooks, timeouts, -z porcelain, null-tolerant status line"
```

---

### Task 1: Backend project skeleton, settings, `/health`

**Files:**
- Create: `backend/pyproject.toml`, `backend/src/researchly/__init__.py`, `backend/src/researchly/config.py`, `backend/src/researchly/app.py`, `backend/tests/__init__.py` (empty), `backend/tests/conftest.py`, `backend/tests/test_app.py`, `pyrightconfig.json`
- Modify: `backend/AGENTS.md`, `AGENTS.md` (Gotchas)
- Generated: `backend/uv.lock`

**Interfaces:**
- Produces: `researchly.config.Settings` (fields below), `researchly.app.create_app(settings: Settings | None = None) -> FastAPI`, and the pytest fixtures `anyio_backend` and `_memory_keyring` (autouse).

- [ ] **Step 1: Write `backend/pyproject.toml`**

```toml
[project]
name = "researchly"
version = "0.0.0"
description = "Local-first multi-agent research assistant"
license = "Apache-2.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "fastapi>=0.120",
  "uvicorn>=0.40",
  "sqlalchemy[asyncio]>=2.1",
  "alembic>=1.16",
  "psycopg[binary]>=3.2",
  "pydantic-settings>=2.10",
  "keyring>=25.6",
  "pyyaml>=6.0.2",
  "litellm==1.105.1",
  "langchain-core==1.6.12",
  "langchain-litellm==0.11.1",
]

[dependency-groups]
dev = [
  "pytest>=8.4",
  "httpx>=0.28",
  "testcontainers[postgres]>=4.10",
  "ruff>=0.14",
  "pyright>=1.1.405",
  "claude-agent-sdk==0.2.169",  # dev-only claude-code provider (spec §7.7); never a runtime dependency
]

[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/researchly"]
exclude = ["src/researchly/llm/dev"]  # spec §7.7: the dev provider is excluded from release builds

[tool.uv]
exclude-newer = "7 days"
exclude-newer-package = { litellm = "14 days" }

[tool.ruff]
line-length = 120
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "ASYNC", "S"]
ignore = ["S101"]

[tool.pyright]
include = ["src", "tests"]
pythonVersion = "3.12"
typeCheckingMode = "standard"
venvPath = "."
venv = ".venv"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-m 'not live'"
markers = [
  "db: needs Postgres 18 + pgvector",
  "live: calls a real model; opt-in, never in CI",
]
```

- [ ] **Step 2: Lock and sync**

Run: `uv lock --directory backend`, then `uv sync --directory backend`
Expected: `uv.lock` is created and `.venv` builds. Check the pins with `uv tree --directory backend --depth 1 | Select-String "litellm|langchain"`; it must show `litellm v1.105.1`, `langchain-core v1.6.12` and `langchain-litellm v0.11.1`. If resolution fails because a pinned version is newer than the cooldown, stop and report. Do not loosen the pins.

- [ ] **Step 3: Failing test + fixtures**

`backend/tests/conftest.py`:
```python
import asyncio

import keyring
import pytest
from keyring.backend import KeyringBackend


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
def _memory_keyring():
    """Tests never touch the real OS keychain."""
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture
def anyio_backend():
    # psycopg's async driver cannot run on Windows' default Proactor loop.
    return ("asyncio", {"loop_factory": asyncio.SelectorEventLoop})
```
`backend/tests/test_app.py`:
```python
import httpx
import pytest

from researchly.app import create_app
from researchly.config import Settings


@pytest.mark.anyio
async def test_health_ok():
    app = create_app(Settings(env="test"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_settings_read_env(monkeypatch):
    monkeypatch.setenv("RESEARCHLY_ENV", "development")
    monkeypatch.setenv("RESEARCHLY_DEV_PROVIDER", "claude-code")
    s = Settings()
    assert (s.env, s.dev_provider, s.llm_fixtures) == ("development", "claude-code", "off")
```

- [ ] **Step 4: Run, expect failure**

Run: `uv run --directory backend pytest -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'researchly.app'`.

- [ ] **Step 5: Implement**

`backend/src/researchly/__init__.py`: `"""Researchly backend."""`

`backend/src/researchly/config.py`:
```python
"""Runtime settings, read from RESEARCHLY_* environment variables."""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RESEARCHLY_")

    env: Literal["production", "development", "test"] = "production"
    database_url: str = "postgresql+psycopg://researchly_app@127.0.0.1:54329/researchly"
    dev_provider: Literal["claude-code"] | None = None
    dev_provider_model: Literal["sonnet", "haiku"] = "sonnet"
    llm_fixtures: Literal["record", "replay", "off"] = "off"
    llm_fixtures_dir: Path = Path("e2e/fixtures/llm")
    llm_fixtures_suite: str = "default"
```
`backend/src/researchly/app.py`:
```python
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
```

- [ ] **Step 6: Run, expect pass; lint and types**

Run: `uv run --directory backend pytest -q`, then `uv run --directory backend ruff check`, `uv run --directory backend ruff format --check`, and `uv run --directory backend pyright`
Expected: 2 passed, and all three checks report no errors.

- [ ] **Step 7: Root `pyrightconfig.json`** (IDE resolution; carried over from step 1a)

```json
{
  "pythonVersion": "3.12",
  "venvPath": "backend",
  "venv": ".venv",
  "executionEnvironments": [
    { "root": ".claude/hooks", "extraPaths": [".claude/hooks", "scripts"] },
    { "root": "scripts", "extraPaths": ["scripts"] },
    { "root": "backend/src" },
    { "root": "backend/tests", "extraPaths": ["backend/src"] }
  ]
}
```

- [ ] **Step 8: Ratify `backend/AGENTS.md`** (carried over from step 1a). Replace the file body with:
```markdown
# Backend guide

- Layout: `src/researchly/{api,runs,agents,services,connectors,llm,memory,security,db}`; tests mirror it under `tests/`.
- Python 3.12, async-first (FastAPI, SQLAlchemy 2 async + psycopg 3; outbound HTTP only via `PoliteClient` in `connectors/http.py`, from step 2).
- Types: pyright `standard` on `src/` and `tests/` (litellm/langchain are not strict-clean); no `Any` in public signatures where a real type exists.
- Settings: `researchly.config.Settings` (`RESEARCHLY_*` env vars). Secrets only via `researchly.security.keychain`.
- DB: models in `db/models.py`; Alembic revisions in `db/migrations/versions/` (never edit an applied one); every revision is covered by the upgrade → downgrade → upgrade test.
- Tests: pytest + anyio (`@pytest.mark.anyio`); `@pytest.mark.db` needs Postgres (`RESEARCHLY_TEST_DATABASE_URL` or Docker via testcontainers); `@pytest.mark.live` is opt-in only. Unit tests never touch the network or the OS keychain.
- LLM in tests: `GenericFakeChatModel` for unit tests; `RecordReplayChatModel` in replay mode for integration.
```
Append to the Gotchas in the root `AGENTS.md`:
```markdown
- Windows asyncio: psycopg async needs `SelectorEventLoop` (tests set it via `anyio_backend`); the dev `claude-code` provider runs the Agent SDK in its own thread on a Proactor loop.
```

- [ ] **Step 9: Commit**

```bash
git add backend pyrightconfig.json AGENTS.md
git commit -m "feat(backend): uv project, settings and /health app factory"
```

---

### Task 2: Database baseline (models, engine, Alembic, round-trip test)

**Files:**
- Create: `backend/alembic.ini`, `backend/src/researchly/db/__init__.py` (empty), `db/base.py`, `db/models.py`, `db/engine.py`, `db/migrate.py`, `db/migrations/env.py`, `db/migrations/script.py.mako`, `db/migrations/versions/0001_baseline.py`
- Modify: `backend/tests/conftest.py` (DB fixtures)
- Test: `backend/tests/db/__init__.py` (empty), `backend/tests/db/test_migrations.py`

**Interfaces:**
- Consumes: `Settings.database_url`.
- Produces:
  - Models `Setting`, `Capability`, `ProviderConfig` and `LlmCall` (columns below).
  - `make_engine(url: str) -> AsyncEngine` and `make_sessionmaker(engine) -> async_sessionmaker[AsyncSession]`.
  - `migrate.upgrade(url: str, rev: str = "head")` and `migrate.downgrade(url: str, rev: str = "base")`.
  - Fixtures `pg_admin_url` (session-scoped) and `db_url` (a fresh empty database per test).

Baseline scope: only the tables step 1 uses (spec §16: `setting`, `capability`, `provider_config`, `llm_call`). Later steps add their tables in new revisions. `llm_call.run_id` is a nullable UUID without an FK; step 3 adds the FK when `run` exists.

- [ ] **Step 1: DB fixtures.** Add to `backend/tests/conftest.py`, moving the new imports into the file's top import block (ruff E402):
```python
import os
import uuid
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


@pytest.fixture(scope="session")
def pg_admin_url() -> Iterator[str]:
    url = os.environ.get("RESEARCHLY_TEST_DATABASE_URL")
    if url:
        yield url
        return
    try:
        from testcontainers.postgres import PostgresContainer

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
    yield make_url(pg_admin_url).set(database=name).render_as_string(hide_password=False)
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()
```

- [ ] **Step 2: Failing test** — `backend/tests/db/test_migrations.py`:
```python
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from researchly.db import migrate
from researchly.db.base import Base

pytestmark = pytest.mark.db
TABLES = {"setting", "capability", "provider_config", "llm_call"}


def test_upgrade_downgrade_upgrade(db_url):
    migrate.upgrade(db_url)
    migrate.downgrade(db_url)
    engine = create_engine(db_url)
    assert TABLES.isdisjoint(inspect(engine).get_table_names())
    migrate.upgrade(db_url)
    assert TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_baseline_extensions_and_bm25_capability(db_url):
    migrate.upgrade(db_url)
    engine = create_engine(db_url)
    with engine.connect() as conn:
        exts = set(conn.scalars(text("SELECT extname FROM pg_extension")))
        bm25 = conn.scalar(text("SELECT value FROM capability WHERE key = 'bm25_engine'"))
    engine.dispose()
    assert {"vector", "pg_trgm"} <= exts
    assert bm25 == ("pg_textsearch" if "pg_textsearch" in exts else "tsvector")


def test_models_match_migrations(db_url):
    migrate.upgrade(db_url)
    engine = create_engine(db_url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []
```

- [ ] **Step 3: Run, expect failure**

Run: `uv run --directory backend pytest tests/db -q`
Expected: FAIL with `ModuleNotFoundError: researchly.db.migrate`. If it shows SKIPPED instead, Docker is not running: start Docker Desktop and rerun.

- [ ] **Step 4: Implement**

`db/base.py`:
```python
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
```
`db/models.py`:
```python
"""Core tables for step 1 (spec §16). Later steps add tables via new Alembic revisions."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from researchly.db.base import Base


class Setting(Base):
    __tablename__ = "setting"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)


class Capability(Base):
    __tablename__ = "capability"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)


class ProviderConfig(Base):
    __tablename__ = "provider_config"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String)
    base_url: Mapped[str | None] = mapped_column(String)
    models: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)  # role -> model override
    key_ref: Mapped[str | None] = mapped_column(String)  # keychain entry name; never the key itself


class LlmCall(Base):
    __tablename__ = "llm_call"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID, index=True)
    agent: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    in_tok: Mapped[int] = mapped_column(Integer)
    cached_tok: Mapped[int] = mapped_column(Integer)
    out_tok: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```
`db/engine.py`:
```python
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
```
`db/migrate.py`:
```python
"""Run Alembic programmatically (used by tests and, later, `researchly start`)."""

from pathlib import Path

from alembic import command
from alembic.config import Config

_MIGRATIONS = Path(__file__).with_name("migrations")


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade(url: str, rev: str = "head") -> None:
    command.upgrade(alembic_config(url), rev)


def downgrade(url: str, rev: str = "base") -> None:
    command.downgrade(alembic_config(url), rev)
```
`db/migrations/env.py`:
```python
from alembic import context
from sqlalchemy import create_engine, pool

from researchly.config import Settings
from researchly.db import models  # noqa: F401  (registers tables on Base.metadata)
from researchly.db.base import Base

url = context.config.get_main_option("sqlalchemy.url") or Settings().database_url
engine = create_engine(url, poolclass=pool.NullPool)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
```
`db/migrations/script.py.mako`:
```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}
revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```
`db/migrations/versions/0001_baseline.py`:
```python
"""baseline: extensions, settings, capabilities, providers, llm ledger

Revision ID: 0001
Revises:
"""

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    available = set(op.get_bind().scalars(sa.text("SELECT name FROM pg_available_extensions")))
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    bm25 = "pg_textsearch" if "pg_textsearch" in available else "tsvector"  # spec §16.1
    if bm25 == "pg_textsearch":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_textsearch")

    op.create_table("setting", sa.Column("key", sa.String(), primary_key=True), sa.Column("value", JSONB(), nullable=False))
    op.create_table("capability", sa.Column("key", sa.String(), primary_key=True), sa.Column("value", JSONB(), nullable=False))
    op.create_table(
        "provider_config",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("base_url", sa.String()),
        sa.Column("models", JSONB(), nullable=False),
        sa.Column("key_ref", sa.String()),
    )
    op.create_table(
        "llm_call",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("run_id", UUID()),
        sa.Column("agent", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("in_tok", sa.Integer(), nullable=False),
        sa.Column("cached_tok", sa.Integer(), nullable=False),
        sa.Column("out_tok", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_llm_call_run_id", "llm_call", ["run_id"])
    op.execute(
        sa.text("INSERT INTO capability (key, value) VALUES ('bm25_engine', CAST(:v AS jsonb))").bindparams(
            v=json.dumps(bm25)
        )
    )


def downgrade() -> None:
    op.drop_index("ix_llm_call_run_id", table_name="llm_call")
    for table in ("llm_call", "provider_config", "capability", "setting"):
        op.drop_table(table)
    op.execute("DROP EXTENSION IF EXISTS pg_textsearch")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS vector")
```
If `test_models_match_migrations` reports diffs (nullability, for example), change the **model**, not the migration: `Mapped[str]` is NOT NULL, `Mapped[str | None]` is nullable, and the `provider_config.models` model column needs `nullable=False` to match.

`backend/alembic.ini` (for the `alembic` CLI; the URL comes from `RESEARCHLY_DATABASE_URL` via `env.py`):
```ini
[alembic]
script_location = %(here)s/src/researchly/db/migrations
```

- [ ] **Step 5: Run, expect pass**

Run: `uv run --directory backend pytest -q`, then `uv run --directory backend pyright`, then `uv run --directory backend ruff check`
Expected: all pass (5 tests), 0 errors.

- [ ] **Step 6: Commit**

```bash
git add backend
git commit -m "feat(db): baseline Alembic revision with extensions, settings, providers and llm ledger"
```

---

### Task 3: Keychain + secret redaction

**Files:**
- Create: `backend/src/researchly/security/__init__.py` (empty), `security/keychain.py`, `security/redact.py`
- Test: `backend/tests/security/__init__.py` (empty), `backend/tests/security/test_keychain.py`, `backend/tests/security/test_redact.py`

**Interfaces:**
- Produces:
  - `get_secret(name: str) -> str | None`, `set_secret(name: str, value: str) -> None` and `delete_secret(name: str) -> None`.
  - `ensure_local_token() -> str` and `KeychainUnavailable(RuntimeError)`.
  - `redact(text: str) -> str`, `register_secret(value: str) -> None` and `RedactingFilter(logging.Filter)`.

The headless-Linux encrypted-file fallback (spec §19.2) belongs with the CLI `onboard`/`doctor` work in plan 1c. Here, a missing backend raises `KeychainUnavailable` with a clear message.

- [ ] **Step 1: Failing tests**

`test_keychain.py`:
```python
import keyring
import pytest
from keyring.backends.fail import Keyring as FailKeyring

from researchly.security import keychain
from researchly.security.redact import redact


def test_set_get_delete_roundtrip():
    keychain.set_secret("anthropic", "sk-ant-abcdefghijklmnopqrstuvwxyz")
    assert keychain.get_secret("anthropic") == "sk-ant-abcdefghijklmnopqrstuvwxyz"
    keychain.delete_secret("anthropic")
    keychain.delete_secret("anthropic")  # idempotent
    assert keychain.get_secret("anthropic") is None


def test_secrets_read_from_keychain_are_redacted_in_text():
    keychain.set_secret("custom", "plain-secret-value-123")
    assert "plain-secret-value-123" not in redact("key=plain-secret-value-123")


def test_ensure_local_token_is_stable():
    first = keychain.ensure_local_token()
    assert len(first) >= 32
    assert keychain.ensure_local_token() == first


def test_no_backend_raises_clear_error():
    keyring.set_keyring(FailKeyring())
    with pytest.raises(keychain.KeychainUnavailable):
        keychain.get_secret("anthropic")
```
`test_redact.py`:
```python
import logging

from researchly.security.redact import RedactingFilter, redact


def test_redacts_known_key_shapes_and_emails():
    text = "a sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWX b sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX c AIza" + "x" * 35 + " d me@example.org e Bearer abcdefghijklmnopqrstu"
    out = redact(text)
    for leaked in ("sk-ant-api03", "sk-proj-", "AIza", "me@example.org", "abcdefghijklmnopqrstu"):
        assert leaked not in out
    assert out.count("[REDACTED]") == 5


def test_logging_filter_redacts_args():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "key %s", ("sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWX",), None)
    RedactingFilter().filter(record)
    assert record.getMessage() == "key [REDACTED]"
```

- [ ] **Step 2: Run, expect failure** — `uv run --directory backend pytest tests/security -q` fails with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`security/redact.py`:
```python
"""Scrub secrets from text before it reaches logs or fixtures (spec §7.6, §19.2)."""

import logging
import re

_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),  # OpenAI / Anthropic style keys
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),  # Google API keys
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),  # emails
]
_known: set[str] = set()


def register_secret(value: str) -> None:
    if len(value) >= 8:
        _known.add(value)


def redact(text: str) -> str:
    for secret in _known:
        text = text.replace(secret, "[REDACTED]")
    for pattern in _PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True
```
`security/keychain.py`:
```python
"""OS keychain access (spec §19.2). Secrets are read at call time and never cached."""

import secrets

import keyring
from keyring.errors import KeyringError, NoKeyringError, PasswordDeleteError

from researchly.security.redact import register_secret

SERVICE = "researchly"
LOCAL_TOKEN = "local-token"


class KeychainUnavailable(RuntimeError):
    pass


def get_secret(name: str) -> str | None:
    try:
        value = keyring.get_password(SERVICE, name)
    except NoKeyringError as exc:
        raise KeychainUnavailable(f"no OS keychain backend available: {exc}") from exc
    if value:
        register_secret(value)
    return value


def set_secret(name: str, value: str) -> None:
    try:
        keyring.set_password(SERVICE, name, value)
    except KeyringError as exc:
        raise KeychainUnavailable(f"cannot write to the OS keychain: {exc}") from exc
    register_secret(value)


def delete_secret(name: str) -> None:
    try:
        keyring.delete_password(SERVICE, name)
    except PasswordDeleteError:
        pass


def ensure_local_token() -> str:
    """The per-install API token (spec §4.3); created on first use."""
    token = get_secret(LOCAL_TOKEN)
    if token is None:
        token = secrets.token_urlsafe(32)
        set_secret(LOCAL_TOKEN, token)
    return token
```
`keyring.backends.fail.Keyring.get_password` raises `NoKeyringError`. If the installed keyring raises another `KeyringError` subclass, widen `get_secret`'s `except` to `KeyringError`.

- [ ] **Step 4: Run, expect pass**: `uv run --directory backend pytest -q`, then `ruff check` and `pyright`. All pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/researchly/security backend/tests/security
git commit -m "feat(security): OS keychain secrets, local token, and secret redaction"
```

---

### Task 4: Localhost guard

**Files:**
- Create: `backend/src/researchly/security/localhost_guard.py`
- Modify: `backend/src/researchly/app.py`, `backend/tests/test_app.py`
- Test: `backend/tests/security/test_localhost_guard.py`

**Interfaces:**
- Consumes: `ensure_local_token()`.
- Produces:
  - `LocalhostGuard(app: ASGIApp, token: str)`, a pure ASGI middleware, and `TOKEN_HEADER = "x-researchly-token"`.
  - `create_app(settings: Settings | None = None, *, token: str | None = None) -> FastAPI`. When `token` is None it uses `ensure_local_token()`.

Spec §4.3 rules:
- The `Host` hostname must be `localhost`, `127.0.0.1` or `::1`.
- If an `Origin` header is present, its netloc must equal `Host` (same-origin only). This also rejects `Origin: null`.
- Every path except `/health` needs `x-researchly-token`, compared in constant time.
- Rejections are 403 JSON responses. WebSocket connections are closed with code 1008.

How the SPA obtains the token is plan 1d's concern: the served shell will inject it.

- [ ] **Step 1: Failing test** — `test_localhost_guard.py`:
```python
import httpx
import pytest

from researchly.app import create_app
from researchly.config import Settings
from researchly.security.localhost_guard import TOKEN_HEADER

TOKEN = "t" * 43


def client(base_url: str = "http://127.0.0.1:8765") -> httpx.AsyncClient:
    app = create_app(Settings(env="test"), token=TOKEN)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url)


@pytest.mark.anyio
async def test_health_open_without_token():
    async with client() as c:
        assert (await c.get("/health")).status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize("base_url", ["http://evil.example:8765", "http://192.168.1.5:8765"])
async def test_foreign_host_rejected(base_url):
    async with client(base_url) as c:
        assert (await c.get("/health")).status_code == 403


@pytest.mark.anyio
async def test_cross_origin_rejected_even_with_token():
    async with client() as c:
        r = await c.get("/openapi.json", headers={TOKEN_HEADER: TOKEN, "origin": "http://evil.example"})
        assert r.status_code == 403
        r = await c.get("/openapi.json", headers={TOKEN_HEADER: TOKEN, "origin": "null"})
        assert r.status_code == 403


@pytest.mark.anyio
async def test_token_required_and_checked():
    async with client() as c:
        assert (await c.get("/openapi.json")).status_code == 403
        assert (await c.get("/openapi.json", headers={TOKEN_HEADER: "wrong"})).status_code == 403
        ok = await c.get("/openapi.json", headers={TOKEN_HEADER: TOKEN, "origin": "http://127.0.0.1:8765"})
        assert ok.status_code == 200


@pytest.mark.anyio
async def test_localhost_and_ipv6_hosts_allowed():
    for base in ("http://localhost:8765", "http://[::1]:8765"):
        async with client(base) as c:
            assert (await c.get("/health")).status_code == 200
```
In `test_app.py`, change `create_app(Settings(env="test"))` to `create_app(Settings(env="test"), token="t" * 43)`.

- [ ] **Step 2: Run, expect failure** (`ModuleNotFoundError`, or `TypeError` for the `token` argument).

- [ ] **Step 3: Implement** — `security/localhost_guard.py`:
```python
"""Localhost guard (spec §4.3): Host allowlist, same-origin only, per-install token."""

import hmac
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

TOKEN_HEADER = "x-researchly-token"
_HOSTS = {"localhost", "127.0.0.1", "::1"}
_OPEN_PATHS = {"/health"}


class LocalhostGuard:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            error = self._check(scope)
            if error is not None:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await JSONResponse({"detail": error}, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def _check(self, scope: Scope) -> str | None:
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host", "")
        if urlsplit(f"//{host}").hostname not in _HOSTS:
            return "host not allowed"
        origin = headers.get("origin")
        if origin is not None and urlsplit(origin).netloc != host:
            return "cross-origin request rejected"
        if scope["path"] in _OPEN_PATHS:
            return None
        if not hmac.compare_digest(headers.get(TOKEN_HEADER, "").encode(), self.token.encode()):
            return "missing or invalid token"
        return None
```
`app.py`: the new signature, with the middleware added:
```python
from researchly.security.keychain import ensure_local_token
from researchly.security.localhost_guard import LocalhostGuard


def create_app(settings: Settings | None = None, *, token: str | None = None) -> FastAPI:
    app = FastAPI(title="Researchly", version="0.0.0")
    app.state.settings = settings or Settings()
    app.add_middleware(LocalhostGuard, token=token or ensure_local_token())
    ...
```

- [ ] **Step 4: Run, expect pass**: `uv run --directory backend pytest -q`, then `ruff check` and `pyright`.

- [ ] **Step 5: Commit**

```bash
git add backend
git commit -m "feat(security): localhost guard with host allowlist, same-origin check and install token"
```

---

### Task 5: Capability table + provider registry (LiteLLM path)

**Files:**
- Create: `backend/src/researchly/llm/__init__.py` (empty), `llm/capabilities.yaml`, `llm/capabilities.py`, `llm/registry.py`
- Test: `backend/tests/llm/__init__.py` (empty), `backend/tests/llm/test_capabilities.py`, `backend/tests/llm/test_registry.py`

**Interfaces:**
- Consumes: `get_secret`, `Settings`.
- Produces:
  - `Role = Literal["strong", "mid", "cheap", "critic"]`.
  - `ModelOverride` (pydantic) and `CapabilityTable(roles: dict[str, dict[str, str]], models: dict[str, ModelOverride])`, plus `load_table(path: Path | None = None) -> CapabilityTable`.
  - `ProviderSpec(provider: str, key_ref: str | None = None, base_url: str | None = None, models: Mapping[str, str] = {})`, a frozen dataclass.
  - `NoModelForRole(LookupError)`.
  - `resolve_model(role: Role, providers: Sequence[ProviderSpec], table: CapabilityTable) -> tuple[ProviderSpec, str]`.
  - `ModelInfo` (pydantic: `input_per_mtok`, `cached_per_mtok`, `output_per_mtok`, `context_window`, `max_output`, `tools`, `json_schema`, `vision`, `prompt_cache`, all optional) and `model_info(model: str, table: CapabilityTable | None = None) -> ModelInfo`.
  - `cost_usd(model: str, in_tok: int, cached_tok: int, out_tok: int, table: CapabilityTable | None = None) -> float`. `in_tok` includes `cached_tok`, following LangChain's `usage_metadata` convention.
  - `chat_model(role: Role, providers: Sequence[ProviderSpec], settings: Settings, table: CapabilityTable | None = None) -> BaseChatModel`.

The role defaults below were checked on 2026-10-28 against LiteLLM 1.105.1's `model_prices_and_context_window.json` and the providers' pages. Anthropic's are from the pricing and model overview pages. OpenAI and Gemini tiers are ordered by price. Ollama and OpenAI-compatible providers have no defaults: the user sets the models per role.

- [ ] **Step 1: Failing tests**

`test_capabilities.py`:
```python
from researchly.llm.capabilities import load_table


def test_default_table_has_role_defaults_for_keyed_providers():
    table = load_table()
    for provider in ("anthropic", "openai", "gemini"):
        assert set(table.roles[provider]) == {"strong", "mid", "cheap"}
    assert table.roles["anthropic"]["cheap"] == "anthropic/claude-haiku-5-5"
```
`test_registry.py`:
```python
import pytest
from langchain_litellm import ChatLiteLLM

from researchly.config import Settings
from researchly.llm import registry
from researchly.llm.capabilities import load_table
from researchly.llm.registry import NoModelForRole, ProviderSpec, chat_model, cost_usd, model_info, resolve_model
from researchly.security.keychain import set_secret

T = load_table()
ANTHROPIC = ProviderSpec("anthropic", key_ref="anthropic")
OPENAI = ProviderSpec("openai", key_ref="openai")


def test_resolve_role_defaults_and_overrides():
    assert resolve_model("mid", [ANTHROPIC], T) == (ANTHROPIC, "anthropic/claude-sonnet-5-5")
    custom = ProviderSpec("anthropic", models={"mid": "anthropic/claude-opus-5-5"})
    assert resolve_model("mid", [custom], T)[1] == "anthropic/claude-opus-5-5"


def test_critic_prefers_a_different_provider():
    assert resolve_model("critic", [ANTHROPIC, OPENAI], T) == (OPENAI, "openai/gpt-6-astra")
    assert resolve_model("critic", [ANTHROPIC], T) == (ANTHROPIC, "anthropic/claude-opus-5-5")


def test_missing_role_model_is_explicit():
    with pytest.raises(NoModelForRole):
        resolve_model("strong", [ProviderSpec("ollama")], T)
    with pytest.raises(NoModelForRole):
        resolve_model("strong", [], T)


def test_model_info_applies_overrides():
    haiku = model_info("anthropic/claude-haiku-5-5", T)  # absent from LiteLLM 1.105.1
    assert (haiku.input_per_mtok, haiku.output_per_mtok, haiku.context_window) == (0.10, 0.50, 1_000_000)
    assert model_info("anthropic/claude-sonnet-5-5", T).cached_per_mtok == 0.10


def test_cost_usd_splits_cached_tokens():
    # 600 uncached * $2 + 400 cached * $0.10 + 200 out * $10, per MTok
    assert cost_usd("anthropic/claude-sonnet-5-5", 1000, 400, 200, T) == pytest.approx(0.00324)


def test_chat_model_reads_key_at_construction_and_never_from_settings():
    set_secret("anthropic", "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWX")
    model = chat_model("cheap", [ANTHROPIC], Settings(env="test"), T)
    assert isinstance(model, ChatLiteLLM)
    assert model.model == "anthropic/claude-haiku-5-5"
    assert model.api_key == "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWX"
    assert "sk-ant" not in repr(model)


def test_litellm_cost_map_is_local():
    import os

    assert os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"
    assert registry.litellm.model_cost  # bundled map loaded without network
```

- [ ] **Step 2: Run, expect failure** (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

`llm/capabilities.yaml`:
```yaml
# Role defaults per provider (spec §7.3) and overrides of LiteLLM's bundled cost map (spec §7.2).
# Checked 2026-10-28 against LiteLLM 1.105.1 and the providers' model and pricing pages.
# Prices are USD per million tokens.
roles:
  anthropic: {strong: anthropic/claude-opus-5-5, mid: anthropic/claude-sonnet-5-5, cheap: anthropic/claude-haiku-5-5}
  openai: {strong: openai/gpt-6-astra, mid: openai/gpt-6.1-sol, cheap: openai/gpt-6-luna}
  gemini: {strong: gemini/gemini-3.1-pro-preview, mid: gemini/gemini-3.8-flash, cheap: gemini/gemini-3.5-flash-lite}
models:
  anthropic/claude-sonnet-5-5:
    cached_per_mtok: 0.10  # LiteLLM 1.105.1 says 0.20; Anthropic: cache hits are 0.05x input
  anthropic/claude-haiku-5-5:  # absent from LiteLLM 1.105.1
    input_per_mtok: 0.10  # ponytail: <=100k-token prompt tier; >100k prompts cost 5x
    cached_per_mtok: 0.01
    output_per_mtok: 0.50
    context_window: 1000000
    max_output: 128000
    tools: true
    json_schema: true
    vision: true
    prompt_cache: true
```
`llm/capabilities.py`:
```python
"""Capability table (spec §7.2): role defaults and per-model overrides. No litellm import here."""

from pathlib import Path

import yaml
from pydantic import BaseModel

_DEFAULT = Path(__file__).with_name("capabilities.yaml")


class ModelOverride(BaseModel):
    input_per_mtok: float | None = None
    cached_per_mtok: float | None = None
    output_per_mtok: float | None = None
    context_window: int | None = None
    max_output: int | None = None
    tools: bool | None = None
    json_schema: bool | None = None
    vision: bool | None = None
    prompt_cache: bool | None = None


class CapabilityTable(BaseModel):
    roles: dict[str, dict[str, str]]
    models: dict[str, ModelOverride] = {}


def load_table(path: Path | None = None) -> CapabilityTable:
    return CapabilityTable.model_validate(yaml.safe_load((path or _DEFAULT).read_text(encoding="utf-8")))
```
`llm/registry.py`:
```python
"""Provider registry (spec §7.1–7.3). The only module that imports litellm (AGENTS.md, spec §19.7)."""

import os

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"  # never fetch the cost map over the network

from collections.abc import Mapping, Sequence  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Literal  # noqa: E402

import litellm  # noqa: E402
from langchain_core.language_models import BaseChatModel  # noqa: E402
from langchain_litellm import ChatLiteLLM  # noqa: E402

from researchly.config import Settings  # noqa: E402
from researchly.llm.capabilities import CapabilityTable, ModelOverride, load_table  # noqa: E402
from researchly.security.keychain import get_secret  # noqa: E402

litellm.telemetry = False

Role = Literal["strong", "mid", "cheap", "critic"]


class NoModelForRole(LookupError):
    pass


class ModelInfo(ModelOverride):
    pass


@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    key_ref: str | None = None
    base_url: str | None = None
    models: Mapping[str, str] = field(default_factory=dict)


def resolve_model(role: Role, providers: Sequence[ProviderSpec], table: CapabilityTable) -> tuple[ProviderSpec, str]:
    if not providers:
        raise NoModelForRole("no LLM provider configured; add one in Settings")
    spec, lookup = providers[0], role
    if role == "critic" and "critic" not in spec.models:
        others = [p for p in providers[1:] if p.provider != spec.provider]
        spec, lookup = (others[0] if others else spec), "strong"
    model = spec.models.get(lookup) or table.roles.get(spec.provider, {}).get(lookup)
    if model is None:
        raise NoModelForRole(f"{spec.provider} has no model for role {role!r}; set one in Settings")
    return spec, model


def model_info(model: str, table: CapabilityTable | None = None) -> ModelInfo:
    try:
        raw = litellm.get_model_info(model)
    except Exception:  # unknown to LiteLLM's map: overrides only
        raw = {}
    base = ModelInfo(
        input_per_mtok=(raw.get("input_cost_per_token") or 0) * 1e6 or None,
        cached_per_mtok=(raw.get("cache_read_input_token_cost") or 0) * 1e6 or None,
        output_per_mtok=(raw.get("output_cost_per_token") or 0) * 1e6 or None,
        context_window=raw.get("max_input_tokens"),
        max_output=raw.get("max_output_tokens"),
        tools=raw.get("supports_function_calling"),
        json_schema=raw.get("supports_response_schema"),
        vision=raw.get("supports_vision"),
        prompt_cache=raw.get("supports_prompt_caching"),
    )
    override = (table or load_table()).models.get(model)
    return base.model_copy(update=override.model_dump(exclude_none=True)) if override else base


def cost_usd(model: str, in_tok: int, cached_tok: int, out_tok: int, table: CapabilityTable | None = None) -> float:
    info = model_info(model, table)
    input_price = info.input_per_mtok or 0.0
    cached_price = info.cached_per_mtok if info.cached_per_mtok is not None else input_price
    return ((in_tok - cached_tok) * input_price + cached_tok * cached_price + out_tok * (info.output_per_mtok or 0.0)) / 1e6


def chat_model(
    role: Role, providers: Sequence[ProviderSpec], settings: Settings, table: CapabilityTable | None = None
) -> BaseChatModel:
    spec, name = resolve_model(role, providers, table or load_table())
    api_key = get_secret(spec.key_ref) if spec.key_ref else None  # fetched per construction, never stored
    return ChatLiteLLM(model=name, api_key=api_key, api_base=spec.base_url)
```
`model_info`'s `except Exception` is intentional: LiteLLM raises a bare `Exception` for unknown models. Leave that comment in place.

- [ ] **Step 4: Run, expect pass**: `uv run --directory backend pytest -q`, then `ruff check` and `pyright`. If `litellm.get_model_info("anthropic/claude-sonnet-5-5")` uses different key names in 1.105.1, print `litellm.get_model_info(...)` once, match the names, and keep the test expectations unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend/src/researchly/llm backend/tests/llm
git commit -m "feat(llm): capability table, role routing and LiteLLM-backed provider registry"
```

---

### Task 6: LLM call ledger

**Files:**
- Create: `backend/src/researchly/llm/ledger.py`
- Test: `backend/tests/llm/test_ledger.py`

**Interfaces:**
- Consumes: `LlmCall`, `make_engine`, `make_sessionmaker`, `migrate.upgrade`, `cost_usd`, and the `db_url` fixture.
- Produces: `LedgerCallback(sessions: async_sessionmaker[AsyncSession], *, research_run_id: uuid.UUID | None, agent: str, role: str, provider: str, model: str)`, an `AsyncCallbackHandler` with `raise_error = True`. It writes one `llm_call` row per model call: status `"ok"` with usage, or `"error"` with zero tokens.

- [ ] **Step 1: Failing test** — `test_ledger.py`:
```python
import uuid
from decimal import Decimal

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from sqlalchemy import select

from researchly.db import migrate
from researchly.db.engine import make_engine, make_sessionmaker
from researchly.db.models import LlmCall
from researchly.llm.ledger import LedgerCallback

pytestmark = [pytest.mark.db, pytest.mark.anyio]


async def test_ledger_writes_usage_and_cost(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    sessions = make_sessionmaker(engine)
    run_id = uuid.uuid4()
    reply = AIMessage(
        content="hi",
        usage_metadata={"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200, "input_token_details": {"cache_read": 400}},
    )
    model = GenericFakeChatModel(messages=iter([reply]))
    cb = LedgerCallback(sessions, research_run_id=run_id, agent="lead", role="mid", provider="anthropic", model="anthropic/claude-sonnet-5-5")
    await model.ainvoke("hello", config={"callbacks": [cb]})
    async with sessions() as s:
        row = (await s.scalars(select(LlmCall))).one()
    await engine.dispose()
    assert (row.run_id, row.agent, row.role, row.status) == (run_id, "lead", "mid", "ok")
    assert (row.in_tok, row.cached_tok, row.out_tok) == (1000, 400, 200)
    assert row.cost_usd == Decimal("0.003240")
    assert row.latency_ms >= 0
```

- [ ] **Step 2: Run, expect failure** (`ModuleNotFoundError: researchly.llm.ledger`).

- [ ] **Step 3: Implement** — `llm/ledger.py`:
```python
"""LLM call ledger (spec §7.5): one llm_call row per model call; feeds budgets and usage."""

import time
import uuid
from decimal import Decimal
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from researchly.db.models import LlmCall
from researchly.llm.registry import cost_usd


class LedgerCallback(AsyncCallbackHandler):
    raise_error = True  # a lost ledger row would break budgets; never swallow it

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        research_run_id: uuid.UUID | None,
        agent: str,
        role: str,
        provider: str,
        model: str,
    ) -> None:
        self.sessions = sessions
        self.research_run_id = research_run_id
        self.agent, self.role, self.provider, self.model = agent, role, provider, model
        self._started: dict[uuid.UUID, float] = {}

    async def on_chat_model_start(
        self, serialized: dict[str, Any], messages: list[list[BaseMessage]], *, run_id: uuid.UUID, **kwargs: Any
    ) -> None:
        self._started[run_id] = time.perf_counter()

    async def on_llm_end(self, response: LLMResult, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        generation = response.generations[0][0]
        usage = (generation.message.usage_metadata if isinstance(generation, ChatGeneration) else None) or {}
        cached = (usage.get("input_token_details") or {}).get("cache_read", 0)
        await self._write(run_id, "ok", usage.get("input_tokens", 0), cached, usage.get("output_tokens", 0))

    async def on_llm_error(self, error: BaseException, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        await self._write(run_id, "error", 0, 0, 0)

    async def _write(self, lc_run_id: uuid.UUID, status: str, in_tok: int, cached: int, out_tok: int) -> None:
        started = self._started.pop(lc_run_id, time.perf_counter())
        cost = Decimal(str(round(cost_usd(self.model, in_tok, cached, out_tok), 6)))
        async with self.sessions() as session, session.begin():
            session.add(
                LlmCall(
                    run_id=self.research_run_id,
                    agent=self.agent,
                    role=self.role,
                    provider=self.provider,
                    model=self.model,
                    in_tok=in_tok,
                    cached_tok=cached,
                    out_tok=out_tok,
                    cost_usd=cost,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    status=status,
                )
            )
```

- [ ] **Step 4: Run, expect pass**: `uv run --directory backend pytest -q`, then `ruff check` and `pyright`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/researchly/llm/ledger.py backend/tests/llm/test_ledger.py
git commit -m "feat(llm): ledger callback writing one llm_call row per model call"
```

---

### Task 7: RecordReplay

**Files:**
- Create: `backend/src/researchly/llm/record_replay.py`
- Modify: `backend/src/researchly/llm/registry.py` (replay branch in `chat_model`)
- Test: `backend/tests/llm/test_record_replay.py`, plus `backend/tests/llm/test_registry.py` (one new test)

**Interfaces:**
- Consumes: `redact`, `Settings.llm_fixtures*`.
- Produces:
  - `FixtureMissing(LookupError)`.
  - `fixture_key(request: dict[str, Any]) -> str`.
  - `RecordReplayChatModel(inner: BaseChatModel | None, mode: Literal["record", "replay"], path: Path, role: str, model_alias: str)`.
  - `fixtures_path(settings: Settings) -> Path`, which returns `settings.llm_fixtures_dir / settings.llm_fixtures_suite / "calls.jsonl"`.

Spec §7.6 behaviour:
- **Key:** `sha256(canonical_json(messages, tools, response_schema, role, model_alias))`. Messages are reduced to `type`, `content`, `tool_calls`, `tool_call_id` and `name`. Their random ids and response metadata are left out, so the key is stable.
- **Storage:** one JSONL file per suite, append-only, one `{key, request, response}` per line. The whole line goes through `redact()` before writing.
- **Replay:** a missing key raises `FixtureMissing` with the key and a unified diff against the nearest stored request.
- **Model alias:** fixtures are recorded through the dev provider (spec §7.7, §23.2), so the alias is always `claude-code/<dev_provider_model>`. Replay never builds a real provider.

- [ ] **Step 1: Failing tests** — `test_record_replay.py`:
```python
import json

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from researchly.llm.record_replay import FixtureMissing, RecordReplayChatModel
from researchly.security.redact import register_secret


@tool
def lookup(query: str) -> str:
    """Look something up."""
    return query


def recorder(path, reply):
    inner = GenericFakeChatModel(messages=iter([reply]))
    return RecordReplayChatModel(inner=inner, mode="record", path=path, role="cheap", model_alias="claude-code/sonnet")


def replayer(path):
    return RecordReplayChatModel(inner=None, mode="replay", path=path, role="cheap", model_alias="claude-code/sonnet")


@pytest.mark.anyio
async def test_record_then_replay_roundtrip(tmp_path):
    path = tmp_path / "suite" / "calls.jsonl"
    reply = AIMessage(content="answer", usage_metadata={"input_tokens": 3, "output_tokens": 1, "total_tokens": 4})
    assert (await recorder(path, reply).ainvoke("question")).content == "answer"
    replayed = await replayer(path).ainvoke("question")
    assert replayed.content == "answer"
    assert replayed.usage_metadata["output_tokens"] == 1
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.anyio
async def test_tool_calls_survive_and_tools_change_the_key(tmp_path):
    path = tmp_path / "calls.jsonl"
    reply = AIMessage(content="", tool_calls=[{"name": "lookup", "args": {"query": "x"}, "id": "call_1"}])
    await recorder(path, reply).bind_tools([lookup]).ainvoke("q")
    replayed = await replayer(path).bind_tools([lookup]).ainvoke("q")
    assert replayed.tool_calls[0]["name"] == "lookup"
    with pytest.raises(FixtureMissing):
        await replayer(path).ainvoke("q")  # same prompt without tools: different key


@pytest.mark.anyio
async def test_missing_fixture_reports_key_and_diff(tmp_path):
    path = tmp_path / "calls.jsonl"
    await recorder(path, AIMessage(content="a")).ainvoke("the original question")
    with pytest.raises(FixtureMissing) as err:
        await replayer(path).ainvoke("the changed question")
    message = str(err.value)
    assert "key" in message and "changed" in message and "original" in message


@pytest.mark.anyio
async def test_recording_scrubs_secrets(tmp_path):
    register_secret("super-secret-token-123")
    path = tmp_path / "calls.jsonl"
    await recorder(path, AIMessage(content="token is super-secret-token-123 mail me@example.org")).ainvoke("hi")
    text = path.read_text(encoding="utf-8")
    assert "super-secret-token-123" not in text and "me@example.org" not in text
    json.loads(text)  # still valid JSON
```
Append to `test_registry.py`:
```python
def test_replay_mode_needs_no_provider(tmp_path):
    from researchly.llm.record_replay import RecordReplayChatModel

    s = Settings(env="test", llm_fixtures="replay", llm_fixtures_dir=tmp_path, llm_fixtures_suite="smoke")
    model = chat_model("strong", [], s, T)
    assert isinstance(model, RecordReplayChatModel)
    assert (model.mode, model.model_alias, model.role) == ("replay", "claude-code/sonnet", "strong")
    assert model.path == tmp_path / "smoke" / "calls.jsonl"
```

- [ ] **Step 2: Run, expect failure** (`ModuleNotFoundError`).

- [ ] **Step 3: Implement** — `llm/record_replay.py`:
```python
"""RecordReplay (spec §7.6): wraps a chat model; records to / replays from committed JSONL fixtures."""

import difflib
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, message_to_dict, messages_from_dict
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.utils.function_calling import convert_to_openai_tool

from researchly.config import Settings
from researchly.security.redact import redact


class FixtureMissing(LookupError):
    pass


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def fixture_key(request: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(request).encode("utf-8")).hexdigest()


def fixtures_path(settings: Settings) -> Path:
    return settings.llm_fixtures_dir / settings.llm_fixtures_suite / "calls.jsonl"


def _message(m: BaseMessage) -> dict[str, Any]:
    return {
        "type": m.type,
        "content": m.content,
        "tool_calls": getattr(m, "tool_calls", []),
        "tool_call_id": getattr(m, "tool_call_id", None),
        "name": m.name,
    }


class RecordReplayChatModel(BaseChatModel):
    inner: BaseChatModel | None = None
    mode: Literal["record", "replay"]
    path: Path
    role: str
    model_alias: str

    @property
    def _llm_type(self) -> str:
        return "record-replay"

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: str | None = None, **kwargs: Any) -> Runnable[LanguageModelInput, AIMessage]:
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        return self.bind(tools=[convert_to_openai_tool(t) for t in tools], **kwargs)

    def _request(self, messages: list[BaseMessage], kwargs: dict[str, Any]) -> dict[str, Any]:
        return {
            "messages": [_message(m) for m in messages],
            "tools": kwargs.get("tools"),
            "response_schema": kwargs.get("response_format"),
            "role": self.role,
            "model_alias": self.model_alias,
        }

    def _entries(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return {e["key"]: e for e in map(json.loads, filter(None, lines))}  # ponytail: re-read per call; index if suites grow large

    def _replay(self, request: dict[str, Any]) -> ChatResult:
        key = fixture_key(request)
        entries = self._entries()
        if key not in entries:
            raise FixtureMissing(self._miss_report(key, request, entries))
        message = messages_from_dict([entries[key]["response"]])[0]
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _miss_report(self, key: str, request: dict[str, Any], entries: dict[str, dict[str, Any]]) -> str:
        head = f"no LLM fixture for key {key} in {self.path}"
        if not entries:
            return head + " (file empty or missing; record with RESEARCHLY_LLM_FIXTURES=record)"
        wanted = json.dumps(request, indent=1, sort_keys=True, default=str).splitlines()
        nearest = max(
            entries.values(),
            key=lambda e: difflib.SequenceMatcher(None, _canonical(e["request"]), _canonical(request)).ratio(),
        )
        stored = json.dumps(nearest["request"], indent=1, sort_keys=True, default=str).splitlines()
        diff = list(difflib.unified_diff(stored, wanted, f"nearest {nearest['key'][:12]}", "requested", lineterm=""))
        return head + "\n" + "\n".join(diff[:40])

    def _record(self, request: dict[str, Any], result: ChatResult) -> None:
        key = fixture_key(request)
        if key in self._entries():
            return
        line = {"key": key, "request": request, "response": message_to_dict(result.generations[0].message)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(redact(_canonical(line)) + "\n")

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any
    ) -> ChatResult:
        request = self._request(messages, kwargs)
        if self.mode == "replay":
            return self._replay(request)
        assert self.inner is not None, "record mode needs an inner model"
        result = self.inner._generate(messages, stop=stop, **kwargs)
        self._record(request, result)
        return result

    async def _agenerate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: AsyncCallbackManagerForLLMRun | None = None, **kwargs: Any
    ) -> ChatResult:
        request = self._request(messages, kwargs)
        if self.mode == "replay":
            return self._replay(request)
        assert self.inner is not None, "record mode needs an inner model"
        result = await self.inner._agenerate(messages, stop=stop, **kwargs)
        self._record(request, result)
        return result
```
In `registry.py`, add at the top of `chat_model`:
```python
    if settings.llm_fixtures == "replay":
        from researchly.llm.record_replay import RecordReplayChatModel, fixtures_path

        return RecordReplayChatModel(
            mode="replay", path=fixtures_path(settings), role=role, model_alias=f"claude-code/{settings.dev_provider_model}"
        )
```

- [ ] **Step 4: Run, expect pass**: `uv run --directory backend pytest -q`, then `ruff check` and `pyright`. If redaction leaves invalid JSON, fix the redaction patterns. Never skip scrubbing.

- [ ] **Step 5: Commit**

```bash
git add backend/src/researchly/llm backend/tests/llm
git commit -m "feat(llm): RecordReplay chat model with scrubbed JSONL fixtures and diff-on-miss"
```

---

### Task 8: Dev-only `claude-code` provider + gating + release test

**Files:**
- Create: `backend/src/researchly/llm/dev/__init__.py` (empty), `backend/src/researchly/llm/dev/claude_code.py`
- Modify: `backend/src/researchly/llm/registry.py` (dev and record branches)
- Test: `backend/tests/llm/test_claude_code.py`, `backend/tests/llm/test_release.py`

**Interfaces:**
- Consumes: `RecordReplayChatModel`, `fixtures_path`, `Settings`.
- Produces: `ClaudeCodeChatModel(model: Literal["sonnet", "haiku"] = "sonnet")`, a `BaseChatModel` that supports `bind_tools`, `with_structured_output` (through tool emulation), `invoke` and `ainvoke`.

Design (spec §7.7):
- Each call runs `claude_agent_sdk.query()` with `ClaudeAgentOptions(tools=[], system_prompt=<app system + tool catalogue>, max_turns=1, setting_sources=[], cwd=<empty temp dir>, model=<alias>, output_format=<schema when tools are bound>)`.
- **Tool calls are emulated.** The bound tools (name, description and parameters) are listed in the system prompt. The structured-output schema is a flat object: `{"type": "final" | "tool_calls", "content": str, "calls": [{"name", "args"}]}`. A flat object avoids the `oneOf` that the spec sketches: the structured-output JSON-schema subset handles a top-level `oneOf` poorly, and the flat object keeps the same two shapes.
- When `tool_choice` forces a call (`with_structured_output` uses `"any"`), `type` is narrowed to `"tool_calls"`.
- Windows: the SDK spawns the CLI as a subprocess, which Selector loops cannot do. So the async path runs the sync path in a thread, and the sync path runs `asyncio.run(..., loop_factory=ProactorEventLoop)` on Windows.
- Streaming is not implemented. LangChain falls back to `generate`, which is enough for recording. Add it if live dev sessions need token streaming.

- [ ] **Step 1: Failing tests**

`test_claude_code.py`:
```python
import os

import pytest
from claude_agent_sdk import ResultMessage
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from researchly.llm.dev import claude_code
from researchly.llm.dev.claude_code import ClaudeCodeChatModel


@tool
def get_weather(city: str) -> str:
    """Current weather for a city."""
    return city


class Verdict(BaseModel):
    label: str
    score: float


def fake_query(structured=None, result=None, seen=None):
    async def _query(*, prompt, options):
        if seen is not None:
            seen.update(prompt=prompt, options=options)
        yield ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1, session_id="s",
            result=result, structured_output=structured,
            usage={"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 7},
        )
    return _query


def test_plain_text_reply_and_isolated_options(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(claude_code, "query", fake_query(result="hello", seen=seen))
    reply = ClaudeCodeChatModel().invoke([SystemMessage("be brief"), HumanMessage("hi")])
    assert reply.content == "hello"
    assert reply.usage_metadata == {"input_tokens": 15, "output_tokens": 7, "total_tokens": 22, "input_token_details": {"cache_read": 5}}
    o = seen["options"]
    assert (o.tools, o.max_turns, o.setting_sources, o.model, o.output_format) == ([], 1, [], "sonnet", None)
    assert o.system_prompt == "be brief"
    assert "hi" in seen["prompt"]


@pytest.mark.anyio
async def test_tool_calls_are_emulated(monkeypatch):
    seen: dict = {}
    structured = {"type": "tool_calls", "content": "", "calls": [{"name": "get_weather", "args": {"city": "Paris"}}]}
    monkeypatch.setattr(claude_code, "query", fake_query(structured=structured, seen=seen))
    reply = await ClaudeCodeChatModel().bind_tools([get_weather]).ainvoke("weather in Paris?")
    assert reply.tool_calls[0]["name"] == "get_weather"
    assert reply.tool_calls[0]["args"] == {"city": "Paris"}
    assert "get_weather" in seen["options"].system_prompt
    assert seen["options"].output_format["type"] == "json_schema"


def test_structured_output_forces_a_call(monkeypatch):
    seen: dict = {}
    structured = {"type": "tool_calls", "content": "", "calls": [{"name": "Verdict", "args": {"label": "ok", "score": 0.9}}]}
    monkeypatch.setattr(claude_code, "query", fake_query(structured=structured, seen=seen))
    verdict = ClaudeCodeChatModel().with_structured_output(Verdict).invoke("judge")
    assert verdict == Verdict(label="ok", score=0.9)
    assert seen["options"].output_format["schema"]["properties"]["type"]["enum"] == ["tool_calls"]


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("RESEARCHLY_LIVE_DEV_PROVIDER") != "1", reason="opt-in: uses the local Claude Code login")
def test_live_tool_call_roundtrip():
    reply = ClaudeCodeChatModel(model="haiku").bind_tools([get_weather]).invoke("What's the weather in Paris? Use the tool.")
    assert reply.tool_calls and reply.tool_calls[0]["name"] == "get_weather"
```
`test_release.py`:
```python
import shutil
import subprocess
import zipfile
from pathlib import Path

import tomllib
from langchain_litellm import ChatLiteLLM

from researchly.config import Settings
from researchly.llm.dev.claude_code import ClaudeCodeChatModel
from researchly.llm.record_replay import RecordReplayChatModel
from researchly.llm.registry import ProviderSpec, chat_model

BACKEND = Path(__file__).resolve().parents[2]
ANTHROPIC = [ProviderSpec("anthropic")]


def test_dev_provider_only_in_development():
    dev = Settings(env="development", dev_provider="claude-code")
    assert isinstance(chat_model("mid", ANTHROPIC, dev), ClaudeCodeChatModel)
    prod = Settings(env="production", dev_provider="claude-code")
    assert isinstance(chat_model("mid", ANTHROPIC, prod), ChatLiteLLM)


def test_record_mode_wraps_dev_provider(tmp_path):
    s = Settings(env="development", dev_provider="claude-code", llm_fixtures="record", llm_fixtures_dir=tmp_path)
    model = chat_model("cheap", ANTHROPIC, s)
    assert isinstance(model, RecordReplayChatModel) and isinstance(model.inner, ClaudeCodeChatModel)


def test_record_mode_refuses_real_providers(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="RESEARCHLY_DEV_PROVIDER"):
        chat_model("cheap", ANTHROPIC, Settings(env="test", llm_fixtures="record", llm_fixtures_dir=tmp_path))


def test_release_wheel_excludes_dev_provider(tmp_path):
    deps = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    assert not any(d.startswith("claude-agent-sdk") for d in deps)
    uv = shutil.which("uv")
    assert uv, "uv must be on PATH"
    subprocess.run([uv, "build", "--wheel", "--out-dir", str(tmp_path)], cwd=BACKEND, check=True, capture_output=True)
    names = zipfile.ZipFile(next(tmp_path.glob("*.whl"))).namelist()
    assert "researchly/llm/registry.py" in names
    assert not [n for n in names if n.startswith("researchly/llm/dev/")]
```

- [ ] **Step 2: Run, expect failure** (`ModuleNotFoundError: researchly.llm.dev`).

- [ ] **Step 3: Implement** — `llm/dev/claude_code.py`:
```python
"""Dev-only provider (spec §7.7): answers the app's LLM calls through the developer's local Claude Code login.

Never shipped (excluded from the wheel) and never used in CI.
"""

import asyncio
import json
import sys
import tempfile
import uuid
from collections.abc import Sequence
from typing import Any, Literal

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.utils.function_calling import convert_to_openai_tool

_LOOP = asyncio.ProactorEventLoop if sys.platform == "win32" else None  # the SDK spawns a subprocess


def _render(messages: list[BaseMessage]) -> tuple[str, str]:
    system = "\n\n".join(m.text for m in messages if isinstance(m, SystemMessage))
    turns: list[str] = []
    for m in messages:
        if isinstance(m, SystemMessage):
            continue
        if isinstance(m, ToolMessage):
            turns.append(f"[tool result {m.tool_call_id}]\n{m.text}")
        elif isinstance(m, AIMessage) and m.tool_calls:
            turns.append(f"[assistant]\n{m.text}\n[tool calls] {json.dumps(m.tool_calls, default=str)}")
        else:
            turns.append(f"[{m.type}]\n{m.text}")
    return system, "\n\n".join(turns)


def _catalogue(tools: list[dict[str, Any]]) -> str:
    lines = ["You can call these tools. To call, reply with type=tool_calls; otherwise type=final with content."]
    for t in tools:
        fn = t["function"]
        lines.append(f"- {fn['name']}: {fn.get('description', '')}\n  parameters: {json.dumps(fn.get('parameters', {}))}")
    return "\n".join(lines)


def _schema(forced: bool) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["tool_calls"] if forced else ["final", "tool_calls"]},
            "content": {"type": "string"},
            "calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "args": {"type": "object"}},
                    "required": ["name", "args"],
                },
            },
        },
        "required": ["type"],
    }


def _usage(result: ResultMessage) -> UsageMetadata:
    u = result.usage or {}
    cached = u.get("cache_read_input_tokens", 0)
    inputs = u.get("input_tokens", 0) + cached + u.get("cache_creation_input_tokens", 0)
    out = u.get("output_tokens", 0)
    return {"input_tokens": inputs, "output_tokens": out, "total_tokens": inputs + out, "input_token_details": {"cache_read": cached}}


class ClaudeCodeChatModel(BaseChatModel):
    model: Literal["sonnet", "haiku"] = "sonnet"

    @property
    def _llm_type(self) -> str:
        return "claude-code"

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: str | None = None, **kwargs: Any) -> Runnable[LanguageModelInput, AIMessage]:
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        return self.bind(tools=[convert_to_openai_tool(t) for t in tools], **kwargs)

    async def _run(self, system: str, prompt: str, output_format: dict[str, Any] | None) -> ResultMessage:
        with tempfile.TemporaryDirectory() as cwd:
            options = ClaudeAgentOptions(
                tools=[], system_prompt=system, max_turns=1, setting_sources=[], cwd=cwd, model=self.model, output_format=output_format
            )
            result: ResultMessage | None = None
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, ResultMessage):
                    result = message
        if result is None or result.is_error:
            raise RuntimeError(f"claude-code provider failed: {result.subtype if result else 'no result message'}")
        return result

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any
    ) -> ChatResult:
        system, prompt = _render(messages)
        tools: list[dict[str, Any]] = kwargs.get("tools") or []
        output_format = None
        if tools:
            system = f"{system}\n\n{_catalogue(tools)}".strip()
            forced = kwargs.get("tool_choice") not in (None, "auto", "none")
            output_format = {"type": "json_schema", "schema": _schema(forced)}
        result = asyncio.run(self._run(system, prompt, output_format), loop_factory=_LOOP)
        data = result.structured_output if tools else None
        if data and data.get("type") == "tool_calls":
            calls = [{"name": c["name"], "args": c["args"], "id": f"call_{uuid.uuid4().hex[:24]}", "type": "tool_call"} for c in data.get("calls", [])]
            message = AIMessage(content=data.get("content", ""), tool_calls=calls, usage_metadata=_usage(result))
        else:
            content = data.get("content", "") if data else (result.result or "")
            message = AIMessage(content=content, usage_metadata=_usage(result))
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: AsyncCallbackManagerForLLMRun | None = None, **kwargs: Any
    ) -> ChatResult:
        return await asyncio.to_thread(self._generate, messages, stop, None, **kwargs)
```
In `registry.py` `chat_model`, after the replay branch and before `resolve_model`:
```python
    if settings.env == "development" and settings.dev_provider == "claude-code":
        from researchly.llm.dev.claude_code import ClaudeCodeChatModel  # absent from release wheels

        dev = ClaudeCodeChatModel(model=settings.dev_provider_model)
        if settings.llm_fixtures == "record":
            from researchly.llm.record_replay import RecordReplayChatModel, fixtures_path

            return RecordReplayChatModel(
                inner=dev, mode="record", path=fixtures_path(settings), role=role, model_alias=f"claude-code/{settings.dev_provider_model}"
            )
        return dev
    if settings.llm_fixtures == "record":
        raise ValueError("recording fixtures needs RESEARCHLY_ENV=development and RESEARCHLY_DEV_PROVIDER=claude-code")
```

- [ ] **Step 4: Run, expect pass**: `uv run --directory backend pytest -q`, then `ruff check` and `pyright`. The live test is deselected by `-m 'not live'`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/researchly/llm backend/tests/llm
git commit -m "feat(llm): dev-only claude-code provider with tool emulation, gating and release-wheel check"
```

- [ ] **Step 6 (controller, after merge): live smoke**

Run: `$env:RESEARCHLY_LIVE_DEV_PROVIDER='1'; uv run --directory backend pytest -m live -q`
Expected: 1 passed. If the result is `error_max_turns`, structured output consumed a turn. In that case set `max_turns=2` in `_run`, update the option assertion in `test_plain_text_reply_and_isolated_options`, rerun, commit `fix(llm): allow structured-output turn in claude-code provider`, and note the cause in `docs/progress.md`.

---

### Task 9: CI backend job, pins, progress, PR (controller)

**Files:**
- Modify: `.github/workflows/ci.yml`, `docs/progress.md`

- [ ] **Step 1: Read the locked pytest version**: `Select-String -Path backend/uv.lock -Pattern '^name = "pytest"$' -Context 0,1`. Use the version on the next line as `<PYTEST>` below.

- [ ] **Step 2: Edit `ci.yml`**
  - In `repo-checks`, give the setup-uv step `with: { version: "0.12.10" }` and change the test step to `--with pytest==<PYTEST>`.
  - Add this job:
```yaml
  backend:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    services:
      postgres:
        image: pgvector/pgvector:pg18
        env:
          POSTGRES_PASSWORD: postgres
        ports: ["5432:5432"]
        options: >-
          --health-cmd pg_isready --health-interval 5s --health-timeout 5s --health-retries 12
    env:
      RESEARCHLY_TEST_DATABASE_URL: postgresql+psycopg://postgres:postgres@localhost:5432/postgres
    defaults:
      run:
        working-directory: backend
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@07a047e608db84b3d8c06cd9a7c7dcc9c355c18e # v10.3.2
        with:
          version: "0.12.10"
          enable-cache: true
          cache-dependency-glob: backend/uv.lock
      - run: uv sync --locked
      - run: uv run ruff check
      - run: uv run ruff format --check
      - run: uv run pyright
      - run: uv run pytest -q
```

- [ ] **Step 3: Full local verification**: the hook tests, `uv run --no-project --python 3.12 scripts/repo_checks.py`, and the full backend suite through `test-runner`. Expect all green, with the DB tests running against Docker rather than skipping.

- [ ] **Step 4: Progress + push + PR**
  - Append a step 1b entry to `docs/progress.md` covering the outcomes, the live-smoke result, and what was deferred:
    - the keychain encrypted-file fallback, deferred to 1c;
    - SPA token delivery, deferred to 1d;
    - dev-provider streaming;
    - the fixture secret-scan CI check, due with the first committed fixtures.
  - Delete the resolved carry-over items.
  - Commit `docs: record step 1b outcome`, then `git push -u origin feat/step1b-backend-core`.
  - Open the PR with `gh pr create`; its body ends with the Claude Code attribution line. Wait for green CI.
