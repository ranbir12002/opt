"""
Invoice Executor - Calls MCP tools via MCP Server HTTP API.

Creates, updates, and deletes invoices in Simpro by calling
the create_invoice / update_invoice / delete_invoice tools
via the MCP Server's HTTP endpoint.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional
import logging

from utils.mcp_tool_client import get_mcp_tool_client

logger = logging.getLogger(__name__)


async def _friendly_invoice_error(
    raw_error: str,
    job_id: Any,
    invoice_body: Dict[str, Any],
    llm_chat: Optional[Callable] = None,
) -> str:
    """Translate a raw Simpro invoice error into a user-friendly message.

    Uses crossroads error_recovery when an LLM chat function is available,
    otherwise falls back to pattern matching.
    """
    if llm_chat:
        try:
            from utils.crossroads import resolve_crossroads

            cr = await resolve_crossroads(
                crossroad_type="error_recovery",
                question=f"Invoice creation error for Job {job_id}: {raw_error}",
                context={
                    "raw_error": raw_error,
                    "operation": "create_invoice",
                    "job_id": job_id,
                    "invoice_type": invoice_body.get("Type"),
                    "per_item": invoice_body.get("PerItem"),
                    "has_cost_centers": bool(invoice_body.get("CostCenters")),
                    "system": "Simpro ERP construction back-office",
                },
                llm_chat=llm_chat,
            )
            msg = cr.get("fields", {}).get("message")
            if msg:
                logger.info(f"🔀 Invoice error_recovery: '{raw_error[:40]}' → '{msg[:40]}'")
                return msg
        except Exception as e:
            logger.warning(f"Invoice error_recovery crossroads failed ({e}), using fallback")

    # Pattern-matching fallback
    lower = raw_error.lower()
    if "cost centers must be removed" in lower or "cost centres must be removed" in lower:
        return (
            f"Job {job_id}: Cannot include cost centres in a consolidated invoice. "
            f"Either switch to PerItem=true or remove cost centre claims."
        )
    if "already" in lower and ("100%" in lower or "claimed" in lower):
        return f"Job {job_id}: One or more cost centres are already fully claimed (100%)."
    if "422" in lower or "unprocessable" in lower:
        return f"Job {job_id}: Simpro rejected the invoice data. Please check the invoice fields."
    if "404" in lower or "not found" in lower:
        return f"Job {job_id}: The job or cost centre was not found in Simpro."
    if "403" in lower or "forbidden" in lower:
        return f"Job {job_id}: Permission denied. Please check your Simpro access."
    if "502" in lower or "503" in lower or "504" in lower or "timeout" in lower:
        return f"Job {job_id}: Simpro is temporarily unavailable. Please retry."
    return raw_error


async def create_invoices_from_agent_result(
    agent_result: Dict[str, Any],
    company_id: int = 2,
    llm_chat: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    Create invoices in Simpro using agent result.

    Takes the invoice bodies prepared by the invoice agent and creates them
    in Simpro by calling the MCP server's create_invoice tool via HTTP.

    Args:
        agent_result: Result from invoice_agent containing job_results
        company_id: Default company ID if not specified in agent result

    Returns:
        Dict with creation results:
        {
            "success": True/False,
            "created": [...],  # Successfully created invoices
            "failed": [...],   # Failed invoices with errors
            "summary": {...}   # Summary statistics
        }
    """
    # Extract job results from agent output
    jobs = agent_result.get("jobs", [])

    if not jobs:
        logger.warning("No jobs found in agent result")
        return {
            "success": False,
            "error": "NO_JOBS",
            "detail": "Agent did not return any invoice bodies to create"
        }

    logger.info(f"Creating {len(jobs)} invoices via MCP server HTTP API...")

    mcp_client = get_mcp_tool_client()
    created = []
    warnings = []
    failed = []

    for job_data in jobs:
        # Extract invoice data
        request_data = job_data.get("request", {})
        job_company_id = request_data.get("company_id") or company_id
        invoice_body = request_data.get("body", {})
        job_id = job_data.get("job_id")

        if not invoice_body:
            logger.error(f"Job {job_id}: No invoice body found")
            failed.append({
                "job_id": job_id,
                "error": "NO_BODY",
                "detail": "Invoice body missing from agent result"
            })
            continue

        try:
            logger.info(f"Creating invoice for JobID={job_id}, CompanyID={job_company_id}")

            # Call tool via MCP server HTTP API
            result = await mcp_client.execute_tool("create_invoice", {
                "company_id": job_company_id,
                "invoice_data": invoice_body
            })

            # MCP server returns {success, data, tool, error}
            # Note: outer "success" = HTTP call succeeded; inner data.success = tool result
            if result.get("success"):
                data = result.get("data", {})

                # Check tool-level success (the tool may catch errors internally
                # and return {success: False} as a normal return value)
                if isinstance(data, dict) and data.get("success") is False:
                    error_msg = data.get("error", "Unknown tool error")
                    logger.error(f"❌ Tool-level failure for JobID={job_id}: {error_msg}")
                    friendly = await _friendly_invoice_error(error_msg, job_id, invoice_body, llm_chat)
                    failed.append({
                        "job_id": job_id,
                        "error": "CREATION_FAILED",
                        "detail": error_msg,
                        "friendly": friendly,
                    })
                    continue

                status = data.get("status", "created")
                invoice_id = data.get("invoice_id")

                if status == "warning":
                    # Simpro accepted but returned no ID (e.g. already fully claimed)
                    logger.warning(
                        f"⚠️ Warning for JobID={job_id}: {data.get('warning', 'No invoice ID returned')}"
                    )
                    warnings.append({
                        "job_id": job_id,
                        "invoice_id": None,
                        "status": "warning",
                        "warning": data.get("warning", "Invoice not created — Simpro returned no ID"),
                        "cost_centre_ids": data.get("cost_centre_ids", []),
                    })
                else:
                    logger.info(f"✅ Invoice created: JobID={job_id}, InvoiceID={invoice_id}")
                    created.append({
                        "job_id": job_id,
                        "invoice_id": invoice_id,
                        "status": "created",
                        "invoice": data.get("invoice", {})
                    })
            else:
                error_msg = result.get("error", "Unknown error")
                logger.error(f"❌ Failed to create invoice for JobID={job_id}: {error_msg}")

                friendly = await _friendly_invoice_error(error_msg, job_id, invoice_body, llm_chat)
                failed.append({
                    "job_id": job_id,
                    "error": "CREATION_FAILED",
                    "detail": error_msg,
                    "friendly": friendly,
                })

        except Exception as e:
            logger.error(f"❌ Exception creating invoice for JobID={job_id}: {e}")
            friendly = await _friendly_invoice_error(str(e), job_id, invoice_body, llm_chat)
            failed.append({
                "job_id": job_id,
                "error": "EXCEPTION",
                "detail": str(e),
                "friendly": friendly,
            })

    # Build result summary
    total = len(jobs)
    success_count = len(created)
    warning_count = len(warnings)
    failed_count = len(failed)

    result = {
        "success": success_count > 0,
        "created": created,
        "warnings": warnings,
        "failed": failed,
        "summary": {
            "total": total,
            "created": success_count,
            "warnings": warning_count,
            "failed": failed_count,
            "success_rate": f"{(success_count/total*100):.1f}%" if total > 0 else "0%"
        }
    }

    logger.info(
        f"Invoice creation complete: {success_count} created, "
        f"{warning_count} warnings, {failed_count} failed out of {total}"
    )

    return result


