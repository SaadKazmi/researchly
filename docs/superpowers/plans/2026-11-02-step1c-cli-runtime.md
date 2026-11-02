# Step 1c — CLI, Runtime Selection and Native Postgres Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `researchly setup | start | stop | status | doctor | config` work end to end.
- The best runtime is picked automatically: Docker mode when a container engine exists (started for the user if it is stopped), else native mode.
- Native mode runs pg0-embedded PostgreSQL 18 plus a supervised app.
- Docker mode runs a compose stack: custom Postgres image with pg_textsearch, app, SearXNG, optional GROBID.
- The pg0 spike is measured on all five CI targets and recorded in an ADR.

**Architecture:**
- A new uv project `cli/` (distribution `researchly-cli`, module `researchly_cli`, console script `researchly`). It depends on the backend through a path source, because native mode runs the app and the migrations from the CLI's own environment.
- Database provisioning (roles, database, untrusted extensions) lives in the backend (`researchly.db.provision`) so both modes share it. Native mode calls it in-process; Docker mode runs it as a one-shot `migrate` service.
- Native supervision: a detached `python -m researchly_cli.supervise` process restarts the app with backoff. It sits inside a kill-on-close Job Object on Windows and in its own process group on POSIX. pg0 runs PostgreSQL as its own daemon and is stopped explicitly.

**Tech Stack:** Python 3.12, uv 0.12.10, Typer, platformdirs, psutil, pg0-embedded 0.15.2, cryptography (AES-GCM), Docker Compose v2, `pgvector/pgvector:0.8.7-pg18-trixie`, pg_textsearch 1.5.1, `searxng/searxng:2026.10.25-54ca1d9cf`, `grobid/grobid:0.9.1-crf`.

**Spec:** `docs/superpowers/specs/2026-10-28-researchly-design.md`: §2 D2, §4.1, §4.2, §4.3, §16.1, §19.2, §19.7, §22, §24 step 1, §26 risks 1 and "engine auto-start". Research: `docs/research/2026-11-02-step1c-cli-runtime.md` (read its last section, "Local verification by the controller").

## Global Constraints

- All Python commands go through `uv` (`uv run --directory backend …`, `uv run --directory cli …`); the system `python` is 3.8.
- Keyless and free by default (spec D14): nothing here may need a paid service. **Never install a container engine** for the user and never use `sudo`; when elevation is needed, print the one command and fall back to native (spec §4.1).
- Secrets come from `researchly.security.keychain` only.
  - They are passed to child processes and compose as environment variables at start and are never written to compose files, `state.json`, `config.json` or logs (spec §16.1).
  - DB passwords and tokens are `secrets.token_urlsafe(32)` values (43 URL-safe characters).
- `pg0.Pg0.drop()` deletes the data directory. Never call it outside test teardown.
- Native PostgreSQL data lives in `<data_dir>/pg/18/native`. Docker bind-mounts `<data_dir>/pg` at `/var/lib/postgresql`, so its data lands in `<data_dir>/pg/18/docker`. `data_dir` is `RESEARCHLY_DATA_DIR`, else `~/.researchly`.
- Exact pins:
  - `pg0-embedded==0.15.2` (bundles PG 18.1 + pgvector 0.8.5; schema must not need pgvector > 0.8.5).
  - Images: `pgvector/pgvector:0.8.7-pg18-trixie`, pg_textsearch `1.5.1` (sha256 `5b90a160c4aa9ab78c72ab41eb761146a601485bff5e271f05a44d3da7493d5b`), `searxng/searxng:2026.10.25-54ca1d9cf`, `grobid/grobid:0.9.1-crf`, `python:3.12-slim-trixie`, `ghcr.io/astral-sh/uv:0.12.10`.
- `cli/pyproject.toml` copies the backend's `[tool.uv] exclude-newer = "7 days"` and `exclude-newer-package = { litellm = "14 days" }` (spec §19.7).
- Tests:
  - Unit tests never touch the real OS keychain (in-memory backend), the real data dir, or the network.
  - `@pytest.mark.native` tests start a real pg0 PostgreSQL and run by default.
  - `@pytest.mark.docker` tests run only with `RESEARCHLY_TEST_DOCKER=1`.
  - `@pytest.mark.db` (backend) needs Postgres as in step 1b.
- The CLI's localhost health probe uses stdlib `urllib`. It is not "outbound HTTP" in the PoliteClient sense, which governs connectors.
- ruff (line length 120, rules E,F,I,UP,B,SIM,ASYNC,S; S101 ignored) and pyright `standard` must be clean in both projects.
- Conventional Commits; every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File map

```
.claude/hooks/{format,typecheck}.py (+ tests)   Task 0: treat cli/ like backend/
backend/src/researchly/config.py                 + data_dir, local_token
backend/src/researchly/app.py                    token from Settings.local_token before the keychain
backend/src/researchly/security/keychain.py      + ensure_secret, EncryptedFileKeyring fallback, store_name
backend/src/researchly/db/provision.py           provision() + setup(); `python -m researchly.db.provision`
backend/pyproject.toml, uv.lock                  + cryptography (direct)
backend/tests/conftest.py                        autouse RESEARCHLY_DATA_DIR → tmp
backend/tests/{test_app.py, security/test_keychain.py, db/test_provision.py}
cli/pyproject.toml, cli/uv.lock
cli/src/researchly_cli/__init__.py
cli/src/researchly_cli/state.py                  data/log dirs, config.json, state.json, Secrets, ports, health, wait_until
cli/src/researchly_cli/supervise.py              spawn detached, Job Object, restart-with-backoff, terminate_tree
cli/src/researchly_cli/engines.py                engine detection, auto-start, runtime selection
cli/src/researchly_cli/native.py                 NativePostgres (pg0) + native start/stop
cli/src/researchly_cli/docker.py                 compose start/stop/build
cli/src/researchly_cli/main.py                   Typer app: config, setup, start, stop, status, doctor
cli/tests/{conftest,test_state,test_config_cmd,test_supervise,test_engines,test_native,test_docker,test_main}.py
infra/postgres/Dockerfile                        pgvector + pg_textsearch (source build, checksum-verified)
infra/app.Dockerfile, infra/app.Dockerfile.dockerignore
infra/compose.yaml, infra/searxng/settings.yml
.github/workflows/ci.yml                         Task 9: cli matrix (5 targets) + docker job
docs/adr/0002-native-postgres-pg0.md             Task 9: spike results
AGENTS.md, docs/progress.md                      Task 9
```

Tasks 0 and 9 edit harness control-plane files (`.claude/**`, `.github/workflows/**`, `AGENTS.md`), so the **controller** does them in the main checkout. Tasks 1–8 go to `implementer` subagents.

Rulings already made (see ledger):
- The keychain fallback uses a machine key only. The spec allows "passphrase **or** machine key"; a passphrase can be added when a user needs it.
- The pre-migration backup belongs to `researchly backup` (step 9).
- `doctor` reports migration state once the app exposes it (Settings → System, step 9).
- Native PostgreSQL health restarts come with native parity (step 9). In 1c, `start` re-ensures PostgreSQL and the supervisor restarts the app.
- Docker mode needs a source checkout until release images ship (step 10).

---

### Task 0: Hooks treat `cli/` like `backend/` (controller)

**Files:**
- Modify: `.claude/hooks/format.py` (`commands_for`), `.claude/hooks/typecheck.py` (`checks_for`)
- Test: `.claude/hooks/tests/test_format.py`, `.claude/hooks/tests/test_typecheck.py`

**Interfaces:**
- Produces:
  - `commands_for(rel, root)` formats `cli/**/*.py` with `uv run --directory cli ruff …`.
  - `checks_for(changed, root)` adds `["uv", "run", "--directory", "cli", "pyright"]` when a `cli/**/*.py` changed and `cli/pyproject.toml` exists.

- [ ] **Step 1: Failing tests** — append to `.claude/hooks/tests/test_typecheck.py`:

```python
def test_checks_for_cli(tmp_path):
    make(tmp_path, "cli/pyproject.toml")
    assert typecheck.checks_for(["cli/src/researchly_cli/main.py"], tmp_path) == [
        ["uv", "run", "--directory", "cli", "pyright"]
    ]
```

and to `.claude/hooks/tests/test_format.py` (match the existing helper used by `test_python_in_backend`):

```python
def test_python_in_cli(tmp_path):
    (tmp_path / "cli").mkdir()
    (tmp_path / "cli/pyproject.toml").write_text("", encoding="utf-8")
    assert format.commands_for("cli/src/researchly_cli/main.py", tmp_path) == [
        ["uv", "run", "--directory", "cli", "ruff", "format", "src/researchly_cli/main.py"],
        ["uv", "run", "--directory", "cli", "ruff", "check", "--fix", "src/researchly_cli/main.py"],
    ]
```

- [ ] **Step 2: Run, expect FAIL:** `uv run --no-project --python 3.12 --with pytest pytest .claude/hooks/tests -q -k cli`
- [ ] **Step 3: Implement.** In `format.commands_for`, loop over `("backend", "cli")` for Python; in `typecheck.checks_for`, likewise:

```python
    for pkg in ("backend", "cli"):
        if any(p.startswith(f"{pkg}/") and p.endswith(".py") for p in changed) and (root / pkg / "pyproject.toml").exists():
            cmds.append(["uv", "run", "--directory", pkg, "pyright"])
```

- [ ] **Step 4: Run all hook tests, expect PASS:** `uv run --no-project --python 3.12 --with pytest pytest .claude/hooks/tests scripts/tests -q`
- [ ] **Step 5: Commit** `chore(hooks): format and typecheck the cli project`

---

### Task 1: Backend settings, env token, `ensure_secret`, encrypted-file keychain fallback

**Files:**
- Modify: `backend/src/researchly/config.py`, `backend/src/researchly/app.py`, `backend/src/researchly/security/keychain.py`, `backend/pyproject.toml` (+ `uv.lock`), `backend/tests/conftest.py`
- Test: `backend/tests/test_app.py`, `backend/tests/security/test_keychain.py`

**Interfaces:**
- Produces:
  - `Settings.data_dir: Path` (env `RESEARCHLY_DATA_DIR`, default `~/.researchly`).
  - `Settings.local_token: SecretStr | None` (env `RESEARCHLY_LOCAL_TOKEN`).
  - `keychain.ensure_secret(name: str) -> str`.
  - `keychain.store_name() -> str` (`"OS keychain (<Backend>)"` or `"encrypted file (<path>)"`).
  - `keychain.EncryptedFileKeyring(directory: Path)`.
  - `create_app()` uses `Settings.local_token` when set and never touches the keychain then.

- [ ] **Step 1: Failing tests.** In `backend/tests/conftest.py`, extend the autouse fixture so no test can touch the real data dir:

```python
@pytest.fixture(autouse=True)
def _memory_keyring(tmp_path, monkeypatch):
    """Tests never touch the real OS keychain or the real data dir."""
    monkeypatch.setenv("RESEARCHLY_DATA_DIR", str(tmp_path / "data"))
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)
```

Append to `backend/tests/test_app.py`:

