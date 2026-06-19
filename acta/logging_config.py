"""Logging setup for ACTA."""

from __future__ import annotations

from contextvars import ContextVar, Token
import json
import logging
from typing import Any

_CONFIGURED = False
_request_id_ctx: ContextVar[str | None] = ContextVar("acta_request_id", default=None)
_user_id_ctx: ContextVar[str | None] = ContextVar("acta_user_id", default=None)
_ORIGINAL_FACTORY = logging.getLogRecordFactory()


def _record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    record = _ORIGINAL_FACTORY(*args, **kwargs)
    record.request_id = _request_id_ctx.get() or "-"
    record.user_id = _user_id_ctx.get() or "-"
    return record


class _JsonFormatter(logging.Formatter):
    _RESERVED = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "user_id": getattr(record, "user_id", "-"),
        }
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", log_json: bool = False) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    logging.setLogRecordFactory(_record_factory)
    handler = logging.StreamHandler()
    if log_json:
        handler.setFormatter(_JsonFormatter(datefmt="%Y-%m-%dT%H:%M:%S%z"))
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-7s | %(name)s | request_id=%(request_id)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
    root.addHandler(handler)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"acta.{name}")


def bind_request_context(request_id: str | None = None, user_id: str | None = None) -> tuple[Token, Token]:
    return _request_id_ctx.set(request_id), _user_id_ctx.set(user_id)


def update_user_context(user_id: str | None) -> Token:
    return _user_id_ctx.set(user_id)


def reset_request_context(tokens: tuple[Token, Token]) -> None:
    request_token, user_token = tokens
    _request_id_ctx.reset(request_token)
    _user_id_ctx.reset(user_token)
