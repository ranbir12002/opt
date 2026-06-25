# backend/api/chat.py
# [Updated: Agent-First Routing with File Support]

import re
import sys
import time
import asyncio
import httpx
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import os
import json
from typing import Any, Dict, List, Optional, Tuple
import logging
from tools.invoice_executor import (
    create_invoices_from_agent_result,
    update_invoices_from_agent_result,
    delete_invoices_from_agent_result,
    is_tool_available,
)
from tools.schedule_executor import execute_schedule_operations, is_tool_available as is_schedule_tool_available
from tools.workorder_executor import execute_workorder_operations
from utils.thinking_plan import ThinkingPlan
from utils.history_filter import filter_history
from utils.decision_journal import new_request_id, record_decision, record_trace, update_trace_outcome
from utils.intent_tool_alignment import check_intent_tool_alignment
from utils.context_manager import get_or_create_scratchpad, update_context_after_turn, build_enriched_history
from pathlib import Path
# Logging is configured centrally in main.py (console + file)
logger = logging.getLogger(__name__)
backend_path = Path(__file__).parent.parent
sys.path.insert(0, str(backend_path))
# Agent support
from agents.registry import AGENT_REGISTRY, is_agent, load_agent
from utils.llm import chat as llm_chat, transcribe_audio, chat_with_override
from auth.auth import get_current_user
from fastapi import Depends
from auth.database import (
    is_agent_enabled_for_org,
    get_monthly_usage,
    get_monthly_agent_usage,
    get_org_agent_plan,
    get_org_by_id,
    log_usage,
    is_operation_allowed_for_user,
    get_platform_llm_config,
)
from presenter_router import present, PresentRequest
from utils.post_filter import apply_post_filters, apply_department_filter, apply_post_execution_qualifiers
from utils.http_pool import get_mcp_pool, get_extractor_pool, get_health_pool
from utils.s3 import upload_file_bytes, list_session_files

load_dotenv()

router = APIRouter()


# Configuration
MCP_CLIENT_URL = os.getenv("MCP_CLIENT_URL", "http://localhost:3001")
EXTRACTOR_URL = os.getenv("EXTRACTOR_URL", "http://127.0.0.1:8010/extract")
EXTRACTOR_TIMEOUT_S = float(os.getenv("EXTRACTOR_TIMEOUT_S", "60"))

# Option A: Python Executor feature flag
# false = use Node.js mcp-client (current, stable)
# true  = use Python executor (new intelligence stack)
USE_PYTHON_EXECUTOR = os.getenv("USE_PYTHON_EXECUTOR", "false").lower() == "true"

MAX_FILES = 6
MAX_FILE_BYTES = 15_000_000

# Per-user session state (keyed by compound key (user_id, org_id) for tenant isolation)
_user_contexts: Dict[Tuple[int, int], Dict[str, Any]] = {}


def _get_user_context(user_id: int, org_id: Optional[int]) -> Dict[str, Any]:
    org_id_val = org_id if org_id is not None else 0
    key = (user_id, org_id_val)
    if key not in _user_contexts:
        _user_contexts[key] = {
            "last_message": None,
            "conversation_history": [],
            # ── Session Context Layer: cross-path data continuity ──
            "last_structured_data": None,   # Actual filtered data dict from last response
            "last_route": None,             # "mcp" | "schedule" | "workorder" | "invoice"
            "last_department": None,        # Department qualifier if applied (e.g., "Roofing")
            "last_tool_names": None,        # MCP tool names called (e.g., ["get_schedules"])
            "last_intent": None,            # Full intent_result dict from analyze_intent()
            "session_context_ts": None,     # time.time() when captured — enables 5-min TTL
        }
    return _user_contexts[key]

# Pending clarification sessions (session_id → context needed to resume)
_pending_sessions: Dict[str, Dict[str, Any]] = {}

# ── Session TTL: automatic cleanup of expired clarification sessions ──
_SESSION_TTL = 600              # 10 minutes — generous for form filling
_SESSION_CLEANUP_INTERVAL = 60  # Scan for expired sessions every 60s


async def _cleanup_expired_sessions():
    """Background task: evict expired pending sessions every 60s."""
    while True:
        await asyncio.sleep(_SESSION_CLEANUP_INTERVAL)
        now = time.time()
        expired = [
            sid for sid, s in _pending_sessions.items()
            if now - s.get("created_at", 0) > _SESSION_TTL
        ]
        for sid in expired:
            _pending_sessions.pop(sid, None)
        if expired:
            logger.info("🧹 Session cleanup: evicted %d expired sessions", len(expired))
        # Clean orphaned multi-action queue refs
        for uid_key, ctx in _user_contexts.items():
            maq = ctx.get("multi_action_queue")
            if not maq or not maq.get("pending_sids"):
                continue
            before = len(maq["pending_sids"])
            maq["pending_sids"] = [s for s in maq["pending_sids"] if s in _pending_sessions]
            if len(maq["pending_sids"]) < before:
                logger.info(
                    "🧹 Cleaned %d orphaned multi-action sids for user/org %s",
                    before - len(maq["pending_sids"]), str(uid_key),
                )
            if not maq["pending_sids"]:
                ctx.pop("multi_action_queue", None)
                ctx.pop("pending_clarification_sid", None)


def _session_expires_in(sid: str) -> int:
    """Seconds remaining before a pending session expires."""
    sess = _pending_sessions.get(sid)
    if not sess:
        return 0
    return max(0, int(_SESSION_TTL - (time.time() - sess.get("created_at", 0))))


# Cache of last failed operation per user/org — for instant retry without re-resolution
# (user_id, org_id) → { agent_name, agent_result, company_id, timestamp }
_last_failed_ops: Dict[Tuple[int, int], Dict[str, Any]] = {}

class ChatRequest(BaseModel):
    message: str

class ClarifyRequest(BaseModel):
    session_id: str
    clarifications: Dict[str, Dict[str, Any]]  # { "row_num": { "SectionID": 123, ... } }
    custom_entries: Optional[List[Dict[str, Any]]] = None  # [{row, field, value}] from "Other" input

    class Config:
        extra = "allow"  # frontend sends _summary, _agent, _contradiction

class ChatResponse(BaseModel):
    reply: str
    envelope: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    needs_clarification: Optional[bool] = None
    clarification_data: Optional[Dict[str, Any]] = None

class TranscribeResponse(BaseModel):
    text: str


# ============================================================================
# Document Type Detection
# ============================================================================

def _detect_document_type(filename: str, content_type: str, message: str) -> str:
    """Detect document type from context"""
    filename_lower = filename.lower()
    message_lower = message.lower()
    
    if any(word in message_lower for word in ['invoice', 'bill', 'payment']):
        return 'invoice_data'
    
    if any(word in message_lower for word in ['quote', 'quotation', 'estimate']):
        return 'quote_data'
    
    if any(word in message_lower for word in ['work order', 'wo ']):
        return 'work_order'
    
    if filename_lower.endswith(('.xlsx', '.xls', '.csv')):
        return 'tabular_data'
    
    return 'unknown'


# ============================================================================
# File Helpers
# ============================================================================