```python
@pytest.mark.anyio
async def test_env_token_is_used_without_the_keychain(monkeypatch):
    def no_keychain() -> str:
        raise AssertionError("keychain must not be read when RESEARCHLY_LOCAL_TOKEN is set")

    monkeypatch.setattr("researchly.app.ensure_local_token", no_keychain)
    monkeypatch.setenv("RESEARCHLY_LOCAL_TOKEN", "e" * 43)
    app = create_app(Settings(env="test"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        allowed = await client.get("/nope", headers={TOKEN_HEADER: "e" * 43})
        denied = await client.get("/nope")
    assert (allowed.status_code, denied.status_code) == (404, 403)
    assert "e" * 43 not in redact(f"token={'e' * 43}")


def test_data_dir_defaults_to_home_and_reads_env(monkeypatch, tmp_path):
    monkeypatch.delenv("RESEARCHLY_DATA_DIR", raising=False)
    assert Settings().data_dir == Path.home() / ".researchly"
    monkeypatch.setenv("RESEARCHLY_DATA_DIR", str(tmp_path))
    assert Settings().data_dir == tmp_path
```

(add imports: `from pathlib import Path`, `from researchly.security.localhost_guard import TOKEN_HEADER`, `from researchly.security.redact import redact`).

In `backend/tests/security/test_keychain.py`, **replace** `test_no_backend_raises_clear_error` (spec §19.2 now requires a fallback) and append:

```python
def test_no_os_keychain_falls_back_to_encrypted_file(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCHLY_DATA_DIR", str(tmp_path))
    keyring.set_keyring(FailKeyring())
    keychain.set_secret("anthropic", "sk-ant-abcdefghijklmnopqrstuvwxyz")
    assert keychain.get_secret("anthropic") == "sk-ant-abcdefghijklmnopqrstuvwxyz"
    assert keychain.store_name() == f"encrypted file ({tmp_path / 'secrets.json'})"
    assert "sk-ant-abcdefghijklmnopqrstuvwxyz" not in (tmp_path / "secrets.json").read_text("utf-8")
    keychain.delete_secret("anthropic")
    assert keychain.get_secret("anthropic") is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_encrypted_file_and_key_are_private(tmp_path):
    keychain.EncryptedFileKeyring(tmp_path).set_password("researchly", "x", "value-123456")
    assert (tmp_path / "secrets.key").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "secrets.json").stat().st_mode & 0o777 == 0o600


def test_tampered_secrets_file_raises_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCHLY_DATA_DIR", str(tmp_path))
    keyring.set_keyring(FailKeyring())
    keychain.set_secret("anthropic", "value-123456")
    (tmp_path / "secrets.key").write_bytes(b"\0" * 32)  # a different machine key
    with pytest.raises(keychain.KeychainUnavailable):
        keychain.get_secret("anthropic")


def test_os_keychain_store_name():
    assert keychain.store_name() == "OS keychain (MemoryKeyring)"


def test_ensure_secret_generates_once():
    first = keychain.ensure_secret("db-app-password")
    assert len(first) == 43
    assert keychain.ensure_secret("db-app-password") == first
```

(add `import sys`).

- [ ] **Step 2: Run, expect FAIL:** `uv run --directory backend pytest tests/test_app.py tests/security/test_keychain.py -q`
- [ ] **Step 3: Implement.**

`backend/pyproject.toml`: add `"cryptography>=46"` to `dependencies` (already locked transitively at 50.0.2; it becomes direct), then `uv lock --directory backend`.

`backend/src/researchly/config.py`, add fields and imports:

```python
from pydantic import SecretStr
...
    data_dir: Path = Path.home() / ".researchly"  # spec §16.1
    local_token: SecretStr | None = None  # injected by the CLI (spec §16.1); else read from the keychain
```

`backend/src/researchly/app.py`:

```python
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
```

`backend/src/researchly/security/keychain.py` (full file):

```python
"""Secrets (spec §19.2): the OS keychain, read at call time and never cached.

Hosts with no keychain backend (headless Linux) fall back to an AES-GCM encrypted file in the data dir whose key
is a 0600 machine-key file next to it.
"""

import base64
import contextlib
import json
import os
import secrets
from pathlib import Path

import keyring
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from keyring.backend import KeyringBackend
from keyring.backends import fail
from keyring.errors import KeyringError, PasswordDeleteError

from researchly.config import Settings
from researchly.security.redact import register_secret

SERVICE = "researchly"
LOCAL_TOKEN = "local-token"  # noqa: S105 - keychain entry name, not the secret itself


class KeychainUnavailable(RuntimeError):
    pass


class EncryptedFileKeyring(KeyringBackend):
    """Never auto-detected: keyring only builds backends constructible without arguments."""

    priority = 0  # type: ignore[assignment]

    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.path = directory / "secrets.json"
        self.key_path = directory / "secrets.key"

    def _key(self) -> bytes:
        if not self.key_path.exists():
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(AESGCM.generate_key(bit_length=256))
        return self.key_path.read_bytes()

    def _entries(self) -> dict[str, str]:
        return json.loads(self.path.read_text("utf-8")) if self.path.exists() else {}

    def _save(self, entries: dict[str, str]) -> None:
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(entries, fh)
        os.replace(tmp, self.path)

    def get_password(self, service: str, username: str) -> str | None:
        blob = self._entries().get(f"{service}/{username}")
        if blob is None:
            return None
        raw = base64.b64decode(blob)
        try:
            return AESGCM(self._key()).decrypt(raw[:12], raw[12:], f"{service}/{username}".encode()).decode()
        except InvalidTag as exc:
            raise KeyringError(f"{self.path} cannot be decrypted with {self.key_path}") from exc

    def set_password(self, service: str, username: str, password: str) -> None:
        nonce = os.urandom(12)
        sealed = AESGCM(self._key()).encrypt(nonce, password.encode(), f"{service}/{username}".encode())
        entries = self._entries()
        entries[f"{service}/{username}"] = base64.b64encode(nonce + sealed).decode()
        self._save(entries)

    def delete_password(self, service: str, username: str) -> None:
        entries = self._entries()
        if entries.pop(f"{service}/{username}", None) is None:
            raise PasswordDeleteError(username)
        self._save(entries)


def _backend() -> KeyringBackend:
    if isinstance(keyring.get_keyring(), fail.Keyring):
        keyring.set_keyring(EncryptedFileKeyring(Settings().data_dir))
    return keyring.get_keyring()


def store_name() -> str:
    """Which store holds secrets, for `researchly doctor` (spec §19.2)."""
    backend = _backend()
    if isinstance(backend, EncryptedFileKeyring):
        return f"encrypted file ({backend.path})"
    return f"OS keychain ({type(backend).__name__})"


def get_secret(name: str) -> str | None:
    try:
        value = _backend().get_password(SERVICE, name)
    except KeyringError as exc:  # locked, denied, or an undecryptable fallback file
        raise KeychainUnavailable(f"OS keychain unavailable: {exc}") from exc
    if value:
        register_secret(value)
    return value


def set_secret(name: str, value: str) -> None:
    try:
        _backend().set_password(SERVICE, name, value)
    except KeyringError as exc:
        raise KeychainUnavailable(f"cannot write to the OS keychain: {exc}") from exc
    register_secret(value)


def delete_secret(name: str) -> None:
    with contextlib.suppress(PasswordDeleteError):
        _backend().delete_password(SERVICE, name)


def ensure_secret(name: str) -> str:
    """Read a secret, generating a random one on first use."""
    value = get_secret(name)
    if value is None:
        value = secrets.token_urlsafe(32)
        set_secret(name, value)
    return value


def ensure_local_token() -> str:
    """The per-install API token (spec §4.3)."""
    return ensure_secret(LOCAL_TOKEN)
```

Note: `test_locked_keychain_raises_clear_error` monkeypatches `keyring.get_password`. Since `get_secret` now calls `_backend().get_password`, update that test to patch the backend method instead: `monkeypatch.setattr(type(keyring.get_keyring()), "get_password", locked)` (the autouse backend is `MemoryKeyring`; patching via `monkeypatch` restores it). Keep its assertion.

- [ ] **Step 4: Run, expect PASS:**
  - `uv run --directory backend pytest -q` (all backend tests)
  - `uv run --directory backend ruff check`
  - `uv run --directory backend ruff format --check`
  - `uv run --directory backend pyright`
- [ ] **Step 5: Commit** `feat(security): encrypted-file keychain fallback, ensure_secret, env-injected app token`

---

### Task 2: Database provisioning (`researchly.db.provision`)

**Files:**
- Create: `backend/src/researchly/db/provision.py`
- Test: `backend/tests/db/test_provision.py`

**Interfaces:**
- Consumes: `researchly.db.migrate.upgrade(url)`.
- Produces:
  - `provision(superuser_url: str, app_password: str, ro_password: str, database: str = "researchly") -> str` (idempotent; returns the app-role URL, `postgresql+psycopg://researchly_app:<pw>@host:port/<database>`).
  - `setup(superuser_url, app_password, ro_password, database="researchly") -> str` (provision, then migrate as the app role).
  - `python -m researchly.db.provision` reads `RESEARCHLY_DB_SUPERUSER_URL`, `RESEARCHLY_DB_APP_PASSWORD` and `RESEARCHLY_DB_RO_PASSWORD`.
  - Constants `APP_ROLE = "researchly_app"`, `RO_ROLE = "researchly_ro"`.

- [ ] **Step 1: Failing tests** — `backend/tests/db/test_provision.py`:

```python
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError

from researchly.db import provision

pytestmark = pytest.mark.db
APP_PW, RO_PW = "a" * 20, "r" * 20


@pytest.fixture
def target(db_url):
    name = f"p_{uuid.uuid4().hex[:12]}"
    yield name
    admin = create_engine(db_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def test_setup_creates_database_owned_and_migrated_by_app_role(db_url, target):
    app_url = provision.setup(db_url, APP_PW, RO_PW, database=target)
    url = make_url(app_url)
    assert (url.username, url.password, url.database) == ("researchly_app", APP_PW, target)
    engine = create_engine(app_url)
    with engine.connect() as conn:
        owner = conn.scalar(text("SELECT tableowner FROM pg_tables WHERE tablename = 'capability'"))
        exts = set(conn.scalars(text("SELECT extname FROM pg_extension")))
    engine.dispose()
    assert owner == "researchly_app"
    assert {"vector", "pg_trgm"} <= exts


def test_setup_is_idempotent(db_url, target):
    provision.setup(db_url, APP_PW, RO_PW, database=target)
    provision.setup(db_url, APP_PW, RO_PW, database=target)


def test_read_only_role_reads_but_cannot_write(db_url, target):
    provision.setup(db_url, APP_PW, RO_PW, database=target)
    ro = create_engine(make_url(db_url).set(username="researchly_ro", password=RO_PW, database=target))
    with ro.connect() as conn:
        assert conn.scalar(text("SELECT value FROM capability WHERE key = 'bm25_engine'")) in ("tsvector", "pg_textsearch")
        with pytest.raises(ProgrammingError):
            conn.execute(text("INSERT INTO setting (key, value) VALUES ('x', '1')"))
    ro.dispose()


@pytest.mark.parametrize("bad", ["short", "x'; DROP ROLE postgres; --" + "a" * 16])
def test_rejects_unsafe_passwords(db_url, target, bad):
    with pytest.raises(ValueError):
        provision.provision(db_url, bad, RO_PW, database=target)


def test_rejects_unsafe_database_name(db_url):
    with pytest.raises(ValueError):
        provision.provision(db_url, APP_PW, RO_PW, database='x"; DROP DATABASE postgres; --')
```

