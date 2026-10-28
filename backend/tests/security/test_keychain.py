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
