import os
import re
import sys
from pathlib import Path

import keyring
import pytest
from keyring.backends.fail import Keyring as FailKeyring
from keyring.errors import KeyringLocked

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


def test_locked_keychain_raises_clear_error(monkeypatch):
    def locked(*_args):
        raise KeyringLocked("locked")

    monkeypatch.setattr(type(keyring.get_keyring()), "get_password", locked)
    with pytest.raises(keychain.KeychainUnavailable):
        keychain.get_secret("anthropic")


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
    (tmp_path / "secrets.key").write_bytes(b"\x00" * 32)  # a different machine key
    with pytest.raises(keychain.KeychainUnavailable):
        keychain.get_secret("anthropic")


def test_os_keychain_store_name():
    assert keychain.store_name() == "OS keychain (MemoryKeyring)"


def test_ensure_secret_generates_once():
    first = keychain.ensure_secret("db-app-password")
    assert re.fullmatch(r"[0-9a-f]{64}", first)  # never a leading "-": pg0 passes the password as a CLI argument
    assert keychain.ensure_secret("db-app-password") == first


@pytest.fixture
def file_store(tmp_path, monkeypatch):
    """No OS keychain: secrets go to the encrypted file in tmp_path."""
    monkeypatch.setenv("RESEARCHLY_DATA_DIR", str(tmp_path))
    keyring.set_keyring(FailKeyring())
    return tmp_path


def test_key_file_race_uses_the_winners_key(file_store, monkeypatch):
    winner = b"\x01" * 32
    real_open = os.open

    def racing_open(path, flags, mode=0o777):
        if flags & os.O_EXCL:  # another process creates the key between our exists() check and our create
            Path(path).write_bytes(winner)
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", racing_open)
    keychain.set_secret("anthropic", "value-123456")
    assert (file_store / "secrets.key").read_bytes() == winner
    assert keychain.get_secret("anthropic") == "value-123456"


def test_key_file_of_the_wrong_length_is_unavailable(file_store):
    (file_store / "secrets.key").write_bytes(b"\x01" * 16)
    with pytest.raises(keychain.KeychainUnavailable):
        keychain.set_secret("anthropic", "value-123456")


@pytest.mark.parametrize("content", ["{not json", '{"researchly/anthropic": "!!!"}'])
def test_corrupt_secrets_file_is_unavailable(file_store, content):
    keychain.set_secret("other", "value-123456")  # creates the key
    (file_store / "secrets.json").write_text(content, "utf-8")
    with pytest.raises(keychain.KeychainUnavailable):
        keychain.get_secret("anthropic")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_a_leftover_tmp_file_is_made_private_before_the_secret_is_written(tmp_path):
    (tmp_path / "secrets.tmp").write_text("{}", "utf-8")
    (tmp_path / "secrets.tmp").chmod(0o644)
    keychain.EncryptedFileKeyring(tmp_path).set_password("researchly", "x", "value-123456")
    assert (tmp_path / "secrets.json").stat().st_mode & 0o777 == 0o600


def test_invalid_settings_do_not_leak_a_validation_error(file_store, monkeypatch):
    monkeypatch.setenv("RESEARCHLY_ENV", "not-an-env")
    with pytest.raises(keychain.KeychainUnavailable) as caught:
        keychain.get_secret("anthropic")
    assert "\n" not in str(caught.value) and "not-an-env" not in str(caught.value)
