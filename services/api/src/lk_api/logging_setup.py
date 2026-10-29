"""Structured JSON logging that never includes document text unless explicitly enabled."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

_SENSITIVE_KEYS = {"password", "secret", "token", "authorization", "api_key", "cookie",
                   "raw_text", "normalized_text", "text", "narration", "player_input"}
_STD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def __init__(self, *, include_document_text: bool = False) -> None:
        super().__init__()
        self.include_document_text = include_document_text

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STD or key.startswith("_"):
                continue
            if key.lower() in _SENSITIVE_KEYS and not (
                self.include_document_text and key in {"raw_text", "normalized_text", "text"}
            ):
                value = "[redacted]"
            payload[key] = value if isinstance(value, (int, float, bool, type(None))) else str(value)
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload)


def configure_logging(level: str = "INFO", *, include_document_text: bool = False) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(include_document_text=include_document_text))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
