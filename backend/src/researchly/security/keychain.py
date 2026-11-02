"""Secrets (spec §19.2): the OS keychain, read at call time and never cached.

Hosts with no keychain backend (headless Linux) fall back to an AES-GCM encrypted file in the data dir whose key
is a 0600 machine-key file next to it.
"""

import base64
import contextlib
import json
import os
import secrets
import sys
from pathlib import Path

import keyring
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from keyring.backend import KeyringBackend
from keyring.backends import fail
from keyring.errors import KeyringError, PasswordDeleteError
from pydantic import ValidationError

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
            try:
                fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass  # another process created it first: use theirs
            else:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(AESGCM.generate_key(bit_length=256))
        key = self.key_path.read_bytes()
        if len(key) != 32:
            raise KeyringError(f"{self.key_path} is not a 256-bit key")
        return key

    def _entries(self) -> dict[str, str]:
        try:
            return json.loads(self.path.read_text("utf-8")) if self.path.exists() else {}
        except ValueError as exc:  # json.JSONDecodeError and bad UTF-8 included
            raise KeyringError(f"{self.path} is corrupt") from exc

    def _save(self, entries: dict[str, str]) -> None:
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        if sys.platform != "win32":
            os.fchmod(fd, 0o600)  # the mode above applies only when the file is new
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(entries, fh)
        os.replace(tmp, self.path)

    def get_password(self, service: str, username: str) -> str | None:
        blob = self._entries().get(f"{service}/{username}")
        if blob is None:
            return None
        try:
            raw = base64.b64decode(blob)
            return AESGCM(self._key()).decrypt(raw[:12], raw[12:], f"{service}/{username}".encode()).decode()
        except (InvalidTag, ValueError) as exc:  # ValueError: bad base64 or a truncated nonce
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
        try:
            directory = Settings().data_dir
        except ValidationError as exc:  # its text echoes RESEARCHLY_* values, which may be secrets
            fields = ", ".join(str(error["loc"][0]) for error in exc.errors() if error["loc"])
            raise KeychainUnavailable(
                f"cannot locate the secrets file: invalid RESEARCHLY_* settings ({fields})"
            ) from exc
        keyring.set_keyring(EncryptedFileKeyring(directory))
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
        value = secrets.token_hex(32)  # hex: no leading "-" (argv), safe in URLs and DDL
        set_secret(name, value)
    return value


def ensure_local_token() -> str:
    """The per-install API token (spec §4.3)."""
    return ensure_secret(LOCAL_TOKEN)
