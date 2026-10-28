import logging
import sys

from researchly.security.redact import RedactingFilter, redact, register_secret


def test_redacts_known_key_shapes_and_emails():
    text = (
        "a sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWX b sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX c AIza"
        + "x" * 35
        + " d me@example.org e Bearer abcdefghijklmnopqrstu"
    )
    out = redact(text)
    for leaked in ("sk-ant-api03", "sk-proj-", "AIza", "me@example.org", "abcdefghijklmnopqrstu"):
        assert leaked not in out
    assert out.count("[REDACTED]") == 5


def test_logging_filter_redacts_args():
    record = logging.LogRecord(
        "x", logging.INFO, __file__, 1, "key %s", ("sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWX",), None
    )
    RedactingFilter().filter(record)
    assert record.getMessage() == "key [REDACTED]"


def test_overlapping_secrets_redact_longest_first():
    register_secret("overlap-secret-AAAA")
    register_secret("overlap-secret-AAAA-and-more-BBBB")
    out = redact("x overlap-secret-AAAA-and-more-BBBB y")
    assert out == "x [REDACTED] y"


def test_whitespace_only_secret_is_ignored():
    register_secret("          ")
    assert redact("a          b") == "a          b"


def test_logging_filter_redacts_exception_text():
    secret = "tb-secret-value-1234567890"  # noqa: S105 - test fixture value
    register_secret(secret)
    try:
        raise ValueError(f"boom {secret}")
    except ValueError:
        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
    RedactingFilter().filter(record)
    formatted = logging.Formatter().format(record)
    assert secret not in formatted
    assert "[REDACTED]" in formatted
