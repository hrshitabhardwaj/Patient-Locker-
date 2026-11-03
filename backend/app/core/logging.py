"""Logging setup with a redaction filter so contact details never reach log output.

Code should log patient UUIDs, never names, phone numbers, emails or document text.
The filter is a safety net for mistakes, not a licence to log personal data.
"""

import logging
import re
import sys

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 10 to 14 digits with optional single space/hyphen separators and an optional leading "+".
# Hyphens and colons around the match are excluded so UUID segments and times are left alone.
_PHONE_RE = re.compile(r"(?<![\w\-:])\+?\d(?:[\s-]?\d){9,13}(?![\w\-:])")


# Query strings can carry search terms such as patient names; keep only the path.
_QUERY_STRING_RE = re.compile(r"(/[^\s?\"']*)\?[^\s\"']*")
# Libraries that log every outbound URL at INFO.
_NOISY_LOGGERS = ("httpx", "httpx2", "httpcore", "urllib3")


def redact(text: str) -> str:
    text = _QUERY_STRING_RE.sub(r"\1?[query]", text)
    text = _EMAIL_RE.sub("[email]", text)
    return _PHONE_RE.sub("[phone]", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        record.msg = redact(message)
        record.args = None
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactingFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
