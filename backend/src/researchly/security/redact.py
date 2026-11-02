"""Scrub secrets from text before it reaches logs or fixtures (spec §7.6, §19.2)."""

import logging
import re

_PATTERNS = [
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{20,}"),  # OpenAI / Anthropic style keys; not "risk-of-bias-…"
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),  # Google API keys
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),  # emails
]
_known: set[str] = set()


def register_secret(value: str) -> None:
    if len(value.strip()) >= 8:
        _known.add(value)


def redact(text: str) -> str:
    for secret in sorted(_known, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    for pattern in _PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        record.__dict__.pop("color_message", None)  # uvicorn's coloured formatter would print it unformatted
        if record.exc_info:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
            record.exc_info = None  # formatters must use the redacted exc_text, never re-render the traceback
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


class RedactingArgsFilter(logging.Filter):
    """For formatters that unpack `record.args` (uvicorn's AccessFormatter): redact each string argument in place."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        return True