- [ ] **Step 2: Run, expect FAIL:** `uv run --directory backend pytest tests/db/test_provision.py -q`
- [ ] **Step 3: Implement** `backend/src/researchly/db/provision.py`:

```python
"""Database provisioning (spec §16.1): roles, database and untrusted extensions, created as the superuser.

The CLI calls setup() before starting the app (native mode); Docker mode runs `python -m researchly.db.provision`
as a one-shot service with RESEARCHLY_DB_SUPERUSER_URL / _APP_PASSWORD / _RO_PASSWORD in the environment.
"""

import os
import re

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from researchly.db import migrate

APP_ROLE = "researchly_app"
RO_ROLE = "researchly_ro"
_PASSWORD = re.compile(r"[A-Za-z0-9_-]{16,}")  # token_urlsafe output; DDL cannot take bind parameters
_DATABASE = re.compile(r"[a-z_][a-z0-9_]{0,62}")


def provision(superuser_url: str, app_password: str, ro_password: str, database: str = "researchly") -> str:
    """Idempotent. Returns the app-role URL for `database`."""
    if not all(_PASSWORD.fullmatch(pw) for pw in (app_password, ro_password)):
        raise ValueError("database passwords must be 16+ URL-safe characters")
    if not _DATABASE.fullmatch(database):
        raise ValueError(f"invalid database name {database!r}")
    admin = create_engine(superuser_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            for role, password in ((APP_ROLE, app_password), (RO_ROLE, ro_password)):
                exists = conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})
                conn.execute(text(f"{'ALTER' if exists else 'CREATE'} ROLE {role} LOGIN PASSWORD '{password}'"))
            conn.execute(text(f"GRANT pg_read_all_data TO {RO_ROLE}"))
            if not conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": database}):
                conn.execute(text(f'CREATE DATABASE "{database}" OWNER {APP_ROLE}'))
    finally:
        admin.dispose()
    # Untrusted extensions need the superuser, inside the target database; the baseline migration's
    # CREATE EXTENSION IF NOT EXISTS is then a no-op for them. pg_trgm is trusted: the migration creates it.
    db_admin = create_engine(make_url(superuser_url).set(database=database), isolation_level="AUTOCOMMIT")
    try:
        with db_admin.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            if "pg_textsearch" in (conn.scalar(text("SHOW shared_preload_libraries")) or ""):
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_textsearch"))
    finally:
        db_admin.dispose()
    app_url = make_url(superuser_url).set(username=APP_ROLE, password=app_password, database=database)
    return app_url.render_as_string(hide_password=False)


def setup(superuser_url: str, app_password: str, ro_password: str, database: str = "researchly") -> str:
    """Provision, then migrate as the schema owner. Returns the app-role URL."""
    app_url = provision(superuser_url, app_password, ro_password, database)
    migrate.upgrade(app_url)
    return app_url


if __name__ == "__main__":
    env = os.environ
    setup(env["RESEARCHLY_DB_SUPERUSER_URL"], env["RESEARCHLY_DB_APP_PASSWORD"], env["RESEARCHLY_DB_RO_PASSWORD"])
```

- [ ] **Step 4: Run, expect PASS:**
  - `uv run --directory backend pytest -q`
  - `uv run --directory backend ruff check`
  - `uv run --directory backend ruff format --check`
  - `uv run --directory backend pyright`

  (DB tests need Docker running locally or `RESEARCHLY_TEST_DATABASE_URL`. A skip is not a pass: confirm the 5 tests ran.)
- [ ] **Step 5: Commit** `feat(db): provisioning of roles, database and untrusted extensions`

---

### Task 3: CLI project skeleton, local state, `config get|set`

**Files:**
- Create: `cli/pyproject.toml`, `cli/uv.lock` (generated), `cli/src/researchly_cli/__init__.py`, `cli/src/researchly_cli/state.py`, `cli/src/researchly_cli/main.py`
- Test: `cli/tests/__init__.py` (empty), `cli/tests/conftest.py`, `cli/tests/test_state.py`, `cli/tests/test_config_cmd.py`

**Interfaces:**
- Consumes: `researchly.config.Settings.data_dir`, `researchly.security.keychain.ensure_secret`, `ensure_local_token` (Task 1).
- Produces (`researchly_cli.state`):
  - `RUNTIMES = ("auto", "docker", "native")`
  - `data_dir() -> Path`; `log_dir() -> Path` (env `RESEARCHLY_LOG_DIR`, else `platformdirs.user_log_path("researchly", appauthor=False)`; created)
  - `load_config() -> dict[str, str]` (defaults `{"runtime": "auto"}`); `save_config(config: dict[str, str]) -> None`
  - `@dataclass(frozen=True) State(mode: Literal["docker", "native"], port: int, compose: tuple[str, ...] = (), supervisor_pid: int | None = None, supervisor_created: float | None = None, pg_port: int | None = None)` with property `url -> "http://127.0.0.1:<port>"`
  - `read_state() -> State | None`; `write_state(State) -> None`; `clear_state() -> None`
  - `@dataclass(frozen=True) Secrets(pg_superuser, db_app, db_ro, local_token, searxng: str)`; `load_secrets() -> Secrets`
  - `free_port(preferred: int) -> int`; `healthy(port: int) -> bool`; `wait_until(check, timeout, interval=1.0, *, sleep=time.sleep, clock=time.monotonic) -> bool`
- Produces (`researchly_cli.main`): `app: typer.Typer` with `config get KEY` and `config set KEY VALUE` (only key `runtime`; bad key or value → exit 2).

- [ ] **Step 1: Project files.** `cli/pyproject.toml`:

```toml
[project]
name = "researchly-cli"
version = "0.0.0"
description = "Researchly command line: setup, runtime selection and supervision"
license = "Apache-2.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "researchly",
  "typer>=0.20",
  "platformdirs>=4.3",
  "psutil>=7.0",
  "pg0-embedded==0.15.2",  # native PostgreSQL 18 + pgvector (spec §4.1); runtime binary, pinned exactly
]

[project.scripts]
researchly = "researchly_cli.main:app"

[dependency-groups]
dev = [
  "pytest>=8.4",
  "ruff>=0.14",
  "pyright>=1.1.405",
]

[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/researchly_cli"]

[tool.uv]
exclude-newer = "7 days"
exclude-newer-package = { litellm = "14 days" }

[tool.uv.sources]
researchly = { path = "../backend", editable = true }

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
markers = [
  "native: starts a real pg0 PostgreSQL (spec §26 risk 1 spike)",
  "docker: needs a running container engine; runs only with RESEARCHLY_TEST_DOCKER=1",
]
```

`cli/src/researchly_cli/__init__.py`: `"""The researchly command line (spec F10.1, §4)."""`

Then run `uv lock --directory cli` and `uv sync --directory cli`.

- [ ] **Step 2: Failing tests.** `cli/tests/conftest.py`:

```python
import os

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
def _isolated(tmp_path, monkeypatch):
    """Each test gets its own data and log dirs and an in-memory keychain."""
    monkeypatch.setenv("RESEARCHLY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RESEARCHLY_LOG_DIR", str(tmp_path / "logs"))
    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    yield
    keyring.set_keyring(previous)


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RESEARCHLY_TEST_DOCKER") == "1":
        return
    skip = pytest.mark.skip(reason="Docker-mode test: set RESEARCHLY_TEST_DOCKER=1 with a running engine")
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(skip)
```

`cli/tests/test_state.py`:

```python
import socket

from researchly_cli import state


def test_state_roundtrip():
    run = state.State(mode="docker", port=8765, compose=("docker", "compose"))
    state.write_state(run)
    assert state.read_state() == run
    assert run.url == "http://127.0.0.1:8765"
    state.clear_state()
    assert state.read_state() is None


def test_config_defaults_and_persists():
    assert state.load_config() == {"runtime": "auto"}
    state.save_config({"runtime": "native"})
    assert state.load_config()["runtime"] == "native"


def test_secrets_are_generated_once_and_distinct():
    first = state.load_secrets()
    assert state.load_secrets() == first
    values = [first.pg_superuser, first.db_app, first.db_ro, first.local_token, first.searxng]
    assert len(set(values)) == 5
    assert all(len(v) == 43 for v in values)


def test_free_port_prefers_requested_then_falls_back():
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        taken = busy.getsockname()[1]
        assert state.free_port(taken) != taken
    assert state.free_port(taken) == taken


def test_healthy_is_false_when_nothing_listens():
    assert not state.healthy(state.free_port(0))


def test_wait_until_polls_until_true_or_timeout():
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    answers = iter([False, False, True])
    assert state.wait_until(lambda: next(answers), 10, sleep=sleep, clock=lambda: now[0])
    assert now[0] == 2.0
    assert not state.wait_until(lambda: False, 3, sleep=sleep, clock=lambda: now[0])
```

`cli/tests/test_config_cmd.py`:

```python
from typer.testing import CliRunner

from researchly_cli.main import app

runner = CliRunner()


def test_runtime_defaults_to_auto():
    result = runner.invoke(app, ["config", "get", "runtime"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "auto"


def test_set_runtime_persists():
    assert runner.invoke(app, ["config", "set", "runtime", "native"]).exit_code == 0
    assert runner.invoke(app, ["config", "get", "runtime"]).stdout.strip() == "native"


def test_rejects_unknown_runtime_and_key():
    assert runner.invoke(app, ["config", "set", "runtime", "kubernetes"]).exit_code == 2
    assert runner.invoke(app, ["config", "get", "colour"]).exit_code == 2
```

- [ ] **Step 3: Run, expect FAIL:** `uv run --directory cli pytest -q`
- [ ] **Step 4: Implement.** `cli/src/researchly_cli/state.py`:

```python
"""The CLI's local state (spec §4.2, §16.1): data and log dirs, config.json, state.json, secrets, ports, health."""

import json
import os
import socket
import time
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import platformdirs

from researchly.config import Settings
from researchly.security.keychain import ensure_local_token, ensure_secret

RUNTIMES = ("auto", "docker", "native")


def data_dir() -> Path:
    return Settings().data_dir


def log_dir() -> Path:
    override = os.environ.get("RESEARCHLY_LOG_DIR")
    path = Path(override) if override else platformdirs.user_log_path("researchly", appauthor=False)
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_config() -> dict[str, str]:
    path = data_dir() / "config.json"
    return {"runtime": "auto", **(json.loads(path.read_text("utf-8")) if path.exists() else {})}


def save_config(config: dict[str, str]) -> None:
    path = data_dir() / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), "utf-8")


@dataclass(frozen=True)
class State:
    """What `start` launched, so `stop` and `status` can find it. Never holds secrets."""

    mode: Literal["docker", "native"]
    port: int
    compose: tuple[str, ...] = ()
    supervisor_pid: int | None = None
    supervisor_created: float | None = None
    pg_port: int | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def read_state() -> State | None:
    path = data_dir() / "state.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text("utf-8"))
    return State(**{**raw, "compose": tuple(raw.get("compose", ()))})


def write_state(state: State) -> None:
    path = data_dir() / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2), "utf-8")


def clear_state() -> None:
    (data_dir() / "state.json").unlink(missing_ok=True)


@dataclass(frozen=True)
class Secrets:
    pg_superuser: str
    db_app: str
    db_ro: str
    local_token: str
    searxng: str


def load_secrets() -> Secrets:
    """Generated on first use and kept in the OS keychain (spec §16.1, §19.2)."""
    return Secrets(
        pg_superuser=ensure_secret("pg-superuser-password"),
        db_app=ensure_secret("db-app-password"),
        db_ro=ensure_secret("db-ro-password"),
        local_token=ensure_local_token(),
        searxng=ensure_secret("searxng-secret"),
    )


def free_port(preferred: int) -> int:
    """`preferred` if it is free on 127.0.0.1, else any free port."""
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", preferred))
        except OSError:
            s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:  # noqa: S310 - fixed local URL
            return response.status == 200
    except OSError:
        return False


def wait_until(
    check: Callable[[], bool],
    timeout: float,
    interval: float = 1.0,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    deadline = clock() + timeout
    while not check():
        if clock() >= deadline:
            return False
        sleep(interval)
    return True
```

