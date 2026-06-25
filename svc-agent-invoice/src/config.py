# config.py
from __future__ import annotations
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parent

SOP_DOCX_PATH = str(_SRC_DIR / "sop" / "invoice_creation_sop.md")

MCP_BASE = "http://127.0.0.1:8000"
HTTP_TIMEOUT = 10
RETRIES = 3

# NEW: model + optional system routing
LLM_MODEL = "gpt-4.1"
