"""Console logging with defense-in-depth secret redaction."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from typing import TextIO

_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|authorization)[\"']?\s*[:=]\s*)"
    r"(?:Bearer\s+\S+|\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)


def redact_text(text: str, secrets: Iterable[str] = ()) -> str:
    """Redact known secret values and common credential-shaped fields."""

    redacted = str(text)
    known_values = sorted({value for value in secrets if value}, key=len, reverse=True)
    for value in known_values:
        redacted = redacted.replace(value, "<redacted>")

    redacted = _BEARER_PATTERN.sub("Bearer <redacted>", redacted)
    return _KEY_VALUE_PATTERN.sub(lambda match: f"{match.group(1)}<redacted>", redacted)


class SecretRedactingFormatter(logging.Formatter):
    """Redact the final rendered record, including formatted exceptions."""

    converter = time.gmtime

    def __init__(self, *, secrets: Iterable[str] = ()) -> None:
        super().__init__(
            fmt="%(asctime)sZ %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        self._secrets = tuple(value for value in secrets if value)

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record), self._secrets)


def configure_logging(
    level: str | int = "INFO",
    *,
    secrets: Iterable[str] = (),
    stream: TextIO | None = None,
) -> logging.Logger:
    """Configure and return the isolated JARVIS X application logger."""

    if isinstance(level, str):
        numeric_level = getattr(logging, level.upper(), None)
        if not isinstance(numeric_level, int):
            raise ValueError("Unsupported logging level.")
    else:
        numeric_level = level

    logger = logging.getLogger("jarvis_x")
    logger.setLevel(numeric_level)
    logger.propagate = False

    for handler in list(logger.handlers):
        if getattr(handler, "_jarvis_x_managed", False):
            logger.removeHandler(handler)
            handler.close()

    handler = logging.StreamHandler(stream)
    handler.setLevel(numeric_level)
    handler.setFormatter(SecretRedactingFormatter(secrets=secrets))
    setattr(handler, "_jarvis_x_managed", True)
    logger.addHandler(handler)
    return logger