`cli/src/researchly_cli/main.py`:

```python
"""`researchly` command line (spec F10.1, §4)."""

import typer

from researchly_cli import state

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Researchly: a local-first research assistant.")
config_app = typer.Typer(no_args_is_help=True, help="Read or change CLI settings.")
app.add_typer(config_app, name="config")

_CHOICES = {"runtime": state.RUNTIMES}


@app.callback()
def main() -> None:
    """Researchly: a local-first research assistant."""


def _known(key: str) -> tuple[str, ...]:
    if key not in _CHOICES:
        raise typer.BadParameter(f"unknown key {key!r}; known: {', '.join(_CHOICES)}")
    return _CHOICES[key]


@config_app.command("get")
def config_get(key: str) -> None:
    """Print a setting."""
    _known(key)
    typer.echo(state.load_config()[key])


@config_app.command("set")
def config_set(key: str, value: str) -> None:
    """Change a setting, e.g. `researchly config set runtime docker`."""
    if value not in _known(key):
        raise typer.BadParameter(f"{key} must be one of {', '.join(_known(key))}")
    state.save_config({**state.load_config(), key: value})
    typer.echo(f"{key} = {value}")
```

- [ ] **Step 5: Run, expect PASS:**
  - `uv run --directory cli pytest -q`
  - `uv run --directory cli ruff check`
  - `uv run --directory cli ruff format --check`
  - `uv run --directory cli pyright`
  - Also `uv run --directory cli researchly --help`: it prints the `config` group.
- [ ] **Step 6: Commit** `feat(cli): researchly-cli project, local state and config command`

---

### Task 4: Supervisor (detached spawn, Job Object, restart-with-backoff, terminate)

**Files:**
- Create: `cli/src/researchly_cli/supervise.py`
- Test: `cli/tests/test_supervise.py`

**Interfaces:**
- Consumes: `state.wait_until` (Task 3).
- Produces:
  - `detached() -> dict[str, Any]` (Popen kwargs).
  - `spawn(cmd: Sequence[str], log: Path, env: dict[str, str] | None = None) -> psutil.Process`.
  - `supervisor_cmd(log: Path, cmd: Sequence[str]) -> list[str]`.
  - `run_forever(cmd, log, *, popen=subprocess.Popen, sleep=time.sleep, clock=time.monotonic) -> NoReturn`.
  - `rotate(log: Path, max_bytes: int = MAX_LOG_BYTES) -> None`.
  - `terminate_tree(pid: int, created: float | None = None, timeout: float = 10.0) -> None`.
  - `kill_on_close_job() -> None` (Windows only).
  - Entry point `python -m researchly_cli.supervise LOG -- CMD...`.

- [ ] **Step 1: Failing tests** — `cli/tests/test_supervise.py`:

```python
import sys

import psutil
import pytest

from researchly_cli.state import wait_until
from researchly_cli.supervise import rotate, run_forever, spawn, supervisor_cmd, terminate_tree

SLEEPER = [sys.executable, "-c", "import time; time.sleep(120)"]


def _sleepers(root: psutil.Process) -> list[psutil.Process]:
    found = []
    for p in root.children(recursive=True):
        try:
            if "time.sleep(120)" in " ".join(p.cmdline()):
                found.append(p)
        except psutil.NoSuchProcess:
            pass
    return found


def _alive(p: psutil.Process) -> bool:
    try:
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _start(tmp_path) -> tuple[psutil.Process, list[psutil.Process]]:
    sup = spawn(supervisor_cmd(tmp_path / "app.log", SLEEPER), tmp_path / "supervisor.log")
    assert wait_until(lambda: bool(_sleepers(sup)), 30, 0.2), (tmp_path / "supervisor.log").read_text()
    return sup, _sleepers(sup)


def test_terminate_tree_stops_supervisor_and_child(tmp_path):
    sup, kids = _start(tmp_path)
    terminate_tree(sup.pid, sup.create_time())
    assert wait_until(lambda: not _alive(sup) and not any(_alive(k) for k in kids), 10, 0.2)


def test_child_dies_with_a_killed_supervisor(tmp_path):
    sup, kids = _start(tmp_path)
    sup.kill()  # a crash: no clean shutdown
    if sys.platform != "win32":
        terminate_tree(sup.pid)  # POSIX: `stop` sweeps the supervisor's process group
    # Windows: the kill-on-close Job Object takes the child down by itself
    assert wait_until(lambda: not any(_alive(k) for k in kids), 10, 0.2)


def test_terminate_tree_ignores_a_reused_pid(tmp_path):
    sup, kids = _start(tmp_path)
    terminate_tree(sup.pid, sup.create_time() - 1000)  # recorded create time does not match: not ours
    assert _alive(sup) and all(_alive(k) for k in kids)
    terminate_tree(sup.pid, sup.create_time())


class _Exited:
    def wait(self) -> int:
        return 1


class _Stop(Exception):
    pass


def test_backoff_doubles_to_30_and_resets_after_a_long_run(tmp_path):
    ticks = iter([0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 100])  # (start, end) per run; the 7th run lasts 94 s
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 7:
            raise _Stop

    with pytest.raises(_Stop):
        run_forever(["x"], tmp_path / "app.log", popen=lambda *a, **k: _Exited(), sleep=sleep, clock=lambda: next(ticks))
    assert sleeps == [2, 4, 8, 16, 30, 30, 2]


def test_rotate_keeps_one_previous_log(tmp_path):
    log = tmp_path / "app.log"
    log.write_bytes(b"x" * 11)
    rotate(log, max_bytes=10)
    assert not log.exists()
    assert (tmp_path / "app.log.1").read_bytes() == b"x" * 11
```

- [ ] **Step 2: Run, expect FAIL:** `uv run --directory cli pytest tests/test_supervise.py -q`
- [ ] **Step 3: Implement** `cli/src/researchly_cli/supervise.py`:

```python
"""Native-mode supervision (spec §4.2).

A detached supervisor (`python -m researchly_cli.supervise LOG -- CMD...`) restarts its child with backoff.
Stopping it takes the whole tree down. On Windows the supervisor sits in a kill-on-close Job Object, so even a
crashed supervisor takes its children with it. On POSIX it leads its own process group, which `terminate_tree`
sweeps.
"""

import contextlib
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NoReturn

import psutil

MAX_LOG_BYTES = 10 * 1024 * 1024


def detached() -> dict[str, Any]:
    """Popen kwargs for a process that outlives the CLI and leads its own process group."""
    if sys.platform == "win32":
        return {
            "creationflags": subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_BREAKAWAY_FROM_JOB
        }
    return {"start_new_session": True}


def spawn(cmd: Sequence[str], log: Path, env: dict[str, str] | None = None) -> psutil.Process:
    """Start `cmd` detached, appending its output to `log`."""
    log.parent.mkdir(parents=True, exist_ok=True)
    kwargs = detached()
    with log.open("ab") as out:
        try:
            child = subprocess.Popen(list(cmd), stdin=subprocess.DEVNULL, stdout=out, stderr=out, env=env, **kwargs)  # noqa: S603
        except PermissionError:  # the CLI's own Job Object forbids breakaway (some terminals and CI hosts)
            kwargs["creationflags"] &= ~subprocess.CREATE_BREAKAWAY_FROM_JOB
            child = subprocess.Popen(list(cmd), stdin=subprocess.DEVNULL, stdout=out, stderr=out, env=env, **kwargs)  # noqa: S603
    return psutil.Process(child.pid)


def supervisor_cmd(log: Path, cmd: Sequence[str]) -> list[str]:
    return [sys.executable, "-m", "researchly_cli.supervise", str(log), "--", *cmd]


def rotate(log: Path, max_bytes: int = MAX_LOG_BYTES) -> None:
    if log.exists() and log.stat().st_size > max_bytes:
        log.replace(log.with_name(log.name + ".1"))


def run_forever(
    cmd: Sequence[str],
    log: Path,
    *,
    popen: Callable[..., Any] = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> NoReturn:
    """Restart `cmd` whenever it exits, 2, 4, 8 … 30 s apart; a run longer than a minute resets the backoff."""
    failures = 0
    while True:
        rotate(log)
        started = clock()
        with log.open("ab") as out:
            popen(list(cmd), stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT).wait()
        failures = 1 if clock() - started > 60 else failures + 1
        sleep(min(2**failures, 30))


def terminate_tree(pid: int, created: float | None = None, timeout: float = 10.0) -> None:
    """Stop a supervisor and everything under it: terminate, wait, then kill."""
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        proc = None
    if proc is not None and created is not None and proc.create_time() != created:
        return  # the PID now belongs to an unrelated process
    procs: list[psutil.Process] = []
    if proc is not None:
        with contextlib.suppress(psutil.NoSuchProcess):
            procs = [proc, *proc.children(recursive=True)]
    for p in procs:
        with contextlib.suppress(psutil.NoSuchProcess):
            p.terminate()
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        with contextlib.suppress(psutil.NoSuchProcess):
            p.kill()
    if sys.platform != "win32":  # orphans of a crashed supervisor are still in its process group
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)


def kill_on_close_job() -> None:
    """Put this process in a Job Object that kills every member when its last handle closes (Windows)."""
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class Extended(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", Basic),
            ("IoInfo", ctypes.c_ulonglong * 6),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = k32.CreateJobObjectW(None, None)
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not job or not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # 9: extended
        raise ctypes.WinError(ctypes.get_last_error())
    if not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    # The handle is deliberately never closed: the job lives exactly as long as this process.


def main(argv: Sequence[str]) -> NoReturn:
    args = list(argv)
    sep = args.index("--")
    if sys.platform == "win32":
        kill_on_close_job()
    run_forever(args[sep + 1 :], Path(args[0]))


if __name__ == "__main__":
    main(sys.argv[1:])
```

Verified by the controller on Windows: the Job Object structure above, with a hard-killed parent, takes its child down.

Windows caveat to check: in a venv, `sys.executable` is the venv launcher, which starts the base interpreter as a child. So the PID that `spawn` returns may be the launcher, not the process that called `kill_on_close_job`. `test_child_dies_with_a_killed_supervisor` exercises exactly this. If it fails on Windows, root-cause it (superpowers:systematic-debugging) rather than weakening the test, and report the finding.

- [ ] **Step 4: Run, expect PASS:**
  - `uv run --directory cli pytest -q`
  - `uv run --directory cli ruff check`
  - `uv run --directory cli ruff format --check`
  - `uv run --directory cli pyright`
- [ ] **Step 5: Commit** `feat(cli): detached supervisor with kill-on-close job, backoff and tree termination`

---

### Task 5: Container engine detection, auto-start, runtime selection

**Files:**
- Create: `cli/src/researchly_cli/engines.py`
- Test: `cli/tests/test_engines.py`

