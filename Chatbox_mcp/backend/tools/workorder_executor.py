# Chatbox_mcp/backend/tools/workorder_executor.py
"""
Work Order Executor - Creates, updates, and deletes contractor jobs in Simpro via MCP Server HTTP API.

Takes validated contractor job payloads from the workorder agent
and calls create_contractor_job / update_contractor_job / delete_contractor_job MCP tools via HTTP.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
import logging

from utils.mcp_tool_client import get_mcp_tool_client

logger = logging.getLogger(__name__)


async def _friendly_wo_error(
    raw_error: str,
    job_id: Any,
    cost_centre_id: Any,
    operation: str,
    llm_chat: Optional[Callable] = None,
) -> str:
    """Translate a raw Simpro work order error into a user-friendly message."""
    if llm_chat:
        try:
            from utils.crossroads import resolve_crossroads

            cr = await resolve_crossroads(
                crossroad_type="error_recovery",
                question=f"Work order {operation} error for Job {job_id}, CC {cost_centre_id}: {raw_error}",
                context={
                    "raw_error": raw_error,
                    "operation": f"{operation}_contractor_job",
                    "job_id": job_id,
                    "cost_centre_id": cost_centre_id,
                    "system": "Simpro ERP construction back-office",
                },
                llm_chat=llm_chat,
            )
            msg = cr.get("fields", {}).get("message")
            if msg:
                logger.info(f"🔀 WO error_recovery: '{raw_error[:40]}' → '{msg[:40]}'")
                return msg
        except Exception as e:
            logger.warning(f"WO error_recovery crossroads failed ({e}), using fallback")

    # Pattern-matching fallback
    lower = raw_error.lower()
    if "404" in lower or "not found" in lower:
        return f"Job {job_id}, CC {cost_centre_id}: Resource not found in Simpro."
    if "403" in lower or "forbidden" in lower:
        return f"Job {job_id}: Permission denied. Please check your Simpro access."
    if "422" in lower or "unprocessable" in lower:
        return f"Job {job_id}, CC {cost_centre_id}: Simpro rejected the data. Please check the fields."
    if "contractor" in lower and "cannot" in lower:
        return f"Job {job_id}: Cannot modify contractor field on an existing work order."
    if "502" in lower or "503" in lower or "504" in lower or "timeout" in lower:
        return f"Simpro is temporarily unavailable. Please retry."
    return raw_error


async def execute_workorder_operations(
    agent_result: Dict[str, Any],
    company_id: int = 2,
    llm_chat: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    Create, update, and/or delete contractor jobs in Simpro from agent result.

    Args:
        agent_result: Contains:
            contractor_jobs: list of payloads to CREATE
            contractor_job_updates: list of payloads to UPDATE (PATCH)
            contractor_job_deletes: list of payloads to DELETE
        company_id: Default company ID

    Returns:
        {
            "success": bool,
            "created": [...],
            "updated": [...],
            "deleted": [...],
            "failed": [...],
            "summary": {total, succeeded, failed, created_count, updated_count, deleted_count, message}
        }
    """
    contractor_jobs = agent_result.get("contractor_jobs", [])
    contractor_job_updates = agent_result.get("contractor_job_updates", [])
    contractor_job_deletes = agent_result.get("contractor_job_deletes", [])

    if not contractor_jobs and not contractor_job_updates and not contractor_job_deletes:
        logger.warning("No contractor jobs found in agent result")
        return {
            "success": False,
            "error": "NO_CONTRACTOR_JOBS",
            "detail": "No contractor jobs to create, update, or delete.",
        }

    mcp_client = get_mcp_tool_client()
    created: List[Dict[str, Any]] = []
    updated: List[Dict[str, Any]] = []
    deleted: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    # ── CREATE operations ─────────────────────────────────────────────────
    if contractor_jobs:
        logger.info(f"Creating {len(contractor_jobs)} contractor job(s) via MCP server HTTP API...")

    for payload in contractor_jobs:
        job_id = payload.get("job_id")
        section_id = payload.get("section_id")
        cost_centre_id = payload.get("cost_centre_id")
        contractor_job_data = payload.get("contractor_job_data", {})
        contractor_name = payload.get("contractor_name", "")

        if not all([job_id, section_id, cost_centre_id, contractor_job_data]):
            logger.error(
                f"Skipping contractor job with missing fields: "
                f"job={job_id}, sec={section_id}, cc={cost_centre_id}"
            )
            failed.append({
                "job_id": job_id,
                "cost_centre_id": cost_centre_id,
                "contractor_name": contractor_name,
                "error": "MISSING_FIELDS",
                "detail": "Required fields (job_id, section_id, cost_centre_id, contractor_job_data) missing.",
            })
            continue

        try:
            logger.info(
                f"Creating contractor job: job={job_id}, sec={section_id}, "
                f"cc={cost_centre_id}, contractor={contractor_name}"
            )

            # Only send fields the Simpro API accepts
            _SIMPRO_CJ_CREATE_FIELDS = {
                "Contractor", "Description", "Materials", "Labor", "TaxCode",
                "DateIssued", "ContractorSupplyMaterials", "Items", "Status",
                "OrderNo", "Notes", "InternalNotes",
            }
            clean_data = {k: v for k, v in contractor_job_data.items() if k in _SIMPRO_CJ_CREATE_FIELDS}

            result = await mcp_client.execute_tool("create_contractor_job", {
                "job_id": job_id,
                "section_id": section_id,
                "cost_centre_id": cost_centre_id,
                "contractor_job_data": clean_data,
            })

            if result.get("success"):
                data = result.get("data", {})

                # Check tool-level success
                if isinstance(data, dict) and data.get("success") is False:
                    error_msg = data.get("error", "Unknown tool error")
                    logger.error(
                        f"Tool-level failure creating contractor job: job={job_id}, "
                        f"cc={cost_centre_id}: {error_msg}"
                    )
                    friendly = await _friendly_wo_error(error_msg, job_id, cost_centre_id, "create", llm_chat)
                    failed.append({
                        "job_id": job_id,
                        "cost_centre_id": cost_centre_id,
                        "contractor_name": contractor_name,
                        "error": "CREATION_FAILED",
                        "detail": error_msg,
                        "friendly": friendly,
                    })
                else:
                    cj_id = data.get("contractor_job", {}).get("ID") if isinstance(data.get("contractor_job"), dict) else None
                    logger.info(
                        f"Contractor job created: job={job_id}, cc={cost_centre_id}, "
                        f"contractor={contractor_name}, CJ_ID={cj_id}"
                    )
                    created.append({
                        "job_id": job_id,
                        "section_id": section_id,
                        "cost_centre_id": cost_centre_id,
                        "contractor_name": contractor_name,
                        "contractor_job_id": cj_id,
                        "status": "created",
                        "materials_total": payload.get("materials_total", 0),
                        "labour_total": payload.get("labour_total", 0),
                        "item_count": payload.get("item_count", 0),
                    })
            else:
                error_msg = result.get("error", "Unknown error")
                logger.error(
                    f"Failed to create contractor job: job={job_id}, "
                    f"cc={cost_centre_id}: {error_msg}"
                )
                friendly = await _friendly_wo_error(error_msg, job_id, cost_centre_id, "create", llm_chat)
                failed.append({
                    "job_id": job_id,
                    "cost_centre_id": cost_centre_id,
                    "contractor_name": contractor_name,
                    "error": "CREATION_FAILED",
                    "detail": error_msg,
                    "friendly": friendly,
                })

        except Exception as e:
            logger.error(
                f"Exception creating contractor job: job={job_id}, "
                f"cc={cost_centre_id}: {e}"
            )
            friendly = await _friendly_wo_error(str(e), job_id, cost_centre_id, "create", llm_chat)
            failed.append({
                "job_id": job_id,
                "cost_centre_id": cost_centre_id,
                "contractor_name": contractor_name,
                "error": "EXCEPTION",
                "detail": str(e),
                "friendly": friendly,
            })

    # ── UPDATE operations ─────────────────────────────────────────────────
    if contractor_job_updates:
        logger.info(f"Updating {len(contractor_job_updates)} contractor job(s) via MCP server HTTP API...")

    for payload in contractor_job_updates:
        job_id = payload.get("job_id")
        section_id = payload.get("section_id")
        cost_centre_id = payload.get("cost_centre_id")
        contractor_job_data = payload.get("contractor_job_data", {})
        contractor_name = payload.get("contractor_name", "")
        cj_id = payload.get("_existing_cj_id")

        if not all([job_id, section_id, cost_centre_id, cj_id, contractor_job_data]):
            logger.error(
                f"Skipping contractor job update with missing fields: "
                f"job={job_id}, sec={section_id}, cc={cost_centre_id}, cj={cj_id}"
            )
            failed.append({
                "job_id": job_id,
                "cost_centre_id": cost_centre_id,
                "contractor_name": contractor_name,
                "error": "MISSING_FIELDS",
                "detail": "Required fields for update missing.",
            })
            continue

        # Only send fields the Simpro API accepts — strip internal config keys
        _SIMPRO_CJ_FIELDS = {
            "Description", "Materials", "Labor", "TaxCode",
            "DateIssued", "ContractorSupplyMaterials", "Items", "Status",
            "OrderNo", "Notes", "InternalNotes",
        }
        patch_data = {k: v for k, v in contractor_job_data.items() if k in _SIMPRO_CJ_FIELDS}

        try:
            logger.info(
                f"Updating contractor job {cj_id}: job={job_id}, sec={section_id}, "
                f"cc={cost_centre_id}, contractor={contractor_name}"
            )

            result = await mcp_client.execute_tool("update_contractor_job", {
                "job_id": job_id,
                "section_id": section_id,
                "cost_centre_id": cost_centre_id,
                "contractor_job_id": cj_id,
                "contractor_job_data": patch_data,
            })

            if result.get("success"):
                data = result.get("data", {})

                # Check tool-level success
                if isinstance(data, dict) and data.get("success") is False:
                    error_msg = data.get("error", "Unknown tool error")
                    logger.error(
                        f"Tool-level failure updating contractor job {cj_id}: "
                        f"job={job_id}, cc={cost_centre_id}: {error_msg}"
                    )
                    friendly = await _friendly_wo_error(error_msg, job_id, cost_centre_id, "update", llm_chat)
                    failed.append({
                        "job_id": job_id,
                        "cost_centre_id": cost_centre_id,
                        "contractor_name": contractor_name,
                        "contractor_job_id": cj_id,
                        "error": "UPDATE_FAILED",
                        "detail": error_msg,
                        "friendly": friendly,
                    })
                else:
                    logger.info(
                        f"Contractor job updated: CJ_ID={cj_id}, job={job_id}, "
                        f"cc={cost_centre_id}, contractor={contractor_name}"
                    )
                    updated.append({
                        "job_id": job_id,
                        "section_id": section_id,
                        "cost_centre_id": cost_centre_id,
                        "contractor_name": contractor_name,
                        "contractor_job_id": cj_id,
                        "status": "updated",
                        "materials_total": payload.get("materials_total", 0),
                        "labour_total": payload.get("labour_total", 0),
                        "item_count": payload.get("item_count", 0),
                    })
            else:
                error_msg = result.get("error", "Unknown error")
                logger.error(
                    f"Failed to update contractor job {cj_id}: "
                    f"job={job_id}, cc={cost_centre_id}: {error_msg}"
                )
                friendly = await _friendly_wo_error(error_msg, job_id, cost_centre_id, "update", llm_chat)
                failed.append({
                    "job_id": job_id,
                    "cost_centre_id": cost_centre_id,
                    "contractor_name": contractor_name,
                    "contractor_job_id": cj_id,
                    "error": "UPDATE_FAILED",
                    "detail": error_msg,
                    "friendly": friendly,
                })

        except Exception as e:
            logger.error(
                f"Exception updating contractor job {cj_id}: "
                f"job={job_id}, cc={cost_centre_id}: {e}"
            )
            friendly = await _friendly_wo_error(str(e), job_id, cost_centre_id, "update", llm_chat)
            failed.append({
                "job_id": job_id,
                "cost_centre_id": cost_centre_id,
                "contractor_name": contractor_name,
                "contractor_job_id": cj_id,
                "error": "EXCEPTION",
                "detail": str(e),
                "friendly": friendly,
            })

    # ── DELETE operations ─────────────────────────────────────────────────
    if contractor_job_deletes:
        logger.info(f"Deleting {len(contractor_job_deletes)} contractor job(s) via MCP server HTTP API...")

    for payload in contractor_job_deletes:
        job_id = payload.get("job_id")
        section_id = payload.get("section_id")
        cost_centre_id = payload.get("cost_centre_id")
        contractor_job_id = payload.get("contractor_job_id")
        contractor_name = payload.get("contractor_name", "")

        if not all([job_id, section_id, cost_centre_id, contractor_job_id]):
            logger.error(
                f"Skipping contractor job delete with missing fields: "
                f"job={job_id}, sec={section_id}, cc={cost_centre_id}, cj={contractor_job_id}"
            )
            failed.append({
                "job_id": job_id,
                "cost_centre_id": cost_centre_id,
                "contractor_name": contractor_name,
                "contractor_job_id": contractor_job_id,
                "error": "MISSING_FIELDS",
                "detail": "Required fields for delete missing (job_id, section_id, cost_centre_id, contractor_job_id).",
            })
            continue

        try:
            logger.info(
                f"Deleting contractor job {contractor_job_id}: job={job_id}, "
                f"sec={section_id}, cc={cost_centre_id}, contractor={contractor_name}"
            )

            result = await mcp_client.execute_tool("delete_contractor_job", {
                "job_id": job_id,
                "section_id": section_id,
                "cost_centre_id": cost_centre_id,
                "contractor_job_id": contractor_job_id,
            })

            if result.get("success"):
                data = result.get("data", {})

                # Check tool-level success
                if isinstance(data, dict) and data.get("success") is False:
                    error_msg = data.get("error", "Unknown tool error")
                    logger.error(
                        f"Tool-level failure deleting contractor job {contractor_job_id}: "
                        f"job={job_id}, cc={cost_centre_id}: {error_msg}"
                    )
                    friendly = await _friendly_wo_error(error_msg, job_id, cost_centre_id, "delete", llm_chat)
                    failed.append({
                        "job_id": job_id,
                        "cost_centre_id": cost_centre_id,
                        "contractor_name": contractor_name,
                        "contractor_job_id": contractor_job_id,
                        "error": "DELETION_FAILED",
                        "detail": error_msg,
                        "friendly": friendly,
                    })
                else:
                    logger.info(
                        f"Contractor job deleted: CJ_ID={contractor_job_id}, "
                        f"job={job_id}, cc={cost_centre_id}"
                    )
                    deleted.append({
                        "job_id": job_id,
                        "section_id": section_id,
                        "cost_centre_id": cost_centre_id,
                        "contractor_name": contractor_name,
                        "contractor_job_id": contractor_job_id,
                        "status": "deleted",
                    })
            else:
                error_msg = result.get("error", "Unknown error")
                logger.error(
                    f"Failed to delete contractor job {contractor_job_id}: "
                    f"job={job_id}, cc={cost_centre_id}: {error_msg}"
                )
                friendly = await _friendly_wo_error(error_msg, job_id, cost_centre_id, "delete", llm_chat)
                failed.append({
                    "job_id": job_id,
                    "cost_centre_id": cost_centre_id,
                    "contractor_name": contractor_name,
                    "contractor_job_id": contractor_job_id,
                    "error": "DELETION_FAILED",
                    "detail": error_msg,
                    "friendly": friendly,
                })

        except Exception as e:
            logger.error(
                f"Exception deleting contractor job {contractor_job_id}: "
                f"job={job_id}, cc={cost_centre_id}: {e}"
            )
            friendly = await _friendly_wo_error(str(e), job_id, cost_centre_id, "delete", llm_chat)
            failed.append({
                "job_id": job_id,
                "cost_centre_id": cost_centre_id,
                "contractor_name": contractor_name,
                "contractor_job_id": contractor_job_id,
                "error": "EXCEPTION",
                "detail": str(e),
                "friendly": friendly,
            })

    # ── Summary ───────────────────────────────────────────────────────────
    total = len(contractor_jobs) + len(contractor_job_updates) + len(contractor_job_deletes)
    success_count = len(created) + len(updated) + len(deleted)
    failed_count = len(failed)

    parts = []
    if created:
        parts.append(f"Created {len(created)}")
    if updated:
        parts.append(f"Updated {len(updated)}")
    if deleted:
        parts.append(f"Deleted {len(deleted)}")
    summary_msg = (
        f"{', '.join(parts)} of {total} contractor job(s)"
        if parts else f"0 of {total} contractor job(s)"
    )
    if failed_count:
        summary_msg += f", {failed_count} failed"
    summary_msg += "."

    logger.info(f"Contractor job operations complete: {summary_msg}")

    return {
        "success": success_count > 0,
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "failed": failed,
        "summary": {
            "total": total,
            "succeeded": success_count,
            "failed": failed_count,
            "created_count": len(created),
            "updated_count": len(updated),
            "deleted_count": len(deleted),
            "message": summary_msg,
        },
    }