async def update_invoices_from_agent_result(
    agent_result: Dict[str, Any],
    company_id: int = 2,
    llm_chat: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    Update invoices in Simpro using agent result.

    Takes invoice update payloads from the invoice agent and patches them
    in Simpro by calling the MCP server's update_invoice tool via HTTP.

    Args:
        agent_result: Result from invoice_agent containing invoice_updates
        company_id: Default company ID if not specified

    Returns:
        Dict with update results:
        {
            "success": True/False,
            "updated": [...],
            "failed": [...],
            "summary": {...}
        }
    """
    updates = agent_result.get("invoice_updates", [])

    if not updates:
        logger.warning("No invoice updates found in agent result")
        return {
            "success": False,
            "error": "NO_UPDATES",
            "detail": "Agent did not return any invoice update payloads",
        }

    logger.info(f"Updating {len(updates)} invoice(s) via MCP server HTTP API...")

    mcp_client = get_mcp_tool_client()
    updated = []
    failed = []

    for payload in updates:
        invoice_id = payload.get("invoice_id")
        invoice_data = payload.get("invoice_data", {})

        if not invoice_id:
            logger.error("Skipping invoice update with missing invoice_id")
            failed.append({
                "invoice_id": invoice_id,
                "error": "MISSING_ID",
                "detail": "invoice_id is required for update",
            })
            continue

        if not invoice_data:
            logger.error(f"Skipping invoice {invoice_id} update with empty data")
            failed.append({
                "invoice_id": invoice_id,
                "error": "NO_DATA",
                "detail": "No fields to update",
            })
            continue

        try:
            logger.info(f"Updating invoice {invoice_id}: fields={list(invoice_data.keys())}")

            result = await mcp_client.execute_tool("update_invoice", {
                "invoice_id": invoice_id,
                "invoice_data": invoice_data,
            })

            if result.get("success"):
                data = result.get("data", {})

                # Check tool-level success
                if isinstance(data, dict) and data.get("success") is False:
                    error_msg = data.get("error", "Unknown tool error")
                    logger.error(f"❌ Tool-level failure updating invoice {invoice_id}: {error_msg}")
                    friendly = await _friendly_invoice_error(
                        error_msg, invoice_id, invoice_data, llm_chat
                    )
                    failed.append({
                        "invoice_id": invoice_id,
                        "error": "UPDATE_FAILED",
                        "detail": error_msg,
                        "friendly": friendly,
                    })
                else:
                    logger.info(f"✅ Invoice {invoice_id} updated successfully")
                    updated.append({
                        "invoice_id": invoice_id,
                        "status": "updated",
                        "fields_updated": list(invoice_data.keys()),
                    })
            else:
                error_msg = result.get("error", "Unknown error")
                logger.error(f"❌ Failed to update invoice {invoice_id}: {error_msg}")
                friendly = await _friendly_invoice_error(
                    error_msg, invoice_id, invoice_data, llm_chat
                )
                failed.append({
                    "invoice_id": invoice_id,
                    "error": "UPDATE_FAILED",
                    "detail": error_msg,
                    "friendly": friendly,
                })

        except Exception as e:
            logger.error(f"❌ Exception updating invoice {invoice_id}: {e}")
            friendly = await _friendly_invoice_error(
                str(e), invoice_id, invoice_data, llm_chat
            )
            failed.append({
                "invoice_id": invoice_id,
                "error": "EXCEPTION",
                "detail": str(e),
                "friendly": friendly,
            })

    total = len(updates)
    success_count = len(updated)
    failed_count = len(failed)

    result = {
        "success": success_count > 0,
        "updated": updated,
        "failed": failed,
        "summary": {
            "total": total,
            "updated": success_count,
            "failed": failed_count,
            "message": f"Updated {success_count} of {total} invoice(s)"
                       + (f", {failed_count} failed" if failed_count else "")
                       + ".",
        },
    }

    logger.info(f"Invoice update complete: {result['summary']['message']}")
    return result


async def delete_invoices_from_agent_result(
    agent_result: Dict[str, Any],
    company_id: int = 2,
    llm_chat: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    Delete invoices in Simpro using agent result.

    Takes invoice delete payloads from the invoice agent and deletes them
    in Simpro by calling the MCP server's delete_invoice tool via HTTP.

    Args:
        agent_result: Result from invoice_agent containing invoice_deletes
        company_id: Default company ID if not specified

    Returns:
        Dict with delete results:
        {
            "success": True/False,
            "deleted": [...],
            "failed": [...],
            "summary": {...}
        }
    """
    deletes = agent_result.get("invoice_deletes", [])

    if not deletes:
        logger.warning("No invoice deletes found in agent result")
        return {
            "success": False,
            "error": "NO_DELETES",
            "detail": "Agent did not return any invoice delete payloads",
        }

    logger.info(f"Deleting {len(deletes)} invoice(s) via MCP server HTTP API...")

    mcp_client = get_mcp_tool_client()
    deleted = []
    failed = []

    for payload in deletes:
        invoice_id = payload.get("invoice_id")

        if not invoice_id:
            logger.error("Skipping invoice delete with missing invoice_id")
            failed.append({
                "invoice_id": invoice_id,
                "error": "MISSING_ID",
                "detail": "invoice_id is required for delete",
            })
            continue

        try:
            logger.info(f"Deleting invoice {invoice_id}")

            result = await mcp_client.execute_tool("delete_invoice", {
                "invoice_id": invoice_id,
            })

            if result.get("success"):
                data = result.get("data", {})

                # Check tool-level success
                if isinstance(data, dict) and data.get("success") is False:
                    error_msg = data.get("error", "Unknown tool error")
                    logger.error(f"❌ Tool-level failure deleting invoice {invoice_id}: {error_msg}")
                    friendly = await _friendly_invoice_error(
                        error_msg, invoice_id, {}, llm_chat
                    )
                    failed.append({
                        "invoice_id": invoice_id,
                        "error": "DELETION_FAILED",
                        "detail": error_msg,
                        "friendly": friendly,
                    })
                else:
                    logger.info(f"✅ Invoice {invoice_id} deleted successfully")
                    deleted.append({
                        "invoice_id": invoice_id,
                        "status": "deleted",
                    })
            else:
                error_msg = result.get("error", "Unknown error")
                logger.error(f"❌ Failed to delete invoice {invoice_id}: {error_msg}")
                friendly = await _friendly_invoice_error(
                    error_msg, invoice_id, {}, llm_chat
                )
                failed.append({
                    "invoice_id": invoice_id,
                    "error": "DELETION_FAILED",
                    "detail": error_msg,
                    "friendly": friendly,
                })

        except Exception as e:
            logger.error(f"❌ Exception deleting invoice {invoice_id}: {e}")
            friendly = await _friendly_invoice_error(
                str(e), invoice_id, {}, llm_chat
            )
            failed.append({
                "invoice_id": invoice_id,
                "error": "EXCEPTION",
                "detail": str(e),
                "friendly": friendly,
            })

    total = len(deletes)
    success_count = len(deleted)
    failed_count = len(failed)

    result = {
        "success": success_count > 0,
        "deleted": deleted,
        "failed": failed,
        "summary": {
            "total": total,
            "deleted": success_count,
            "failed": failed_count,
            "message": f"Deleted {success_count} of {total} invoice(s)"
                       + (f", {failed_count} failed" if failed_count else "")
                       + ".",
        },
    }

    logger.info(f"Invoice delete complete: {result['summary']['message']}")
    return result


def is_tool_available() -> bool:
    """MCP tools are always available via HTTP - checked at call time."""
    return True