**Interfaces:**
- Consumes: `supervise.detached()` (Task 4).
- Produces:
  - `run(cmd, timeout=10) -> tuple[int, str]`.
  - `@dataclass(frozen=True) Engine(name: str, cli: str, compose: tuple[str, ...], start: tuple[str, ...] | None, hint: str = "")`.
  - `@dataclass(frozen=True) Selection(mode: Literal["docker", "native"], reason: str, engine: Engine | None = None)`.
  - `class RuntimeUnavailable(RuntimeError)`.
  - `NATIVE_NOTE: str`.
  - `detect(run=run, which=shutil.which, platform=sys.platform) -> Engine | None`.
  - `ready(engine, run=run) -> bool`.
  - `launch(cmd: Sequence[str]) -> None`.
  - `select_runtime(pin: str, engine: Engine | None, *, ready, launch, wait, echo=print, timeout=120.0) -> Selection`.

- [ ] **Step 1: Failing tests** — `cli/tests/test_engines.py`:

```python
import pytest

from researchly_cli.engines import NATIVE_NOTE, Engine, RuntimeUnavailable, detect, select_runtime

COMPOSE_OK = {("docker", "compose", "version"): (0, "Docker Compose version v5.2.0")}


def fake_run(extra: dict[tuple[str, ...], tuple[int, str]] | None = None):
    responses = {**COMPOSE_OK, **(extra or {})}
    return lambda cmd: responses.get(tuple(cmd), (1, ""))


def which_only(*names: str):
    return lambda name: f"/usr/bin/{name}" if name in names else None


def context(name: str) -> dict[tuple[str, ...], tuple[int, str]]:
    return {("docker", "context", "show"): (0, name)}


def test_no_engine():
    assert detect(fake_run(), which_only(), "linux") is None


@pytest.mark.parametrize(
    ("ctx", "name", "start"),
    [
        ("colima", "colima", ("colima", "start")),
        ("rancher-desktop", "rancher-desktop", ("rdctl", "start")),
        ("orbstack", "orbstack", ("orb", "start")),
    ],
)
def test_engine_from_docker_context(ctx, name, start):
    engine = detect(fake_run(context(ctx)), which_only("docker"), "darwin")
    assert engine == Engine(name, "docker", ("docker", "compose"), start)


def test_docker_desktop_on_macos():
    engine = detect(fake_run(context("desktop-linux")), which_only("docker"), "darwin")
    assert engine is not None and engine.start == ("open", "-a", "Docker")


def test_docker_desktop_on_windows(tmp_path, monkeypatch):
    exe = tmp_path / "Programs" / "DockerDesktop" / "Docker Desktop.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    engine = detect(fake_run(context("desktop-linux")), which_only("docker"), "win32")
    assert engine is not None and (engine.name, engine.start) == ("docker-desktop", (str(exe),))


def test_rootless_docker_on_linux():
    run = fake_run({**context("default"), ("systemctl", "--user", "cat", "docker.service"): (0, "")})
    engine = detect(run, which_only("docker"), "linux")
    assert engine is not None and engine.start == ("systemctl", "--user", "start", "docker")


def test_rootful_docker_on_linux_needs_elevation():
    engine = detect(fake_run(context("default")), which_only("docker"), "linux")
    assert engine is not None and engine.start is None and engine.hint == "sudo systemctl start docker"


def test_docker_without_compose_plugin():
    engine = detect(lambda cmd: (0, "colima") if cmd[1] == "context" else (1, ""), which_only("docker"), "darwin")
    assert engine is not None and engine.compose == ()


def test_podman_needs_a_compose_provider():
    bare = detect(fake_run(), which_only("podman"), "darwin")
    assert bare is not None and bare.compose == () and bare.start == ("podman", "machine", "start")
    full = detect(fake_run(), which_only("podman", "podman-compose"), "linux")
    assert full is not None and full.compose == ("podman", "compose") and full.start is None


COLIMA = Engine("colima", "docker", ("docker", "compose"), ("colima", "start"))


def boom(*_args, **_kwargs):
    raise AssertionError("must not be called")


def test_pinned_native_never_probes():
    selection = select_runtime("native", None, ready=boom, launch=boom, wait=boom)
    assert selection.mode == "native"


def test_ready_engine_selects_docker():
    selection = select_runtime("auto", COLIMA, ready=lambda e: True, launch=boom, wait=boom)
    assert (selection.mode, selection.engine) == ("docker", COLIMA)


def test_stopped_engine_is_started_then_selected():
    launched: list[tuple[str, ...]] = []
    states = iter([False, True])
    selection = select_runtime(
        "auto", COLIMA, ready=lambda e: next(states), launch=lambda cmd: launched.append(tuple(cmd)),
        wait=lambda check, timeout: check(), echo=lambda msg: None,
    )
    assert selection.mode == "docker"
    assert launched == [("colima", "start")]


def test_start_timeout_falls_back_to_native_on_auto():
    selection = select_runtime(
        "auto", COLIMA, ready=lambda e: False, launch=lambda cmd: None, wait=lambda check, timeout: False,
        echo=lambda msg: None,
    )
    assert selection.mode == "native"
    assert "did not become ready" in selection.reason and NATIVE_NOTE in selection.reason


def test_launch_failure_falls_back():
    def missing(cmd):
        raise FileNotFoundError(cmd[0])

    selection = select_runtime("auto", COLIMA, ready=lambda e: False, launch=missing, wait=boom, echo=lambda m: None)
    assert selection.mode == "native" and "could not start colima" in selection.reason


def test_elevation_needed_explains_the_command():
    rootful = Engine("docker", "docker", ("docker", "compose"), None, "sudo systemctl start docker")
    selection = select_runtime("auto", rootful, ready=lambda e: False, launch=boom, wait=boom)
    assert selection.mode == "native" and "sudo systemctl start docker" in selection.reason


def test_engine_without_compose_falls_back():
    no_compose = Engine("podman", "podman", (), ("podman", "machine", "start"))
    assert select_runtime("auto", no_compose, ready=boom, launch=boom, wait=boom).mode == "native"


def test_pinned_docker_raises_when_unavailable():
    with pytest.raises(RuntimeUnavailable, match="no container engine"):
        select_runtime("docker", None, ready=boom, launch=boom, wait=boom)
```

- [ ] **Step 2: Run, expect FAIL:** `uv run --directory cli pytest tests/test_engines.py -q`
- [ ] **Step 3: Implement** `cli/src/researchly_cli/engines.py`:

```python
"""Container engine detection, auto-start and runtime selection (spec §4.1, D2).

Never installs an engine and never elevates. When starting one needs root, print the one command and use native.
"""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from researchly_cli.supervise import detached

Runner = Callable[[Sequence[str]], tuple[int, str]]

NATIVE_NOTE = (
    "Using native mode. A container engine (Docker, Podman, Colima, Rancher Desktop or OrbStack) unlocks BM25 "
    "keyword search, GROBID and the container sandbox; `researchly doctor` lists what is degraded."
)


def run(cmd: Sequence[str], timeout: float = 10) -> tuple[int, str]:
    try:
        done = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return done.returncode, done.stdout.strip()


@dataclass(frozen=True)
class Engine:
    name: str
    cli: str
    compose: tuple[str, ...]
    start: tuple[str, ...] | None  # None: starting it needs elevation; `hint` is the command to give the user
    hint: str = ""


@dataclass(frozen=True)
class Selection:
    mode: Literal["docker", "native"]
    reason: str
    engine: Engine | None = None


class RuntimeUnavailable(RuntimeError):
    pass


def _docker_desktop_exe(which: Callable[[str], str | None]) -> Path | None:
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "DockerDesktop" / "Docker Desktop.exe",  # per-user
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker" / "Docker" / "Docker Desktop.exe",
    ]
    if found := which("docker"):  # …\Docker\Docker\resources\bin\docker.exe
        candidates += [parent / "Docker Desktop.exe" for parent in Path(found).resolve().parents[:3]]
    return next((p for p in candidates if p.is_file()), None)


def detect(run: Runner = run, which: Callable[[str], str | None] = shutil.which, platform: str = sys.platform) -> Engine | None:
    """The installed engine, running or not (spec §4.1 step 1)."""
    if which("docker"):
        compose = ("docker", "compose") if run(["docker", "compose", "version"])[0] == 0 else ()
        context = run(["docker", "context", "show"])[1]
        if context.startswith("colima"):
            return Engine("colima", "docker", compose, ("colima", "start"))
        if context == "rancher-desktop":
            return Engine("rancher-desktop", "docker", compose, ("rdctl", "start"))
        if context == "orbstack":
            return Engine("orbstack", "docker", compose, ("orb", "start"))
        if platform == "win32" and (exe := _docker_desktop_exe(which)):
            return Engine("docker-desktop", "docker", compose, (str(exe),))
        if platform == "darwin" and (context == "desktop-linux" or Path("/Applications/Docker.app").exists()):
            return Engine("docker-desktop", "docker", compose, ("open", "-a", "Docker"))
        if platform.startswith("linux"):
            if context == "desktop-linux":
                return Engine("docker-desktop", "docker", compose, ("systemctl", "--user", "start", "docker-desktop"))
            if context == "rootless" or run(["systemctl", "--user", "cat", "docker.service"])[0] == 0:
                return Engine("docker-rootless", "docker", compose, ("systemctl", "--user", "start", "docker"))
            return Engine("docker", "docker", compose, None, "sudo systemctl start docker")
        return Engine("docker", "docker", compose, None, "start your Docker engine")
    if which("podman"):
        compose = ("podman", "compose") if which("docker-compose") or which("podman-compose") else ()
        start = None if platform.startswith("linux") else ("podman", "machine", "start")
        return Engine("podman", "podman", compose, start, "systemctl --user start podman.socket")
    return None


def ready(engine: Engine, run: Runner = run) -> bool:
    return run([engine.cli, "info"])[0] == 0


def launch(cmd: Sequence[str]) -> None:
    """Start an engine without waiting: some start commands are GUIs, others block until the engine is up."""
    subprocess.Popen(  # noqa: S603
        list(cmd), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **detached()
    )


def select_runtime(
    pin: str,
    engine: Engine | None,
    *,
    ready: Callable[[Engine], bool],
    launch: Callable[[Sequence[str]], None],
    wait: Callable[[Callable[[], bool], float], bool],
    echo: Callable[[str], None] = print,
    timeout: float = 120.0,
) -> Selection:
    """Spec §4.1: Docker mode when an engine is (or can be made) ready, else native; a pin overrides."""
    if pin == "native":
        return Selection("native", "runtime pinned to native")
    problem = _docker_problem(engine, ready, launch, wait, echo, timeout)
    if problem is None and engine is not None:
        return Selection("docker", f"{engine.name} is ready", engine)
    if pin == "docker":
        raise RuntimeUnavailable(f"runtime pinned to docker, but {problem}")
    return Selection("native", f"{problem}. {NATIVE_NOTE}")


def _docker_problem(
    engine: Engine | None,
    ready: Callable[[Engine], bool],
    launch: Callable[[Sequence[str]], None],
    wait: Callable[[Callable[[], bool], float], bool],
    echo: Callable[[str], None],
    timeout: float,
) -> str | None:
    if engine is None:
        return "no container engine was found"
    if not engine.compose:
        return f"{engine.name} has no compose support (install the Docker Compose plugin or podman-compose)"
    if ready(engine):
        return None
    if engine.start is None:
        return f"{engine.name} is stopped and starting it needs elevation; run `{engine.hint}`"
    echo(f"Starting {engine.name} (waiting up to {int(timeout)} s)…")
    try:
        launch(engine.start)
    except OSError as exc:
        return f"could not start {engine.name}: {exc}"
    if wait(lambda: ready(engine), timeout):
        return None
    return f"{engine.name} did not become ready within {int(timeout)} s"
```

