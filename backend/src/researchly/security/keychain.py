"""OS keychain access (spec §19.2). Secrets are read at call time and never cached."""

import contextlib
import secrets

import keyring
from keyring.errors import KeyringError, NoKeyringError, PasswordDeleteError

from researchly.security.redact import register_secret

SERVICE = "researchly"
LOCAL_TOKEN = "local-token"  # noqa: S105 - keychain entry name, not the secret itself


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
    with contextlib.suppress(PasswordDeleteError):
        keyring.delete_password(SERVICE, name)


def ensure_local_token() -> str:
    """The per-install API token (spec §4.3); created on first use."""
    token = get_secret(LOCAL_TOKEN)
    if token is None:
        token = secrets.token_urlsafe(32)
        set_secret(LOCAL_TOKEN, token)
    return token
