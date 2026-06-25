# src/extractor_utils.py

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Optional


def setup_logging(service_name: str = "svc-extractor") -> None:
    """
    Simple structured-ish logging (stdout).
    Keep it minimal for v1. You can upgrade later.
    """
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format=f"%(asctime)s | {service_name} | %(levelname)s | %(message)s",
    )


def now_ms() -> int:
    return int(time.time() * 1000)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip().lower()
    if v in {"1", "true", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "no", "n", "off"}:
        return False
    return default


def to_jsonable(obj: Any) -> Any:
    """
    Convert dataclasses + nested dataclasses to pure JSON-serializable objects.
    """
    if is_dataclass(obj):
        return to_jsonable(asdict(obj))

    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple)):
        return [to_jsonable(x) for x in obj]

    # Basic JSON types pass through
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj

    # Fallback: stringify unknown types
    return str(obj)


def safe_json_dumps(payload: Any) -> str:
    return json.dumps(to_jsonable(payload), ensure_ascii=False)


class ServiceConfig:
    """
    Central config for extractor microservice.
    Set via env vars (works in Docker + local).
    """
    MAX_UPLOAD_MB: int = env_int("EXTRACTOR_MAX_UPLOAD_MB", 15)
    ENABLE_OCR_DEFAULT: bool = env_bool("EXTRACTOR_ENABLE_OCR_DEFAULT", True)
    PDF_MAX_OCR_PAGES: int = env_int("EXTRACTOR_PDF_MAX_OCR_PAGES", 10)