- [ ] **Step 4: Run, expect PASS:**
  - `uv run --directory cli pytest -q`
  - `uv run --directory cli ruff check`
  - `uv run --directory cli ruff format --check`
  - `uv run --directory cli pyright`

  Then a live smoke on your box: `uv run --directory cli python -c "from researchly_cli import engines; e = engines.detect(); print(e, e and engines.ready(e))"`. Report the output.
- [ ] **Step 5: Commit** `feat(cli): container engine detection, auto-start and runtime selection`

---

### Task 6: Native mode (pg0 PostgreSQL + supervised app) and the pg0 spike test

**Files:**
- Create: `cli/src/researchly_cli/native.py`
- Test: `cli/tests/test_native.py`

**Interfaces:**
- Consumes:
  - `state.{data_dir, log_dir, free_port, healthy, wait_until, State, Secrets}` (Task 3).
  - `supervise.{spawn, supervisor_cmd, terminate_tree}` (Task 4).
  - `researchly.db.provision.setup` (Task 2).
  - `Settings.local_token` via env `RESEARCHLY_LOCAL_TOKEN` (Task 1).
- Produces:
  - `PG_PORT = 54329`; `APP_PORT = 8765`.
  - `class NativeStartError(RuntimeError)`.
  - `NativePostgres(data_dir: Path, password: str)` with `.pg: Pg0`, `.start() -> tuple[str, int]` (superuser URL, port) and `.stop() -> None`.
  - `start(secrets: Secrets) -> State`; `stop(run: State, secrets: Secrets) -> None`.

pg0 facts (verified on Windows by the controller):
- `start()` on a running instance raises `Pg0AlreadyRunningError`, so check `.running` and use `.info()`. `stop()` is idempotent.
- The postmaster is a detached daemon.
- Cold start including initdb is about 7 s; warm start is about 0.5 s.
- `InstanceInfo` has `.port` and `.data_dir`.

- [ ] **Step 1: Failing test** — `cli/tests/test_native.py`:

```python
import os
import platform
import sys
import time
from pathlib import Path

import psutil
import pytest
from sqlalchemy import create_engine, text

from researchly_cli import native, state

pytestmark = pytest.mark.native


def _report(line: str) -> None:
    """Spike measurements (spec §26 risk 1) go to the CI job summary for ADR 0002."""
    print(line)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"- {sys.platform}/{platform.machine()}: {line}\n")


@pytest.fixture
def secrets():
    s = state.load_secrets()
    yield s
    pg = native.NativePostgres(state.data_dir(), s.pg_superuser).pg
    pg.stop()
    pg.drop()  # test teardown only: drop() deletes the data dir


def test_native_start_serves_the_app_on_pg0_and_stops_cleanly(secrets):
    began = time.monotonic()
    run = native.start(secrets)
    cold = time.monotonic() - began
    try:
        assert state.healthy(run.port)
        assert run.pg_port is not None
        engine = create_engine(f"postgresql+psycopg://researchly_app:{secrets.db_app}@127.0.0.1:{run.pg_port}/researchly")
        with engine.connect() as conn:
            exts = set(conn.scalars(text("SELECT extname FROM pg_extension")))
            bm25 = conn.scalar(text("SELECT value FROM capability WHERE key = 'bm25_engine'"))
        engine.dispose()
        assert {"vector", "pg_trgm"} <= exts
        assert bm25 == "tsvector"
        info = native.NativePostgres(state.data_dir(), secrets.pg_superuser).pg.info()
        assert Path(info.data_dir).resolve() == (state.data_dir() / "pg" / "18" / "native").resolve()

        # the supervisor restarts a crashed app
        sup = psutil.Process(run.supervisor_pid)
        apps = [p for p in sup.children(recursive=True) if "uvicorn" in " ".join(p.cmdline())]
        for p in apps:
            p.kill()
        assert state.wait_until(lambda: not state.healthy(run.port), 10, 0.2)
        assert state.wait_until(lambda: state.healthy(run.port), 30, 0.5)
    finally:
        native.stop(run, secrets)
    assert not state.healthy(run.port)
    assert not native.NativePostgres(state.data_dir(), secrets.pg_superuser).pg.running

    began = time.monotonic()
    warm_run = native.start(secrets)
    warm = time.monotonic() - began
    native.stop(warm_run, secrets)
    _report(f"pg0 native start: cold {cold:.1f} s (initdb + provision + migrate + app), warm {warm:.1f} s")
```

- [ ] **Step 2: Run, expect FAIL:** `uv run --directory cli pytest tests/test_native.py -q`
- [ ] **Step 3: Implement** `cli/src/researchly_cli/native.py`:

```python
"""Native runtime (spec §4.1, §4.2): pg0-embedded PostgreSQL 18 plus a supervised app process."""

import hashlib
import os
import sys
from pathlib import Path

from pg0 import Pg0

from researchly.db.provision import setup as setup_database
from researchly_cli import state
from researchly_cli.supervise import spawn, supervisor_cmd, terminate_tree

PG_PORT = 54329  # spec §16.1 default; another free port when it is taken (the actual one is read back from info)
APP_PORT = 8765


class NativeStartError(RuntimeError):
    pass


class NativePostgres:
    """pg0 runs PostgreSQL as a detached daemon, so stop() is explicit. Never call Pg0.drop(): it deletes the data."""

    def __init__(self, data_dir: Path, password: str) -> None:
        pgdata = (data_dir / "pg" / "18" / "native").resolve()
        pgdata.mkdir(parents=True, exist_ok=True)
        name = "researchly-" + hashlib.sha256(str(pgdata).encode()).hexdigest()[:8]  # one pg0 instance per data dir
        self._password = password
        self.pg = Pg0(
            name=name,
            port=state.free_port(PG_PORT),  # ignored when the instance is already running: start() reads info()
            username="postgres",
            password=password,
            database="postgres",
            data_dir=str(pgdata),
        )

    def start(self) -> tuple[str, int]:
        """Start the server, or reuse it if it is already running. Returns (superuser URL, port)."""
        info = self.pg.info() if self.pg.running else self.pg.start()
        return f"postgresql+psycopg://postgres:{self._password}@127.0.0.1:{info.port}/postgres", info.port

    def stop(self) -> None:
        self.pg.stop()


def start(secrets: state.Secrets) -> state.State:
    superuser_url, pg_port = NativePostgres(state.data_dir(), secrets.pg_superuser).start()
    app_url = setup_database(superuser_url, secrets.db_app, secrets.db_ro)
    port = state.free_port(APP_PORT)
    uvicorn = [sys.executable, "-m", "uvicorn", "--factory", "researchly.app:create_app", "--host", "127.0.0.1", "--port", str(port)]
    env = {**os.environ, "RESEARCHLY_DATABASE_URL": app_url, "RESEARCHLY_LOCAL_TOKEN": secrets.local_token}
    logs = state.log_dir()
    supervisor = spawn(supervisor_cmd(logs / "app.log", uvicorn), logs / "supervisor.log", env)
    run = state.State(
        mode="native", port=port, supervisor_pid=supervisor.pid, supervisor_created=supervisor.create_time(), pg_port=pg_port
    )
    if not state.wait_until(lambda: state.healthy(port), 60):
        stop(run, secrets)
        raise NativeStartError(f"the app did not become healthy within 60 s; see {logs / 'app.log'}")
    return run


def stop(run: state.State, secrets: state.Secrets) -> None:
    if run.supervisor_pid is not None:
        terminate_tree(run.supervisor_pid, run.supervisor_created)
    NativePostgres(state.data_dir(), secrets.pg_superuser).stop()
```

- [ ] **Step 4: Run, expect PASS:**
  - `uv run --directory cli pytest -q -s`: report the `pg0 native start` line in your report.
  - `uv run --directory cli ruff check`
  - `uv run --directory cli ruff format --check`
  - `uv run --directory cli pyright`
- [ ] **Step 5: Commit** `feat(cli): native runtime on pg0 PostgreSQL with a supervised app`

---

### Task 7: Docker infra (Postgres image, app image, compose, SearXNG) and Docker mode

**Files:**
- Create: `infra/postgres/Dockerfile`, `infra/app.Dockerfile`, `infra/app.Dockerfile.dockerignore`, `infra/compose.yaml`, `infra/searxng/settings.yml`, `cli/src/researchly_cli/docker.py`
- Test: `cli/tests/test_docker.py`

**Interfaces:**
- Consumes:
  - `state.{data_dir, free_port, State, Secrets}` (Task 3).
  - `engines.{detect, ready}` (Task 5, test only).
  - `python -m researchly.db.provision` (Task 2).
- Produces:
  - `COMPOSE_FILE: Path`; `APP_PORT = 8765`.
  - `class DockerStartError(RuntimeError)`.
  - `compose_env(secrets: Secrets, port: int) -> dict[str, str]`.
  - `build(secrets: Secrets, compose: Sequence[str]) -> None`.
  - `start(secrets: Secrets, compose: Sequence[str]) -> State`; `stop(run: State, secrets: Secrets) -> None`.

- [ ] **Step 1: Failing tests** — `cli/tests/test_docker.py`:

```python
import re
import subprocess
import uuid

import pytest

from researchly_cli import docker, engines, state


def test_compose_env_supplies_every_required_variable():
    required = set(re.findall(r"\$\{(RESEARCHLY_[A-Z_]+):\?", docker.COMPOSE_FILE.read_text("utf-8")))
    env = docker.compose_env(state.load_secrets(), 8765)
    assert required and required <= env.keys()


def test_compose_file_pins_every_image():
    text = docker.COMPOSE_FILE.read_text("utf-8")
    for image in re.findall(r"image:\s*(\S+)", text):
        assert image.endswith(":dev") or re.search(r":\d", image), image  # local builds or pinned tags, never latest


@pytest.mark.docker
def test_docker_stack_serves_health_with_bm25(monkeypatch):
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", f"researchly-test-{uuid.uuid4().hex[:6]}")
    engine = engines.detect()
    assert engine is not None and engine.compose and engines.ready(engine)
    secrets = state.load_secrets()
    run = docker.start(secrets, engine.compose)
    try:
        assert state.healthy(run.port)

        def psql(sql: str) -> str:
            cmd = [*engine.compose, "-f", str(docker.COMPOSE_FILE), "exec", "-T", "postgres",
                   "psql", "-U", "postgres", "-d", "researchly", "-tAc", sql]
            env = docker.compose_env(secrets, run.port)
            return subprocess.run(cmd, env=env, capture_output=True, text=True, check=True).stdout.strip()  # noqa: S603

        assert {"vector", "pg_trgm", "pg_textsearch"} <= set(psql("SELECT string_agg(extname, ',') FROM pg_extension").split(","))
        assert psql("SELECT value #>> '{}' FROM capability WHERE key = 'bm25_engine'") == "pg_textsearch"
    finally:
        docker.stop(run, secrets)
    assert not state.healthy(run.port)
```