async def _normalize_uploads(files: Optional[List[UploadFile]], session_id: str) -> List[Dict[str, Any]]:
    """Normalize uploaded files and upload to S3 if configured"""
    if not files:
        return []
    
    seen = set()
    out: List[Dict[str, Any]] = []
    
    for f in files[:MAX_FILES]:
        raw = await f.read()
        if len(raw) > MAX_FILE_BYTES:
            logger.warning(f"File {f.filename} too large, skipping")
            continue
        
        sig = (f.filename or "", len(raw), f.content_type or "")
        if sig in seen:
            continue
        
        seen.add(sig)
        
        s3_data = {}
        if os.getenv("AWS_S3_BUCKET"):
            try:
                s3_data = upload_file_bytes(
                    session_id=session_id,
                    filename=f.filename or "upload.bin",
                    file_bytes=raw,
                    content_type=f.content_type or "application/octet-stream"
                )
            except Exception as s3_err:
                logger.error(f"S3 upload failed for {f.filename}: {s3_err}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"S3 upload failed: {str(s3_err)}")
        else:
            logger.warning("AWS_S3_BUCKET is not set. Skipping S3 upload.")
            
        out.append({
            "filename": f.filename or "upload.bin",
            "content_type": f.content_type or "application/octet-stream",
            "bytes": raw,
            **s3_data
        })
        logger.info(f"✅ File: {f.filename} ({len(raw)} bytes), S3: {s3_data}")
    
    return out


async def _call_extractor(
    attachments: List[Dict[str, Any]], 
    doc_type_hint: str = "unknown"
) -> Optional[Dict[str, Any]]:
    """Call extractor service"""
    if not attachments:
        return None

    a = attachments[0]
    file_tuple = (
        a.get("filename") or "upload.bin",
        a.get("bytes") or b"",
        a.get("content_type") or "application/octet-stream",
    )

    logger.info(f"📤 Extractor: {a.get('filename')} (type: {doc_type_hint})")
    
    try:
        client = get_extractor_pool()
        r = await client.post(
            EXTRACTOR_URL,
            files={"file": file_tuple},
            data={"doc_type_hint": doc_type_hint},
        )
        r.raise_for_status()
        result = r.json()

        logger.info(f"✅ Extracted: type={result.get('detected_type')}, "
                   f"useful={result.get('is_useful')}, "
                   f"tables={len(result.get('tables', []))}")

        return result
    except Exception as e:
        logger.error(f"❌ Extractor failed: {e}")
        return None


def _quick_text_snippet(attachments: List[Dict[str, Any]], max_chars: int = 120_000) -> Optional[str]:
    """Extract text from attachments"""
    if not attachments:
        return None

    chunks: List[str] = []
    total = 0

    for att in attachments:
        ct = (att.get("content_type") or "").lower()
        name = (att.get("filename") or "").lower()
        b = att.get("bytes") or b""

        # Text files
        try:
            is_texty = any(x in ct for x in ["text/", "json"]) or name.endswith((".txt", ".json", ".csv"))
            if is_texty:
                s = b.decode("utf-8", errors="ignore")
                chunks.append(s)
                total += len(s)
                if total >= max_chars:
                    break
                continue
        except Exception:
            pass

        # Excel files
        try:
            is_excel = "spreadsheetml" in ct or name.endswith((".xlsx", ".xls"))
            if is_excel:
                import io
                try:
                    import openpyxl
                    import csv as _csv
                    wb = openpyxl.load_workbook(io.BytesIO(b), read_only=True, data_only=True)
                    buf = io.StringIO()
                    writer = _csv.writer(buf, quoting=_csv.QUOTE_MINIMAL)
                    line_count = 0
                    for sheet in wb.worksheets:
                        for row in sheet.iter_rows(values_only=True):
                            writer.writerow([str(c or "") for c in row])
                            line_count += 1
                    s = buf.getvalue()
                    lines = [s]  # keep compat with len(lines) log below
                    chunks.append(s)
                    total += len(s)
                    logger.info(f"📊 Excel: {line_count} rows")
                    if total >= max_chars:
                        break
                except ImportError:
                    import xlrd
                    wb = xlrd.open_workbook(file_contents=b)
                    buf2 = io.StringIO()
                    writer2 = _csv.writer(buf2, quoting=_csv.QUOTE_MINIMAL)
                    for sh in wb.sheets():
                        for row_idx in range(sh.nrows):
                            row = sh.row_values(row_idx)
                            writer2.writerow([str(c) for c in row])
                    s = buf2.getvalue()
                    chunks.append(s)
                    total += len(s)
                    if total >= max_chars:
                        break
        except Exception as e:
            logger.error(f"❌ Excel error: {e}")
            pass

        if total >= max_chars:
            break

    combined = "\n---\n".join(chunks)
    return combined[:max_chars] if combined else None


def _normalize_extracted_tables(extracted: Dict[str, Any]) -> None:
    """
    Ensure every table in extractor output has an explicit 'headers' key.

    The extractor service returns tables as:
        {rows: [["Header1","Header2",...], ["val1","val2",...], ...]}
    But the schedule agent expects:
        {headers: ["Header1","Header2",...], rows: [["val1","val2",...], ...]}

    This function splits rows[0] into headers when the key is missing.
    Idempotent — safe to call multiple times (skips tables that already have headers).
    """
    if not extracted or not extracted.get("tables"):
        return

    for table in extracted["tables"]:
        if table.get("headers"):
            # Already has explicit headers (chat path, bulk action, or already normalized)
            continue

        rows = table.get("rows", [])
        if not rows:
            continue

        # First row is column headers, rest is data
        table["headers"] = rows[0]
        table["rows"] = rows[1:]

    logger.info(f"📋 Normalized extracted tables: {[t.get('headers', []) for t in extracted.get('tables', [])]}")


# ============================================================================
# Agent System
# ============================================================================

def _get_agent_runner(agent_name: str):
    """Dynamically load and return an agent runner function, or None."""
    fn = load_agent(agent_name)
    if fn is None:
        return None

    def runner(user_text, extracted=None, raw_attachments=None,
               any_uploaded_text=None, hints=None, conversation_history=None,
               llm_chat_fn=None):
        return fn(
            user_text=user_text,
            extracted=extracted,
            raw_attachments=raw_attachments,
            any_uploaded_text=any_uploaded_text,
            registry_entry=AGENT_REGISTRY[agent_name],
            llm_chat=llm_chat_fn or llm_chat,
            hints=hints,
            conversation_history=conversation_history,
        )
    return runner


class _TokenAccumulator:
    """Wraps an llm_fn to accumulate token usage across multiple calls in a request."""
    def __init__(self, llm_fn=None):
        self.llm_fn = llm_fn or llm_chat
        self.total_input = 0
        self.total_output = 0
        self.model = ""

    def tracked_chat(self, messages, response_format=None, temperature=0.0, **kwargs):
        from utils.llm import LLMResult
        result = self.llm_fn(messages, response_format=response_format,
                             temperature=temperature, return_usage=True, **kwargs)
        if isinstance(result, LLMResult):
            self.total_input += result.input_tokens
            self.total_output += result.output_tokens
            self.model = result.model
            return result.content
        return result


def _get_org_simpro_credentials(org_id: Optional[int]) -> dict:
    """
    Load Simpro credentials for the given org from the DB.
    Returns dict with keys: simpro_token, simpro_url, simpro_company_id.
    All values are None if org_id is absent or the org has no credentials set.
    """
    if not org_id:
        return {"simpro_token": None, "simpro_url": None, "simpro_company_id": None}
    org = get_org_by_id(org_id)
    if not org:
        return {"simpro_token": None, "simpro_url": None, "simpro_company_id": None}
    token = org.get("simpro_access_token") or None
    url = org.get("simpro_api_url") or None
    company_id = org.get("simpro_company_id") or None
    if company_id is not None:
        try:
            company_id = int(company_id)
        except (TypeError, ValueError):
            company_id = None
    return {"simpro_token": token, "simpro_url": url, "simpro_company_id": company_id}


def _get_org_llm_config(org_id: Optional[int]) -> Dict[str, Any]:
    """
    Resolve effective LLM config for an org. (Phase 6)
    Resolution order:
      - use_platform_llm=True (default) → inherit platform_settings global keys
      - use_platform_llm=False → use org-specific llm_* columns (fall back to platform for any missing field)
    Returns {"primary": {provider, model, api_key}, "complex": {provider, model, api_key}}
    """
    platform = get_platform_llm_config()
    if not org_id:
        return platform
    org = get_org_by_id(org_id)
    if not org:
        return platform
    # use_platform_llm defaults to 1 (True) for all existing orgs
    if org.get("use_platform_llm", 1):
        return platform
    # Org-specific keys — fall back to platform fields for any missing value
    primary = {
        "provider": org.get("llm_provider") or platform["primary"]["provider"],
        "model":    org.get("llm_model")    or platform["primary"]["model"],
        "api_key":  org.get("llm_api_key")  or platform["primary"]["api_key"],
    }
    complex_slot = {
        "provider": org.get("llm_complex_provider") or platform["complex"]["provider"],
        "model":    org.get("llm_complex_model")    or platform["complex"]["model"],
        "api_key":  org.get("llm_complex_api_key")  or platform["complex"]["api_key"],
    }
    return {"primary": primary, "complex": complex_slot}


def _check_token_budget(org_id: Optional[int]) -> Optional[str]:
    """Check if the org has exceeded its monthly token budget. Returns error message or None."""
    if not org_id:
        return None

    from datetime import datetime, timedelta, timezone
    org = get_org_by_id(org_id)
    if not org:
        return None

    # Use IST (+05:30) to match how get_monthly_usage() timestamps records
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    usage = get_monthly_usage(org_id, now_ist.year, now_ist.month)
    total_used = usage["total_input_tokens"] + usage["total_output_tokens"]
    limit = org["monthly_token_limit"]

    if total_used >= limit:
        return (
            f"Your organization has reached its monthly token limit "
            f"({total_used:,}/{limit:,} tokens). "
            f"Please contact your administrator to upgrade your plan."
        )
    return None


def _check_agent_token_budget(org_id: Optional[int], agent_name: str) -> Optional[str]:
    """
    Check per-agent monthly token limit from org_agent_plans.monthly_token_limit.
    Returns error message if exceeded, None if ok or no limit set.
    """
    if not org_id:
        return None

    from datetime import datetime, timedelta, timezone
    plan = get_org_agent_plan(org_id, agent_name)
    if not plan or plan.get("monthly_token_limit") is None:
        return None  # No per-agent limit → pass through

    # Use IST to match how usage records are timestamped
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    usage = get_monthly_agent_usage(org_id, agent_name, now_ist.year, now_ist.month)
    total_used = usage["total_input_tokens"] + usage["total_output_tokens"]
    limit = plan["monthly_token_limit"]

    if total_used >= limit:
        return (
            f"The {agent_name} agent has reached its monthly token limit "
            f"({total_used:,}/{limit:,} tokens). "
            f"Please contact your administrator to upgrade."
        )
    return None


def _sse_event(event_type: str, data: dict) -> str:
    """Format a single SSE event. Wire format: event: <type>\\ndata: <json>\\n\\n"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


_STREAM_WORD_DELAY = 0.015  # ~67 words/sec, halved for long responses


async def _stream_text_words(text: str):
    """Yield SSE token events word-by-word from a complete string."""
    words = text.split(" ")
    delay = _STREAM_WORD_DELAY if len(words) <= 80 else _STREAM_WORD_DELAY / 2
    for i, word in enumerate(words):
        chunk = word if i == len(words) - 1 else word + " "
        yield _sse_event("token", {"text": chunk})
        await asyncio.sleep(delay)


async def _stream_cancel_response(message: str):
    """Yield SSE events for a cancellation/redirect message."""
    async for evt in _stream_text_words(message):
        yield evt
    yield _sse_event("done", {})


def _detect_agent_from_file_structure(
    extracted: Dict,
    llm_chat=None,
    user_text: str = "",
) -> Optional[str]:
    """
    Analyze file structure to determine which agent to use.
    Called when user doesn't provide explicit keywords.

    Two-phase detection:
    1. Fast keyword matching against known header synonyms (no LLM cost)
    2. LLM fallback when keyword matching is inconclusive — handles any file format
    """
    if not extracted or not extracted.get("tables"):
        return None

    import re as _re

    headers = extracted["tables"][0].get("headers", [])
    headers_lower = [h.lower().strip() for h in headers]

    # Tokenize headers by splitting on hyphens, underscores, and spaces so that
    # compound names like "Date-Schedule", "Site-Technician", "Time-Start" are
    # matched against single-word indicators (date, technician, start, etc.)
    all_header_tokens: set = set()
    for h in headers_lower:
        all_header_tokens.update(_re.split(r'[-_\s]+', h))
    # Keep full lowercased headers too (for multi-word indicators like "job number")
    all_header_tokens.update(headers_lower)

    # Schedule agent indicators — canonical names + common synonyms
    schedule_indicators = {
        "operation", "op", "action", "type",
        "jobid", "job id", "job", "job number",
        "staffname", "staff name", "staff", "employee", "worker", "technician",
        "blocks", "hours", "hrs", "duration",
        "date", "schedule date", "day",
        "sectionname", "section name", "section",
        "starttime", "start time", "start",
        "costcentrename", "cost centre", "cost center", "cc",
        "allocatedto", "allocated",
    }
    match_count = len(schedule_indicators.intersection(all_header_tokens))
    if match_count >= 3:
        logger.info(f"📋 Detected schedule data from headers ({match_count} matches)")
        return "schedule"

    # Work order agent indicators (re-uploaded WO Excel)
    workorder_indicators = {
        "contractorname", "contractor name", "contractorid", "contractor id",
        "include", "unitcost", "unit cost", "itemname", "item name",
        "contractor", "unit", "item",
    }
    if "include" in all_header_tokens and len(workorder_indicators.intersection(all_header_tokens)) >= 3:
        logger.info("📋 Detected work order data from headers")
        return "workorder"

    # Invoice agent indicators (existing)
    invoice_indicators = {"customerid", "invoicedate", "amount", "jobid", "customer", "invoice"}
    if len(invoice_indicators.intersection(all_header_tokens)) >= 3:
        logger.info("📋 Detected invoice data from headers")
        return "invoice"

    # ── LLM fallback: keyword matching was inconclusive, ask LLM to classify ──
    # Lightweight call (~250 tokens) using headers + sample rows + user message.
    if llm_chat and headers:
        try:
            import json as _json
            sample_rows = [
                dict(zip(headers, row))
                for row in extracted["tables"][0].get("rows", [])[:2]
            ]
            prompt = [
                {
                    "role": "system",
                    "content": (
                        "Classify which back-office agent should handle this uploaded file.\n"
                        "Agents:\n"
                        "- 'schedule': employee scheduling, rostering, shift or time data\n"
                        "- 'invoice': billing, invoices, payment data\n"
                        "- 'workorder': contractor jobs, work orders, materials\n"
                        "- 'none': cannot determine or not relevant\n"
                        "Respond ONLY with valid JSON (no markdown): "
                        '{"agent": "schedule"|"invoice"|"workorder"|"none", "confidence": 0.0-1.0}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Headers: {headers}\n"
                        f"Sample rows: {sample_rows}\n"
                        f"User message: {user_text[:200]}"
                    ),
                },
            ]
            raw = llm_chat(prompt, temperature=0.0, sanitize=False)
            # Strip markdown code fences that LLMs sometimes wrap JSON in
            cleaned = _re.sub(r'^```(?:json)?\s*|\s*```\s*$', '', raw.strip(), flags=_re.MULTILINE).strip()
            if not cleaned:
                raise ValueError("LLM returned empty response")
            result = _json.loads(cleaned)
            agent = result.get("agent")
            confidence = float(result.get("confidence", 0.0))
            if agent in ("schedule", "invoice", "workorder") and confidence >= 0.6:
                logger.info(f"🤖 LLM file routing: {agent} (confidence={confidence:.2f})")
                return agent
        except Exception as e:
            logger.warning(f"LLM file routing failed: {e}")

    return None


def _summarize_multi_action_result(result: Dict[str, Any]) -> str:
    """Build a concise summary of a multi-action orchestrated result for conversation history."""
    multi_results = result.get("multi_action_results", [])
    summary = result.get("summary", {})
    parts = []
    for mr in multi_results:
        status = "OK" if mr.get("success") else "FAILED"
        parts.append(f"{mr.get('description', 'Action')}: {status}")
    total = summary.get("total", len(parts))
    succeeded = summary.get("succeeded", 0)
    return f"[multi-action: {succeeded}/{total} succeeded — {'; '.join(parts)}]"


def _build_multi_action_partial_envelope(
    mac: Dict[str, Any],
    question: str = "",
    llm_fn=None,
) -> Optional[Dict[str, Any]]:
    """
    Build an envelope from already-completed sub-request results in a
    multi-action context. Used to show completed results alongside a
    clarification form as a single merged table.

    Returns None if there are no completed results to show.
    """
    completed_items = []
    for cr in mac.get("completed_results", []):
        rd = cr.get("result_data", {})
        for item in rd.get("results", []):
            completed_items.append(item)
    for fr in mac.get("failed_results", []):
        rd = fr.get("result_data", {})
        for item in rd.get("results", []):
            completed_items.append(item)

    if not completed_items:
        return None

    combined_data = {
        "success": True,
        "results": completed_items,
        "summary": {
            "total": len(completed_items),
            "succeeded": sum(1 for it in completed_items if it.get("Status") != "Failed"),
            "failed": sum(1 for it in completed_items if it.get("Status") == "Failed"),
        },
    }
    try:
        return _format_with_presenter(data=combined_data, question=question, llm_fn=llm_fn)
    except Exception as e:
        logger.warning(f"⚠️  Failed to build partial envelope: {e}")
        return None


def _check_multi_action_chain(
    user_id: int,
    org_id: Optional[int],
    current_session_id: str,
    current_success: bool = True,
    current_result_data: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    After a clarification resolves, check if it was part of a multi-action
    sequence and return the next clarification or a final summary.

    Returns:
        None if not part of a multi-action sequence.
        {"type": "next_clarification", ...} if more clarifications to chain.
        {"type": "final_summary", ...} if all done.
    """
    user_context = _get_user_context(user_id, org_id)
    maq = user_context.get("multi_action_queue")
    if not maq:
        return None

    # Record current sub-request's result
    current_meta = maq.get("pending_meta", {}).pop(current_session_id, {})
    # If no meta found, use the tracked current_sub_index
    sub_index = current_meta.get("sub_index", maq.pop("current_sub_index", None))
    description = current_meta.get("description", maq.pop("current_description", ""))

    if current_success:
        maq["completed_results"].append({
            "sub_index": sub_index,
            "description": description,
            "success": True,
            "source": "clarification",
            "result_data": current_result_data,
        })
    else:
        maq["failed_results"].append({
            "sub_index": sub_index,
            "description": description,
            "success": False,
            "error": (current_result_data or {}).get("message", "failed"),
            "source": "clarification",
            "result_data": current_result_data,
        })

    # Try to find next valid pending clarification
    while maq["pending_sids"]:
        next_sid = maq["pending_sids"].pop(0)
        next_meta = maq["pending_meta"].pop(next_sid, {})
        next_session = _pending_sessions.get(next_sid)

        if next_session:
            user_context["pending_clarification_sid"] = next_sid
            # Track current for the next chain round
            maq["current_sub_index"] = next_meta.get("sub_index")
            maq["current_description"] = next_meta.get("description", "")
            total = maq["total_sub_requests"]
            done = len(maq["completed_results"]) + len(maq["failed_results"])
            last_agent_result = next_session.get("_last_agent_result", {})

            logger.info(
                f"🔗 Multi-action chain: presenting clarification for "
                f"action {next_meta.get('sub_index', '?')+1} ({done}/{total} done)"
            )

            return {
                "type": "next_clarification",
                "next_sid": next_sid,
                "next_meta": next_meta,
                "next_session": next_session,
                "clarification_data": {
                    "session_id": next_sid,
                    "agent": next_meta.get("agent", "schedule"),
                    "clarifications": last_agent_result.get("clarifications", []),
                    "clarification_count": len(last_agent_result.get("clarifications", [])),
                    "multi_action_context": {
                        "sub_index": next_meta.get("sub_index"),
                        "description": next_meta.get("description"),
                        "total_sub_requests": total,
                        "progress": f"{done}/{total}",
                    },
                },
                "progress_message": (
                    f"Action {done} of {total} done. "
                    f"Next: {next_meta.get('description', 'action')} needs clarification."
                ),
            }
        else:
            # Session expired/missing — record as failed
            logger.warning(f"⚠️  Multi-action chain: session {next_sid} expired, skipping")
            maq["failed_results"].append({
                "sub_index": next_meta.get("sub_index"),
                "description": next_meta.get("description", ""),
                "success": False,
                "error": "Session expired",
                "source": "clarification",
            })

    # All done — build final summary
    logger.info("✅ Multi-action chain: all clarifications resolved, building final summary")
    summary = _build_multi_action_final_summary(maq)
    user_context.pop("multi_action_queue", None)
    user_context.pop("pending_clarification_sid", None)
    return {
        "type": "final_summary",
        "summary": summary,
    }


def _build_multi_action_final_summary(maq: Dict[str, Any]) -> Dict[str, Any]:
    """Build a merged summary after all multi-action sub-requests are resolved."""
    total = maq.get("total_sub_requests", 0)
    completed = maq.get("completed_results", [])
    failed = maq.get("failed_results", [])
    succeeded = sum(1 for r in completed if r.get("success"))
    failed_count = len(failed) + sum(1 for r in completed if not r.get("success"))
    descs = maq.get("sub_descriptions", {})

    parts = []
    if succeeded == total:
        parts.append(f"All {total} operations completed successfully.")
    elif succeeded > 0:
        parts.append(f"{succeeded} of {total} operations completed. {failed_count} failed.")
    else:
        parts.append(f"All {total} operations failed.")

    parts.append("")
    all_results = sorted(completed + failed, key=lambda x: x.get("sub_index", 0) if x.get("sub_index") is not None else 999)
    for r in all_results:
        idx = r.get("sub_index")
        desc = r.get("description") or descs.get(idx, f"Action {idx}")
        status = "completed" if r.get("success") else "FAILED"
        display_idx = idx + 1 if isinstance(idx, int) else "?"
        line = f"  {display_idx}. {desc} -- {status}"
        if not r.get("success") and r.get("error"):
            line += f"\n     Error: {r['error']}"
        parts.append(line)

    # Collect all result items for merged envelope
    all_items = []
    for r in sorted(completed + failed, key=lambda x: x.get("sub_index", 0) if x.get("sub_index") is not None else 999):
        rd = r.get("result_data") or {}
        items = rd.get("results", [])
        all_items.extend(items)

    return {
        "success": failed_count == 0,
        "message": "\n".join(parts),
        "is_multi_action": True,
        "results": all_items if all_items else None,
        "summary": {
            "total": total,
            "succeeded": succeeded,
            "failed": failed_count,
        },
    }


async def _emit_multi_action_chain(
    current_user: dict,
    session_id: str,
    success: bool,
    result_data: Optional[Dict[str, Any]],
    plan: Any,
):
    """
    Async generator that checks the multi-action queue and yields SSE events
    for the next clarification or the final summary.

    Usage in clarify SSE generators:
        _chain_events = [evt async for evt in _emit_multi_action_chain(...)]
        if _chain_events:
            for evt in _chain_events:
                yield evt
            return

    If nothing is yielded, the caller should proceed with its normal done path.
    """
    user_id = current_user.get("id", 0)
    org_id = current_user.get("org_id")
    _llm_fn = _make_org_llm_fn(org_id)
    chain = _check_multi_action_chain(user_id, org_id, session_id, success, result_data)
    if not chain:
        return  # Not part of a multi-action — caller proceeds normally

    if chain["type"] == "next_clarification":
        # Build merged envelope from all completed results so far
        next_clar_data = chain["clarification_data"]
        user_context = _get_user_context(user_id, org_id)
        maq = user_context.get("multi_action_queue", {})
        partial_envelope = _build_multi_action_partial_envelope(
            {"completed_results": maq.get("completed_results", []),
             "failed_results": maq.get("failed_results", [])},
            question="",
            llm_fn=_llm_fn,
        )
        yield _sse_event("result", {
            "reply": chain["progress_message"],
            "needs_clarification": True,
            "clarification_data": next_clar_data,
            "envelope": partial_envelope,
        })
        yield _sse_event("done", {"plan": plan.snapshot})

    elif chain["type"] == "final_summary":
        summary = chain["summary"]
        # Build merged envelope from all completed results
        if summary.get("results"):
            try:
                envelope = _format_with_presenter(data=summary, question="", llm_fn=_llm_fn)
                reply_text = envelope.get("summary", summary["message"])
                async for evt in _stream_text_words(reply_text):
                    yield evt
                yield _sse_event("envelope", {"envelope": envelope})
            except Exception as e:
                logger.warning(f"⚠️  Failed to build final multi-action envelope: {e}")
                async for evt in _stream_text_words(summary["message"]):
                    yield evt
        else:
            async for evt in _stream_text_words(summary["message"]):
                yield evt
        yield _sse_event("done", {"plan": plan.snapshot})


def _summarize_agent_result(agent_name: str, result: Dict[str, Any], user_text: str = "") -> str:
    """
    Build a concise text summary of an agent result for conversation history.
    Keeps tokens low while giving enough context for follow-up resolution.
    Includes both human-readable names AND IDs so LLM can resolve references.
    """
    # ── Agent-specific summaries (run BEFORE generic failure check so we
    #    capture IDs, action type, and target details even on failure) ──

    if agent_name == "workorder":
        parts = []
        # Capture the action and target IDs from results
        results_list = result.get("results", [])
        summary = result.get("summary", {})
        message = result.get("message", "")

        # Identify the operation type from results
        for r in results_list:
            status = r.get("status", "")
            cj_id = r.get("contractor_job_id", "")
            contractor = r.get("contractor_name", "")
            error = r.get("error", "")
            label = f"CJ {cj_id}" if cj_id else ""
            if contractor:
                label += f" ({contractor})" if label else contractor
            if status == "failed":
                parts.append(f"FAILED {label}: {error}" if error else f"FAILED {label}")
            elif status:
                parts.append(f"{status.upper()} {label}")

        # Also capture delete payloads from agent pre-execution data
        for d in result.get("contractor_job_deletes", []):
            cj_id = d.get("contractor_job_id", "")
            contractor = d.get("contractor_name", "")
            parts.append(f"DELETE CJ {cj_id} ({contractor})")

        if not parts and message:
            parts.append(message)
        if not parts and isinstance(summary, dict):
            parts.append(summary.get("message", str(summary)))
        elif not parts and summary:
            parts.append(str(summary))

        success_str = "succeeded" if result.get("success") else "FAILED"
        action_hint = ""
        if any("DELETE" in p or "delete" in p.lower() for p in parts):
            action_hint = "action=delete, "
        elif any("CREATE" in p or "create" in p.lower() for p in parts):
            action_hint = "action=create, "
        elif any("UPDATE" in p or "update" in p.lower() for p in parts):
            action_hint = "action=update, "

        user_hint = f", user_request=\"{user_text[:80]}\"" if user_text else ""
        return f"[workorder agent {success_str}: {action_hint}{'; '.join(parts)}{user_hint}]"

    # ── Schedule agent (handles both success and failure) ──

    if agent_name == "schedule":
        # If needs_clarification, include parsed data so follow-up LLM
        # parser can extract fields from conversation history
        if result.get("needs_clarification"):
            original = result.get("original_extracted", {})
            clarifications = result.get("clarifications", [])
            fields_needing = [c.get("field", "?") for c in clarifications]
            parsed_summary = ""
            if isinstance(original, dict) and "tables" in original:
                tables = original.get("tables", [])
                if tables:
                    headers = tables[0].get("headers", [])
                    rows = tables[0].get("rows", [])
                    if rows:
                        pairs = [f"{h}={v}" for h, v in zip(headers, rows[0]) if v and str(v).strip()]
                        parsed_summary = ", ".join(pairs)
            return (
                f"[schedule agent NEEDS CLARIFICATION: "
                f"fields_to_clarify={fields_needing}, "
                f"already_parsed=[{parsed_summary}], "
                f"user_request=\"{user_text[:80]}\"]"
            )

        agent_output = result.get("agent_output", {})
        schedules = (agent_output.get("schedules") or result.get("schedules", []))

        # For failed executions, extract schedule data from failed items
        failed_items = result.get("failed", [])
        if not schedules and failed_items:
            schedules = [f.get("schedule", {}) for f in failed_items if f.get("schedule")]

        if not schedules:
            if not result.get("success"):
                error = result.get("error", "") or result.get("message", "unknown error")
                # Include parsed field data so follow-up parser can inherit values
                parsed_summary = ""
                original = result.get("original_extracted", {})
                if isinstance(original, dict) and "tables" in original:
                    tables = original.get("tables", [])
                    if tables:
                        headers = tables[0].get("headers", [])
                        rows = tables[0].get("rows", [])
                        if rows:
                            pairs = [f"{h}={v}" for h, v in zip(headers, rows[0]) if v and str(v).strip()]
                            parsed_summary = ", ".join(pairs)
                if parsed_summary:
                    return (
                        f"[schedule agent FAILED: {error}, "
                        f"already_parsed=[{parsed_summary}], "
                        f"user_request=\"{user_text[:80]}\"]"
                    )
                return f"[schedule agent FAILED: {error}, user_request=\"{user_text[:80]}\"]"
            return f"[{agent_name} agent completed but produced no schedules]"

        success = result.get("success", False)
        # Build per-schedule error lookup from failed items
        failed_errors = {}
        for f in failed_items:
            sched = f.get("schedule", {})
            key = (sched.get("staff_id"), sched.get("cost_centre_id"), sched.get("date"))
            failed_errors[key] = f.get("error", "")

        parts = []
        for s in schedules:
            key = (s.get("staff_id"), s.get("cost_centre_id"), s.get("date"))
            is_failed = key in failed_errors
            status_prefix = "FAILED" if is_failed else "COMPLETED"
            op = s.get("operation", "CREATE")
            staff_id = s.get("staff_id", "?")
            staff_name = s.get("staff_name", "")
            job_id = s.get("job_id", s.get("quote_id", "?"))
            job_name = s.get("job_name", "")
            date = s.get("date", "?")
            blocks = s.get("blocks", "?")
            start_time = s.get("start_time", "")
            schedule_id = s.get("schedule_id", "")
            cost_centre_id = s.get("cost_centre_id", "")
            cost_centre_name = s.get("cost_centre_name", "")
            section_id = s.get("section_id", "")
            section_name = s.get("section_name", "")
            desc = (f"{status_prefix} {op} schedule: staff_name={staff_name or '?'}, "
                    f"staff_id={staff_id}, job_id={job_id}")
            if job_name:
                desc += f", job_name={job_name}"
            desc += f", section_id={section_id}"
            if section_name:
                desc += f", section_name={section_name}"
            desc += f", cost_centre_id={cost_centre_id}"
            if cost_centre_name:
                desc += f", cost_centre_name={cost_centre_name}"
            desc += f", date={date}, blocks={blocks}"
            if start_time:
                desc += f", start_time={start_time}"
            if schedule_id:
                desc += f", schedule_id={schedule_id}"
            if is_failed and failed_errors[key]:
                desc += f", error={_extract_simpro_error(failed_errors[key])}"
            parts.append(desc)
        return "; ".join(parts)

    # ── Invoice agent (handles both success and failure) ──

    if agent_name == "invoice":
        success = result.get("success", False)
        status_prefix = "COMPLETED" if success else "FAILED"
        summary = result.get("summary", {})
        created = result.get("created", [])
        # Build a richer summary with entity details for follow-up resolution
        if created:
            parts = []
            for inv in created:
                inv_id = inv.get("invoice_id", inv.get("ID", "?"))
                job_id = inv.get("job_id", "?")
                job_name = inv.get("job_name", "")
                inv_type = inv.get("type", inv.get("Type", ""))
                desc = f"{status_prefix} CREATE invoice: invoice_id={inv_id}, job_id={job_id}"
                if job_name:
                    desc += f", job_name={job_name}"
                if inv_type:
                    desc += f", type={inv_type}"
                # Include cost centre info if available
                cost_centres = inv.get("cost_centres", [])
                if cost_centres:
                    cc_strs = [f"{cc.get('id', '?')} ({cc.get('name', '')})" for cc in cost_centres[:5]]
                    desc += f", cost_centres=[{', '.join(cc_strs)}]"
                parts.append(desc)
            return "; ".join(parts)
        if not success:
            error = result.get("error", "") or result.get("message", "unknown error")
            return f"[invoice agent FAILED: {error}, user_request=\"{user_text[:80]}\"]"
        if summary:
            return f"{status_prefix} Invoice agent: {json.dumps(summary, default=str)}"
        return f"[{status_prefix} {agent_name} agent]"

    # ── Generic failure check (for agents without specific handlers above) ──

    if not result.get("success"):
        error = result.get("error", "")
        message = result.get("message", "")
        summary_info = result.get("summary", {})
        summary_msg = summary_info.get("message", "") if isinstance(summary_info, dict) else str(summary_info)
        detail = error or message or summary_msg or "unknown error"
        return f"[{agent_name} agent FAILED: {detail}, user_request=\"{user_text[:80]}\"]"

    # Generic fallback for future agents
    summary = result.get("summary", result.get("message", ""))
    if summary:
        return f"[{agent_name} agent: {summary}]"
    return f"[{agent_name} agent completed successfully]"


def _build_execution_response(
    agent_name: str,
    execution_result: Dict[str, Any],
    agent_result: Dict[str, Any],
    user_id: Optional[int] = None,
    org_id: Optional[int] = None,
    company_id: int = 2,
) -> Dict[str, Any]:
    """Build frontend response from executor result. Used by both normal and retry paths.
    Also manages the _last_failed_ops cache (store on failure, clear on success)."""

    # Cache or clear failure data
    if user_id is not None:
        key = (user_id, org_id if org_id is not None else 0)
        if execution_result.get("failed"):
            _last_failed_ops[key] = {
                "agent_name": agent_name,
                "agent_result": agent_result,
                "company_id": company_id,
                "timestamp": time.time(),
            }
            logger.info(f"💾 Cached failed {agent_name} operation for user/org {key} (for retry)")
        else:
            if _last_failed_ops.pop(key, None):
                logger.info(f"🧹 Cleared failure cache for user/org {key} (operation succeeded)")

    if agent_name == "schedule":
        operation = agent_result.get("schedules", [{}])[0].get("operation", "CREATE").lower() + "d"
        all_results = []
        for item in execution_result.get(operation, []):
            sched = item.get("schedule", {})
            all_results.append({
                "Status": operation.capitalize(),
                "Staff": sched.get("staff_name", f"Staff {sched.get('staff_id', '?')}"),
                "Job ID": sched.get("job_id", sched.get("quote_id", "")),
                "Date": sched.get("date", ""),
                "Blocks": sched.get("blocks", ""),
                "Start Time": sched.get("start_time", ""),
                "Cost Centre": sched.get("cost_centre_name", sched.get("cost_centre_id", "")),
                "Section": sched.get("section_name", sched.get("section_id", "")),
            })
        for item in execution_result.get("failed", []):
            sched = item.get("schedule", {})
            all_results.append({
                "Status": "Failed",
                "Staff": sched.get("staff_name", f"Staff {sched.get('staff_id', '?')}"),
                "Job ID": sched.get("job_id", sched.get("quote_id", "")),
                "Date": sched.get("date", ""),
                "Blocks": sched.get("blocks", ""),
                "Start Time": sched.get("start_time", ""),
                "Error": _extract_simpro_error(item.get("error", "")),
            })
        # Include resolution errors from partial execution (rows that failed
        # entity resolution but other rows proceeded successfully).
        for err in agent_result.get("errors", []):
            ctx = err.get("row_context", {})
            all_results.append({
                "Status": "Failed",
                "Staff": ctx.get("staff", ""),
                "Job ID": ctx.get("job", ""),
                "Date": ctx.get("date", ""),
                "Blocks": "",
                "Start Time": "",
                "Error": err.get("friendly", err.get("error", "Resolution failed")),
            })
        return {
            "success": execution_result.get("success"),
            "summary": execution_result.get("summary"),
            "results": all_results,
            "failed": execution_result.get("failed", []),
        }

    elif agent_name == "workorder":
        all_results = []
        for cj in execution_result.get("created", []):
            all_results.append(cj)
        for cj in execution_result.get("updated", []):
            all_results.append(cj)
        for cj in execution_result.get("deleted", []):
            all_results.append(cj)
        for cj in execution_result.get("failed", []):
            cj.setdefault("status", "failed")
            # Surface user-friendly error from crossroads error_recovery
            if cj.get("friendly"):
                cj["error_message"] = cj.pop("friendly")
            all_results.append(cj)
        return {
            "success": execution_result.get("success"),
            "summary": execution_result.get("summary"),
            "results": all_results,
            "failed": execution_result.get("failed", []),
        }

    elif agent_name == "invoice":
        # Keys that are internal Simpro structures — strip to avoid
        # the presenter turning them into confusing sub-tables.
        _INVOICE_STRIP_KEYS = {"CostCenters", "Retainage", "Jobs"}

        def _clean_invoice(inv: dict) -> dict:
            """Remove nested arrays from the invoice object."""
            inner = inv.get("invoice")
            if isinstance(inner, dict):
                inv["invoice"] = {
                    k: v for k, v in inner.items()
                    if k not in _INVOICE_STRIP_KEYS
                }
            return inv

        all_results = []
        for inv in execution_result.get("created", []):
            all_results.append(_clean_invoice(inv))
        for inv in execution_result.get("warnings", []):
            all_results.append(_clean_invoice(inv))
        for inv in execution_result.get("failed", []):
            inv.setdefault("status", "failed")
            # Surface user-friendly error from crossroads error_recovery
            if inv.get("friendly"):
                inv["error_message"] = inv.pop("friendly")
            all_results.append(inv)
        for sk in agent_result.get("skipped", []):
            all_results.append({
                "job_id": sk.get("job_id"),
                "status": "skipped",
                "warning": sk.get("reason", "Missing data — skipped"),
            })
        return {
            "success": execution_result.get("success"),
            "summary": execution_result.get("summary"),
            "results": all_results,
            "failed": execution_result.get("failed", []),
        }

    # Fallback
    return {
        "success": execution_result.get("success"),
        "summary": execution_result.get("summary"),
        "failed": execution_result.get("failed", []),
    }


async def _run_agent(
    agent_name: str,
    user_text: str,
    attachments: List[Dict[str, Any]],
    conversation_history: Optional[List[Dict[str, str]]] = None,
    intent_action: Optional[str] = None,
    intent_follow_up: bool = False,
    current_user: Optional[Dict[str, Any]] = None,
    llm_chat_fn=None,
    session_context: Optional[Dict[str, Any]] = None,
    reuse_fields: Optional[Dict[str, Any]] = None,
    changed_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run specified agent"""
    logger.info(f"🤖 Running agent: {agent_name}")

    # Detect document type
    doc_type_hint = "unknown"
    if attachments:
        first_file = attachments[0]
        doc_type_hint = _detect_document_type(
            first_file.get("filename", ""),
            first_file.get("content_type", ""),
            user_text
        )

    # Extract structured data
    extracted = None
    if attachments:
        extracted = await _call_extractor(attachments, doc_type_hint)
        if extracted and not extracted.get("is_useful"):
            logger.warning("Extraction not useful")
            extracted = None
        elif extracted:
            _normalize_extracted_tables(extracted)

    # Get text snippet
    any_uploaded_text = _quick_text_snippet(attachments)
    
    if any_uploaded_text:
        logger.info(f"📝 Text: {len(any_uploaded_text)} chars")

    # Prepare hints with company ID from user's org
    company_id = str(current_user.get("simpro_company_id") or "") if current_user else ""
    if not company_id:
        return {
            "success": False,
            "error": "Simpro Company ID is not configured for your organisation.",
            "response": "Your organisation does not have a Simpro Company ID configured. Please ask your admin to set it in the super admin settings.",
        }
    
    # Try to extract company ID from Excel data if available
    # Tables are already normalized (_normalize_extracted_tables splits headers from rows)
    if extracted and extracted.get("tables"):
        for table in extracted.get("tables", []):
            headers = table.get("headers", [])
            rows = table.get("rows", [])

            if not headers or not rows:
                continue

            for data_row in rows:
                row_dict = dict(zip(headers, data_row))

                if "CompanyID" in row_dict and row_dict["CompanyID"]:
                    company_id = str(row_dict["CompanyID"])
                    logger.info(f"📋 Using CompanyID from Excel: {company_id}")
                    break

    
    # Convert to integer
    try:
        company_id = int(company_id)
    except (ValueError, TypeError):
        logger.error(f"⚠️  Invalid CompanyID: {company_id!r}")
        return {
            "success": False,
            "error": f"Invalid Simpro Company ID: {company_id!r}",
            "response": "Your organisation has an invalid Simpro Company ID configured. Please ask your admin to fix it in the super admin settings.",
        }
    
    hints = {"CompanyID": company_id}

    # Inject org_id so proxies can look up per-tenant SOP overrides (Phase 5)
    _agent_org_id = current_user.get("org_id") if current_user else None
    if _agent_org_id:
        hints["org_id"] = _agent_org_id

    # Inject per-tenant Simpro credentials into hints so agent proxies can
    # create a tenant-specific MCPToolClient instead of the global singleton.
    _agent_creds = _get_org_simpro_credentials(_agent_org_id)
    if _agent_creds["simpro_token"] and _agent_creds["simpro_url"]:
        hints["simpro_token"] = _agent_creds["simpro_token"]
        hints["simpro_url"] = _agent_creds["simpro_url"]
        if _agent_creds["simpro_company_id"]:
            hints["simpro_company_id"] = _agent_creds["simpro_company_id"]

    if intent_action:
        hints["action"] = intent_action
        logger.info(f"🎯 Intent action passed to agent: {intent_action}")
    if intent_follow_up:
        hints["follow_up"] = True
        logger.info("🔄 Follow-up flag set — agent will prioritize conversation history")
    if session_context:
        hints["session_context"] = session_context
        logger.info(
            f"📦 Session context attached: route={session_context.get('route')}, "
            f"dept={session_context.get('department')}, "
            f"data_keys={list(session_context.get('structured_data', {}).keys()) if isinstance(session_context.get('structured_data'), dict) else 'N/A'}"
        )
    # Follow-up context bridge: structured reuse/changed fields from intent analyzer
    if reuse_fields:
        hints["reuse_fields"] = reuse_fields
        logger.info(f"🔗 Reuse fields: {reuse_fields}")
    if changed_fields:
        hints["changed_fields"] = changed_fields
        logger.info(f"🔀 Changed fields: {changed_fields}")
    logger.info(f"🏢 Using CompanyID: {company_id}")

    # Run agent (dynamic loading)
    runner = _get_agent_runner(agent_name)
    if not runner:
        return {"success": False, "error": f"Agent {agent_name} not implemented"}

    try:
        logger.info(f"▶️  Executing {agent_name} agent...")

        agent_result = runner(
            user_text=user_text,
            extracted=extracted,
            raw_attachments=attachments if not extracted else None,
            any_uploaded_text=any_uploaded_text,
            hints=hints,
            conversation_history=conversation_history,
            llm_chat_fn=llm_chat_fn,
        )

        # Await if the result is a coroutine (async agent)
        if hasattr(agent_result, '__await__'):
            agent_result = await agent_result

        logger.info(f"✅ Agent completed: success={agent_result.get('success')}")
        logger.info(f"📊 Agent result keys: {list(agent_result.keys())}")
        logger.info(f"📄 Full result: {json.dumps(agent_result, indent=2, default=str)[:500]}")

        # Store session data for clarification resubmission
        if agent_result.get("needs_clarification") and agent_result.get("session_id"):
            sid = agent_result["session_id"]
            # For chat-mode requests, extracted may be None — use the agent's internally-built data
            session_extracted = extracted or agent_result.get("original_extracted")
            _pending_sessions[sid] = {
                "created_at": time.time(),
                "agent_name": agent_name,
                "user_text": user_text,
                "extracted": session_extracted,
                "any_uploaded_text": any_uploaded_text,
                "hints": hints,
                "company_id": company_id,
            }
            # For workorder clarification, also store payload-level data
            if agent_name == "workorder" and agent_result.get("_clean_payloads") is not None:
                _pending_sessions[sid]["_clean_payloads"] = agent_result["_clean_payloads"]
                _pending_sessions[sid]["_pending_payloads"] = agent_result["_pending_payloads"]
                _pending_sessions[sid]["_existing_map"] = agent_result["_existing_map"]
            # For invoice clarification, store the LLM policy so the
            # /invoice/clarify endpoint can apply user overrides and re-build.
            if agent_name == "invoice" and agent_result.get("_policy") is not None:
                _pending_sessions[sid]["_policy"] = agent_result["_policy"]
            # For invoice job-resolution clarification, store the parsed chat result
            # so we can inject the selected JobID and re-run the pipeline.
            if agent_name == "invoice" and agent_result.get("_chat_result") is not None:
                _pending_sessions[sid]["_chat_result"] = agent_result["_chat_result"]
            # Store clarification options for "Other" custom input LLM resolution
            if agent_result.get("clarifications"):
                _pending_sessions[sid]["_last_agent_result"] = {
                    "clarifications": agent_result["clarifications"],
                }
            logger.info(f"💾 Stored pending session: {sid}")

            # Generate pre-filled corrected Excel when in file_download mode
            if agent_result.get("clarification_mode") == "file_download" and session_extracted:
                try:
                    from tools.generate_corrected_template import generate_corrected_template
                    generate_corrected_template(
                        extracted=session_extracted,
                        clarifications=agent_result.get("clarifications", []),
                        session_id=sid,
                    )
                except Exception as e:
                    logger.warning(f"Failed to generate corrected template: {e}")

        # Step 2: If invoice agent, execute create/update/delete using MCP tools
        if agent_name == "invoice" and (
            agent_result.get("jobs")
            or agent_result.get("invoice_updates")
            or agent_result.get("invoice_deletes")
        ):
            logger.info(f"📤 Executing invoice operations via MCP Server HTTP API...")

            # Check if tool is available
            if not is_tool_available():
                logger.error("❌ Invoice MCP tools not available")
                return {
                    "success": False,
                    "error": "MCP_TOOL_UNAVAILABLE",
                    "message": "Invoice tools are currently unavailable. Please ensure the MCP Server is running and try again.",
                }

            # Dispatch to the correct executor based on operation type
            if agent_result.get("jobs"):
                execution_result = await create_invoices_from_agent_result(
                    agent_result=agent_result,
                    company_id=company_id,
                    llm_chat=llm_chat_fn,
                )
            elif agent_result.get("invoice_updates"):
                execution_result = await update_invoices_from_agent_result(
                    agent_result=agent_result,
                    company_id=company_id,
                    llm_chat=llm_chat_fn,
                )
            elif agent_result.get("invoice_deletes"):
                execution_result = await delete_invoices_from_agent_result(
                    agent_result=agent_result,
                    company_id=company_id,
                    llm_chat=llm_chat_fn,
                )

            logger.info(f"✅ Invoice operation: {execution_result.get('summary', {})}")
            user_id = current_user.get("id") if current_user else None
            org_id = current_user.get("org_id") if current_user else None
            return _build_execution_response("invoice", execution_result, agent_result, user_id, org_id, company_id)

        # Step 3: If schedule agent, execute schedule operations using MCP tools
        if agent_name == "schedule" and agent_result.get("schedules"):
            logger.info(f"📤 Executing schedule operations via MCP Server HTTP API...")

            # Check if tools are available
            if not is_schedule_tool_available():
                logger.error("❌ Schedule MCP tools not available")
                return {
                    "success": False,
                    "error": "MCP_TOOL_UNAVAILABLE",
                    "message": "Schedule tools are currently unavailable. Please ensure the MCP Server is running and try again.",
                }

            # Execute schedule operations via MCP Server HTTP API
            execution_result = await execute_schedule_operations(
                agent_result=agent_result,
                company_id=company_id,
                llm_chat=llm_chat
            )

            logger.info(f"✅ Schedule execution: {execution_result.get('summary', {})}")
            user_id = current_user.get("id") if current_user else None
            org_id = current_user.get("org_id") if current_user else None
            return _build_execution_response("schedule", execution_result, agent_result, user_id, org_id, company_id)

        # Step 4: If workorder agent, handle two-phase flow
        if agent_name == "workorder":
            if agent_result.get("phase") == "prepare" and agent_result.get("wo_review_rows"):
                # Phase A: Return rows as a table with CSV download
                # The presenter will format it with a CSV button.
                # We pass the rows through the presenter for consistent rendering.
                return {
                    "success": True,
                    "phase": "prepare",
                    "wo_review_rows": agent_result["wo_review_rows"],
                    "message": agent_result.get("message"),
                    "targets_count": agent_result.get("targets_count"),
                    "total_items": agent_result.get("total_items"),
                }
            elif (agent_result.get("contractor_jobs")
                  or agent_result.get("contractor_job_updates")
                  or agent_result.get("contractor_job_deletes")):
                # Execute contractor job create/update/delete via MCP
                logger.info("📤 Executing contractor job operations via MCP Server HTTP API...")
                execution_result = await execute_workorder_operations(
                    agent_result=agent_result,
                    company_id=company_id,
                    llm_chat=llm_chat_fn,
                )
                logger.info(f"✅ Contractor job operations: {execution_result.get('summary', {})}")
                user_id = current_user.get("id") if current_user else None
                org_id = current_user.get("org_id") if current_user else None
                return _build_execution_response("workorder", execution_result, agent_result, user_id, org_id, company_id)

        # For other agents or clarification needed, return result directly
        return agent_result
        
    except Exception as e:
        logger.error(f"❌ Agent error: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"Something went wrong while processing your request: {e}"
        }

# ============================================================================
# MCP Client
# ============================================================================

def _enrich_message_with_reuse_fields(
    message: str,
    intent_result: Optional[Dict[str, Any]],
) -> str:
    """Prepend reuse_fields context to MCP message for follow-up queries.

    When the intent analyzer extracted structured fields from a previous
    operation, inject them as explicit context so the MCP LLM doesn't have
    to hunt through conversation history.
    """
    if not intent_result or not intent_result.get("follow_up"):
        return message
    reuse = intent_result.get("reuse_fields")
    changed = intent_result.get("changed_fields")
    if not reuse and not changed:
        return message
    parts = []
    if reuse:
        field_strs = [f"{k}={v}" for k, v in reuse.items()]
        parts.append(f"From the previous operation: {', '.join(field_strs)}")
    if changed:
        field_strs = [f"{k}={v}" for k, v in changed.items()]
        parts.append(f"User wants to change: {', '.join(field_strs)}")
    context = "[CONTEXT FROM PREVIOUS OPERATION: " + ". ".join(parts) + "]\n\n"
    return context + message


async def _call_mcp_client(
    message: str,
    history: List[Dict[str, str]] = None,
    force_model: str = None
) -> Dict[str, Any]:
    """Call MCP Client for normal queries"""
    logger.info(f"📡 MCP Client: {message[:80]}...")

    try:
        client = get_mcp_pool()
        response = await client.post(
            f"{MCP_CLIENT_URL}/api/chat",
            json={
                "message": message,
                "history": history or [],
                "forceModel": force_model
            }
        )
        response.raise_for_status()
        result = response.json()

        logger.info(f"✅ MCP responded")
        return result
    
    except Exception as e:
        logger.error(f"❌ MCP error: {e}")
        return {
            "success": False,
            "error": str(e),
            "response": "Sorry, couldn't connect to MCP server."
        }

def _make_org_llm_fn(org_id: Optional[int]):
    """
    Build a per-request LLM callable that uses the org's keys from DB.
    Used by handlers that don't have _effective_llm_chat in scope (clarify, contradiction, etc.)
    """
    slot = _get_org_llm_config(org_id)["primary"]
    def _fn(messages, response_format=None, temperature=0.0, **kw):
        kw.pop("complexity", None)
        return chat_with_override(messages, response_format=response_format,
                                  temperature=temperature,
                                  provider=slot["provider"], model=slot["model"],
                                  api_key=slot["api_key"], **kw)
    return _fn


def _format_with_presenter(data: Any, question: str = "Invoice Creation Results",
                           extra_hints: Optional[Dict] = None,
                           llm_fn=None) -> Dict[str, Any]:
    """
    Format data using presenter_router.py (SINGLE FORMATTER)

    Args:
        data: Data to format (agent result)
        question: User's question/context
        extra_hints: Additional hints to pass to the presenter LLM.
                     Use {"llm_draft": "..."} to pass a streamed response for
                     personality-aware rewrite instead of raw-JSON summarisation.

    Returns:
        Presenter envelope ready for frontend
    """

    try:
        logger.info("📊 Calling presenter_router...")
        # Create request matching presenter_router's format
        hints = {
            "llm_schema_hints": True,  # Use LLM for better column names
            "allow_llm": True
        }
        if extra_hints:
            hints.update(extra_hints)
        req = PresentRequest(
            question=question,
            payload=data,
            hints=hints,
            llm_fn=llm_fn,
        )
        
        # Call presenter directly (no HTTP, no async needed)
        result = present(req)
        
        logger.info("✅ Presenter returned envelope")
        return result.envelope
        
    except Exception as e:
        logger.error(f"❌ Presenter error: {e}", exc_info=True)
        
        # Minimal fallback if presenter fails
        return {
            "title": "Results",
            "summary": "Error formatting results",
            "blocks": [{
                "type": "json",
                "title": "Raw Data",
                "spec": {
                    "json": data,
                    "downloads": [{
                        "type": "json",
                        "blob": "data:application/json," + json.dumps(data, default=str)
                    }]
                }
            }]
        }

# ============================================================================
# User Format Preference Detection
# ============================================================================

_SUMMARY_ONLY_PATTERNS = [
    r"\bno\s+table\b",
    r"\bwithout\s+(a\s+)?table\b",
    r"\bdon'?t\s+(show|need|want)\s+(a\s+)?table\b",
    r"\bonly\s+(a\s+)?summary\b",
    r"\bjust\s+(a\s+)?summary\b",
    r"\bsummary\s+only\b",
    r"\bsummar(y|ize|ise)\s+(it|this|that|the\s+data)\b",
    r"\bjust\s+(tell|explain|describe)\b",
    r"\bskip\s+the\s+table\b",
]
_SUMMARY_ONLY_RE = re.compile("|".join(_SUMMARY_ONLY_PATTERNS), re.IGNORECASE)


def _wants_summary_only(message: str) -> bool:
    """Detect if the user explicitly asked for no table / summary only."""
    return bool(_SUMMARY_ONLY_RE.search(message))


# ============================================================================
# Tool Data Extraction
# ============================================================================

def _extract_tool_data(tool_calls: List[Dict[str, Any]]) -> Optional[Any]:
    """
    Extract structured data from MCP tool call results.

    When the LLM chains multiple tool calls (e.g. get_job_sections → 3×
    get_job_section_cost_centres), this function combines the results from
    repeated calls of the same tool into a single merged payload so the
    presenter can display all the data — not just the last call's result.
    """
    if not tool_calls:
        return None

    # Collect all successful results grouped by tool name
    from collections import defaultdict
    by_tool: Dict[str, List[Dict]] = defaultdict(list)

    for tc in tool_calls:
        result = tc.get("result")
        if result is None:
            continue
        if isinstance(result, dict) and result.get("success") is False:
            continue

        name = tc.get("name", "unknown")

        if isinstance(result, dict):
            # Unwrap the MCP wrapper: result = {success, data: {actual payload}, tool, error, formatted}
            # The actual data with list keys (cost_centres, jobs, etc.) is inside "data"
            inner = result.get("data")
            if isinstance(inner, dict):
                payload = {k: v for k, v in inner.items() if k not in ("formatted",)}
            else:
                payload = {k: v for k, v in result.items() if k not in ("formatted", "tool", "error")}

            data_keys = {k for k in payload if k not in ("success",)}
            if data_keys:
                by_tool[name].append(payload)
        elif isinstance(result, list) and result:
            by_tool[name].append(result)

    if not by_tool:
        return None

    # If only one tool type produced data, use it directly
    if len(by_tool) == 1:
        tool_name, results = next(iter(by_tool.items()))
        if len(results) == 1:
            return results[0]
        return _merge_tool_results(results)

    # Multiple tool types → pick the one with the most actual data items.
    # This prevents empty leaf-tool results (called many times) from
    # overriding an earlier tool that returned the real data.
    # Tiebreakers: call count, then last occurrence order.
    def _tool_data_volume(tool_results: List[Dict]) -> int:
        """Count total data items across all results for a tool.

        Lists are counted by length; single-record dicts count as 1.
        This ensures detail tools (get_*_details returning {"job": {...}})
        score at least 1 and aren't beaten by empty list results.
        """
        _skip = ("success", "tool", "error", "formatted",
                 "page", "page_size", "metadata")
        total = 0
        for r in tool_results:
            if isinstance(r, list):
                total += len(r)
            elif isinstance(r, dict):
                for k, v in r.items():
                    if k in _skip:
                        continue
                    if isinstance(v, list):
                        total += len(v)
                    elif isinstance(v, dict) and v:
                        total += 1  # single-record data counts as 1
        return total

    primary_tool = max(by_tool, key=lambda t: (
        _tool_data_volume(by_tool[t]),
        len(by_tool[t]),
        [i for i, tc in enumerate(tool_calls) if tc.get("name") == t][-1],
    ))

    logger.info(f"🔀 Multiple tool types ({list(by_tool.keys())}), using primary: {primary_tool}")

    results = by_tool[primary_tool]
    if len(results) == 1:
        return results[0]
    return _merge_tool_results(results)


def _dedup_records(records: List[Any]) -> List[Any]:
    """
    Remove duplicate records from a merged list using content hashing.

    ERP-agnostic — makes no assumptions about field names or ID fields.
    Two records are considered identical if their full JSON content matches.
    Preserves original ordering, keeping the first occurrence.
    """
    import json as _json
    seen: set = set()
    deduped: List[Any] = []
    for record in records:
        try:
            key = _json.dumps(record, sort_keys=True, default=str)
        except Exception:
            key = str(record)
        if key not in seen:
            seen.add(key)
            deduped.append(record)
    if len(deduped) < len(records):
        logger.info(f"_dedup_records: removed {len(records) - len(deduped)} duplicate record(s)")
    return deduped


def _merge_tool_results(results: List[Any]) -> Any:
    """
    Merge multiple results from the same tool into one combined payload.

    Handles two patterns:
    1. List-valued data keys (e.g. "cost_centres": [...]) → concatenate
    2. Dict-valued data keys (e.g. "cost_centre": {...}) → collect into list

    Pattern 2 occurs when a tool returns a single record per call and the
    LLM calls it multiple times (e.g. get_job_cost_centre_details for each
    cost centre).
    """
    if not results:
        return None

    # If all results are plain lists, concatenate
    if all(isinstance(r, list) for r in results):
        merged: list = []
        for r in results:
            merged.extend(r)
        return merged

    # All results are dicts — merge data fields
    if not all(isinstance(r, dict) for r in results):
        return results[-1]  # mixed types, fall back to last

    meta_keys = {"success", "tool", "error", "formatted", "page", "page_size"}

    # Find list-valued keys (pattern 1: each result has a list)
    list_keys: set = set()
    for r in results:
        for k, v in r.items():
            if isinstance(v, list) and k not in meta_keys:
                list_keys.add(k)

    # Find dict-valued data keys (pattern 2: each result has a single record)
    # A key qualifies if it holds a dict in ALL results and is not metadata
    dict_keys: set = set()
    if not list_keys:
        # Only use pattern 2 if no list keys found
        candidate_keys: set = set()
        for r in results:
            for k, v in r.items():
                if isinstance(v, dict) and k not in meta_keys:
                    candidate_keys.add(k)
        # Key must be a dict in every result that has it
        for k in candidate_keys:
            if all(isinstance(r.get(k), dict) for r in results if k in r):
                dict_keys.add(k)

    # Build merged result
    merged_dict: dict = {}

    if list_keys:
        # Pattern 1: concatenate list fields, then deduplicate by record hash
        merged_dict = dict(results[-1])
        for key in list_keys:
            combined: list = []
            for r in results:
                val = r.get(key)
                if isinstance(val, list):
                    combined.extend(val)
            merged_dict[key] = _dedup_records(combined)

    elif dict_keys:
        # Pattern 2: collect single-record dicts into a list
        # Pick the primary data key (usually only one, e.g. "cost_centre")
        primary_key = max(dict_keys, key=lambda k: sum(1 for r in results if k in r))
        collected: list = []
        for r in results:
            val = r.get(primary_key)
            if isinstance(val, dict):
                collected.append(val)
        # Use plural form for the merged key (cost_centre → cost_centres)
        plural_key = primary_key + "s" if not primary_key.endswith("s") else primary_key
        merged_dict["success"] = True
        merged_dict[plural_key] = collected
        merged_dict["count"] = len(collected)
        return merged_dict

    else:
        merged_dict = dict(results[-1])

    # Recalculate count if present
    if "count" in merged_dict and list_keys:
        total = 0
        for key in list_keys:
            total += len(merged_dict.get(key, []))
        merged_dict["count"] = total

    return merged_dict


def _build_data_context(
    structured_data: Any, max_records: int = 100
) -> str:
    """Build a compact data-context string from structured data for conversation history.

    Auto-extracts key identifiers (IDs, References, Names, Types, Statuses)
    from the main data array so follow-up LLM calls can reference real values
    instead of hallucinating them.

    Returns an empty string when there is nothing useful to extract.
    """
    if not structured_data or not isinstance(structured_data, dict):
        return ""

    # ── Find the main data array (largest non-metadata list) ──
    _META = {
        "success", "total", "page", "page_size", "count", "error",
        "tool", "formatted", "metadata",
    }

    best_key: Optional[str] = None
    best_len = 0

    for k, v in structured_data.items():
        if k in _META:
            continue
        if isinstance(v, list) and len(v) > best_len:
            best_key = k
            best_len = len(v)

    if not best_key or best_len == 0:
        return ""

    data_array = structured_data[best_key]

    # Ensure the array contains dicts (records)
    if not isinstance(data_array[0], dict):
        return ""

    # ── Identify key fields from the first record ──
    _ID_PATTERNS = {"ID", "Id", "Reference", "Name", "Type", "Status"}
    _ID_SUFFIXES = ("ID", "Id")

    def _is_key_field(field_name: str) -> bool:
        if field_name in _ID_PATTERNS:
            return True
        if field_name.endswith(_ID_SUFFIXES):
            return True
        return False

    def _extract_fields(record: dict, prefix: str = "") -> List[tuple]:
        """Extract key fields from a record, flattening one level of nested dicts."""
        pairs = []
        for k, v in record.items():
            full_key = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
            if isinstance(v, dict):
                # Flatten one level deep — extract ID/Name/Type from nested objects
                for nk, nv in v.items():
                    if _is_key_field(nk) and not isinstance(nv, (dict, list)):
                        nested_key = f"{k}.{nk}"
                        pairs.append((nested_key, nv))
            elif isinstance(v, list):
                continue  # skip nested arrays
            elif _is_key_field(k):
                if v is not None and (not isinstance(v, str) or len(str(v)) <= 100):
                    pairs.append((k, v))
        return pairs

    # ── Build compact context lines ──
    total = len(data_array)
    capped = data_array[:max_records]
    lines = [f"[Data Context — {total} {best_key}]"]

    for record in capped:
        if not isinstance(record, dict):
            continue
        pairs = _extract_fields(record)
        if pairs:
            line = " ".join(f"{k}={v}" for k, v in pairs)
            lines.append(line)

    if total > max_records:
        lines.append(f"... and {total - max_records} more")

    # Enforce a character budget (~2500 chars) to keep token usage reasonable
    result = "\n".join(lines)
    if len(result) > 2500:
        # Truncate to fit — re-build with fewer records
        truncated_lines = [lines[0]]
        char_count = len(lines[0])
        for line in lines[1:]:
            if char_count + len(line) + 1 > 2400:
                remaining = total - (len(truncated_lines) - 1)
                truncated_lines.append(f"... and {remaining} more")
                break
            truncated_lines.append(line)
            char_count += len(line) + 1
        result = "\n".join(truncated_lines)

    return result


# ── Session Context: cross-path data handoff ──────────────────────────

_SESSION_CONTEXT_TTL = 300  # 5 minutes


def _build_session_context(
    user_context: Dict[str, Any], is_follow_up: bool
) -> Optional[Dict[str, Any]]:
    """Build session context dict for cross-path handoff.

    Returns None when there is no usable context (not a follow-up,
    data is missing, or the TTL has expired).
    """
    if not is_follow_up:
        return None

    data = user_context.get("last_structured_data")
    ts = user_context.get("session_context_ts")

    if not data or not ts or (time.time() - ts > _SESSION_CONTEXT_TTL):
        return None

    return {
        "structured_data": data,
        "route": user_context.get("last_route"),
        "department": user_context.get("last_department"),
        "tool_names": user_context.get("last_tool_names"),
        "intent": user_context.get("last_intent"),
    }


def _save_session_context(
    user_context: Dict[str, Any],
    *,
    structured_data: Any = None,
    route: Optional[str] = None,
    department: Optional[str] = None,
    tool_names: Optional[List[str]] = None,
    intent_result: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist session context snapshot into user_context."""
    user_context["last_structured_data"] = structured_data
    user_context["last_route"] = route
    user_context["last_department"] = department
    user_context["last_tool_names"] = tool_names
    user_context["last_intent"] = intent_result
    user_context["session_context_ts"] = time.time()


def _extract_simpro_error(raw: str) -> str:
    """Extract a clean error message from raw MCP/Simpro error strings.

    Simpro errors come wrapped like:
        'Execution error: 422: {"errors":[{"message":"..."}]}'
    This helper strips the wrapper and returns the inner message(s).
    """
    if not raw:
        return "Unknown error"

    # Try to find the JSON portion and extract "message" fields
    import re as _re
    json_match = _re.search(r'\{.*\}', raw, _re.DOTALL)
    if json_match:
        try:
            obj = json.loads(json_match.group(0))
            errors = obj.get("errors") or []
            messages = [e.get("message", "") for e in errors if e.get("message")]
            if messages:
                return "; ".join(messages)
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    # Strip common prefixes
    cleaned = raw
    for prefix in ("Execution error: ", "Validation error: "):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]

    # Strip leading HTTP status code (e.g. "422: ...")
    status_match = _re.match(r'^\d{3}:\s*', cleaned)
    if status_match:
        cleaned = cleaned[status_match.end():]

    return cleaned.strip() or raw


# ============================================================================
# Main Chat Logic
# ============================================================================

async def _chat_core(
    message: str,
    attachments: Optional[List[Dict[str, Any]]],
    current_user: Optional[Dict[str, Any]] = None,
    skip_validation: bool = False,
) -> Dict:
    """
    Core chat logic with AGENT-FIRST routing
    """
    message = (message or "").strip()
    if not message:
        return {"reply": "Please enter a message."}

    _t0 = time.perf_counter()  # ← request timing
    _request_id = new_request_id()

    logger.info(f"\n{'='*70}")
    logger.info(f"📨 Message: {message[:80]}")
    logger.info(f"📎 Files: {len(attachments) if attachments else 0}")
    logger.info(f"👤 User: {current_user.get('email', '?') if current_user else 'anonymous'}")
    logger.info(f"{'='*70}")

    # Token budget enforcement
    org_id = current_user.get("org_id") if current_user else None
    user_id = current_user.get("id") if current_user else 0
    budget_error = _check_token_budget(org_id)
    if budget_error:
        return {"reply": budget_error}

    # Load per-tenant Simpro credentials (Phase 1: multi-tenancy)
    _org_creds = _get_org_simpro_credentials(org_id)

    # Per-org LLM routing (Phase 6)
    _org_llm = _get_org_llm_config(org_id)
    _primary_slot = _org_llm["primary"]
    _complex_slot_llm = _org_llm["complex"]

    def _make_llm_fn(slot):
        if any(slot.values()):
            def _fn(messages, response_format=None, temperature=0.0, **kw):
                kw.pop("complexity", None)
                return chat_with_override(messages, response_format=response_format,
                                          temperature=temperature,
                                          provider=slot["provider"], model=slot["model"],
                                          api_key=slot["api_key"], **kw)
            return _fn
        # No per-org override — route through chat_with_override with no overrides
        # so the global LLM_PROVIDER/LLM_MODEL from .env are used uniformly.
        # This avoids silently hitting _fallback() when _openai_client is None.
        def _global_fn(messages, response_format=None, temperature=0.0, **kw):
            kw.pop("complexity", None)
            return chat_with_override(messages, response_format=response_format,
                                      temperature=temperature,
                                      provider=None, model=None, api_key=None, **kw)
        return _global_fn

    _primary_llm_fn = _make_llm_fn(_primary_slot)
    _complex_llm_fn = _make_llm_fn(_complex_slot_llm) if any(_complex_slot_llm.values()) else _primary_llm_fn

    def _effective_llm_chat(messages, response_format=None, temperature=0.0, **kw):
        """Routes to primary or complex LLM based on complexity= kwarg."""
        complexity = kw.pop("complexity", "standard")
        fn = _complex_llm_fn if complexity == "high" else _primary_llm_fn
        return fn(messages, response_format=response_format, temperature=temperature, **kw)

    # Per-user session context
    user_context = _get_user_context(user_id, org_id)
    _prev_message = user_context.get("last_message", "")  # capture before overwrite (for follow-up filters)
    user_context["last_message"] = message

    # Token accumulator for this request (uses per-org LLM routing)
    accumulator = _TokenAccumulator(llm_fn=_effective_llm_chat)

    # ========================================================================
    # STEP 1: Determine routing via LLM Intent Analyzer
    # ========================================================================
    from utils.intent_analyzer import analyze_intent
    from utils.crossroads import resolve_crossroads

    agent_name = None
    intent_result = None

    # 1a. File-structure detection — build file_context for intent analyzer
    _file_context = None
    if attachments:
        first_file = attachments[0]
        doc_type_hint = _detect_document_type(
            first_file.get("filename", ""),
            first_file.get("content_type", ""),
            message
        )
        quick_extracted = await _call_extractor(attachments, doc_type_hint)
        if quick_extracted:
            _normalize_extracted_tables(quick_extracted)
            _detected = _detect_agent_from_file_structure(
                quick_extracted, llm_chat=_effective_llm_chat, user_text=message
            )
            _tbl = quick_extracted.get("tables", [{}])[0] if quick_extracted.get("tables") else {}
            _file_context = {
                "filename": first_file.get("filename", "uploaded_file"),
                "headers": _tbl.get("headers", []),
                "row_count": len(_tbl.get("rows", [])),
                "detected_agent": _detected,
            }
            agent_name = _detected  # quick-path: use header detection immediately if confident
            if agent_name:
                logger.info(f"🎯 File structure detected: {agent_name} agent")

    # 1b. LLM intent analysis (always runs; file_context enriches it when present)
    history = user_context.get("conversation_history", [])
    _scratchpad = get_or_create_scratchpad(user_context)
    intent_result = analyze_intent(
        message=message,
        conversation_history=history,
        llm_chat=_effective_llm_chat,
        session_context=_scratchpad.to_context_string() or None,
        file_context=_file_context,
    )
    logger.info(f"🎯 Intent action from message: {intent_result.get('action')}")

    candidate_agent = intent_result.get("agent")
    confidence = intent_result.get("confidence", 0.0)

    # ── Cancel intent: always takes precedence over file/header routing ──
    if intent_result.get("intent") == "cancel_request":
        cancelled_sid = user_context.pop("pending_clarification_sid", None)
        if cancelled_sid:
            _pending_sessions.pop(cancelled_sid, None)
            logger.info(f"🚫 User cancelled pending clarification: {cancelled_sid}")
        cancelled_maq = user_context.pop("multi_action_queue", None)
        if cancelled_maq:
            for orphan_sid in cancelled_maq.get("pending_sids", []):
                _pending_sessions.pop(orphan_sid, None)
            logger.info(f"🚫 Cleared multi-action queue with {len(cancelled_maq.get('pending_sids', []))} pending")
        agent_name = None  # Route to MCP for friendly cancellation response
        logger.info("🚫 Cancel intent detected — cleared all pending state, routing to MCP")

    # Resolve agent_name from intent result if header detection didn't find one
    elif not agent_name:
        if candidate_agent and confidence >= 0.5:
            if load_agent(candidate_agent) is not None:
                agent_name = candidate_agent
                logger.info(f"🎯 LLM routed to: {agent_name} (confidence={confidence:.2f}, action={intent_result.get('action')})")
            else:
                logger.info(f"🔍 LLM suggested agent '{candidate_agent}' but it's not available")
        else:
            # Deterministic override: if there's a pending clarification session
            # and the LLM didn't route to any agent, the user is likely answering
            # the clarification (e.g., providing a bare name like "jarrad edwards").
            pending_sid = user_context.get("pending_clarification_sid")
            if pending_sid and pending_sid in _pending_sessions:
                pending_agent = _pending_sessions[pending_sid].get("agent_name")
                pending_action = _pending_sessions[pending_sid].get("hints", {}).get("action", "create")
                if pending_agent and load_agent(pending_agent) is not None:
                    agent_name = pending_agent
                    intent_result = {
                        **intent_result,
                        "agent": pending_agent,
                        "action": pending_action,
                        "follow_up": True,
                        "confidence": 0.85,
                        "intent": f"{pending_agent}_crud",
                    }
                    logger.info(
                        f"🔄 Pending clarification override: routing to {agent_name} "
                        f"(session={pending_sid}, action={pending_action})"
                    )
            if not agent_name:
                logger.info(f"🔍 No agent match (intent={intent_result.get('intent')}, confidence={confidence:.2f}) → MCP")

    # ── Journal: record routing decision ──
    if intent_result:
        record_decision(
            request_id=_request_id, org_id=org_id, user_id=user_id,
            dimension="routing",
            decision_type="intent_analysis",
            decision_value=agent_name or "chat",
            confidence=intent_result.get("confidence", 0.0),
            reasoning=f"intent={intent_result.get('intent')}, action={intent_result.get('action')}",
            context={"follow_up": intent_result.get("follow_up", False)},
        )
        record_trace(
            request_id=_request_id, org_id=org_id, user_id=user_id,
            intent=intent_result.get("intent", ""),
            agent=agent_name or "chat",
            action=intent_result.get("action") or "",
            confidence=intent_result.get("confidence", 0.0),
            message_preview=message[:100],
        )

    # ── HISTORY GATING: suppress for standalone queries ─────────────
    # The intent analyzer (above) already received full history to detect
    # follow-ups. For standalone queries (follow_up=false), hide history
    # from all downstream LLM calls to prevent answer drift.
    is_follow_up = bool(intent_result.get("follow_up")) if intent_result else False
    effective_history = history if is_follow_up else []
    if not is_follow_up and history:
        logger.info("🧹 History gated: standalone query, %d entries hidden from downstream", len(history))

    # ── MULTI-ACTION ORCHESTRATION ──────────────────────────────────
    # If the intent analyzer detected multiple independent CRUD operations,
    # run each as a parallel sub-request through the orchestrator.
    if (
        not skip_validation
        and intent_result
        and intent_result.get("is_multi_action")
        and intent_result.get("sub_requests")
    ):
        from utils.multi_action_orchestrator import orchestrate_multi_action
        logger.info(f"🔀 Multi-action detected: {len(intent_result['sub_requests'])} sub-requests")
        result = await orchestrate_multi_action(
            sub_requests=intent_result["sub_requests"],
            original_message=message,
            attachments=attachments or [],
            current_user=current_user,
            effective_history=effective_history,
            accumulator=accumulator,
            user_context=user_context,
            run_agent_fn=_run_agent,
            org_id=org_id,
            intent_result=intent_result,
        )

        # Store in conversation history
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": _summarize_multi_action_result(result)})
        user_context["conversation_history"] = history[-10:]

        # Handle clarification
        if result.get("needs_clarification"):
            clar_data = result.get("clarification_data", {})
            if clar_data.get("session_id"):
                user_context["pending_clarification_sid"] = clar_data["session_id"]
            # Build multi-action clarification queue for chaining
            mac = clar_data.get("multi_action_context", {})
            remaining = mac.get("remaining_clarification_sids", [])
            if remaining or mac.get("completed_results") or mac.get("failed_results"):
                user_context["multi_action_queue"] = {
                    "original_message": message,
                    "total_sub_requests": mac.get("total_sub_requests", 0),
                    "pending_sids": [r["session_id"] for r in remaining],
                    "pending_meta": {
                        r["session_id"]: {
                            "sub_index": r["sub_index"],
                            "description": r["description"],
                            "agent": r["agent"],
                        }
                        for r in remaining
                    },
                    "completed_results": list(mac.get("completed_results", [])),
                    "failed_results": list(mac.get("failed_results", [])),
                    "sub_descriptions": mac.get("sub_descriptions", {}),
                    "current_sub_index": mac.get("sub_index"),
                    "current_description": mac.get("description"),
                }
                logger.info(
                    f"📋 Multi-action queue built: {len(remaining)} remaining "
                    f"clarifications after current, "
                    f"{len(mac.get('completed_results', []))} completed, "
                    f"{len(mac.get('failed_results', []))} failed"
                )

            # Build merged envelope from already-completed sub-requests
            completed_envelope = _build_multi_action_partial_envelope(mac, message)
            if completed_envelope:
                result["envelope"] = completed_envelope

            return result

        # Log token usage
        _duration_ms = int((time.perf_counter() - _t0) * 1000)
        if org_id and (accumulator.total_input or accumulator.total_output):
            log_usage(
                org_id=org_id, user_id=user_id,
                agent_name="multi_action",
                input_tokens=accumulator.total_input,
                output_tokens=accumulator.total_output,
                model_name=accumulator.model,
                request_path="/api/chat",
                duration_ms=_duration_ms,
            )
        logger.info(f"⏱️  Multi-action completed in {_duration_ms}ms")
        return result

    # ── INPUT CONTRADICTION CHECK (non-streaming path) ──────────────
    if not skip_validation and (agent_name or (intent_result and intent_result.get("intent"))):
        from utils.input_validator import validate_user_input
        detected_route = agent_name or intent_result.get("intent", "query")
        contradiction = await validate_user_input(
            user_message=message,
            detected_route=detected_route,
            llm_chat=accumulator.tracked_chat,
            conversation_history=effective_history,
        )
        if contradiction:
            session_id = contradiction["clarification_data"]["session_id"]
            _pending_sessions[session_id] = {
                "created_at": time.time(),
                "type": "contradiction",
                "original_message": message,
                "agent_name": agent_name,
                "intent_result": intent_result,
                "attachments": attachments,
                "user_id": user_id,
            }
            _contra_clar = contradiction["clarification_data"]
            _contra_sid = _contra_clar.get("session_id")
            if _contra_sid:
                _contra_clar["expires_in"] = _session_expires_in(_contra_sid)
            return {
                "reply": contradiction.get("message", "Your request has conflicting information."),
                "needs_clarification": True,
                "clarification_data": _contra_clar,
                "metadata": {"agent": agent_name or "chat"},
            }

    if agent_name:
        # Agent gating: check if this agent is enabled for the user's org
        if org_id and not is_agent_enabled_for_org(org_id, agent_name):
            return {
                "reply": f"The {agent_name} agent is not available on your current plan. "
                         f"Please contact your administrator to upgrade."
            }

        # Per-agent token budget check
        agent_budget_error = _check_agent_token_budget(org_id, agent_name)
        if agent_budget_error:
            return {"reply": agent_budget_error}

        # Per-role operation permission check (Phase 4)
        operation = intent_action or "query"
        if user_id and org_id and not is_operation_allowed_for_user(user_id, org_id, agent_name, operation):
            return {
                "reply": f"Your role does not have permission to {operation} {agent_name.replace('_', ' ')} records. "
                         f"Please contact your administrator."
            }

        logger.info(f"→ Routing to {agent_name} agent")

        # Run agent with conversation history and tracked LLM calls
        history = user_context.get("conversation_history", [])
        intent_follow_up = is_follow_up

        # ── Retry shortcut: re-execute from cached resolved data ──────────
        cached = _last_failed_ops.get((user_id, org_id or 0))
        result = None
        if (
            intent_follow_up
            and cached
            and cached["agent_name"] == agent_name
            and time.time() - cached.get("timestamp", 0) < 300  # 5-min expiry
        ):
            logger.info(f"🔄 Retry: re-executing {agent_name} from cached resolved data (skipping agent)")
            cached_agent_result = cached["agent_result"]
            cached_company_id = cached["company_id"]

            try:
                if agent_name == "schedule" and cached_agent_result.get("schedules"):
                    execution_result = await execute_schedule_operations(
                        agent_result=cached_agent_result,
                        company_id=cached_company_id,
                        llm_chat=accumulator.tracked_chat,
                    )
                    result = _build_execution_response(
                        "schedule", execution_result, cached_agent_result, user_id, org_id, cached_company_id
                    )

                elif agent_name == "workorder" and (
                    cached_agent_result.get("contractor_jobs")
                    or cached_agent_result.get("contractor_job_updates")
                    or cached_agent_result.get("contractor_job_deletes")
                ):
                    execution_result = await execute_workorder_operations(
                        agent_result=cached_agent_result,
                        company_id=cached_company_id,
                        llm_chat=accumulator.tracked_chat,
                    )
                    result = _build_execution_response(
                        "workorder", execution_result, cached_agent_result, user_id, org_id, cached_company_id
                    )

                elif agent_name == "invoice" and (
                    cached_agent_result.get("jobs")
                    or cached_agent_result.get("invoice_updates")
                    or cached_agent_result.get("invoice_deletes")
                ):
                    if cached_agent_result.get("jobs"):
                        execution_result = await create_invoices_from_agent_result(
                            agent_result=cached_agent_result,
                            company_id=cached_company_id,
                            llm_chat=accumulator.tracked_chat,
                        )
                    elif cached_agent_result.get("invoice_updates"):
                        execution_result = await update_invoices_from_agent_result(
                            agent_result=cached_agent_result,
                            company_id=cached_company_id,
                            llm_chat=accumulator.tracked_chat,
                        )
                    elif cached_agent_result.get("invoice_deletes"):
                        execution_result = await delete_invoices_from_agent_result(
                            agent_result=cached_agent_result,
                            company_id=cached_company_id,
                            llm_chat=accumulator.tracked_chat,
                        )
                    result = _build_execution_response(
                        "invoice", execution_result, cached_agent_result, user_id, org_id, cached_company_id
                    )

            except Exception as e:
                logger.error(f"❌ Retry execution failed: {e}")
                result = {"success": False, "error": "RETRY_FAILED", "message": f"Retry failed: {e}"}

        # ── Clarification follow-up: re-run with original context ─────────
        # When the user types a correction (e.g., "its actually job id 20527")
        # instead of using the clarification dropdown, merge the correction
        # with the original request by re-running the agent with the original
        # user_text. The enriched conversation history (Fix A) gives the LLM
        # parser the already-parsed fields so it can apply the correction.
        if result is None and intent_follow_up:
            pending_sid = user_context.get("pending_clarification_sid")
            if pending_sid and pending_sid in _pending_sessions:
                session = _pending_sessions[pending_sid]
                if session.get("agent_name") == agent_name:
                    logger.info(
                        f"🔄 Follow-up with pending clarification {pending_sid} — "
                        f"re-running {agent_name} with original request"
                    )
                    # Preserve original action (create, not update)
                    original_action = session.get("hints", {}).get("action", "create")
                    _session_ctx = _build_session_context(user_context, True)
                    # Include the user's follow-up answer in history so the
                    # parser can see it (e.g. "its jarrad edwards" as the
                    # answer to a missing staff_name clarification).
                    follow_up_history = list(history)
                    follow_up_history.append({"role": "user", "content": message})
                    result = await _run_agent(
                        agent_name=agent_name,
                        user_text=session["user_text"],  # original full request
                        attachments=[],
                        conversation_history=filter_history(follow_up_history, agent_name),
                        intent_action=original_action,
                        intent_follow_up=True,
                        current_user=current_user,
                        llm_chat_fn=accumulator.tracked_chat,
                        session_context=_session_ctx,
                        reuse_fields=intent_result.get("reuse_fields") if intent_result else None,
                        changed_fields=intent_result.get("changed_fields") if intent_result else None,
                    )
                    # Clean up consumed session
                    _pending_sessions.pop(pending_sid, None)
                    user_context.pop("pending_clarification_sid", None)

        # ── Normal path: full agent run ───────────────────────────────────
        if result is None:
            # Clear stale pending session when user starts a fresh request
            if not intent_follow_up:
                stale_sid = user_context.pop("pending_clarification_sid", None)
                if stale_sid:
                    _pending_sessions.pop(stale_sid, None)
                    logger.info(f"🗑️ Cleared abandoned clarification session: {stale_sid}")
                # Also clear any stale multi-action queue + its pending sessions
                stale_maq = user_context.pop("multi_action_queue", None)
                if stale_maq:
                    for orphan_sid in stale_maq.get("pending_sids", []):
                        _pending_sessions.pop(orphan_sid, None)
                    logger.info(f"🗑️ Cleared abandoned multi-action queue with {len(stale_maq.get('pending_sids', []))} pending")

            _session_ctx = _build_session_context(user_context, intent_follow_up)
            result = await _run_agent(
                agent_name, message, attachments or [],
                conversation_history=filter_history(effective_history, agent_name),
                intent_action=intent_result.get("action") if intent_result else None,
                intent_follow_up=intent_follow_up,
                current_user=current_user,
                llm_chat_fn=accumulator.tracked_chat,
                session_context=_session_ctx,
                reuse_fields=intent_result.get("reuse_fields") if intent_result else None,
                changed_fields=intent_result.get("changed_fields") if intent_result else None,
            )

        # Track pending clarification session for follow-up detection
        if result.get("needs_clarification") and result.get("session_id"):
            user_context["pending_clarification_sid"] = result["session_id"]
        elif not result.get("needs_clarification"):
            # Clear stale pending session when request completes without clarification
            stale_sid = user_context.pop("pending_clarification_sid", None)
            if stale_sid:
                _pending_sessions.pop(stale_sid, None)
                logger.info(f"🗑️ Cleared stale clarification session: {stale_sid}")

        # Store agent interaction in conversation history (all outcomes)
        summary_text = _summarize_agent_result(agent_name, result, user_text=message)
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": summary_text})
        # Update scratchpad with extracted entity IDs and actions
        scratchpad = get_or_create_scratchpad(user_context)
        scratchpad.extract_from_agent_result(agent_name, result, summary_text, message)
        user_context["conversation_history"] = await update_context_after_turn(
            user_context, history, llm_chat_fn=accumulator.tracked_chat,
        )

        # ── Session Context: capture agent result for cross-path follow-ups ──
        if result.get("success"):
            _agent_data = result
            _save_session_context(
                user_context,
                structured_data=_agent_data,
                route=agent_name,
                department=intent_result.get("department") if intent_result else None,
                intent_result=intent_result,
            )

        # Log token usage for the agent path (covers all outcomes: success, error, clarification)
        _duration_ms = int((time.perf_counter() - _t0) * 1000)
        _clarification_rounds = 1 if result.get("needs_clarification") else 0
        if org_id and (accumulator.total_input or accumulator.total_output):
            log_usage(
                org_id=org_id, user_id=user_id,
                agent_name=agent_name,
                input_tokens=accumulator.total_input,
                output_tokens=accumulator.total_output,
                model_name=accumulator.model,
                request_path="/api/chat",
                duration_ms=_duration_ms,
                clarification_rounds=_clarification_rounds,
            )
        logger.info(f"⏱️  Request completed in {_duration_ms}ms | tokens: {accumulator.total_input}in/{accumulator.total_output}out | clarification: {_clarification_rounds}")

        # ── Journal: record agent outcome ──
        _agent_outcome = "clarification" if result.get("needs_clarification") else ("success" if result.get("success") else "failure")
        update_trace_outcome(request_id=_request_id, outcome=_agent_outcome, duration_ms=_duration_ms)
        record_decision(
            request_id=_request_id, org_id=org_id, user_id=user_id,
            dimension="routing",
            decision_type="agent_outcome",
            decision_value=_agent_outcome,
            duration_ms=_duration_ms,
        )

        # Handle clarification responses (don't format with presenter yet)
        if result.get("needs_clarification"):
            logger.info("❓ Agent needs clarification")

            # Safety net: if all clarifications have 0 options AND no free_text fields,
            # return text error instead of picker
            clarifications = result.get("clarifications", [])
            has_ui_clarifications = [
                c for c in clarifications
                if c.get("options") or c.get("type") == "free_text"
            ]
            if clarifications and not has_ui_clarifications:
                error_msgs = [c.get("message", "Unknown issue") for c in clarifications]
                return {"reply": "❌ Could not process the request:\n" + "\n".join(f"• {m}" for m in error_msgs)}

            # Strip internal data before sending to frontend
            _INTERNAL_KEYS = {"original_extracted", "_clean_payloads", "_pending_payloads", "_existing_map", "_policy"}
            frontend_result = {k: v for k, v in result.items() if k not in _INTERNAL_KEYS}
            frontend_result["agent"] = agent_name
            # Inject session TTL so frontend can show expiry countdown
            _clar_sid = result.get("session_id")
            if _clar_sid:
                frontend_result["expires_in"] = _session_expires_in(_clar_sid)
            return {
                "reply": result.get("message", "Please provide additional information."),
                "needs_clarification": True,
                "clarification_data": frontend_result,
                "metadata": {
                    "agent": agent_name
                }
            }

        if not result.get("success"):
            # Handle partial execution results (some succeeded, some failed)
            summary = result.get("summary")
            failed_items = result.get("failed", [])
            if summary and summary.get("total", 0) > 0:
                succeeded = summary.get("succeeded", 0)
                total = summary.get("total", 0)
                failed_count = summary.get("failed", 0)

                parts = []
                if succeeded > 0:
                    parts.append(f"**{succeeded}/{total}** operation(s) completed successfully.")
                if failed_count > 0:
                    parts.append(f"**{failed_count}** operation(s) failed:")
                    for fi in failed_items:
                        # Use 'detail' for the human-readable error, fall back to 'error' code
                        raw_err = fi.get("detail") or fi.get("error", "Unknown error")
                        err = _extract_simpro_error(raw_err)
                        # Build a context label — works for schedules AND work orders
                        sched = fi.get("schedule", {})
                        if sched:
                            label = sched.get("staff_name", f"Staff {sched.get('staff_id', '?')}")
                        elif fi.get("contractor_name"):
                            cj_id = fi.get("contractor_job_id", "")
                            label = f"CJ {cj_id} ({fi['contractor_name']})" if cj_id else fi["contractor_name"]
                        elif fi.get("contractor_job_id"):
                            label = f"CJ {fi['contractor_job_id']}"
                        elif fi.get("job_id"):
                            label = f"Job {fi['job_id']}"
                        else:
                            label = "Item"
                        parts.append(f"  - {label}: {err}")

                # Still format via presenter if there were any successes
                if succeeded > 0:
                    envelope = _format_with_presenter(data=result, question=message, llm_fn=_effective_llm_chat)
                    reply_text = "\n".join(parts)
                    return {
                        "reply": reply_text,
                        "envelope": envelope,
                        "metadata": {"agent": agent_name, "success": False, "summary": summary}
                    }
                else:
                    return {"reply": "\n".join(parts)}

            # Use crossroads error_recovery for user-friendly error messages
            raw_message = result.get("message", "")
            errors = result.get("errors", [])

            if errors:
                # Check for friendly messages already set by agent
                friendly_parts = []
                for e in errors:
                    friendly = e.get("friendly") or e.get("error", str(e))
                    friendly_parts.append(f"• {friendly}")
                error_details = "\n".join(friendly_parts)
                summary = raw_message or "Something went wrong."
                return {"reply": f"❌ {summary}\n\n{error_details}"}

            # Single error — try crossroads for a better message
            if raw_message:
                try:
                    cr_result = await resolve_crossroads(
                        crossroad_type="error_recovery",
                        question=f"Agent error: {raw_message}",
                        context={
                            "raw_error": raw_message,
                            "error_code": result.get("error", "UNKNOWN"),
                            "agent": agent_name,
                        },
                        llm_chat=_effective_llm_chat,
                    )
                    if cr_result.get("fields", {}).get("message"):
                        return {"reply": f"❌ {cr_result['fields']['message']}"}
                except Exception:
                    pass

            return {"reply": f"❌ {raw_message or result.get('error', 'Unknown error')}"}

        logger.info("📊 Formatting result with presenter...")

        # For workorder prepare phase, send wo_review_rows as primary data
        # so the presenter renders all items as the main table (with CSV/Excel).
        presenter_data = result
        extra_hints = {}
        if (
            agent_name == "workorder"
            and result.get("phase") == "prepare"
            and result.get("wo_review_rows")
        ):
            presenter_data = result["wo_review_rows"]
            extra_hints["context"] = (
                "This is a REVIEW TABLE — work orders have NOT been created yet. "
                "The user must review these items, edit the Include column, "
                "download the CSV/Excel, and re-upload to create the actual contractor jobs. "
                "Do NOT say work orders were 'created' or 'completed'. "
                "Describe this as a review sheet ready for user confirmation."
            )

        envelope = _format_with_presenter(
            data=presenter_data,
            question=message,
            extra_hints=extra_hints or None,
            llm_fn=_effective_llm_chat,
        )

        return {
            "reply": envelope.get("summary", "Results ready."),
            "envelope": envelope,
            "metadata": {
                "agent": agent_name,
                "success": result.get("success"),
                "summary": result.get("summary", {})
            }
        }


    # ========================================================================
    # STEP 2: No agent → Use MCP Client (normal chat)
    # ========================================================================

    logger.info("→ Routing to MCP Client")

    # Enrich message with follow-up context bridge for MCP path
    mcp_message = _enrich_message_with_reuse_fields(message, intent_result)

    history = user_context.get("conversation_history", [])
    enriched_history = build_enriched_history(user_context, history)

    if USE_PYTHON_EXECUTOR:
        from mcp_python_executor import get_python_executor
        _py_executor = get_python_executor(
            user_id=user_id, org_id=org_id, request_id=_request_id,
            **_org_creds,
            llm_provider=_org_llm["primary"].get("provider"),
            llm_model=_org_llm["primary"].get("model"),
            llm_api_key=_org_llm["primary"].get("api_key"),
        )
        result = await _py_executor.execute_chat(
            user_message=mcp_message,
            history=enriched_history,
        )
    else:
        result = await _call_mcp_client(message, history)

    # Update history
    if result.get("success"):
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": result.get("response", "")})
        user_context["conversation_history"] = history[-10:]

    # Log MCP path token usage from metadata
    _duration_ms = int((time.perf_counter() - _t0) * 1000)
    mcp_usage = (result.get("metadata") or {}).get("usage") or {}
    if mcp_usage and org_id:
        log_usage(
            org_id=org_id, user_id=user_id,
            agent_name="chat",
            input_tokens=mcp_usage.get("inputTokens", 0),
            output_tokens=mcp_usage.get("outputTokens", 0),
            model_name=mcp_usage.get("model", ""),
            request_path="/api/chat",
            duration_ms=_duration_ms,
        )
    logger.info(f"⏱️  MCP request completed in {_duration_ms}ms")

    response_text = result.get("response", "")

    # ---- Route structured tool results through presenter ----
    tool_calls = result.get("toolCalls") or []
    structured_data = _extract_tool_data(tool_calls)

    # ── Journal: record MCP tool alignment + outcome ──
    _tool_names = [tc.get("name", "") for tc in tool_calls if tc.get("name")]
    _mcp_outcome = "success" if result.get("success") else "failure"
    if _tool_names:
        _alignment, _align_score, _align_reason = check_intent_tool_alignment(
            intent_result.get("intent") if intent_result else None,
            _tool_names,
        )
        record_decision(
            request_id=_request_id, org_id=org_id, user_id=user_id,
            dimension="tool_alignment",
            decision_type="intent_tool_check",
            decision_value=_alignment,
            confidence=_align_score,
            reasoning=_align_reason,
            context={"intent": intent_result.get("intent") if intent_result else None, "tools": _tool_names[:10]},
        )
    update_trace_outcome(
        request_id=_request_id,
        outcome=_mcp_outcome,
        duration_ms=_duration_ms,
        tool_sequence=_tool_names,
    )

    _post_filter_applied = False

    # Generic post-execution qualifier filtering.
    # Triggered when the intent analyzer detected qualifiers that cannot be
    # expressed as URL-level API filters (e.g. department requires a lookup).
    # URL-resolvable qualifiers (Staff.Type, Status, etc.) are already applied
    # inside the executor and never reach here.
    _pq_qualifiers: Dict[str, str] = {}
    if intent_result:
        if intent_result.get("department"):
            _pq_qualifiers["department"] = intent_result["department"]
    if _pq_qualifiers and structured_data:
        try:
            from utils.mcp_tool_client import MCPToolClient
            from utils.mcp_executor import MCPToolExecutor
            _pq_executor = MCPToolExecutor(
                tool_registry=MCPToolClient(**_org_creds),
                company_id=_org_creds.get("simpro_company_id") or (current_user.get("simpro_company_id", 2) if current_user else 2),
            )
            structured_data = await apply_post_execution_qualifiers(
                structured_data, _pq_qualifiers, _pq_executor, org_id=org_id or 0
            )
            _post_filter_applied = True
        except Exception as e:
            logger.warning(f"Post-execution qualifier filter failed (non-fatal): {e}")

    # ── Update conversation history (after post-filtering so data context reflects final results) ──
    if result.get("success"):
        history_text = response_text
        if structured_data:
            data_ctx = _build_data_context(structured_data)
            if data_ctx:
                history_text = f"{response_text}\n\n{data_ctx}"
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": history_text})
        user_context["conversation_history"] = history[-10:]

    # ── Session Context: capture MCP response for cross-path follow-ups ──
    _sc_department = intent_result.get("department") if intent_result else None
    _sc_tool_names = [tc.get("name", "") for tc in tool_calls if tc.get("name")]
    _save_session_context(
        user_context,
        structured_data=structured_data,  # None when conversational
        route="mcp",
        department=_sc_department,
        tool_names=_sc_tool_names,
        intent_result=intent_result,
    )

    if structured_data:
        if _wants_summary_only(message):
            logger.info("📝 User requested summary only — skipping presenter, using LLM reply")
            return {"reply": response_text}

        logger.info("📊 MCP returned structured data — routing through presenter")
        envelope = _format_with_presenter(data=structured_data, question=message, llm_fn=_effective_llm_chat)
        return {
            "reply": response_text,
            "envelope": envelope,
        }

    # No structured data — return plain text (conversational response)
    return {"reply": response_text}


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/chat", response_model=ChatResponse)
async def handle_chat(
    message: str = Form(...),
    session_id: Optional[str] = Form(None),
    files: Optional[List[UploadFile]] = File(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Main chat endpoint (requires authentication)

    Routes:
    1. Agent mode (if keywords + files detected)
    2. MCP Client mode (default)
    """
    logger.info(f"📨 POST /chat: len={len(message)}, session_id={session_id}, files={len(files) if files else 0}")

    import uuid
    if not session_id:
        session_id = f"chat_sess_{uuid.uuid4().hex[:12]}"

    attachments = await _normalize_uploads(files, session_id)
    return await _chat_core(message, attachments, current_user=current_user)


async def _chat_sse_generator(
    message: str,
    attachments: Optional[List[Dict[str, Any]]],
    current_user: Optional[Dict[str, Any]],
):
    """
    SSE generator with progressive status updates and word-by-word streaming.
    Mirrors _chat_core routing logic but yields events at each stage.
    _chat_core() is NOT called — this IS the streaming version of it.
    """
    from utils.intent_analyzer import analyze_intent
    from utils.crossroads import resolve_crossroads
    from utils.thinking_plan import ThinkingPlan

    plan: Optional[ThinkingPlan] = None  # Agentic thinking panel (declared before try so finally can access it)
    try:
        # ── Early exits ──────────────────────────────────────────────────
        message = (message or "").strip()
        if not message:
            yield _sse_event("result", {"reply": "Please enter a message."})
            return

        _t0 = time.perf_counter()  # ← request timing
        _request_id = new_request_id()

        org_id = current_user.get("org_id") if current_user else None
        user_id = current_user.get("id") if current_user else 0
        budget_error = _check_token_budget(org_id)
        if budget_error:
            yield _sse_event("result", {"reply": budget_error})
            return

        # Load per-tenant Simpro credentials (Phase 1: multi-tenancy)
        _org_creds = _get_org_simpro_credentials(org_id)

        # Per-org LLM routing (Phase 6)
        _org_llm_sse = _get_org_llm_config(org_id)
        _primary_slot_sse = _org_llm_sse["primary"]
        _complex_slot_sse = _org_llm_sse["complex"]

        def _make_llm_fn_sse(slot):
            if any(slot.values()):
                def _fn(messages, response_format=None, temperature=0.0, **kw):
                    kw.pop("complexity", None)
                    return chat_with_override(messages, response_format=response_format,
                                              temperature=temperature,
                                              provider=slot["provider"], model=slot["model"],
                                              api_key=slot["api_key"], **kw)
                return _fn
            return llm_chat

        _primary_llm_fn_sse = _make_llm_fn_sse(_primary_slot_sse)
        _complex_llm_fn_sse = _make_llm_fn_sse(_complex_slot_sse) if any(_complex_slot_sse.values()) else _primary_llm_fn_sse

        def _effective_llm_chat_sse(messages, response_format=None, temperature=0.0, **kw):
            complexity = kw.pop("complexity", "standard")
            fn = _complex_llm_fn_sse if complexity == "high" else _primary_llm_fn_sse
            return fn(messages, response_format=response_format, temperature=temperature, **kw)

        user_context = _get_user_context(user_id, org_id)
        _prev_message = user_context.get("last_message", "")  # capture before overwrite (for follow-up filters)
        user_context["last_message"] = message
        accumulator = _TokenAccumulator(llm_fn=_effective_llm_chat_sse)
        plan: Optional[ThinkingPlan] = None  # Agentic thinking panel

        # ── STEP 1: Intent analysis ──────────────────────────────────────
        yield _sse_event("status", {"message": "Understanding your request..."})

        agent_name = None
        intent_result = None

        # 1a. File-structure detection — build file_context for intent analyzer
        _file_context = None
        if attachments:
            first_file = attachments[0]
            doc_type_hint = _detect_document_type(
                first_file.get("filename", ""),
                first_file.get("content_type", ""),
                message,
            )
            quick_extracted = await _call_extractor(attachments, doc_type_hint)
            if quick_extracted:
                _normalize_extracted_tables(quick_extracted)
                _detected = _detect_agent_from_file_structure(
                    quick_extracted, llm_chat=_effective_llm_chat_sse, user_text=message
                )
                _tbl = quick_extracted.get("tables", [{}])[0] if quick_extracted.get("tables") else {}
                _file_context = {
                    "filename": first_file.get("filename", "uploaded_file"),
                    "headers": _tbl.get("headers", []),
                    "row_count": len(_tbl.get("rows", [])),
                    "detected_agent": _detected,
                }
                agent_name = _detected  # quick-path: use header detection immediately if confident

        # 1b. LLM intent classification (always runs; file_context enriches it when present)
        history = user_context.get("conversation_history", [])
        _scratchpad = get_or_create_scratchpad(user_context)
        intent_result = analyze_intent(
            message=message,
            conversation_history=history,
            llm_chat=_effective_llm_chat_sse,
            session_context=_scratchpad.to_context_string() or None,
            file_context=_file_context,
        )
        candidate_agent = intent_result.get("agent")
        confidence = intent_result.get("confidence", 0.0)

        # ── Cancel intent: always takes precedence over file/header routing ──
        if intent_result.get("intent") == "cancel_request":
            cancelled_sid = user_context.pop("pending_clarification_sid", None)
            if cancelled_sid:
                _pending_sessions.pop(cancelled_sid, None)
                logger.info(f"🚫 User cancelled pending clarification: {cancelled_sid}")
            cancelled_maq = user_context.pop("multi_action_queue", None)
            if cancelled_maq:
                for orphan_sid in cancelled_maq.get("pending_sids", []):
                    _pending_sessions.pop(orphan_sid, None)
                logger.info(f"🚫 Cleared multi-action queue with {len(cancelled_maq.get('pending_sids', []))} pending")
            agent_name = None
            logger.info("🚫 Cancel intent detected — cleared all pending state, routing to MCP")

        # Resolve agent_name from intent result if header detection didn't find one
        elif not agent_name:
            if candidate_agent and confidence >= 0.5:
                if load_agent(candidate_agent) is not None:
                    agent_name = candidate_agent
            else:
                # Deterministic override: if there's a pending clarification session
                # and the LLM didn't route to any agent, the user is likely answering
                # the clarification (e.g., providing a bare name like "jarrad edwards").
                pending_sid = user_context.get("pending_clarification_sid")
                if pending_sid and pending_sid in _pending_sessions:
                    pending_agent = _pending_sessions[pending_sid].get("agent_name")
                    pending_action = _pending_sessions[pending_sid].get("hints", {}).get("action", "create")
                    if pending_agent and load_agent(pending_agent) is not None:
                        agent_name = pending_agent
                        intent_result = {
                            **intent_result,
                            "agent": pending_agent,
                            "action": pending_action,
                            "follow_up": True,
                            "confidence": 0.85,
                            "intent": f"{pending_agent}_crud",
                        }
                        logger.info(
                            f"🔄 Pending clarification override: routing to {agent_name} "
                            f"(session={pending_sid}, action={pending_action})"
                        )

        # ── Journal: record routing decision (SSE path) ──
        if intent_result:
            record_decision(
                request_id=_request_id, org_id=org_id, user_id=user_id,
                dimension="routing",
                decision_type="intent_analysis",
                decision_value=agent_name or "chat",
                confidence=intent_result.get("confidence", 0.0),
                reasoning=f"intent={intent_result.get('intent')}, action={intent_result.get('action')}",
                context={"follow_up": intent_result.get("follow_up", False)},
            )
            record_trace(
                request_id=_request_id, org_id=org_id, user_id=user_id,
                intent=intent_result.get("intent", ""),
                agent=agent_name or "chat",
                action=intent_result.get("action") or "",
                confidence=intent_result.get("confidence", 0.0),
                message_preview=message[:100],
            )

        # ── HISTORY GATING: suppress for standalone queries ─────────────
        is_follow_up = bool(intent_result.get("follow_up")) if intent_result else False
        effective_history = history if is_follow_up else []
        if not is_follow_up and history:
            logger.info("🧹 History gated (SSE): standalone query, %d entries hidden", len(history))

        # ── THINKING PLAN: create from intent result ─────────────────────
        plan_steps = (intent_result.get("plan_steps") or []) if intent_result else []
        if plan_steps:
            plan = ThinkingPlan(plan_steps)
            yield plan.done(0)  # First step is the "Understood: ..." summary

        # ── MULTI-ACTION ORCHESTRATION (SSE path) ─────────────────────────
        if (
            intent_result
            and intent_result.get("is_multi_action")
            and intent_result.get("sub_requests")
        ):
            from utils.multi_action_orchestrator import orchestrate_multi_action
            n_subs = len(intent_result["sub_requests"])
            logger.info(f"🔀 Multi-action detected (SSE): {n_subs} sub-requests")
            yield _sse_event("status", {"message": f"Running {n_subs} operations in parallel..."})
            if plan:
                yield plan.advance()

            result = await orchestrate_multi_action(
                sub_requests=intent_result["sub_requests"],
                original_message=message,
                attachments=attachments or [],
                current_user=current_user,
                effective_history=effective_history,
                accumulator=accumulator,
                user_context=user_context,
                run_agent_fn=_run_agent,
                org_id=org_id,
                intent_result=intent_result,
            )

            # Store in conversation history
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": _summarize_multi_action_result(result)})
            user_context["conversation_history"] = history[-10:]

            # Finish thinking plan
            if plan:
                yield plan.finish_all()

            # Handle clarification
            if result.get("needs_clarification"):
                clar_data = result.get("clarification_data", {})
                if clar_data.get("session_id"):
                    user_context["pending_clarification_sid"] = clar_data["session_id"]
                # Build multi-action clarification queue for chaining
                mac = clar_data.get("multi_action_context", {})
                remaining = mac.get("remaining_clarification_sids", [])
                if remaining or mac.get("completed_results") or mac.get("failed_results"):
                    user_context["multi_action_queue"] = {
                        "original_message": message,
                        "total_sub_requests": mac.get("total_sub_requests", 0),
                        "pending_sids": [r["session_id"] for r in remaining],
                        "pending_meta": {
                            r["session_id"]: {
                                "sub_index": r["sub_index"],
                                "description": r["description"],
                                "agent": r["agent"],
                            }
                            for r in remaining
                        },
                        "completed_results": list(mac.get("completed_results", [])),
                        "failed_results": list(mac.get("failed_results", [])),
                        "sub_descriptions": mac.get("sub_descriptions", {}),
                        # Track the first clarification's sub_index too
                        "current_sub_index": mac.get("sub_index"),
                        "current_description": mac.get("description"),
                    }
                    logger.info(
                        f"📋 Multi-action queue built: {len(remaining)} remaining "
                        f"clarifications after current, "
                        f"{len(mac.get('completed_results', []))} completed, "
                        f"{len(mac.get('failed_results', []))} failed"
                    )

                # Build merged envelope from already-completed sub-requests
                completed_envelope = _build_multi_action_partial_envelope(mac, message)
                if completed_envelope:
                    result["envelope"] = completed_envelope

                yield _sse_event("result", result)
                return

            # Log token usage
            _duration_ms = int((time.perf_counter() - _t0) * 1000)
            if org_id and (accumulator.total_input or accumulator.total_output):
                log_usage(
                    org_id=org_id, user_id=user_id,
                    agent_name="multi_action",
                    input_tokens=accumulator.total_input,
                    output_tokens=accumulator.total_output,
                    model_name=accumulator.model,
                    request_path="/api/chat/stream",
                    duration_ms=_duration_ms,
                )
            logger.info(f"⏱️  Multi-action (SSE) completed in {_duration_ms}ms")
            yield _sse_event("result", result)
            return

        # ── INPUT CONTRADICTION CHECK ────────────────────────────────────
        # LLM-powered: detects logical contradictions (e.g. "7am-1pm for 24hrs")
        # before any parsing/resolution. Returns clarification if conflicts found.
        if agent_name or (intent_result and intent_result.get("intent")):
            from utils.input_validator import validate_user_input
            detected_route = agent_name or intent_result.get("intent", "query")
            contradiction = await validate_user_input(
                user_message=message,
                detected_route=detected_route,
                llm_chat=accumulator.tracked_chat,
                conversation_history=effective_history,
            )
            if contradiction:
                # Store session for resolution
                session_id = contradiction["clarification_data"]["session_id"]
                _pending_sessions[session_id] = {
                    "created_at": time.time(),
                    "type": "contradiction",
                    "original_message": message,
                    "agent_name": agent_name,
                    "intent_result": intent_result,
                    "attachments": attachments,
                    "user_id": user_id,
                }
                if plan:
                    cur = plan.current_in_progress()
                    if cur is not None:
                        yield plan.done(cur, "Need your input")
                _contra_clar_sse = contradiction["clarification_data"]
                _contra_sid_sse = _contra_clar_sse.get("session_id")
                if _contra_sid_sse:
                    _contra_clar_sse["expires_in"] = _session_expires_in(_contra_sid_sse)
                yield _sse_event("result", {
                    "reply": contradiction.get("message", "Your request has conflicting information."),
                    "needs_clarification": True,
                    "clarification_data": _contra_clar_sse,
                    "metadata": {"agent": agent_name or "chat"},
                })
                return

        # ── AGENT PATH ───────────────────────────────────────────────────
        if agent_name:
            if org_id and not is_agent_enabled_for_org(org_id, agent_name):
                yield _sse_event("result", {
                    "reply": f"The {agent_name} agent is not available on your current plan. "
                             f"Please contact your administrator to upgrade."
                })
                return

            # Per-agent token budget check
            agent_budget_error = _check_agent_token_budget(org_id, agent_name)
            if agent_budget_error:
                yield _sse_event("result", {"reply": agent_budget_error})
                return

            # Per-role operation permission check (Phase 4)
            sse_operation = (intent_result.get("action") if intent_result else None) or "query"
            if user_id and org_id and not is_operation_allowed_for_user(user_id, org_id, agent_name, sse_operation):
                yield _sse_event("result", {
                    "reply": f"Your role does not have permission to {sse_operation} {agent_name.replace('_', ' ')} records. "
                             f"Please contact your administrator."
                })
                return

            yield _sse_event("status", {"message": f"Running {agent_name} agent..."})
            # Advance thinking plan: start next pending step (e.g. "Looking up staff...")
            if plan:
                yield plan.advance()

            history = user_context.get("conversation_history", [])
            intent_follow_up = is_follow_up

            # Retry shortcut
            cached = _last_failed_ops.get((user_id, org_id or 0))
            result = None
            if (
                intent_follow_up
                and cached
                and cached["agent_name"] == agent_name
                and time.time() - cached.get("timestamp", 0) < 300
            ):
                yield _sse_event("status", {"message": "Retrying previous operation..."})
                cached_agent_result = cached["agent_result"]
                cached_company_id = cached["company_id"]
                try:
                    if agent_name == "schedule" and cached_agent_result.get("schedules"):
                        execution_result = await execute_schedule_operations(
                            agent_result=cached_agent_result,
                            company_id=cached_company_id,
                            llm_chat=accumulator.tracked_chat,
                        )
                        result = _build_execution_response(
                            "schedule", execution_result, cached_agent_result, user_id, org_id, cached_company_id
                        )
                    elif agent_name == "workorder" and (
                        cached_agent_result.get("contractor_jobs")
                        or cached_agent_result.get("contractor_job_updates")
                        or cached_agent_result.get("contractor_job_deletes")
                    ):
                        execution_result = await execute_workorder_operations(
                            agent_result=cached_agent_result,
                            company_id=cached_company_id,
                            llm_chat=accumulator.tracked_chat,
                        )
                        result = _build_execution_response(
                            "workorder", execution_result, cached_agent_result, user_id, org_id, cached_company_id
                        )
                    elif agent_name == "invoice" and (
                        cached_agent_result.get("jobs")
                        or cached_agent_result.get("invoice_updates")
                        or cached_agent_result.get("invoice_deletes")
                    ):
                        if cached_agent_result.get("jobs"):
                            execution_result = await create_invoices_from_agent_result(
                                agent_result=cached_agent_result,
                                company_id=cached_company_id,
                                llm_chat=accumulator.tracked_chat,
                            )
                        elif cached_agent_result.get("invoice_updates"):
                            execution_result = await update_invoices_from_agent_result(
                                agent_result=cached_agent_result,
                                company_id=cached_company_id,
                                llm_chat=accumulator.tracked_chat,
                            )
                        elif cached_agent_result.get("invoice_deletes"):
                            execution_result = await delete_invoices_from_agent_result(
                                agent_result=cached_agent_result,
                                company_id=cached_company_id,
                                llm_chat=accumulator.tracked_chat,
                            )
                        result = _build_execution_response(
                            "invoice", execution_result, cached_agent_result, user_id, org_id, cached_company_id
                        )
                except Exception as e:
                    logger.error(f"SSE retry failed: {e}")
                    result = {"success": False, "error": "RETRY_FAILED", "message": f"Retry failed: {e}"}

            # ── Clarification follow-up: re-run with original context ─────
            if result is None and intent_follow_up:
                pending_sid = user_context.get("pending_clarification_sid")
                if pending_sid and pending_sid in _pending_sessions:
                    session = _pending_sessions[pending_sid]
                    if session.get("agent_name") == agent_name:
                        logger.info(
                            f"🔄 SSE follow-up with pending clarification {pending_sid} — "
                            f"re-running {agent_name} with original request"
                        )
                        original_action = session.get("hints", {}).get("action", "create")
                        _session_ctx = _build_session_context(user_context, True)
                        follow_up_history = list(history)
                        follow_up_history.append({"role": "user", "content": message})
                        result = await _run_agent(
                            agent_name=agent_name,
                            user_text=session["user_text"],
                            attachments=[],
                            conversation_history=filter_history(follow_up_history, agent_name),
                            intent_action=original_action,
                            intent_follow_up=True,
                            current_user=current_user,
                            llm_chat_fn=accumulator.tracked_chat,
                            session_context=_session_ctx,
                            reuse_fields=intent_result.get("reuse_fields") if intent_result else None,
                            changed_fields=intent_result.get("changed_fields") if intent_result else None,
                        )
                        _pending_sessions.pop(pending_sid, None)
                        user_context.pop("pending_clarification_sid", None)

            # Normal path: full agent run
            if result is None:
                # Clear stale pending session when user starts a fresh request
                if not intent_follow_up:
                    stale_sid = user_context.pop("pending_clarification_sid", None)
                    if stale_sid:
                        _pending_sessions.pop(stale_sid, None)
                        logger.info(f"🗑️ Cleared abandoned clarification session: {stale_sid}")
                    # Also clear any stale multi-action queue + its pending sessions
                    stale_maq = user_context.pop("multi_action_queue", None)
                    if stale_maq:
                        for orphan_sid in stale_maq.get("pending_sids", []):
                            _pending_sessions.pop(orphan_sid, None)
                        logger.info(f"🗑️ Cleared abandoned multi-action queue with {len(stale_maq.get('pending_sids', []))} pending")

                _session_ctx = _build_session_context(user_context, intent_follow_up)
                result = await _run_agent(
                    agent_name, message, attachments or [],
                    conversation_history=filter_history(effective_history, agent_name),
                    intent_action=intent_result.get("action") if intent_result else None,
                    intent_follow_up=intent_follow_up,
                    current_user=current_user,
                    llm_chat_fn=accumulator.tracked_chat,
                    session_context=_session_ctx,
                    reuse_fields=intent_result.get("reuse_fields") if intent_result else None,
                    changed_fields=intent_result.get("changed_fields") if intent_result else None,
                )

            # Update conversation history
            summary_text = _summarize_agent_result(agent_name, result, user_text=message)
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": summary_text})
            scratchpad = get_or_create_scratchpad(user_context)
            scratchpad.extract_from_agent_result(agent_name, result, summary_text, message)
            user_context["conversation_history"] = await update_context_after_turn(
                user_context, history, llm_chat_fn=accumulator.tracked_chat,
            )

            # ── Session Context: capture agent result for cross-path follow-ups ──
            if result.get("success"):
                _agent_data = result
                _save_session_context(
                    user_context,
                    structured_data=_agent_data,
                    route=agent_name,
                    department=intent_result.get("department") if intent_result else None,
                    intent_result=intent_result,
                )

            _duration_ms = int((time.perf_counter() - _t0) * 1000)
            _clarification_rounds = 1 if result.get("needs_clarification") else 0
            if org_id and (accumulator.total_input or accumulator.total_output):
                log_usage(
                    org_id=org_id, user_id=user_id,
                    agent_name=agent_name,
                    input_tokens=accumulator.total_input,
                    output_tokens=accumulator.total_output,
                    model_name=accumulator.model,
                    request_path="/api/chat/stream",
                    duration_ms=_duration_ms,
                    clarification_rounds=_clarification_rounds,
                )
            logger.info(f"⏱️  Stream request completed in {_duration_ms}ms | tokens: {accumulator.total_input}in/{accumulator.total_output}out | clarification: {_clarification_rounds}")

            # ── Journal: record agent outcome (SSE path) ──
            _agent_outcome = "clarification" if result.get("needs_clarification") else ("success" if result.get("success") else "failure")
            update_trace_outcome(request_id=_request_id, outcome=_agent_outcome, duration_ms=_duration_ms)
            record_decision(
                request_id=_request_id, org_id=org_id, user_id=user_id,
                dimension="routing",
                decision_type="agent_outcome",
                decision_value=_agent_outcome,
                duration_ms=_duration_ms,
            )

            # Track pending clarification session for follow-up detection
            if result.get("needs_clarification") and result.get("session_id"):
                user_context["pending_clarification_sid"] = result["session_id"]
            elif not result.get("needs_clarification"):
                stale_sid = user_context.pop("pending_clarification_sid", None)
                if stale_sid:
                    _pending_sessions.pop(stale_sid, None)

            # ── Clarification: send as single result event ───────────────
            if result.get("needs_clarification"):
                # Mark all thinking steps as done so the persisted panel
                # shows "Completed" with a checkmark, not a spinner.
                if plan:
                    cur = plan.current_in_progress()
                    if cur is not None:
                        yield plan.done(cur, "Need your input")
                    # Skip remaining pending steps
                    while plan.next_pending() is not None:
                        yield plan.skip(plan.next_pending(), "Waiting for input")

                clarifications = result.get("clarifications", [])
                has_ui_clarifications = [
                    c for c in clarifications
                    if c.get("options") or c.get("type") == "free_text"
                ]
                if clarifications and not has_ui_clarifications:
                    error_msgs = [c.get("message", "Unknown issue") for c in clarifications]
                    yield _sse_event("result", {
                        "reply": "Could not process the request:\n" + "\n".join(f"• {m}" for m in error_msgs)
                    })
                    return

                _INTERNAL_KEYS = {"original_extracted", "_clean_payloads", "_pending_payloads", "_existing_map", "_policy"}
                frontend_result = {k: v for k, v in result.items() if k not in _INTERNAL_KEYS}
                frontend_result["agent"] = agent_name
                _clar_sid_sse = result.get("session_id")
                if _clar_sid_sse:
                    frontend_result["expires_in"] = _session_expires_in(_clar_sid_sse)
                yield _sse_event("result", {
                    "reply": result.get("message", "Please provide additional information."),
                    "needs_clarification": True,
                    "clarification_data": frontend_result,
                    "metadata": {"agent": agent_name},
                })
                return

            # ── Failure path ─────────────────────────────────────────────
            if not result.get("success"):
                if plan:
                    cur = plan.current_in_progress()
                    if cur is not None:
                        yield plan.fail(cur, "Something went wrong")
                    else:
                        # Mark last pending step as failed
                        nxt = plan.next_pending()
                        if nxt is not None:
                            yield plan.fail(nxt, "Something went wrong")
                summary = result.get("summary")
                failed_items = result.get("failed", [])
                if summary and summary.get("total", 0) > 0:
                    succeeded = summary.get("succeeded", 0)
                    total = summary.get("total", 0)
                    failed_count = summary.get("failed", 0)
                    parts = []
                    if succeeded > 0:
                        parts.append(f"**{succeeded}/{total}** operation(s) completed successfully.")
                    if failed_count > 0:
                        parts.append(f"**{failed_count}** operation(s) failed:")
                        for fi in failed_items:
                            raw_err = fi.get("detail") or fi.get("error", "Unknown error")
                            err = _extract_simpro_error(raw_err)
                            sched = fi.get("schedule", {})
                            if sched:
                                label = sched.get("staff_name", f"Staff {sched.get('staff_id', '?')}")
                            elif fi.get("contractor_name"):
                                cj_id = fi.get("contractor_job_id", "")
                                label = f"CJ {cj_id} ({fi['contractor_name']})" if cj_id else fi["contractor_name"]
                            elif fi.get("contractor_job_id"):
                                label = f"CJ {fi['contractor_job_id']}"
                            elif fi.get("job_id"):
                                label = f"Job {fi['job_id']}"
                            else:
                                label = "Item"
                            parts.append(f"  - {label}: {err}")

                    reply_text = "\n".join(parts)
                    if succeeded > 0:
                        yield _sse_event("status", {"message": "Formatting results..."})
                        envelope = _format_with_presenter(data=result, question=message, llm_fn=_effective_llm_chat_sse)
                        async for evt in _stream_text_words(reply_text):
                            yield evt
                        yield _sse_event("envelope", {"envelope": envelope})
                    else:
                        async for evt in _stream_text_words(reply_text):
                            yield evt
                        yield _sse_event("envelope", {"envelope": None})
                    return

                raw_message = result.get("message", "")
                errors = result.get("errors", [])
                if errors:
                    friendly_parts = [f"• {e.get('friendly') or e.get('error', str(e))}" for e in errors]
                    error_text = f"❌ {raw_message or 'Something went wrong.'}\n\n" + "\n".join(friendly_parts)
                    async for evt in _stream_text_words(error_text):
                        yield evt
                    yield _sse_event("envelope", {"envelope": None})
                    return

                if raw_message:
                    try:
                        cr_result = await resolve_crossroads(
                            crossroad_type="error_recovery",
                            question=f"Agent error: {raw_message}",
                            context={
                                "raw_error": raw_message,
                                "error_code": result.get("error", "UNKNOWN"),
                                "agent": agent_name,
                            },
                            llm_chat=_effective_llm_chat_sse,
                        )
                        if cr_result.get("fields", {}).get("message"):
                            async for evt in _stream_text_words("❌ " + cr_result["fields"]["message"]):
                                yield evt
                            yield _sse_event("envelope", {"envelope": None})
                            return
                    except Exception:
                        pass

                async for evt in _stream_text_words("❌ " + (raw_message or result.get("error", "Unknown error"))):
                    yield evt
                yield _sse_event("envelope", {"envelope": None})
                return

            # ── Success: format with presenter then stream ────────────────
            yield _sse_event("status", {"message": "Formatting results..."})
            # Advance to final "Preparing summary" step
            if plan:
                yield plan.advance()

            presenter_data = result
            extra_hints = {}
            if (
                agent_name == "workorder"
                and result.get("phase") == "prepare"
                and result.get("wo_review_rows")
            ):
                presenter_data = result["wo_review_rows"]
                extra_hints["context"] = (
                    "This is a REVIEW TABLE — work orders have NOT been created yet. "
                    "The user must review these items, edit the Include column, "
                    "download the CSV/Excel, and re-upload to create the actual contractor jobs. "
                    "Do NOT say work orders were 'created' or 'completed'. "
                    "Describe this as a review sheet ready for user confirmation."
                )

            envelope = _format_with_presenter(
                data=presenter_data,
                question=message,
                extra_hints=extra_hints or None,
                llm_fn=_effective_llm_chat_sse,
            )

            reply_text = envelope.get("summary", "Results ready.")
            # Finish all remaining thinking steps
            if plan:
                yield plan.finish_all()
            async for evt in _stream_text_words(reply_text):
                yield evt
            yield _sse_event("envelope", {"envelope": envelope})
            return

        # ── MCP PATH (True LLM streaming via Node.js SSE) ────────────────

        # ── INPUT CONTRADICTION CHECK ────────────────────────────────────
        # Same check as the agent path — catch logical impossibilities
        # (e.g. "show invoices from last week created today") before the
        # LLM orchestration loop starts. Non-blocking: failures pass through.
        from utils.input_validator import validate_user_input
        contradiction = await validate_user_input(
            user_message=message,
            detected_route="query",
            llm_chat=accumulator.tracked_chat,
            conversation_history=effective_history,
        )
        if contradiction:
            session_id = contradiction["clarification_data"]["session_id"]
            _pending_sessions[session_id] = {
                "created_at": time.time(),
                "type": "contradiction",
                "original_message": message,
                "agent_name": None,
                "intent_result": intent_result,
                "attachments": [],
                "user_id": user_id,
            }
            user_context["pending_clarification_sid"] = session_id
            if plan:
                cur = plan.current_in_progress()
                if cur is not None:
                    yield plan.done(cur, "Need your input")
            _clar_data = contradiction["clarification_data"]
            _clar_sid = _clar_data.get("session_id")
            if _clar_sid:
                _clar_data["expires_in"] = _session_expires_in(_clar_sid)
            yield _sse_event("result", {
                "reply": contradiction.get("message", "Your request has conflicting information."),
                "needs_clarification": True,
                "clarification_data": _clar_data,
                "metadata": {"agent": "chat"},
            })
            yield _sse_event("done", {"plan": plan.snapshot if plan else None})
            return

        yield _sse_event("status", {"message": "Searching Simpro data..."})
        # Start next thinking step for MCP queries
        if plan:
            yield plan.advance()

        # Enrich message with follow-up context bridge for MCP path
        mcp_message = _enrich_message_with_reuse_fields(message, intent_result)

        history = user_context.get("conversation_history", [])
        # Build enriched history with running summary + scratchpad for MCP path
        enriched_history = build_enriched_history(user_context, history)

        # Stream from MCP executor (Python or Node.js depending on flag)
        response_text = ""
        mcp_result = None
        got_any_tokens = False

        # If a department filter will be applied, suppress live token forwarding.
        # The LLM generates its summary on unfiltered data, which would show the
        # wrong count/items to the user. Buffering the text (not forwarding tokens)
        # means the user sees only the presenter's correct summary from the envelope.
        _dept_intent = intent_result.get("department") if intent_result else None
        _suppress_token_stream = bool(_dept_intent)

        if USE_PYTHON_EXECUTOR:
            # ── Python executor path ──────────────────────────────────────────
            try:
                from mcp_python_executor import get_python_executor
                _py_executor = get_python_executor(
                    user_id=user_id, org_id=org_id, request_id=_request_id,
                    **_org_creds,
                    llm_provider=_org_llm_sse["primary"].get("provider"),
                    llm_model=_org_llm_sse["primary"].get("model"),
                    llm_api_key=_org_llm_sse["primary"].get("api_key"),
                )
                async for evt in _py_executor.execute_chat_stream(
                    user_message=mcp_message,
                    history=enriched_history,
                ):
                    if evt["type"] == "status":
                        yield _sse_event("status", {"message": evt.get("message", "")})
                    elif evt["type"] == "token":
                        text = evt.get("text", "")
                        response_text += text
                        if not _suppress_token_stream:
                            got_any_tokens = True
                            yield _sse_event("token", {"text": text})
                    elif evt["type"] == "thinking":
                        if plan:
                            ev = evt.get("event")
                            if ev == "tools_start":
                                yield plan.advance()
                            elif ev == "answer_ready":
                                yield plan.finish_all()
                    elif evt["type"] == "result":
                        # ── MCP clarification: surface immediately to frontend ──
                        if evt.get("clarification"):
                            clar = evt["clarification"]
                            sid = clar.get("session_id")
                            if sid:
                                # Mirror into _pending_sessions so user_context
                                # tracking (pending_clarification_sid) works the
                                # same way as the agent path.
                                from mcp_python_executor import _mcp_pending_sessions
                                if sid in _mcp_pending_sessions:
                                    _pending_sessions[sid] = _mcp_pending_sessions[sid]
                                user_context["pending_clarification_sid"] = sid
                            yield _sse_event("result", {
                                "reply": "I found multiple matches. Please select the one you meant.",
                                "needs_clarification": True,
                                "clarification_data": clar,
                            })
                            yield _sse_event("done", {"plan": plan.snapshot if plan else None})
                            return
                        mcp_result = {
                            "success": evt.get("success", True),
                            "response": evt.get("response", response_text),
                            "toolCalls": evt.get("toolCalls", []),
                            "metadata": evt.get("metadata", {}),
                        }
                        if not got_any_tokens:
                            response_text = mcp_result.get("response", "")
                    elif evt["type"] == "done":
                        pass
            except Exception as e:
                logger.error(f"Python executor stream error: {e}", exc_info=True)
                mcp_result = mcp_result or {}
        else:
            # ── Node.js path (original) ───────────────────────────────────────
            try:
                client = get_mcp_pool()
                async with client.stream(
                    "POST",
                    f"{MCP_CLIENT_URL}/api/chat/stream",
                    json={"message": message, "history": history or []},
                ) as stream_resp:
                    stream_resp.raise_for_status()
                    sse_buf = ""

                    async for raw_chunk in stream_resp.aiter_text():
                        sse_buf += raw_chunk
                        parts = sse_buf.split("\n\n")
                        sse_buf = parts.pop()

                        for part in parts:
                            if not part.strip():
                                continue
                            lines = part.strip().split("\n")
                            evt_type = ""
                            evt_data = ""
                            for ln in lines:
                                if ln.startswith("event: "):
                                    evt_type = ln[7:].strip()
                                elif ln.startswith("data: "):
                                    evt_data = ln[6:]

                            if not evt_type or not evt_data:
                                continue

                            if evt_type == "status":
                                yield _sse_event("status", json.loads(evt_data))
                            elif evt_type == "token":
                                payload = json.loads(evt_data)
                                response_text += payload.get("text", "")
                                if not _suppress_token_stream:
                                    got_any_tokens = True
                                    yield _sse_event("token", payload)
                            elif evt_type == "result":
                                mcp_result = json.loads(evt_data)
                                if not got_any_tokens:
                                    response_text = mcp_result.get("response", "")
                            elif evt_type == "done":
                                pass

            except Exception as e:
                logger.error(f"MCP stream error: {e}", exc_info=True)
                if not got_any_tokens:
                    result = await _call_mcp_client(mcp_message, filter_history(effective_history, "mcp"))
                    response_text = result.get("response", "")
                    mcp_result = result
                else:
                    mcp_result = mcp_result or {}

        # Log usage
        _duration_ms = int((time.perf_counter() - _t0) * 1000)
        mcp_usage = (mcp_result.get("metadata") or {}).get("usage") or {} if mcp_result else {}
        if mcp_usage and org_id:
            log_usage(
                org_id=org_id, user_id=user_id,
                agent_name="chat",
                input_tokens=mcp_usage.get("inputTokens", 0),
                output_tokens=mcp_usage.get("outputTokens", 0),
                model_name=mcp_usage.get("model", ""),
                request_path="/api/chat/stream",
                duration_ms=_duration_ms,
            )
        logger.info(f"⏱️  MCP stream request completed in {_duration_ms}ms")

        # Advance thinking plan: MCP data received, move to processing/formatting
        if plan:
            yield plan.advance()

        # Check for structured data → presenter
        tool_calls = (mcp_result.get("toolCalls") or []) if mcp_result else []
        structured_data = _extract_tool_data(tool_calls)

        # ── Journal: record MCP tool alignment + outcome (SSE path) ──
        _tool_names = [tc.get("name", "") for tc in tool_calls if tc.get("name")]
        _mcp_outcome = "success" if (mcp_result and mcp_result.get("success")) else "failure"
        if _tool_names:
            _alignment, _align_score, _align_reason = check_intent_tool_alignment(
                intent_result.get("intent") if intent_result else None,
                _tool_names,
            )
            record_decision(
                request_id=_request_id, org_id=org_id, user_id=user_id,
                dimension="tool_alignment",
                decision_type="intent_tool_check",
                decision_value=_alignment,
                confidence=_align_score,
                reasoning=_align_reason,
                context={"intent": intent_result.get("intent") if intent_result else None, "tools": _tool_names[:10]},
            )
        update_trace_outcome(
            request_id=_request_id,
            outcome=_mcp_outcome,
            duration_ms=_duration_ms,
            tool_sequence=_tool_names,
        )
        # Mirror agent path: record outcome as a decision_journal row too.
        # update_trace_outcome writes to request_traces; this writes to decision_journal
        # so the capability radar can query MCP outcomes the same way as agent outcomes.
        record_decision(
            request_id=_request_id, org_id=org_id, user_id=user_id,
            dimension="routing",
            decision_type="mcp_outcome",
            decision_value=_mcp_outcome,
            duration_ms=_duration_ms,
        )

        _post_filter_applied = False

        # Generic post-execution qualifier filtering.
        _pq_qualifiers: Dict[str, str] = {}
        if intent_result:
            if intent_result.get("department"):
                _pq_qualifiers["department"] = intent_result["department"]
        if _pq_qualifiers and structured_data:
            try:
                from utils.mcp_tool_client import MCPToolClient
                from utils.mcp_executor import MCPToolExecutor
                _pq_executor = MCPToolExecutor(
                    tool_registry=MCPToolClient(**_org_creds),
                    company_id=_org_creds.get("simpro_company_id") or (current_user.get("simpro_company_id", 2) if current_user else 2),
                )
                structured_data = await apply_post_execution_qualifiers(
                    structured_data, _pq_qualifiers, _pq_executor, org_id=org_id or 0
                )
                _post_filter_applied = True
            except Exception as e:
                logger.warning(f"Post-execution qualifier filter failed (non-fatal): {e}")

        # ── Update conversation history (after post-filtering so data context reflects final results) ──
        if mcp_result and mcp_result.get("success"):
            history_text = response_text
            if structured_data:
                data_ctx = _build_data_context(structured_data)
                if data_ctx:
                    history_text = f"{response_text}\n\n{data_ctx}"
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": history_text})
            # Populate scratchpad with entity IDs from MCP response
            scratchpad = get_or_create_scratchpad(user_context)
            scratchpad.extract_from_mcp_response(history_text)
            # Update running summary + cap history (mirrors agent path)
            user_context["conversation_history"] = await update_context_after_turn(
                user_context, history, llm_chat_fn=accumulator.tracked_chat,
            )

        # ── Session Context: capture MCP response for cross-path follow-ups ──
        _sc_department = intent_result.get("department") if intent_result else None
        _sc_tool_names = [tc.get("name", "") for tc in tool_calls if tc.get("name")]
        _save_session_context(
            user_context,
            structured_data=structured_data,  # None when conversational
            route="mcp",
            department=_sc_department,
            tool_names=_sc_tool_names,
            intent_result=intent_result,
        )

        if structured_data:
            if _wants_summary_only(message):
                logger.info("📝 User requested summary only — skipping presenter")
                if not got_any_tokens and response_text:
                    async for evt in _stream_text_words(response_text):
                        yield evt
                yield _sse_event("envelope", {"envelope": None})
                return

            yield _sse_event("status", {"message": "Formatting results..."})
            if plan:
                yield plan.advance()
            # Pass the streamed LLM text as a draft hint so _llm_summary()
            # rewrites it with personality rules.  BUT if post-filters changed
            # the data the draft is stale (generated on unfiltered records) —
            # skip it so the presenter summarises from the filtered payload.
            _use_draft = response_text.strip() and not _post_filter_applied
            envelope = _format_with_presenter(
                data=structured_data,
                question=message,
                extra_hints={"llm_draft": response_text.strip()} if _use_draft else None,
                llm_fn=_effective_llm_chat_sse,
            )

            # If no tokens were streamed (fallback path), stream text now.
            # Skip when token streaming was suppressed due to department filtering —
            # the LLM text reflects unfiltered data; the envelope summary is correct.
            if plan:
                yield plan.finish_all()
            if not got_any_tokens and response_text and not _post_filter_applied:
                async for evt in _stream_text_words(response_text):
                    yield evt
            yield _sse_event("envelope", {"envelope": envelope})
            return

        # Plain conversational response
        if plan:
            yield plan.finish_all()
        if not got_any_tokens and response_text:
            # Fallback: fake-stream word by word
            async for evt in _stream_text_words(response_text):
                yield evt

        if response_text or got_any_tokens:
            yield _sse_event("envelope", {"envelope": None})
        else:
            yield _sse_event("result", {"reply": "No response from server."})

    except Exception as e:
        logger.error(f"SSE generator error: {e}", exc_info=True)
        yield _sse_event("result", {"reply": f"An error occurred: {str(e)}"})
    finally:
        yield _sse_event("done", {"plan": plan.snapshot if plan else None})


@router.post("/chat/stream")
async def handle_chat_stream(
    message: str = Form(...),
    session_id: Optional[str] = Form(None),
    files: Optional[List[UploadFile]] = File(None),
    current_user: dict = Depends(get_current_user),
):
    """SSE streaming version of /api/chat. Returns progressive status updates."""
    logger.info(f"📨 POST /chat/stream: len={len(message)}, session_id={session_id}, files={len(files) if files else 0}")
    
    import uuid
    if not session_id:
        session_id = f"chat_sess_{uuid.uuid4().hex[:12]}"

    attachments = await _normalize_uploads(files, session_id)
    return StreamingResponse(
        _chat_sse_generator(message, attachments, current_user),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/chat-json", response_model=ChatResponse)
async def handle_chat_json(
    request: ChatRequest,
    current_user: dict = Depends(get_current_user),
):
    """JSON-only endpoint (requires authentication)"""
    return await _chat_core(request.message, None, current_user=current_user)


@router.get("/health")
async def health_check():
    """Health check"""
    mcp_status = "unknown"
    extractor_status = "unknown"
    
    try:
        hc = get_health_pool()
        r = await hc.get(f"{MCP_CLIENT_URL}/health")
        mcp_status = "healthy" if r.status_code == 200 else "unhealthy"
    except Exception:
        mcp_status = "unreachable"

    try:
        hc = get_health_pool()
        r = await hc.get(EXTRACTOR_URL.replace("/extract", "/health"))
        extractor_status = "healthy" if r.status_code == 200 else "unhealthy"
    except Exception:
        extractor_status = "unreachable"
    
    return {
        "status": "healthy",
        "service": "chatbox-backend-api",
        "version": "2.1.0-agent-first",
        "agents": list(AGENT_REGISTRY.keys()),
        "mcp_client": {"url": MCP_CLIENT_URL, "status": mcp_status},
        "extractor": {"url": EXTRACTOR_URL, "status": extractor_status}
    }


@router.get("/templates/schedule")
async def download_schedule_template():
    """Download Excel template for schedule bulk operations"""
    template_path = Path(__file__).parent.parent / "static" / "templates" / "schedule_template.xlsx"

    if not template_path.exists():
        raise HTTPException(status_code=404, detail="Template not found")

    return FileResponse(
        path=template_path,
        filename="schedule_template.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@router.get("/schedule/download-corrected/{session_id}")
async def download_corrected_template(session_id: str):
    """
    Download corrected Excel template with user's data and highlighted issues.

    This endpoint is called when there are >5 clarifications (file download mode).
    The corrected Excel file should be pre-generated and stored temporarily.
    """
    corrected_path = Path(__file__).parent.parent / "static" / "temp" / f"corrected_{session_id}.xlsx"

    if not corrected_path.exists():
        # Try to generate from session data if available
        session = _pending_sessions.get(session_id)
        if session and session.get("extracted"):
            try:
                from tools.generate_corrected_template import generate_corrected_template
                # Retrieve clarifications from the agent result stored in session
                # The clarifications are not stored in _pending_sessions, so fall back
                logger.warning(f"Corrected template not pre-generated for {session_id}, generating on-demand")
            except Exception as e:
                logger.warning(f"On-demand template generation failed: {e}")

        if not corrected_path.exists():
            logger.warning(f"Corrected template not found for session {session_id}, returning base template")
            template_path = Path(__file__).parent.parent / "static" / "templates" / "schedule_template.xlsx"
            return FileResponse(
                path=template_path,
                filename=f"schedule_corrected_{session_id}.xlsx",
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

    return FileResponse(
        path=corrected_path,
        filename=f"schedule_corrected_{session_id}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@router.post("/contradiction/clarify/{session_id}")
async def handle_contradiction_clarify(
    session_id: str,
    body: ClarifyRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Resolve input contradictions and re-enter the normal pipeline.

    The user picked which value they meant for each contradiction.
    We ask the LLM to rewrite the original message with contradictions resolved,
    then re-run through _chat_core as if the user sent the clean message.
    """
    logger.info(f"📝 Contradiction resolution for session: {session_id}")

    session = _pending_sessions.pop(session_id, None)
    if not session:
        return {
            "reply": "This clarification session has expired. Please re-send your original message to start fresh.",
            "session_expired": True,
        }

    # Clear the pending clarification tracker for this user
    user_id = current_user.get("id", 0)
    user_ctx = _get_user_context(user_id, current_user.get("org_id"))
    if user_ctx.get("pending_clarification_sid") == session_id:
        user_ctx.pop("pending_clarification_sid", None)

    original_message = session["original_message"]
    attachments = session.get("attachments") or []

    # Build resolution context from user selections
    resolutions = []
    for row_key, fields in body.clarifications.items():
        for field, value in fields.items():
            resolutions.append({"field": field, "chosen": value})

    # Ask LLM to rewrite the original message with contradictions resolved
    import json as _json
    rewrite_messages = [
        {
            "role": "system",
            "content": (
                "The user sent a message with conflicting information. "
                "They have now clarified which value they meant for each conflict. "
                "Rewrite the original message keeping everything the same but REMOVING "
                "the part they did NOT choose and keeping ONLY their chosen value. "
                "Do NOT include both the time range and the duration — only the one they picked. "
                "Output ONLY the rewritten message, nothing else."
            ),
        },
        {
            "role": "user",
            "content": _json.dumps({
                "original_message": original_message,
                "user_resolutions": resolutions,
            }),
        },
    ]

    _contradiction_llm_fn = _make_org_llm_fn(current_user.get("org_id"))
    try:
        rewritten = _contradiction_llm_fn(rewrite_messages, temperature=0.0)
        rewritten = rewritten.strip().strip('"').strip("'")
    except Exception as e:
        logger.error(f"Contradiction rewrite failed: {e}")
        rewritten = original_message  # Fallback: use original

    logger.info(f"✏️  Rewritten message: {rewritten[:120]}")

    # Re-enter the normal chat pipeline — skip validation to prevent loop
    return await _chat_core(rewritten, attachments, current_user=current_user, skip_validation=True)


async def _resolve_custom_clarification(
    custom_entries: list,
    row_selections: dict,
    session: dict,
) -> dict:
    """
    Use LLM to interpret custom 'Other' clarification inputs.

    For each custom entry, sends context to resolve_crossroads() which returns
    the correct field(s) and value(s) to merge into the row.
    """
    from utils.crossroads import resolve_crossroads

    for entry in custom_entries:
        row_str = entry.get("row", "")
        field = entry.get("field", "")
        custom_text = entry.get("value", "")

        if not custom_text or not field:
            continue

        # Get the original clarification options that were shown
        last_result = session.get("_last_agent_result", {})
        clarifications = last_result.get("clarifications", [])
        try:
            row_num = int(row_str)
        except (ValueError, TypeError):
            row_num = 0
        matching_clar = next(
            (c for c in clarifications if c.get("row") == row_num and c.get("field") == field),
            {},
        )
        options_shown = matching_clar.get("options", [])

        context = {
            "original_user_request": session.get("user_text", ""),
            "field_being_clarified": field,
            "options_shown": [{"id": o["id"], "name": o["name"]} for o in options_shown[:10]],
            "user_custom_input": custom_text,
            "agent": session.get("agent_name", ""),
        }

        result = await resolve_crossroads(
            crossroad_type="clarification_custom",
            question=(
                f"The user was asked to clarify the '{field}' field. "
                f"Instead of picking from the dropdown, they typed: \"{custom_text}\". "
                f"Determine what field and value this represents. "
                f"Return a JSON with the resolved field(s) and value(s). "
                f"If it's a numeric ID for the same entity type, use the ID field "
                f"(e.g. {{\"StaffID\": 4032}}). "
                f"If it's a name, use the Name field "
                f"(e.g. {{\"StaffName\": \"Nicholas Gubby\"}}). "
                f"If the user is correcting the entity TYPE "
                f"(e.g., 'its actually a job id not a site'), return the corrected field "
                f"(e.g. {{\"JobID\": 20527}})."
            ),
            context=context,
            llm_chat=llm_chat,
        )

        decision = result.get("decision", "resolved")

        # ── Skip: user wants to skip this row ──
        if decision == "skip":
            row_selections.setdefault(row_str, {})["__skip__"] = True
            row_selections[row_str].pop(f"{field}__custom", None)
            logger.info(f"🚫 Custom clarification: user requested skip for row {row_str} ('{custom_text}')")
            continue

        # ── Cancel all: user wants to cancel the entire operation ──
        if decision == "cancel_all":
            row_selections["__cancel_all__"] = True
            logger.info(f"🚫 Custom clarification: user requested cancel all ('{custom_text}')")
            return row_selections

        # ── Redirect: user wants to change the request direction ──
        if decision == "redirect":
            new_intent = result.get("new_intent", custom_text)
            row_selections["__redirect__"] = new_intent
            row_selections.setdefault(row_str, {}).pop(f"{field}__custom", None)
            logger.info(f"🔀 Custom clarification: user wants to redirect — '{new_intent}'")
            return row_selections

        # ── Resolved: normal field resolution ──
        resolved_fields = result.get("fields", {})
        if resolved_fields:
            if row_str not in row_selections:
                row_selections[row_str] = {}
            row_selections[row_str].update(resolved_fields)
            row_selections[row_str].pop(f"{field}__custom", None)
            logger.info(f"🧠 Custom clarification resolved: '{custom_text}' → {resolved_fields}")
        else:
            # Fallback: treat as the original field's name value
            if row_str not in row_selections:
                row_selections[row_str] = {}
            row_selections[row_str][field] = custom_text
            row_selections[row_str].pop(f"{field}__custom", None)
            logger.warning(f"⚠️ LLM couldn't resolve custom input, using as {field}={custom_text}")

    return row_selections


@router.post("/schedule/clarify/{session_id}")
async def handle_schedule_clarify(
    session_id: str,
    body: ClarifyRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Accept user's clarification selections and re-run the schedule agent.

    Returns SSE stream with thinking plan progress (same as /chat/stream).
    The frontend sends:
        { "session_id": "sched_xxx", "clarifications": { "2": { "SectionID": 123 } } }

    We merge those IDs into the original extracted data rows and re-run
    the agent so it resolves without asking again.
    """
    logger.info(f"📝 Clarification submitted for session: {session_id}")

    # Retrieve stored session (must happen synchronously before generator)
    session = _pending_sessions.pop(session_id, None)
    if not session:
        return StreamingResponse(
            _stream_cancel_response(
                "This clarification session has expired. Please re-send your original message to start fresh."
            ),
            media_type="text/event-stream",
        )

    # Clear the pending clarification tracker for this user
    user_id = current_user.get("id", 0)
    user_ctx = _get_user_context(user_id, current_user.get("org_id"))
    if user_ctx.get("pending_clarification_sid") == session_id:
        user_ctx.pop("pending_clarification_sid", None)

    extracted = session["extracted"]
    user_text = session["user_text"]
    hints = session["hints"]
    company_id = session["company_id"]
    agent_name = session["agent_name"]

    # ── Handle custom "Other" entries via LLM ──
    custom_entries = body.custom_entries or []
    # Clean raw __custom keys from row selections
    for row_str, sels in list(body.clarifications.items()):
        if isinstance(sels, dict):
            for key in list(sels.keys()):
                if key.endswith("__custom"):
                    sels.pop(key)

    if custom_entries:
        body.clarifications = await _resolve_custom_clarification(
            custom_entries, body.clarifications, session
        )
        logger.info(f"🧠 Resolved {len(custom_entries)} custom clarification(s)")

    # ── Handle cancel_all / redirect from custom clarification ──
    if body.clarifications.get("__cancel_all__"):
        logger.info("🚫 User requested cancel all during clarification")
        return StreamingResponse(
            _stream_cancel_response("Operation cancelled as requested."),
            media_type="text/event-stream",
        )
    if body.clarifications.get("__redirect__"):
        redirect_intent = body.clarifications["__redirect__"]
        logger.info(f"🔀 User wants to redirect during clarification: {redirect_intent}")
        return StreamingResponse(
            _stream_cancel_response(
                f"Understood. Your previous request has been cancelled. "
                f"Please send your new request: \"{redirect_intent}\""
            ),
            media_type="text/event-stream",
        )

    # Merge user selections into the extracted table rows
    if extracted and extracted.get("tables"):
        table = extracted["tables"][0]
        headers = table.get("headers", [])
        rows = table.get("rows", [])

        # Build normalized lookup for headers (strip case + underscores → actual header name)
        # e.g. "StartTime" → key "starttime", so "start_time" also matches via normalization
        def _norm(s: str) -> str:
            return s.lower().replace("_", "")

        header_ci = {_norm(h): h for h in headers}

        # Track which fields were custom name values (need stale ID cleared)
        custom_name_fields = set()
        for entry in custom_entries:
            row_str = entry.get("row", "")
            resolved = body.clarifications.get(row_str, {})
            for rfield in resolved:
                if rfield.endswith("Name") and not rfield.endswith("ID"):
                    custom_name_fields.add((row_str, rfield))

        for row_str, selections in body.clarifications.items():
            if row_str.startswith("_"):
                continue
            if not isinstance(selections, dict):
                continue
            try:
                row_idx = int(row_str) - 2  # row numbers are 2-based (row 1 = headers)
            except (ValueError, TypeError):
                continue
            if 0 <= row_idx < len(rows):
                # Handle skip flag: mark the row for skipping by the agent
                if selections.get("__skip__"):
                    row_dict = dict(zip(headers, rows[row_idx]))
                    row_dict["__skip__"] = True
                    if "__skip__" not in headers:
                        headers.append("__skip__")
                    rows[row_idx] = [row_dict.get(h, "") for h in headers]
                    logger.info(f"🚫 Row {row_str} marked for skipping")
                    continue

                row_dict = dict(zip(headers, rows[row_idx]))
                # Merge user-selected IDs into the row
                for field, value in selections.items():
                    # Match field to existing header (case + underscore insensitive)
                    # e.g. "start_time" from crossroads → "StartTime" in headers
                    actual_field = header_ci.get(_norm(field), field)
                    # Multi-select arrays: take first value for CSV-based agents
                    cell_value = value[0] if isinstance(value, list) and value else value
                    row_dict[actual_field] = cell_value
                    # Also add the field to headers if missing
                    if actual_field not in headers:
                        headers.append(actual_field)
                        header_ci[_norm(actual_field)] = actual_field

                    # For custom name fields, clear the stale ID so entity resolver re-runs
                    if (row_str, field) in custom_name_fields:
                        id_field_name = field.replace("Name", "ID")
                        id_actual = header_ci.get(_norm(id_field_name), id_field_name)
                        if id_actual in row_dict and id_actual != actual_field:
                            row_dict[id_actual] = ""
                            logger.info(f"🔄 Cleared stale {id_actual} for custom {field} re-resolution")

                    # Clear the original clarified field if LLM returned a different field
                    # e.g., Schedule was clarified but LLM returned StaffName instead
                    for entry in custom_entries:
                        if entry.get("row") == row_str:
                            orig_field = entry.get("field", "")
                            orig_actual = header_ci.get(_norm(orig_field), orig_field)
                            if orig_actual != actual_field and orig_actual in row_dict:
                                row_dict[orig_actual] = ""
                                logger.info(f"🔄 Cleared stale {orig_actual} (originally clarified) → LLM resolved to {actual_field}")

                # Rebuild the row array from updated dict
                rows[row_idx] = [row_dict.get(h, "") for h in headers]

        table["headers"] = headers
        table["rows"] = rows
        logger.info(f"✅ Merged clarifications into {len(body.clarifications)} rows")

    # Return SSE stream so frontend gets thinking plan progress
    return StreamingResponse(
        _clarify_sse_generator(
            agent_name=agent_name,
            user_text=user_text,
            extracted=extracted,
            session=session,
            hints=hints,
            company_id=company_id,
            current_user=current_user,
            session_id=session_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _clarify_sse_generator(
    agent_name: str,
    user_text: str,
    extracted: dict,
    session: dict,
    hints: dict,
    company_id: int,
    current_user: dict,
    session_id: str,
):
    """SSE generator for clarification re-runs — emits thinking plan events."""
    _t0 = time.perf_counter()

    # Build thinking plan for the re-run
    plan = ThinkingPlan([
        "Applying your selections",
        "Resolving identifiers",
        "Executing operations",
        "Preparing results",
    ])
    yield plan.done(0, "Selections merged")

    runner = _get_agent_runner(agent_name)
    if not runner:
        yield plan.fail(1, "Agent not available")
        yield plan.finish_all()
        yield _sse_event("result", {"reply": f"❌ Agent {agent_name} not available"})
        yield _sse_event("done", {"plan": plan.snapshot})
        return

    user_id = current_user.get("id", 0)
    user_context = _get_user_context(user_id, current_user.get("org_id"))
    history = user_context.get("conversation_history", [])

    yield plan.start(1, "Resolving staff, jobs, sections...")

    try:
        agent_result = runner(
            user_text=user_text,
            extracted=extracted,
            raw_attachments=None,
            any_uploaded_text=session.get("any_uploaded_text"),
            hints=hints,
            conversation_history=history,
        )

        if hasattr(agent_result, '__await__'):
            agent_result = await agent_result

        logger.info(f"✅ Re-run completed: success={agent_result.get('success')}")

        # Log clarification round usage
        _duration_ms = int((time.perf_counter() - _t0) * 1000)
        _still_clarifying = 1 if agent_result.get("needs_clarification") else 0
        org_id = current_user.get("org_id") if current_user else None
        if org_id:
            log_usage(
                org_id=org_id, user_id=current_user.get("id", 0),
                agent_name=agent_name,
                input_tokens=0, output_tokens=0,
                request_path=f"/schedule/clarify/{session_id}",
                duration_ms=_duration_ms,
                clarification_rounds=1 + _still_clarifying,
            )
        logger.info(f"⏱️  Clarification round completed in {_duration_ms}ms | still_needs: {bool(_still_clarifying)}")

        # If still needs clarification, store session again
        if agent_result.get("needs_clarification") and agent_result.get("session_id"):
            yield plan.done(1, "Need more info")
            # Mark all remaining steps as done/skipped so the persisted
            # panel shows "Completed" with a checkmark, not a spinner.
            yield plan.skip(2, "Waiting for input")
            yield plan.skip(3, "Waiting for input")

            new_sid = agent_result["session_id"]
            _pending_sessions[new_sid] = {
                "created_at": time.time(),
                "agent_name": agent_name,
                "user_text": user_text,
                "extracted": extracted,
                "any_uploaded_text": session.get("any_uploaded_text"),
                "hints": hints,
                "company_id": company_id,
            }
            if agent_result.get("clarifications"):
                _pending_sessions[new_sid]["_last_agent_result"] = {
                    "clarifications": agent_result["clarifications"],
                }
            logger.info(f"💾 Still needs clarification, new session: {new_sid}")
            frontend_result = {k: v for k, v in agent_result.items() if k != "original_extracted"}
            frontend_result["agent"] = agent_name
            frontend_result["expires_in"] = _session_expires_in(new_sid)
            yield _sse_event("result", {
                "reply": agent_result.get("message", "Additional information needed."),
                "needs_clarification": True,
                "clarification_data": frontend_result,
            })
            yield _sse_event("done", {"plan": plan.snapshot})
            return

        if not agent_result.get("success"):
            yield plan.fail(1, "Resolution failed")
            message = agent_result.get("message", "")
            errors = agent_result.get("errors", [])
            if errors:
                def _fmt_err(e):
                    msg = e.get('friendly', e.get('error', str(e)))
                    ctx = e.get('row_context', {})
                    parts = [v for v in [ctx.get('staff'), ctx.get('job'), ctx.get('date')] if v]
                    if parts:
                        return f"• {' | '.join(parts)}: {msg}"
                    return f"• Row {e.get('row', '?')}: {msg}"
                error_details = "\n".join(_fmt_err(e) for e in errors)
                friendly = message or "Something went wrong."
                yield _sse_event("result", {"reply": f"❌ {friendly}\n\n{error_details}"})
            else:
                yield _sse_event("result", {"reply": f"❌ {message or agent_result.get('error', 'Unknown error')}"})
            # Check multi-action chain even on failure — continue to next sub-request
            _chain_events = [evt async for evt in _emit_multi_action_chain(current_user, session_id, False, agent_result, plan)]
            if _chain_events:
                for evt in _chain_events:
                    yield evt
                return
            yield _sse_event("done", {"plan": plan.snapshot})
            return

        yield plan.done(1, "All identifiers resolved")

        # Execute schedule operations if resolved
        if agent_name == "schedule" and agent_result.get("schedules"):
            if not is_schedule_tool_available():
                yield plan.fail(2, "Tools unavailable")
                yield _sse_event("result", {"reply": "❌ Schedule tools are currently unavailable. Please ensure the MCP Server is running and try again."})
                yield _sse_event("done", {"plan": plan.snapshot})
                return

            yield plan.start(2, "Creating/updating schedules...")

            execution_result = await execute_schedule_operations(
                agent_result=agent_result,
                company_id=company_id,
                llm_chat=llm_chat
            )

            logger.info(f"✅ Schedule execution after clarification: {execution_result.get('summary', {})}")

            # Update conversation history
            history.append({"role": "user", "content": f"[Clarifications submitted for session {session_id}]"})
            history.append({"role": "assistant", "content": _summarize_agent_result(agent_name, execution_result, user_text=user_text)})
            user_context["conversation_history"] = history[-10:]

            user_id = current_user.get("id") if current_user else None
            combined = _build_execution_response("schedule", execution_result, agent_result, user_id, current_user.get("org_id") if current_user else None, company_id)

            yield plan.done(2, f"{execution_result.get('summary', {}).get('succeeded', 0)} succeeded")

            # If all failed, show text error instead of empty envelope
            if not execution_result.get("success") and execution_result.get("failed"):
                failed_items = execution_result["failed"]
                parts = [f"**{len(failed_items)}** operation(s) failed:"]
                for fi in failed_items:
                    err = fi.get("friendly") or _extract_simpro_error(fi.get("detail") or fi.get("error", "Unknown error"))
                    sched = fi.get("schedule", {})
                    label = sched.get("staff_name", f"Staff {sched.get('staff_id', '?')}")
                    parts.append(f"  - {label}: {err}")
                yield plan.fail(3, "Completed with errors")
                reply_text = "\n".join(parts)
                async for evt in _stream_text_words(reply_text):
                    yield evt
                # Check multi-action chain on failure — continue to next sub-request
                _chain_events = [evt async for evt in _emit_multi_action_chain(current_user, session_id, False, execution_result, plan)]
                if _chain_events:
                    for evt in _chain_events:
                        yield evt
                    return
                yield _sse_event("done", {"plan": plan.snapshot})
                return

            yield plan.start(3, "Formatting results...")
            envelope = _format_with_presenter(data=combined, question=user_text, llm_fn=_make_org_llm_fn(current_user.get("org_id")))
            yield plan.done(3)

            reply_text = envelope.get("summary", "Schedule operations completed.")
            async for evt in _stream_text_words(reply_text):
                yield evt
            yield _sse_event("envelope", {"envelope": envelope})

            # Check multi-action chain — present next clarification or final summary
            _chain_events = [evt async for evt in _emit_multi_action_chain(current_user, session_id, True, combined, plan)]
            if _chain_events:
                for evt in _chain_events:
                    yield evt
                return

            yield _sse_event("done", {"plan": plan.snapshot})
            return

        yield plan.finish_all()
        yield _sse_event("result", {"reply": "Processed successfully.", "metadata": {"agent": agent_name}})
        yield _sse_event("done", {"plan": plan.snapshot})

    except Exception as e:
        logger.error(f"❌ Clarification re-run error: {e}", exc_info=True)
        if plan.current_in_progress() is not None:
            yield plan.fail(plan.current_in_progress(), str(e)[:50])
        yield _sse_event("result", {"reply": f"❌ Something went wrong while processing your clarifications: {e}"})
        yield _sse_event("done", {"plan": plan.snapshot})


@router.post("/workorder/clarify/{session_id}")
async def handle_workorder_clarify(
    session_id: str,
    body: ClarifyRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Accept user's WO duplicate resolution choices and execute creates/updates.

    Returns SSE stream with thinking plan progress.
    Expects body.clarifications like:
        { "1": { "WO_Action": "update_24112" }, "2": { "WO_Action": "create_new" } }
    """
    logger.info(f"📝 WO Clarification submitted for session: {session_id}")

    session = _pending_sessions.pop(session_id, None)
    if not session:
        return StreamingResponse(
            _stream_cancel_response(
                "This clarification session has expired. Please re-send your original message to start fresh."
            ),
            media_type="text/event-stream",
        )

    # Clear the pending clarification tracker for this user
    user_id = current_user.get("id", 0)
    user_ctx = _get_user_context(user_id, current_user.get("org_id"))
    if user_ctx.get("pending_clarification_sid") == session_id:
        user_ctx.pop("pending_clarification_sid", None)

    company_id = session.get("company_id", 2)
    user_text = session.get("user_text", "")
    clean_payloads = session.get("_clean_payloads", [])
    pending_payloads = session.get("_pending_payloads", [])

    # ── Handle cancel_all / redirect from custom clarification ──
    custom_entries = body.custom_entries or []
    if custom_entries:
        body.clarifications = await _resolve_custom_clarification(
            custom_entries, body.clarifications, session
        )
    if body.clarifications.get("__cancel_all__"):
        logger.info("🚫 User requested cancel all during WO clarification")
        return StreamingResponse(
            _stream_cancel_response("Work order operation cancelled as requested."),
            media_type="text/event-stream",
        )
    if body.clarifications.get("__redirect__"):
        redirect_intent = body.clarifications["__redirect__"]
        logger.info(f"🔀 User wants to redirect during WO clarification: {redirect_intent}")
        return StreamingResponse(
            _stream_cancel_response(
                f"Understood. Your work order request has been cancelled. "
                f"Please send your new request: \"{redirect_intent}\""
            ),
            media_type="text/event-stream",
        )

    # Process user choices
    create_payloads = list(clean_payloads)
    update_payloads = []

    for row_str, selections in body.clarifications.items():
        if not isinstance(selections, dict):
            continue
        # Skip rows marked for skipping
        if selections.get("__skip__"):
            logger.info(f"🚫 WO row {row_str} skipped by user")
            continue
        try:
            idx = int(row_str) - 1  # rows are 1-based in clarification
        except (ValueError, TypeError):
            continue
        if idx < 0 or idx >= len(pending_payloads):
            continue

        payload = pending_payloads[idx]
        action = selections.get("WO_Action", "create_new")
        # Guard: if multi-select array, take first value
        if isinstance(action, list):
            action = action[0] if action else "create_new"

        if action.startswith("update_"):
            cj_id_str = action.replace("update_", "")
            try:
                cj_id = int(cj_id_str)
            except ValueError:
                create_payloads.append(payload)
                continue
            payload["_existing_cj_id"] = cj_id
            update_payloads.append(payload)
        else:
            create_payloads.append(payload)

    # Execute via workorder_executor
    combined_result = {
        "contractor_jobs": create_payloads,
        "contractor_job_updates": update_payloads,
    }

    return StreamingResponse(
        _wo_clarify_sse_generator(
            combined_result=combined_result,
            company_id=company_id,
            user_text=user_text,
            current_user=current_user,
            session_id=session_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _wo_clarify_sse_generator(
    combined_result: dict,
    company_id: int,
    user_text: str,
    current_user: dict,
    session_id: str,
):
    """SSE generator for workorder clarification — emits thinking plan events."""
    _t0 = time.perf_counter()

    plan = ThinkingPlan([
        "Applying your selections",
        "Executing work order operations",
        "Preparing results",
    ])
    yield plan.done(0, "Selections merged")
    yield plan.start(1, "Creating/updating work orders...")

    try:
        execution_result = await execute_workorder_operations(
            agent_result=combined_result,
            company_id=company_id,
        )

        _duration_ms = int((time.perf_counter() - _t0) * 1000)
        logger.info(f"⏱️  WO clarification completed in {_duration_ms}ms")
        org_id = current_user.get("org_id") if current_user else None
        if org_id:
            log_usage(
                org_id=org_id, user_id=current_user.get("id", 0),
                agent_name="workorder",
                request_path=f"/workorder/clarify/{session_id}",
                duration_ms=_duration_ms,
                clarification_rounds=1,
            )

        # Cache management for retry
        wo_user_id = current_user.get("id") if current_user else None
        wo_org_id = current_user.get("org_id") if current_user else None
        if wo_user_id is not None:
            wo_key = (wo_user_id, wo_org_id or 0)
            if execution_result.get("failed"):
                _last_failed_ops[wo_key] = {
                    "agent_name": "workorder",
                    "agent_result": combined_result,
                    "company_id": company_id,
                    "timestamp": time.time(),
                }
            else:
                _last_failed_ops.pop(wo_key, None)

        # Flatten results
        all_results = []
        for cj in execution_result.get("created", []):
            all_results.append(cj)
        for cj in execution_result.get("updated", []):
            all_results.append(cj)
        for cj in execution_result.get("failed", []):
            cj.setdefault("status", "failed")
            if cj.get("detail"):
                cj["detail"] = _extract_simpro_error(cj["detail"])
            all_results.append(cj)

        yield plan.done(1, f"{len(execution_result.get('created', [])) + len(execution_result.get('updated', []))} succeeded")

        # If all failed, show a clear text error
        if not execution_result.get("success") and execution_result.get("failed"):
            failed_items = execution_result["failed"]
            parts = [f"**{len(failed_items)}** operation(s) failed:"]
            for fi in failed_items:
                err = fi.get("friendly") or _extract_simpro_error(fi.get("detail") or fi.get("error", "Unknown error"))
                name = fi.get("contractor_name", "")
                cj_id = fi.get("contractor_job_id", "")
                label = f"CJ {cj_id} ({name})" if cj_id and name else (name or f"CJ {cj_id}" if cj_id else "Item")
                parts.append(f"  - {label}: {err}")
            yield plan.fail(2, "Completed with errors")
            reply_text = "\n".join(parts)
            async for evt in _stream_text_words(reply_text):
                yield evt
            # Check multi-action chain on failure — continue to next sub-request
            _chain_events = [evt async for evt in _emit_multi_action_chain(current_user, session_id, False, execution_result, plan)]
            if _chain_events:
                for evt in _chain_events:
                    yield evt
                return
            yield _sse_event("done", {"plan": plan.snapshot})
            return

        combined = {
            "success": execution_result.get("success"),
            "summary": execution_result.get("summary"),
            "results": all_results,
        }

        yield plan.start(2, "Formatting results...")
        envelope = _format_with_presenter(data=combined, question=user_text, llm_fn=_make_org_llm_fn(current_user.get("org_id")))

        # Update conversation history
        wo_user_id_for_history = current_user.get("id") if current_user else None
        if wo_user_id_for_history is not None:
            ctx = _get_user_context(wo_user_id_for_history, current_user.get("org_id") if current_user else None)
            history = ctx.get("conversation_history", [])
            history.append({"role": "user", "content": user_text})
            history.append({
                "role": "assistant",
                "content": _summarize_agent_result("workorder", combined, user_text=user_text),
            })
            ctx["conversation_history"] = history[-10:]

        yield plan.done(2)

        reply_text = envelope.get("summary", execution_result.get("summary", {}).get("message", "Work order operations completed."))
        async for evt in _stream_text_words(reply_text):
            yield evt
        yield _sse_event("envelope", {"envelope": envelope})

        # Check multi-action chain — present next clarification or final summary
        _chain_events = [evt async for evt in _emit_multi_action_chain(current_user, session_id, True, combined, plan)]
        if _chain_events:
            for evt in _chain_events:
                yield evt
            return

        yield _sse_event("done", {"plan": plan.snapshot})

    except Exception as e:
        logger.error(f"❌ WO clarification error: {e}", exc_info=True)
        if plan.current_in_progress() is not None:
            yield plan.fail(plan.current_in_progress(), str(e)[:50])
        yield _sse_event("result", {"reply": f"❌ Something went wrong: {e}"})
        yield _sse_event("done", {"plan": plan.snapshot})


# ============================================================================
# Invoice Clarification
# ============================================================================


def _parse_claim_value(text: str) -> dict:
    """Parse user claim input like '100%', '$5000 ex tax', '50' → claim dict."""
    val = (text or "").strip().lower()
    if not val:
        return {}

    # "100%", "50%"
    if val.endswith("%"):
        try:
            return {"claim_percent": float(val.rstrip("%").strip())}
        except ValueError:
            pass

    # "$5000 ex tax", "$3000", "5000 ex tax"
    cleaned = val.replace("$", "").replace(",", "")
    cleaned = cleaned.replace("ex tax", "").replace("extax", "").replace("ex", "").strip()
    try:
        num = float(cleaned)
        # If original had $ or "ex tax" or number > 100 → treat as dollar amount
        if "$" in val or "ex" in val or num > 100:
            return {"claim_extax": num}
        # Small number without $ → treat as percentage
        return {"claim_percent": num}
    except ValueError:
        pass

    return {}


def _merge_claim_value(chat_result: dict, field: str, value, logger):
    """Merge a Claim_XXXXX field value into the matching cost centre."""
    try:
        cc_id = int(field.split("_", 1)[1])
    except (ValueError, IndexError):
        return
    # Defensive: claim fields are free_text (scalar), but guard against list
    if isinstance(value, list):
        value = value[0] if value else ""
    parsed = _parse_claim_value(str(value))
    if not parsed:
        logger.warning(f"  Could not parse claim value '{value}' for CC {cc_id}")
        return
    for inv in chat_result.get("invoices", []):
        for cc in inv.get("cost_centres", []):
            if cc.get("cost_centre_id") == cc_id:
                cc.update(parsed)
                logger.info(f"  Claim merged for CC {cc_id}: {parsed}")
                return


@router.post("/invoice/clarify/{session_id}")
async def handle_invoice_clarify(
    session_id: str,
    body: ClarifyRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Accept user's SOP-deviation confirmations and re-build invoice bodies.

    Returns SSE stream with thinking plan progress.
    The frontend sends confirmation selections like:
        { "1": { "PerItem": "true" }, "1": { "InvoiceType": "TaxInvoice" } }

    We apply the user's choices to the stored LLM policy, re-parse the
    CSV, re-build invoice bodies, and execute the invoices.
    """
    logger.info(f"📝 Invoice clarification submitted for session: {session_id}")
    _t0 = time.perf_counter()

    session = _pending_sessions.pop(session_id, None)
    if not session:
        return {
            "reply": "This clarification session has expired. Please re-send your original message to start fresh.",
            "session_expired": True,
        }

    # Clear the pending clarification tracker for this user
    user_id = current_user.get("id", 0)
    user_ctx = _get_user_context(user_id, current_user.get("org_id"))
    if user_ctx.get("pending_clarification_sid") == session_id:
        user_ctx.pop("pending_clarification_sid", None)

    user_text = session["user_text"]
    hints = session["hints"]
    company_id = session["company_id"]
    policy = session.get("_policy", {})
    any_uploaded_text = session.get("any_uploaded_text")
    chat_result = session.get("_chat_result")

    # ── Handle custom "Other" entries via LLM ──
    custom_entries = body.custom_entries or []
    for row_str, sels in list(body.clarifications.items()):
        if isinstance(sels, dict):
            for key in list(sels.keys()):
                if key.endswith("__custom"):
                    sels.pop(key)
    if custom_entries:
        body.clarifications = await _resolve_custom_clarification(
            custom_entries, body.clarifications, session
        )
        logger.info(f"🧠 Resolved {len(custom_entries)} custom invoice clarification(s)")

    # ── Handle cancel_all / redirect from custom clarification ──
    if body.clarifications.get("__cancel_all__"):
        logger.info("🚫 User requested cancel all during invoice clarification")
        return {"success": False, "message": "Invoice operation cancelled as requested."}
    if body.clarifications.get("__redirect__"):
        redirect_intent = body.clarifications["__redirect__"]
        logger.info(f"🔀 User wants to redirect during invoice clarification: {redirect_intent}")
        return {
            "success": False,
            "message": f"Understood. Your invoice request has been cancelled. "
                       f"Please send your new request: \"{redirect_intent}\"",
        }

    # ── Entity / claim clarification (user picked from dropdown/multi-select/free-text) ──
    # If the session has a _chat_result, this is a chat-mode entity
    # resolution clarification (job, cost centre, section) or claim follow-up.
    # Inject the selected ID(s) and re-run the full agent pipeline.
    _entity_fields = {"JobID", "CostCentreID", "SectionID"}
    has_entity_selection = chat_result and any(
        _entity_fields & set(sels.keys()) for sels in body.clarifications.values()
    )
    has_claim_fields = chat_result and any(
        any(k.startswith("Claim_") for k in sels.keys())
        for sels in body.clarifications.values()
    )
    if has_entity_selection or has_claim_fields:
        # Extract selections from clarification
        for _row_str, selections in body.clarifications.items():
            if isinstance(selections, dict) and selections.get("__skip__"):
                logger.info(f"🚫 Invoice row {_row_str} skipped by user")
                continue
            for field, value in selections.items():
                # ── Claim follow-up fields (Claim_XXXXX) ──
                if field.startswith("Claim_"):
                    _merge_claim_value(chat_result, field, value, logger)
                    continue

                if field not in _entity_fields:
                    continue

                # ── Multi-select: array of IDs ──
                if isinstance(value, list):
                    if field == "CostCentreID":
                        cc_ids = []
                        for v in value:
                            try:
                                cc_ids.append(int(v))
                            except (ValueError, TypeError):
                                continue
                        if cc_ids:
                            logger.info(f"  CostCentreID multi-select: {cc_ids}")
                            for inv in chat_result.get("invoices", []):
                                inv["cost_centres"] = [
                                    {"cost_centre_id": cid, "cost_centre_name": None,
                                     "claim_percent": None, "claim_extax": None,
                                     "claim_inctax": None, "items": []}
                                    for cid in cc_ids
                                ]
                                break
                    continue

                # ── Single-select: scalar ID ──
                try:
                    int_val = int(value)
                except (ValueError, TypeError):
                    continue

                if field == "JobID":
                    logger.info(f"  JobID selected from clarification: {int_val}")
                    for inv in chat_result.get("invoices", []):
                        if not inv.get("job_id"):
                            inv["job_id"] = int_val
                            break
                elif field == "CostCentreID":
                    logger.info(f"  CostCentreID selected from clarification: {int_val}")
                    for inv in chat_result.get("invoices", []):
                        ccs = inv.get("cost_centres") or []
                        if not ccs:
                            inv["cost_centres"] = [{"cost_centre_id": int_val, "cost_centre_name": None}]
                        else:
                            for cc in ccs:
                                if not cc.get("cost_centre_id"):
                                    cc["cost_centre_id"] = int_val
                                    break
                        break
                elif field == "SectionID":
                    logger.info(f"  SectionID selected from clarification: {int_val}")
                    # Store section_id on the invoice so the re-run can use it
                    # instead of re-resolving from scratch (which causes a loop).
                    for inv in chat_result.get("invoices", []):
                        inv["_section_id"] = int_val
                        break
                elif field == "SectionName":
                    # SectionName selection also carries an ID — store it
                    logger.info(f"  SectionName selected from clarification: {int_val}")
                    for inv in chat_result.get("invoices", []):
                        inv["_section_id"] = int_val
                        break

        # ── Continue entity resolution before converting to CSV ──
        # After merging user selections, try to resolve remaining entities
        # (e.g., after user picks Job, we may need to ask for CostCentre).
        # Only convert to CSV after ALL entities are resolved.
        import sys as _sys
        svc_mod = _sys.modules.get("svc_invoice_agent")
        if svc_mod is None:
            raise HTTPException(status_code=500, detail="Invoice agent module not loaded")

        _resolve_refs = getattr(svc_mod, "_resolve_chat_job_references", None)
        _chat_to_csv = getattr(svc_mod, "_chat_result_to_csv", None)
        if not _chat_to_csv:
            raise HTTPException(status_code=500, detail="Invoice agent _chat_result_to_csv not available")

        # Create MCP executor to continue resolution
        from utils.mcp_tool_client import get_mcp_tool_client
        from utils.llm import chat as llm_chat_fn
        _MCPToolExecutor = getattr(svc_mod, "MCPToolExecutor", None)
        _mcp_executor = None
        if _MCPToolExecutor is not None and _resolve_refs is not None:
            try:
                _mcp_client = get_mcp_tool_client()
                _mcp_executor = _MCPToolExecutor(
                    tool_registry=_mcp_client,
                    company_id=company_id,
                )
            except Exception as e:
                logger.warning(f"  Could not create MCP executor for resolution: {e}")

        # Try to continue resolving (may raise more clarifications)
        if _mcp_executor and _resolve_refs:
            try:
                chat_result = await _resolve_refs(
                    chat_result, _mcp_executor, llm_chat=llm_chat_fn,
                )
                logger.info("  ✅ All entity references resolved")
            except Exception as resolve_err:
                # Check if this is a clarification error (needs user input)
                _AmbiguousErr = getattr(svc_mod, "AmbiguousResolutionError", None)
                _MissingErr = getattr(svc_mod, "MissingFieldError", None)
                _BatchedErr = getattr(svc_mod, "BatchedClarificationError", None)

                is_clarification = (
                    (_AmbiguousErr and isinstance(resolve_err, _AmbiguousErr))
                    or (_MissingErr and isinstance(resolve_err, _MissingErr))
                    or (_BatchedErr and isinstance(resolve_err, _BatchedErr))
                )
                if is_clarification:
                    # Build clarification response and store new session
                    import uuid
                    new_sid = f"inv_{uuid.uuid4().hex[:12]}"
                    clarifications_list = []

                    if _BatchedErr and isinstance(resolve_err, _BatchedErr):
                        for inner in resolve_err.errors:
                            if _AmbiguousErr and isinstance(inner, _AmbiguousErr):
                                clarifications_list.append({
                                    "row": 1, "type": "ambiguous",
                                    "field": inner.field, "message": inner.message,
                                    "options": inner.matches, "operation": "CREATE",
                                    "row_context": {"query": inner.value},
                                })
                            elif _MissingErr and isinstance(inner, _MissingErr):
                                _is_free = inner.context.get("free_text", False)
                                _is_multi = inner.context.get("multi_select", False)
                                opts = inner.context.get("options", [])
                                if _is_free:
                                    clarifications_list.append({
                                        "row": 1, "type": "free_text",
                                        "field": inner.field, "message": inner.message,
                                        "placeholder": inner.context.get("placeholder", inner.field),
                                        "options": [], "operation": "CREATE",
                                        "row_context": {},
                                    })
                                elif opts:
                                    clarifications_list.append({
                                        "row": 1,
                                        "type": "multi_select" if _is_multi else "missing",
                                        "field": inner.field, "message": inner.message,
                                        "options": opts, "operation": "CREATE",
                                        "row_context": {},
                                    })
                    elif _AmbiguousErr and isinstance(resolve_err, _AmbiguousErr):
                        clarifications_list.append({
                            "row": 1, "type": "ambiguous",
                            "field": resolve_err.field, "message": resolve_err.message,
                            "options": resolve_err.matches, "operation": "CREATE",
                            "row_context": {"query": resolve_err.value},
                        })
                    elif _MissingErr and isinstance(resolve_err, _MissingErr):
                        _is_free = resolve_err.context.get("free_text", False)
                        _is_multi = resolve_err.context.get("multi_select", False)
                        opts = resolve_err.context.get("options", [])
                        if _is_free:
                            clarifications_list.append({
                                "row": 1, "type": "free_text",
                                "field": resolve_err.field, "message": resolve_err.message,
                                "placeholder": resolve_err.context.get("placeholder", resolve_err.field),
                                "options": [], "operation": "CREATE",
                                "row_context": {},
                            })
                        elif opts:
                            clarifications_list.append({
                                "row": 1,
                                "type": "multi_select" if _is_multi else "missing",
                                "field": resolve_err.field, "message": resolve_err.message,
                                "options": opts, "operation": "CREATE",
                                "row_context": {},
                            })

                    _pending_sessions[new_sid] = {
                        "created_at": time.time(),
                        "agent_name": "invoice",
                        "user_text": user_text,
                        "extracted": session.get("extracted"),
                        "hints": hints,
                        "company_id": company_id,
                        "_chat_result": chat_result,
                    }
                    if clarifications_list:
                        _pending_sessions[new_sid]["_last_agent_result"] = {
                            "clarifications": clarifications_list,
                        }
                    frontend_result = {
                        "needs_clarification": True,
                        "clarification_mode": "interactive",
                        "session_id": new_sid,
                        "clarification_count": len(clarifications_list),
                        "clarifications": clarifications_list,
                        "message": (resolve_err.message if hasattr(resolve_err, "message")
                                    else str(resolve_err)),
                        "agent": "invoice",
                        "expires_in": _session_expires_in(new_sid),
                    }
                    return {
                        "reply": frontend_result["message"],
                        "needs_clarification": True,
                        "clarification_data": frontend_result,
                    }
                else:
                    # Hard resolution error
                    return {"reply": f"Could not resolve entities: {resolve_err}"}

        # All entities resolved — convert to CSV and re-run
        resolved_csv = _chat_to_csv(chat_result)
        logger.info(f"  Resolved CSV from chat_result: {len(resolved_csv)} chars")

        # Re-run the full agent via the invoice proxy (creates fresh llm/mcp)
        from agents.invoice_proxy import run_invoice_agent as run_invoice_proxy
        agent_result = await run_invoice_proxy(
            llm_chat=llm_chat_fn,
            user_text=user_text,
            registry_entry={},
            any_uploaded_text=resolved_csv,
            hints=hints,
            conversation_history=None,
        )

        # Handle the re-run result
        if agent_result.get("needs_clarification"):
            new_sid = agent_result.get("session_id")
            if new_sid:
                _pending_sessions[new_sid] = {
                    "created_at": time.time(),
                    "agent_name": "invoice",
                    "user_text": user_text,
                    "extracted": session.get("extracted"),
                    "any_uploaded_text": resolved_csv,
                    "hints": hints,
                    "company_id": company_id,
                    "_policy": agent_result.get("_policy"),
                }
                if agent_result.get("clarifications"):
                    _pending_sessions[new_sid]["_last_agent_result"] = {
                        "clarifications": agent_result["clarifications"],
                    }
            frontend_result = {
                k: v for k, v in agent_result.items()
                if k not in {"original_extracted", "_policy", "_chat_result"}
            }
            frontend_result["agent"] = "invoice"
            if new_sid:
                frontend_result["expires_in"] = _session_expires_in(new_sid)
            return {
                "reply": agent_result.get("message", "Additional information needed."),
                "needs_clarification": True,
                "clarification_data": frontend_result,
            }

        if not agent_result.get("success"):
            message = agent_result.get("message", agent_result.get("error", "Unknown error"))
            return {"reply": f"Could not build invoices: {message}"}

        # Execute invoices via MCP
        if agent_result.get("jobs"):
            if not is_tool_available():
                return {"reply": "Invoice tools are currently unavailable. Please ensure the MCP Server is running and try again."}

            creation_result = await create_invoices_from_agent_result(
                agent_result=agent_result,
                company_id=company_id,
            )

            logger.info(f"✅ Invoice creation after entity resolution: {creation_result.get('summary', {})}")

            # Update conversation history
            user_id_val = current_user.get("id", 0)
            user_ctx = _get_user_context(user_id_val, current_user.get("org_id"))
            history = user_ctx.get("conversation_history", [])
            history.append({"role": "user", "content": f"[Invoice entity resolved from clarification session {session_id}]"})
            history.append({"role": "assistant", "content": _summarize_agent_result("invoice", creation_result, user_text=user_text)})
            user_ctx["conversation_history"] = history[-10:]

            user_id = current_user.get("id") if current_user else None
            combined = _build_execution_response("invoice", creation_result, agent_result, user_id, current_user.get("org_id") if current_user else None, company_id)

            if not creation_result.get("success") and creation_result.get("failed"):
                failed_items = creation_result["failed"]
                parts = [f"**{len(failed_items)}** invoice(s) failed:"]
                for fi in failed_items:
                    err = fi.get("friendly") or _extract_simpro_error(fi.get("detail") or fi.get("error", "Unknown error"))
                    job = fi.get("job_id", "?")
                    parts.append(f"  - Job {job}: {err}")
                # Check multi-action chain on failure
                chain = _check_multi_action_chain(user_id, session_id, False, creation_result)
                if chain and chain["type"] == "next_clarification":
                    return {
                        "reply": chain["progress_message"],
                        "needs_clarification": True,
                        "clarification_data": chain["clarification_data"],
                    }
                elif chain and chain["type"] == "final_summary":
                    return {"reply": chain["summary"]["message"]}
                return {"reply": "\n".join(parts)}

            envelope = _format_with_presenter(data=combined, question=user_text, llm_fn=_make_org_llm_fn(current_user.get("org_id")))

            # Check multi-action chain — present next clarification or final summary
            chain = _check_multi_action_chain(user_id, session_id, True, creation_result)
            if chain and chain["type"] == "next_clarification":
                return {
                    "reply": chain["progress_message"],
                    "needs_clarification": True,
                    "clarification_data": chain["clarification_data"],
                }
            elif chain and chain["type"] == "final_summary":
                return {
                    "reply": chain["summary"]["message"],
                    "envelope": envelope,
                }

            return {
                "reply": envelope.get("summary", "Invoice operations completed."),
                "envelope": envelope,
            }

        return {"reply": "Invoice prepared but no jobs to process."}

    # Apply user overrides to the stored policy (SOP deviations)
    for _row_str, selections in body.clarifications.items():
        for field, value in selections.items():
            if field == "PerItem":
                policy["per_item"] = value in ("true", "True", True)
                logger.info(f"  PerItem overridden to: {policy['per_item']}")
            elif field == "InvoiceType":
                policy.setdefault("defaults", {})["Type"] = value
                logger.info(f"  InvoiceType overridden to: {value}")
            elif field == "Stage":
                policy.setdefault("defaults", {})["Stage"] = value
                logger.info(f"  Stage overridden to: {value}")

    # Re-parse the CSV text and re-build invoice bodies
    import sys as _sys
    svc_mod = _sys.modules.get("svc_invoice_agent")
    if svc_mod is None:
        raise HTTPException(status_code=500, detail="Invoice agent module not loaded")

    _parse_csv = getattr(svc_mod, "_parse_attachment_csv", None)
    _build_bodies = getattr(svc_mod, "_build_invoice_bodies", None)
    if not _parse_csv or not _build_bodies:
        raise HTTPException(status_code=500, detail="Invoice agent internal functions not available")

    rows = _parse_csv(any_uploaded_text or "")
    if not rows:
        return {"reply": "Could not re-parse invoice data. Please try again."}

    agent_result = _build_bodies(rows, policy, hints)

    logger.info(f"✅ Invoice re-build: success={agent_result.get('success')}")

    # If still needs clarification (e.g. skipped groups), return new form
    if agent_result.get("needs_clarification"):
        new_sid = agent_result.get("session_id")
        if new_sid:
            _pending_sessions[new_sid] = {
                "created_at": time.time(),
                "agent_name": "invoice",
                "user_text": user_text,
                "extracted": session.get("extracted"),
                "any_uploaded_text": any_uploaded_text,
                "hints": hints,
                "company_id": company_id,
                "_policy": policy,
            }
            if agent_result.get("clarifications"):
                _pending_sessions[new_sid]["_last_agent_result"] = {
                    "clarifications": agent_result["clarifications"],
                }
        frontend_result = {
            k: v for k, v in agent_result.items()
            if k not in {"original_extracted", "_policy"}
        }
        frontend_result["agent"] = "invoice"
        if new_sid:
            frontend_result["expires_in"] = _session_expires_in(new_sid)
        return {
            "reply": agent_result.get("message", "Additional information needed."),
            "needs_clarification": True,
            "clarification_data": frontend_result,
        }

    if not agent_result.get("success"):
        message = agent_result.get("message", agent_result.get("error", "Unknown error"))
        return {"reply": f"Could not build invoices: {message}"}

    # Execute invoices via MCP
    if agent_result.get("jobs"):
        if not is_tool_available():
            return {"reply": "Invoice tools are currently unavailable. Please ensure the MCP Server is running and try again."}

        creation_result = await create_invoices_from_agent_result(
            agent_result=agent_result,
            company_id=company_id,
        )

        _duration_ms = int((time.perf_counter() - _t0) * 1000)
        logger.info(f"⏱️  Invoice clarification completed in {_duration_ms}ms")
        org_id = current_user.get("org_id") if current_user else None
        if org_id:
            log_usage(
                org_id=org_id, user_id=current_user.get("id", 0),
                agent_name="invoice",
                request_path=f"/invoice/clarify/{session_id}",
                duration_ms=_duration_ms,
                clarification_rounds=1,
            )

        # Update conversation history
        user_id_val = current_user.get("id", 0)
        user_ctx = _get_user_context(user_id_val, current_user.get("org_id"))
        history = user_ctx.get("conversation_history", [])
        history.append({"role": "user", "content": f"[Invoice clarifications submitted for session {session_id}]"})
        history.append({"role": "assistant", "content": _summarize_agent_result("invoice", creation_result, user_text=user_text)})
        user_ctx["conversation_history"] = history[-10:]

        user_id = current_user.get("id") if current_user else None
        combined = _build_execution_response("invoice", creation_result, agent_result, user_id, current_user.get("org_id") if current_user else None, company_id)

        # If all failed, show text error
        if not creation_result.get("success") and creation_result.get("failed"):
            failed_items = creation_result["failed"]
            parts = [f"**{len(failed_items)}** invoice(s) failed:"]
            for fi in failed_items:
                err = fi.get("friendly") or _extract_simpro_error(fi.get("detail") or fi.get("error", "Unknown error"))
                job = fi.get("job_id", "?")
                parts.append(f"  - Job {job}: {err}")
            # Check multi-action chain on failure
            chain = _check_multi_action_chain(user_id, session_id, False, creation_result)
            if chain and chain["type"] == "next_clarification":
                return {
                    "reply": chain["progress_message"],
                    "needs_clarification": True,
                    "clarification_data": chain["clarification_data"],
                }
            elif chain and chain["type"] == "final_summary":
                return {"reply": chain["summary"]["message"]}
            return {"reply": "\n".join(parts)}

        envelope = _format_with_presenter(data=combined, question=user_text, llm_fn=_make_org_llm_fn(current_user.get("org_id")))

        # Check multi-action chain — present next clarification or final summary
        chain = _check_multi_action_chain(user_id, session_id, True, creation_result)
        if chain and chain["type"] == "next_clarification":
            return {
                "reply": chain["progress_message"],
                "needs_clarification": True,
                "clarification_data": chain["clarification_data"],
            }
        elif chain and chain["type"] == "final_summary":
            return {
                "reply": chain["summary"]["message"],
                "envelope": envelope,
            }

        return {
            "reply": envelope.get("summary", "Invoice operations completed."),
            "envelope": envelope,
        }

    return {"reply": "Processed successfully.", "metadata": {"agent": "invoice"}}


# ============================================================================
# MCP Clarify
# ============================================================================

@router.post("/mcp/clarify/{session_id}")
async def handle_mcp_clarify(
    session_id: str,
    body: ClarifyRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Accept user's entity selection from an MCP ambiguous-match clarification form
    and re-run the original query with the resolved ID injected.

    The frontend sends (same shape as agent clarify endpoints):
        { "session_id": "mcp_xxx", "clarifications": { "1": { "StaffID": 452 } } }

    We inject the resolved ID as context into the original message and re-run
    the Python executor so the entity resolver sees a pre-resolved ID and skips
    fuzzy matching entirely.
    """
    logger.info(f"📝 MCP clarification submitted for session: {session_id}")

    # Pull from _pending_sessions (mirrored from _mcp_pending_sessions on clarification fire)
    session = _pending_sessions.pop(session_id, None)
    if not session:
        # Also check the executor's own store in case mirroring raced
        try:
            from mcp_python_executor import _mcp_pending_sessions
            session = _mcp_pending_sessions.pop(session_id, None)
        except ImportError:
            pass

    if not session:
        return StreamingResponse(
            _stream_cancel_response(
                "This clarification session has expired. Please re-send your original message to start fresh."
            ),
            media_type="text/event-stream",
        )

    user_id = current_user.get("id", 0)
    user_ctx = _get_user_context(user_id, current_user.get("org_id"))
    if user_ctx.get("pending_clarification_sid") == session_id:
        user_ctx.pop("pending_clarification_sid", None)

    user_message = session["user_message"]
    history = session["history"]
    resolved_filter_key = session["resolved_filter_key"]
    resolved_id_key = session["resolved_id_key"]

    # Extract the selected ID from the clarification payload.
    # ClarificationForm sends: { "1": { "StaffID": 452 } } (exact field name depends on entity type).
    # We don't rely on field naming conventions — just take the first non-skip value
    # from the submitted row. The session already knows where to put it via resolved_id_key.
    selected_id = None
    row_data = body.clarifications.get("1") or body.clarifications.get(1)
    if isinstance(row_data, dict):
        for v in row_data.values():
            if v and v != "__skip__":
                selected_id = v
                break

    if not selected_id:
        return StreamingResponse(
            _stream_cancel_response("Could not read your selection. Please try again."),
            media_type="text/event-stream",
        )

    # Build enriched message: inject the resolved ID as explicit context so
    # the executor's entity resolver sees an ID and skips fuzzy matching.
    # Format mirrors _enrich_message_with_reuse_fields().
    resolved_context = (
        f"[RESOLVED: {resolved_filter_key} → {resolved_id_key}={selected_id}]\n\n"
    )
    enriched_message = resolved_context + user_message

    org_id = current_user.get("org_id") if current_user else None
    # Use live enriched history (scratchpad + summary) rather than the raw
    # history snapshot stored in the session, so the executor has full context.
    enriched_history = build_enriched_history(user_ctx, history)

    return StreamingResponse(
        _mcp_clarify_sse_generator(
            enriched_message=enriched_message,
            history=enriched_history,
            current_user=current_user,
            session_id=session_id,
            org_id=org_id,
            user_id=user_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _mcp_clarify_sse_generator(
    enriched_message: str,
    history: List[Dict],
    current_user: dict,
    session_id: str,
    org_id: Optional[int],
    user_id: int,
):
    """SSE generator: re-runs the Python MCP executor with the resolved entity context."""
    _t0 = time.perf_counter()
    response_text = ""
    mcp_result = None
    got_any_tokens = False

    yield _sse_event("status", {"message": "Applying your selection..."})

    _org_creds = _get_org_simpro_credentials(org_id)
    _org_llm_clarify = _get_org_llm_config(org_id)

    try:
        from mcp_python_executor import get_python_executor
        _py_executor = get_python_executor(
            user_id=user_id, org_id=org_id, **_org_creds,
            llm_provider=_org_llm_clarify["primary"].get("provider"),
            llm_model=_org_llm_clarify["primary"].get("model"),
            llm_api_key=_org_llm_clarify["primary"].get("api_key"),
        )
        async for evt in _py_executor.execute_chat_stream(
            user_message=enriched_message,
            history=history,
        ):
            if evt["type"] == "status":
                yield _sse_event("status", {"message": evt.get("message", "")})
            elif evt["type"] == "token":
                got_any_tokens = True
                text = evt.get("text", "")
                response_text += text
                yield _sse_event("token", {"text": text})
            elif evt["type"] == "result":
                # Another clarification needed (different entity this time)
                if evt.get("clarification"):
                    clar = evt["clarification"]
                    new_sid = clar.get("session_id")
                    if new_sid:
                        from mcp_python_executor import _mcp_pending_sessions
                        if new_sid in _mcp_pending_sessions:
                            _pending_sessions[new_sid] = _mcp_pending_sessions[new_sid]
                        user_ctx = _get_user_context(user_id, org_id)
                        user_ctx["pending_clarification_sid"] = new_sid
                    yield _sse_event("result", {
                        "reply": "I found multiple matches. Please select the one you meant.",
                        "needs_clarification": True,
                        "clarification_data": clar,
                    })
                    yield _sse_event("done", {})
                    return
                mcp_result = {
                    "success": evt.get("success", True),
                    "response": evt.get("response", response_text),
                    "toolCalls": evt.get("toolCalls", []),
                    "metadata": evt.get("metadata", {}),
                }
                if not got_any_tokens:
                    response_text = mcp_result.get("response", "")
            elif evt["type"] == "done":
                pass
    except Exception as e:
        logger.error(f"MCP clarify stream error: {e}", exc_info=True)
        yield _sse_event("result", {"reply": f"An error occurred: {e}"})
        yield _sse_event("done", {})
        return

    # Update conversation history
    user_ctx = _get_user_context(user_id, org_id)
    history_list = user_ctx.get("conversation_history", [])
    if mcp_result and mcp_result.get("success"):
        history_list.append({"role": "user", "content": enriched_message})
        history_list.append({"role": "assistant", "content": response_text})
        user_ctx["conversation_history"] = history_list[-10:]

    # Log usage
    _duration_ms = int((time.perf_counter() - _t0) * 1000)
    mcp_usage = (mcp_result.get("metadata") or {}).get("usage") or {} if mcp_result else {}
    if mcp_usage and org_id:
        log_usage(
            org_id=org_id, user_id=user_id,
            agent_name="chat",
            input_tokens=mcp_usage.get("inputTokens", 0),
            output_tokens=mcp_usage.get("outputTokens", 0),
            model_name=mcp_usage.get("model", ""),
            request_path=f"/mcp/clarify/{session_id}",
            duration_ms=_duration_ms,
        )
    logger.info(f"⏱️  MCP clarify completed in {_duration_ms}ms")

    # Route structured data through presenter, same as main MCP path
    tool_calls = (mcp_result.get("toolCalls") or []) if mcp_result else []
    structured_data = _extract_tool_data(tool_calls)

    if structured_data:
        yield _sse_event("status", {"message": "Formatting results..."})
        envelope = _format_with_presenter(data=structured_data, question=enriched_message, llm_fn=_make_org_llm_fn(org_id))
        if not got_any_tokens and response_text:
            async for evt in _stream_text_words(response_text):
                yield evt
        yield _sse_event("envelope", {"envelope": envelope})
    else:
        if not got_any_tokens and response_text:
            async for evt in _stream_text_words(response_text):
                yield evt
        yield _sse_event("envelope", {"envelope": None})

    yield _sse_event("done", {})


# ============================================================================
# Voice Transcription
# ============================================================================

@router.post("/transcribe", response_model=TranscribeResponse)
async def handle_transcribe(
    audio: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """Transcribe audio to text using OpenAI Whisper API."""
    MAX_AUDIO_BYTES = 25_000_000
    contents = await audio.read()

    if not contents:
        raise HTTPException(status_code=400, detail="Empty audio file")
    if len(contents) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio file too large (max 25 MB)")

    # Reject very short recordings (just container headers, no real audio)
    MIN_AUDIO_BYTES = 2000
    if len(contents) < MIN_AUDIO_BYTES:
        logger.info(f"Transcribe: audio too short ({len(contents)} bytes < {MIN_AUDIO_BYTES}), returning empty")
        return {"text": ""}

    logger.info(f"Transcribe: {audio.filename or 'audio'} ({len(contents)} bytes, type={audio.content_type})")

    # DEBUG: save audio to disk so we can verify what the mic is capturing
    try:
        debug_path = Path(__file__).parent.parent / "debug_audio"
        debug_path.mkdir(exist_ok=True)
        import time as _time
        ext = "wav" if "wav" in (audio.content_type or "") else "webm"
        debug_file = debug_path / f"recording_{int(_time.time())}.{ext}"
        debug_file.write_bytes(contents)
        logger.info(f"DEBUG: saved audio to {debug_file}")
    except Exception:
        pass

    try:
        import io
        audio_io = io.BytesIO(contents)
        audio_io.name = audio.filename or "recording.webm"

        text = transcribe_audio(audio_io)

        logger.info(f"Transcribed ({len(text)} chars): {text[:80]}{'...' if len(text) > 80 else ''}")
        return {"text": text}

    except RuntimeError as e:
        logger.error(f"Transcription config error: {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Transcription failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")


@router.get("/chat/session/{session_id}/files")
async def get_session_files(
    session_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Retrieve all files uploaded during a specific chat session from S3.
    Requires authentication.
    """
    try:
        files = list_session_files(session_id)
        return files
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to list session files: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve session files: {str(e)}")