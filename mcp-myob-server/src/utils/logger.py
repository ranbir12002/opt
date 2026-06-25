"""
Logging configuration for MCP MyOB Server.

Structured JSON logging for production, colored console for development.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from pythonjsonlogger import jsonlogger

from config import settings

# Monkey-patch StreamHandler for Windows broken-pipe safety
_original_stream_flush = logging.StreamHandler.flush
_original_stream_emit = logging.StreamHandler.emit


def _safe_flush(self):
    try:
        _original_stream_flush(self)
    except OSError:
        pass


def _safe_emit(self, record):
    try:
        _original_stream_emit(self, record)
    except OSError:
        pass


logging.StreamHandler.flush = _safe_flush
logging.StreamHandler.emit = _safe_emit


class ColoredFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
        "RESET": "\033[0m",
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.COLORS["RESET"])
        reset = self.COLORS["RESET"]
        record.levelname = f"{color}{record.levelname}{reset}"
        return super().format(record)


class JSONFormatter(jsonlogger.JsonFormatter):
    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record["timestamp"] = self.formatTime(record, self.datefmt)
        log_record["level"] = record.levelname
        log_record["logger"] = record.name


def setup_logging() -> logging.Logger:
    logger = logging.getLogger()
    logger.setLevel(settings.LOG_LEVEL)
    logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(settings.LOG_LEVEL)
    console_handler.setFormatter(
        ColoredFormatter(
            fmt="[%(levelname)s] %(asctime)s - %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(console_handler)

    # File handler (JSON)
    log_file = Path(settings.LOG_FILE)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(settings.LOG_LEVEL)
    file_handler.setFormatter(
        JSONFormatter(
            fmt="%(timestamp)s %(level)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