- [ ] **Step 2: Run, expect FAIL:** `uv run --directory cli pytest tests/test_docker.py -q`
- [ ] **Step 3: Infra files.**

`infra/postgres/Dockerfile` (builds and works; verified by the controller):

```dockerfile
# syntax=docker/dockerfile:1
# Docker-mode PostgreSQL (spec §4.1, §16.1): PG 18 + pgvector + pg_trgm (contrib) + pg_textsearch BM25.
ARG BASE=pgvector/pgvector:0.8.7-pg18-trixie

FROM ${BASE} AS build
ARG PG_TEXTSEARCH_VERSION=1.5.1
ARG PG_TEXTSEARCH_SHA256=5b90a160c4aa9ab78c72ab41eb761146a601485bff5e271f05a44d3da7493d5b
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl ca-certificates postgresql-server-dev-18 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fsSL -o pgt.tar.gz "https://github.com/timescale/pg_textsearch/releases/download/v${PG_TEXTSEARCH_VERSION}/pg_textsearch-${PG_TEXTSEARCH_VERSION}.tar.gz" \
 && echo "${PG_TEXTSEARCH_SHA256}  pgt.tar.gz" | sha256sum -c - \
 && tar xzf pgt.tar.gz --strip-components=1 \
 && make -j"$(nproc)" \
 && make install DESTDIR=/out

FROM ${BASE}
COPY --from=build /out/ /
CMD ["postgres", "-c", "shared_preload_libraries=pg_textsearch"]
```

`infra/app.Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1
# The Researchly app (spec §4.1 Docker mode). Build context: backend/.
FROM python:3.12-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    LITELLM_LOCAL_MODEL_COST_MAP=True
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY . .
RUN uv sync --locked --no-dev --no-editable \
 && useradd --system --uid 10001 researchly
USER researchly
EXPOSE 8000
CMD ["uvicorn", "--factory", "researchly.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
```

`infra/app.Dockerfile.dockerignore` (BuildKit reads `<Dockerfile>.dockerignore`):

```
.venv
**/__pycache__
.pytest_cache
.ruff_cache
tests
```

`infra/searxng/settings.yml`:

```yaml
# SearXNG for Researchly (spec §4.1): private instance on the compose network; JSON API on, rate limiter off.
# The secret comes from SEARXNG_SECRET, injected by the CLI.
use_default_settings: true
server:
  limiter: false
  public_instance: false
  image_proxy: false
search:
  formats: [html, json]
```

`infra/compose.yaml`:

```yaml
# Docker mode (spec §4.1, §16.1). The CLI injects every ${RESEARCHLY_*} value at start; no secret lives here.
# The database is on the internal network only; the app publishes 127.0.0.1 only.
name: researchly

x-app: &app
  build:
    context: ../backend
    dockerfile: ../infra/app.Dockerfile
  image: researchly-app:dev

services:
  postgres:
    build: ./postgres
    image: researchly-postgres:dev
    environment:
      POSTGRES_PASSWORD: ${RESEARCHLY_PG_SUPERUSER_PASSWORD:?}
    volumes:
      - ${RESEARCHLY_DATA_DIR:?}/pg:/var/lib/postgresql
    networks: [internal]
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "postgres"]
      interval: 2s
      timeout: 5s
      retries: 30
    restart: unless-stopped

  migrate:
    <<: *app
    command: ["python", "-m", "researchly.db.provision"]
    environment:
      RESEARCHLY_DB_SUPERUSER_URL: postgresql+psycopg://postgres:${RESEARCHLY_PG_SUPERUSER_PASSWORD:?}@postgres:5432/postgres
      RESEARCHLY_DB_APP_PASSWORD: ${RESEARCHLY_DB_APP_PASSWORD:?}
      RESEARCHLY_DB_RO_PASSWORD: ${RESEARCHLY_DB_RO_PASSWORD:?}
    networks: [internal]
    depends_on:
      postgres: {condition: service_healthy}
    restart: "no"

  app:
    <<: *app
    environment:
      RESEARCHLY_DATABASE_URL: postgresql+psycopg://researchly_app:${RESEARCHLY_DB_APP_PASSWORD:?}@postgres:5432/researchly
      RESEARCHLY_LOCAL_TOKEN: ${RESEARCHLY_LOCAL_TOKEN:?}
    ports: ["127.0.0.1:${RESEARCHLY_PORT:-8765}:8000"]
    networks: [internal, default]
    depends_on:
      postgres: {condition: service_healthy}
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
      interval: 5s
      timeout: 5s
      retries: 12
    restart: unless-stopped

  searxng:
    image: searxng/searxng:2026.10.25-54ca1d9cf
    environment:
      SEARXNG_SECRET: ${RESEARCHLY_SEARXNG_SECRET:?}
    volumes:
      - ./searxng/settings.yml:/etc/searxng/settings.yml:ro
    networks: [internal, default]
    restart: unless-stopped

  grobid:
    image: grobid/grobid:0.9.1-crf
    profiles: [grobid]
    networks: [internal, default]
    restart: unless-stopped

networks:
  internal:
    internal: true
```

- [ ] **Step 4: Implement** `cli/src/researchly_cli/docker.py`:

```python
"""Docker runtime (spec §4.1): the compose stack in infra/, driven through the engine's compose command."""

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from researchly_cli import state

# ponytail: works from a source checkout only; ship the compose file with the release images (step 10)
COMPOSE_FILE = Path(__file__).resolve().parents[3] / "infra" / "compose.yaml"
APP_PORT = 8765


class DockerStartError(RuntimeError):
    pass


def compose_env(secrets: state.Secrets, port: int) -> dict[str, str]:
    """Secrets reach compose only through the environment of the compose process (spec §16.1)."""
    return {
        **os.environ,
        "RESEARCHLY_DATA_DIR": str(state.data_dir().resolve()),
        "RESEARCHLY_PORT": str(port),
        "RESEARCHLY_PG_SUPERUSER_PASSWORD": secrets.pg_superuser,
        "RESEARCHLY_DB_APP_PASSWORD": secrets.db_app,
        "RESEARCHLY_DB_RO_PASSWORD": secrets.db_ro,
        "RESEARCHLY_LOCAL_TOKEN": secrets.local_token,
        "RESEARCHLY_SEARXNG_SECRET": secrets.searxng,
    }


def _compose(compose: Sequence[str], args: Sequence[str], env: dict[str, str]) -> None:
    if not COMPOSE_FILE.is_file():
        raise DockerStartError(f"{COMPOSE_FILE} not found: Docker mode needs a source checkout until release images ship")
    cmd = [*compose, "-f", str(COMPOSE_FILE), *args]
    if subprocess.run(cmd, env=env, check=False).returncode != 0:  # noqa: S603 - fixed argv; output streams to the user
        raise DockerStartError(f"`{' '.join(cmd)}` failed")


def build(secrets: state.Secrets, compose: Sequence[str]) -> None:
    _compose(compose, ["build"], compose_env(secrets, APP_PORT))


def start(secrets: state.Secrets, compose: Sequence[str]) -> state.State:
    port = state.free_port(APP_PORT)
    env = compose_env(secrets, port)
    (state.data_dir() / "pg").mkdir(parents=True, exist_ok=True)
    _compose(compose, ["build"], env)
    _compose(compose, ["up", "-d", "--wait", "postgres"], env)
    _compose(compose, ["run", "--rm", "migrate"], env)  # spec §16.1: a failed migration aborts the start
    _compose(compose, ["up", "-d", "--wait", "app", "searxng"], env)
    return state.State(mode="docker", port=port, compose=tuple(compose))


def stop(run: state.State, secrets: state.Secrets) -> None:
    _compose(run.compose, ["down"], compose_env(secrets, run.port))
```

- [ ] **Step 5: Run, expect PASS.**
  - `uv run --directory cli pytest -q`; the Docker test is skipped.
  - Then, with Docker running locally: `RESEARCHLY_TEST_DOCKER=1 uv run --directory cli pytest tests/test_docker.py -q`. It must pass on this Windows box, which exercises the bind mount of a Windows path.
  - `uv run --directory cli ruff check`
  - `uv run --directory cli ruff format --check`
  - `uv run --directory cli pyright`

  If `docker compose up --wait` rejects something, report the exact error. Do not loosen the healthchecks.
- [ ] **Step 6: Commit** `feat(infra): Docker-mode stack (pg_textsearch image, app image, SearXNG, GROBID) and compose driver`

---

### Task 8: Commands `setup`, `start`, `stop`, `status`, `doctor`

**Files:**
- Modify: `cli/src/researchly_cli/main.py`
- Test: `cli/tests/test_main.py`

**Interfaces:**
- Consumes:
  - `state` (Task 3), `engines` (Task 5), `native` (Task 6), `docker` (Task 7).
  - `researchly.security.keychain.store_name` (Task 1).
- Produces the commands:
  - `researchly setup | start | stop | status | doctor`;
  - `status` exits 1 when not running or unhealthy;
  - every failure exits 1 with a message on stderr.

- [ ] **Step 1: Failing tests** — `cli/tests/test_main.py`:

