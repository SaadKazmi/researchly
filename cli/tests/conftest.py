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
