import logging

from researchly.security.redact import RedactingFilter, redact


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