```python
import pytest
from typer.testing import CliRunner

from researchly_cli import docker, engines, main, native, state
from researchly_cli.main import app

runner = CliRunner()
DESKTOP = engines.Engine("docker-desktop", "docker", ("docker", "compose"), ("open", "-a", "Docker"))


def boom(*_args, **_kwargs):
    raise AssertionError("must not be called")


@pytest.fixture
def healthy(monkeypatch):
    ports: set[int] = set()
    monkeypatch.setattr(state, "healthy", lambda port: port in ports)
    return ports


def test_start_native_writes_state_and_prints_url(monkeypatch, healthy):
    monkeypatch.setattr(main, "_select", lambda: engines.Selection("native", "runtime pinned to native"))
    monkeypatch.setattr(native, "start", lambda secrets: state.State(mode="native", port=9999))
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0, result.output
    assert "http://127.0.0.1:9999" in result.stdout
    assert state.read_state() == state.State(mode="native", port=9999)


def test_start_docker_uses_the_engine_compose_command(monkeypatch, healthy):
    podman = engines.Engine("podman", "podman", ("podman", "compose"), None)
    monkeypatch.setattr(main, "_select", lambda: engines.Selection("docker", "podman is ready", podman))
    seen = []
    monkeypatch.setattr(docker, "start", lambda secrets, compose: seen.append(compose) or state.State(mode="docker", port=9998, compose=tuple(compose)))
    assert runner.invoke(app, ["start"]).exit_code == 0
    assert seen == [("podman", "compose")]


def test_start_is_a_noop_when_already_healthy(monkeypatch, healthy):
    state.write_state(state.State(mode="native", port=9999))
    healthy.add(9999)
    monkeypatch.setattr(main, "_select", boom)
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0 and "Already running" in result.stdout


def test_start_cleans_up_a_crashed_run_first(monkeypatch, healthy):
    state.write_state(state.State(mode="native", port=9999, supervisor_pid=1))
    calls = []
    monkeypatch.setattr(native, "stop", lambda run, secrets: calls.append(("stop", run.port)))
    monkeypatch.setattr(main, "_select", lambda: engines.Selection("native", "pinned"))
    monkeypatch.setattr(native, "start", lambda secrets: calls.append(("start", 0)) or state.State(mode="native", port=9997))
    assert runner.invoke(app, ["start"]).exit_code == 0
    assert calls == [("stop", 9999), ("start", 0)]


def test_start_reports_a_failed_start(monkeypatch, healthy):
    monkeypatch.setattr(main, "_select", lambda: engines.Selection("native", "pinned"))

    def fail(secrets):
        raise native.NativeStartError("the app did not become healthy within 60 s")

    monkeypatch.setattr(native, "start", fail)
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 1 and "did not become healthy" in result.output
    assert state.read_state() is None


def test_pinned_docker_without_an_engine_fails(monkeypatch):
    state.save_config({"runtime": "docker"})
    monkeypatch.setattr(engines, "detect", lambda: None)
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 1 and "no container engine" in result.output


def test_docker_desktop_licence_note_is_shown_once(monkeypatch, healthy):
    monkeypatch.setattr(main, "_select", lambda: engines.Selection("docker", "ready", DESKTOP))
    monkeypatch.setattr(docker, "start", lambda secrets, compose: state.State(mode="docker", port=9996, compose=tuple(compose)))
    first = runner.invoke(app, ["start"]).stdout
    state.clear_state()
    second = runner.invoke(app, ["start"]).stdout
    assert "Docker Desktop" in first and "subscription" in first
    assert "subscription" not in second


def test_stop_clears_state(monkeypatch):
    state.write_state(state.State(mode="docker", port=9996, compose=("docker", "compose")))
    stopped = []
    monkeypatch.setattr(docker, "stop", lambda run, secrets: stopped.append(run.port))
    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 0 and stopped == [9996] and state.read_state() is None


def test_stop_when_not_running():
    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 0 and "not running" in result.stdout


def test_status_exit_codes(healthy):
    assert runner.invoke(app, ["status"]).exit_code == 1
    state.write_state(state.State(mode="native", port=9999))
    assert runner.invoke(app, ["status"]).exit_code == 1
    healthy.add(9999)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0 and "native: running at http://127.0.0.1:9999" in result.stdout


def test_setup_builds_images_in_docker_mode(monkeypatch):
    monkeypatch.setattr(main, "_select", lambda: engines.Selection("docker", "colima is ready", DESKTOP))
    built = []
    monkeypatch.setattr(docker, "build", lambda secrets, compose: built.append(compose))
    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 0 and built == [("docker", "compose")]
    assert "researchly start" in result.stdout


def test_doctor_lists_native_degradations(monkeypatch):
    monkeypatch.setattr(engines, "detect", lambda: None)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    for expected in ("container engine: none found", "secrets store: OS keychain", "tsvector", "status: stopped"):
        assert expected in result.stdout


@pytest.mark.native
def test_native_cli_round_trip():
    state.save_config({"runtime": "native"})
    try:
        assert runner.invoke(app, ["start"]).exit_code == 0
        assert runner.invoke(app, ["status"]).exit_code == 0
        assert runner.invoke(app, ["stop"]).exit_code == 0
        assert runner.invoke(app, ["status"]).exit_code == 1
    finally:
        pg = native.NativePostgres(state.data_dir(), state.load_secrets().pg_superuser).pg
        pg.stop()
        pg.drop()  # test teardown only
```

- [ ] **Step 2: Run, expect FAIL:** `uv run --directory cli pytest tests/test_main.py -q`
- [ ] **Step 3: Implement.** Add to `cli/src/researchly_cli/main.py` (keep the Task 3 code; replace the import line):

```python
from typing import NoReturn

import typer

from researchly.security.keychain import store_name
from researchly_cli import docker, engines, native, state

DEGRADED_NATIVE = (
    "keyword search uses tsvector (pg_textsearch BM25 needs Docker mode)",
    "web search is not available natively yet (native SearXNG spike, plan 1e)",
    "GROBID reference parsing needs Docker mode",
)
LICENCE_NOTE = (
    "Note: Docker Desktop needs a paid subscription for companies with 250+ employees or $10M+ revenue. "
    "Researchly never requires it; any engine or native mode works."
)


def _fail(message: str) -> NoReturn:
    typer.secho(message, err=True, fg=typer.colors.RED)
    raise typer.Exit(1)


def _select() -> engines.Selection:
    pin = state.load_config()["runtime"]
    engine = None if pin == "native" else engines.detect()
    try:
        return engines.select_runtime(
            pin, engine, ready=engines.ready, launch=engines.launch,
            wait=lambda check, timeout: state.wait_until(check, timeout, 2.0), echo=typer.echo,
        )
    except engines.RuntimeUnavailable as exc:
        _fail(str(exc))


def _announce(selection: engines.Selection) -> None:
    typer.echo(f"runtime: {selection.mode} ({selection.reason})")
    if selection.engine is not None and selection.engine.name == "docker-desktop":
        config = state.load_config()
        if not config.get("docker_desktop_note"):
            typer.echo(LICENCE_NOTE)
            state.save_config({**config, "docker_desktop_note": "shown"})


def _stop(run: state.State, secrets: state.Secrets) -> None:
    try:
        (docker.stop if run.mode == "docker" else native.stop)(run, secrets)
    except docker.DockerStartError as exc:
        _fail(str(exc))


@app.command()
def setup() -> None:
    """Choose the runtime (starting a container engine if needed) and prepare it."""
    selection = _select()
    _announce(selection)
    secrets = state.load_secrets()
    if selection.mode == "docker" and selection.engine is not None:
        try:
            docker.build(secrets, selection.engine.compose)
        except docker.DockerStartError as exc:
            _fail(str(exc))
    typer.echo(f"secrets store: {store_name()}")
    typer.echo("Ready. Run `researchly start`.")


@app.command()
def start() -> None:
    """Start Researchly in the best available runtime and print its URL."""
    current = state.read_state()
    secrets = state.load_secrets()
    if current is not None:
        if state.healthy(current.port):
            typer.echo(f"Already running ({current.mode}) at {current.url}")
            return
        _stop(current, secrets)  # leftovers of a crashed run
        state.clear_state()
    selection = _select()
    _announce(selection)
    try:
        if selection.mode == "docker" and selection.engine is not None:
            run = docker.start(secrets, selection.engine.compose)
        else:
            run = native.start(secrets)
    except (docker.DockerStartError, native.NativeStartError) as exc:
        _fail(str(exc))
    state.write_state(run)
    typer.echo(f"Researchly is running at {run.url}")


@app.command()
def stop() -> None:
    """Stop Researchly. Data is kept."""
    current = state.read_state()
    if current is None:
        typer.echo("Researchly is not running.")
        return
    _stop(current, state.load_secrets())
    state.clear_state()
    typer.echo("Stopped.")


@app.command()
def status() -> None:
    """Show whether Researchly is running; exits 1 when it is not healthy."""
    current = state.read_state()
    if current is None:
        typer.echo("stopped")
        raise typer.Exit(1)
    ok = state.healthy(current.port)
    typer.echo(f"{current.mode}: {'running' if ok else 'unhealthy'} at {current.url}")
    if not ok:
        raise typer.Exit(1)


@app.command()
def doctor() -> None:
    """Report runtime, container engine, secrets store, paths and degraded capabilities."""
    config = state.load_config()
    engine = engines.detect()
    typer.echo(f"runtime setting: {config['runtime']}")
    if engine is None:
        typer.echo("container engine: none found")
    else:
        detail = "ready" if engines.ready(engine) else "stopped"
        typer.echo(f"container engine: {engine.name} ({detail}{'' if engine.compose else ', no compose support'})")
    typer.echo(f"secrets store: {store_name()}")
    typer.echo(f"data dir: {state.data_dir()}")
    typer.echo(f"logs: {state.log_dir()}")
    current = state.read_state()
    if current is None:
        typer.echo("status: stopped")
        mode = "native" if config["runtime"] == "native" or engine is None else "docker"
    else:
        typer.echo(f"status: {current.mode} at {current.url} ({'healthy' if state.healthy(current.port) else 'unhealthy'})")
        mode = current.mode
    if mode == "native":
        typer.echo("degraded in native mode:")
        for line in DEGRADED_NATIVE:
            typer.echo(f"  - {line}")
```

- [ ] **Step 4: Run, expect PASS:**
  - `uv run --directory cli pytest -q` (including the native round trip)
  - `uv run --directory cli ruff check`
  - `uv run --directory cli ruff format --check`
  - `uv run --directory cli pyright`

  Then a manual smoke with a throwaway data dir:
  - `RESEARCHLY_DATA_DIR=<tmp> uv run --directory cli researchly config set runtime native`
  - `… researchly start`
  - `… researchly status`
  - `… researchly doctor`
  - `… researchly stop`

  Paste the outputs in your report.
- [ ] **Step 5: Commit** `feat(cli): setup, start, stop, status and doctor commands`

---

### Task 9: CI matrix, Docker job, docs, ADR 0002, PR (controller)

**Files:**
- Modify: `.github/workflows/ci.yml`, `AGENTS.md` (Commands table), `docs/progress.md`
- Create: `docs/adr/0002-native-postgres-pg0.md`

- [ ] **Step 1: CI jobs.** Append to `.github/workflows/ci.yml`:

```yaml
  cli:
    name: cli (${{ matrix.os }})
    runs-on: ${{ matrix.os }}
    continue-on-error: ${{ matrix.os == 'macos-15-intel' }} # Intel macOS runner images are being retired
    timeout-minutes: 25
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-24.04, ubuntu-24.04-arm, windows-2025, macos-15, macos-15-intel]
    defaults:
      run:
        working-directory: cli
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@07a047e608db84b3d8c06cd9a7c7dcc9c355c18e # v10.3.2
        with:
          version: "0.12.10"
          enable-cache: true
          cache-dependency-glob: cli/uv.lock
      - run: uv sync --locked
      - run: uv run ruff check
      - run: uv run ruff format --check
      - run: uv run pyright
      - run: uv run pytest -q -rA

  docker:
    runs-on: ubuntu-24.04
    timeout-minutes: 30
    env:
      RESEARCHLY_TEST_DOCKER: "1"
    defaults:
      run:
        working-directory: cli
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@07a047e608db84b3d8c06cd9a7c7dcc9c355c18e # v10.3.2
        with:
          version: "0.12.10"
          enable-cache: true
          cache-dependency-glob: cli/uv.lock
      - run: uv sync --locked
      - run: uv run pytest -m docker -q -rA
```

- [ ] **Step 2: AGENTS.md Commands table:** add rows `CLI tests | uv run --directory cli pytest -q` (Docker-mode tests: prefix `RESEARCHLY_TEST_DOCKER=1`) and `CLI lint/types | uv run --directory cli ruff check · uv run --directory cli pyright`.
- [ ] **Step 3: Push, open the PR, watch CI.** Collect the `pg0 native start` lines from each matrix job's summary.
- [ ] **Step 4: ADR 0002**, `docs/adr/0002-native-postgres-pg0.md`, per `docs/adr/0000-template.md`:
  - **Decision:** native mode uses pg0-embedded 0.15.2.
  - **Evidence:**
    - per-target CREATE EXTENSION results and cold/warm start times from CI;
    - the Windows local verification;
    - the detached-daemon lifecycle and how stop/crash are handled;
    - the conda-forge/pixi fallback stays documented, not built.
  - If any target fails `vector`/`pg_trgm`, the ADR records the fallback decision for that target instead.
- [ ] **Step 5:** Append the step 1c entry to `docs/progress.md`. Commit `docs: ADR 0002 native Postgres and step 1c outcome`. Final whole-branch review, then merge once CI is green.
