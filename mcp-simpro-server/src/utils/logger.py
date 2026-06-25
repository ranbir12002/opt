"""
Logging configuration for MCP Simpro Server.

Provides structured JSON logging for production with colored console output for development.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from pythonjsonlogger import jsonlogger

from config import settings


# ---------------------------------------------------------------------------
# Monkey-patch logging.StreamHandler to survive broken pipes / invalid
# handles on Windows.  When uvicorn spawns worker sub-processes the
# stdout/stderr handles inherited from the parent can become invalid,
# causing  OSError: [Errno 22] Invalid argument  on every flush().
# Because Python's logging internals (including handleError) also use
# StreamHandler, a subclass on the root logger alone is not enough —
# we need to patch the base class so *every* handler is safe.
# ---------------------------------------------------------------------------
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
    """
    Colored console formatter for better readability in development.
    """

    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
        'RESET': '\033[0m'        # Reset
    }

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with colors"""
        color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        reset = self.COLORS['RESET']

        # Format: [LEVEL] timestamp - message
        record.levelname = f"{color}{record.levelname}{reset}"
        return super().format(record)


class JSONFormatter(jsonlogger.JsonFormatter):
    """
    JSON formatter for structured logging in production.
    """

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any]
    ) -> None:
        """Add custom fields to JSON log record"""
        super().add_fields(log_record, record, message_dict)

        # Add standard fields
        log_record['timestamp'] = self.formatTime(record, self.datefmt)
        log_record['level'] = record.levelname
        log_record['logger'] = record.name

        # Add context if available
        if hasattr(record, 'context'):
            log_record['context'] = record.context


def setup_logging() -> logging.Logger:
    """
    Setup application logging.

    Returns:
        Root logger instance
    """
    # Get root logger
    logger = logging.getLogger()
    logger.setLevel(settings.LOG_LEVEL)

    # Remove existing handlers
    logger.handlers.clear()

    logging.getLogger("watchfiles").setLevel(logging.WARNING)

    # Console handler (colored for development)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(settings.LOG_LEVEL)

    console_format = ColoredFormatter(
        fmt='[%(levelname)s] %(asctime)s - %(name)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    # File handler (JSON for production)
    log_file = Path(settings.LOG_FILE)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(settings.LOG_LEVEL)

    json_format = JSONFormatter(
        fmt='%(timestamp)s %(level)s %(name)s %(message)s',
        datefmt='%Y-%m-%dT%H:%M:%S'
    )
    file_handler.setFormatter(json_format)
    logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for a specific module.

    Args:
        name: Logger name (usually __name__)

    Returns:
        Logger instance

    Example:
        >>> logger = get_logger(__name__)
        >>> logger.info("Hello world")
    """
    return logging.getLogger(name)
