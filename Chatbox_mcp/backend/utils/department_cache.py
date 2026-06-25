# backend/utils/department_cache.py
"""
Per-org department mapping cache.

Loads setup cost centres + chart of accounts from the org's MCP executor once daily,
combines with the org's stored department_mapping (from DB), and builds bidirectional
lookup maps:
  - cost_centre_id/name  ->  department name
  - department name       ->  [cost_centre entries]

The mapping is DB-only — no disk file fallback. If no mapping is stored for an org,
departments fall back to raw chart-of-accounts account names.

Usage:
    from utils.department_cache import get_department_cache

    cache = await get_department_cache(mcp_executor, org_id=42)
    dept = cache.get_department_for_cost_centre(cc_id=5)
    ccs = cache.get_cost_centres_for_department("Plumbing")
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Daily TTL: 24 hours
_CACHE_TTL_SECONDS = 86400

# Per-org cache state (keyed by org_id)
_cache_store: Dict[int, "DepartmentCache"] = {}
_cache_timestamps: Dict[int, float] = {}
_cache_locks: Dict[int, asyncio.Lock] = {}


def _get_lock(org_id: int) -> asyncio.Lock:
    """Lazily create a per-org asyncio lock (must be called in a running loop)."""
    if org_id not in _cache_locks:
        _cache_locks[org_id] = asyncio.Lock()
    return _cache_locks[org_id]


class DepartmentCache:
    """In-memory bidirectional department lookup for one org."""

    def __init__(
        self,
        cc_id_to_dept: Dict[int, str],
        cc_name_to_dept: Dict[str, str],
        dept_to_cost_centres: Dict[str, List[Dict[str, Any]]],
        department_names: List[str],
        setup_cost_centres: List[Dict[str, Any]],
        account_number_to_name: Dict[str, str],
        drift_warnings: List[str],
    ):
        self.cc_id_to_dept = cc_id_to_dept
        self.cc_name_to_dept = cc_name_to_dept
        self.dept_to_cost_centres = dept_to_cost_centres
        self.department_names = department_names
        self.setup_cost_centres = setup_cost_centres
        self.account_number_to_name = account_number_to_name
        self.drift_warnings = drift_warnings

    def get_department_for_cost_centre(
        self,
        cc_id: Optional[int] = None,
        cc_name: Optional[str] = None,
    ) -> Optional[str]:
        """Lookup department by cost centre ID or name."""
        if cc_id is not None and cc_id in self.cc_id_to_dept:
            return self.cc_id_to_dept[cc_id]
        if cc_name:
            return self.cc_name_to_dept.get(cc_name.lower().strip())
        return None

    def get_cost_centres_for_department(
        self, department: str
    ) -> List[Dict[str, Any]]:
        """Get all cost centres belonging to a department."""
        return self.dept_to_cost_centres.get(department.lower().strip(), [])

    def list_departments(self) -> List[str]:
        """Return all known department names."""
        return list(self.department_names)


def _validate_mapping(
    dept_mapping: Dict[str, List[str]],
    account_number_to_name: Dict[str, str],
) -> List[str]:
    """
    Compare stored dept_mapping against live chart-of-accounts data.
    Returns a list of human-readable warning strings. Empty list = all good.

    Warning types:
    - Account number in mapping no longer exists in Simpro (stale)
    - Account name changed in Simpro (renamed)
    - New accounts exist in Simpro that aren't mapped (unassigned — informational)
    """
    warnings: List[str] = []
    mapped_account_numbers: set = set()

    for dept_name, acct_numbers in dept_mapping.items():
        for acct_no in acct_numbers:
            acct_no_str = str(acct_no).strip()
            mapped_account_numbers.add(acct_no_str)
            if acct_no_str not in account_number_to_name:
                warnings.append(
                    f"Account {acct_no_str} (mapped to '{dept_name}') no longer exists in Simpro — remove or update this mapping."
                )

    # Informational: unmapped accounts (not a blocking warning, just helpful)
    unmapped = [
        f"{acct_no} ({name})"
        for acct_no, name in account_number_to_name.items()
        if acct_no not in mapped_account_numbers
    ]
    if unmapped:
        warnings.append(
            f"{len(unmapped)} account(s) in Simpro are not mapped to any department: "
            + ", ".join(unmapped[:10])
            + (" …and more" if len(unmapped) > 10 else "")
        )

    return warnings


def _build_cache(
    setup_cost_centres: List[Dict[str, Any]],
    chart_of_accounts: List[Dict[str, Any]],
    dept_mapping: Dict[str, List[str]],
    drift_warnings: List[str],
) -> "DepartmentCache":
    """Build the bidirectional lookup from raw API data + mapping."""

    # Step 1: account_number -> account_name
    account_number_to_name: Dict[str, str] = {}
    for acct in chart_of_accounts:
        number = acct.get("Number") or ""
        name = acct.get("Name") or ""
        if number:
            account_number_to_name[str(number).strip()] = name

    # Step 2: account_number -> department_name (from stored mapping)
    account_number_to_dept: Dict[str, str] = {}
    for dept_name, account_numbers in dept_mapping.items():
        for acct_num in account_numbers:
            account_number_to_dept[str(acct_num).strip()] = dept_name

    # Step 3: for each setup cost centre, resolve IncomeAccountNo -> department
    cc_id_to_dept: Dict[int, str] = {}
    cc_name_to_dept: Dict[str, str] = {}
    dept_to_ccs: Dict[str, List[Dict[str, Any]]] = {}

    for cc in setup_cost_centres:
        cc_id = cc.get("ID")
        cc_name = (cc.get("Name") or "").strip()
        income_acct_no = cc.get("IncomeAccountNo")

        if not cc_id:
            continue

        dept: Optional[str] = None
        if income_acct_no:
            acct_no_str = str(income_acct_no).strip()
            # Stored mapping takes priority
            dept = account_number_to_dept.get(acct_no_str)
            # Fallback: use the chart-of-accounts name directly
            if not dept:
                acct_name = account_number_to_name.get(acct_no_str, "")
                if acct_name:
                    dept = acct_name

        if dept:
            cc_id_to_dept[cc_id] = dept
            if cc_name:
                cc_name_to_dept[cc_name.lower()] = dept
            dept_lower = dept.lower().strip()
            if dept_lower not in dept_to_ccs:
                dept_to_ccs[dept_lower] = []
            dept_to_ccs[dept_lower].append({
                "id": cc_id,
                "name": cc_name,
                "income_account_no": income_acct_no,
            })
        else:
            logger.debug(
                f"Setup cost centre {cc_id} ('{cc_name}') has no department "
                f"(IncomeAccountNo={income_acct_no})"
            )

    # Collect unique department names (mapping first, then fallback additions)
    all_depts: List[str] = list(dept_mapping.keys())
    for dept_name in set(cc_id_to_dept.values()):
        if dept_name not in all_depts:
            all_depts.append(dept_name)

    logger.info(
        f"DepartmentCache built: {len(cc_id_to_dept)} cost centres mapped to "
        f"{len(dept_to_ccs)} departments"
    )

    return DepartmentCache(
        cc_id_to_dept=cc_id_to_dept,
        cc_name_to_dept=cc_name_to_dept,
        dept_to_cost_centres=dept_to_ccs,
        department_names=all_depts,
        setup_cost_centres=setup_cost_centres,
        account_number_to_name=account_number_to_name,
        drift_warnings=drift_warnings,
    )


async def get_department_cache(mcp_executor: Any, org_id: int) -> "DepartmentCache":
    """
    Get or build the department cache for a specific org.

    Uses a daily TTL per org. First call (or after TTL expiry) fetches live data
    from the org's MCP executor and builds the cache.
    """
    now = time.monotonic()
    cached = _cache_store.get(org_id)
    ts = _cache_timestamps.get(org_id, 0.0)
    if cached is not None and (now - ts) < _CACHE_TTL_SECONDS:
        return cached

    lock = _get_lock(org_id)
    async with lock:
        # Double-check after acquiring lock
        now = time.monotonic()
        cached = _cache_store.get(org_id)
        ts = _cache_timestamps.get(org_id, 0.0)
        if cached is not None and (now - ts) < _CACHE_TTL_SECONDS:
            return cached

        logger.info(
            f"Building department cache for org_id={org_id} "
            "(loading setup cost centres + chart of accounts)..."
        )

        # Fetch both endpoints in parallel
        setup_cc_result, coa_result = await asyncio.gather(
            mcp_executor.call_tool("get_setup_cost_centres", {"columns": "ID,Name,IncomeAccountNo"}),
            mcp_executor.call_tool("get_chart_of_accounts", {"columns": "ID,Name,Number"}),
        )

        cc_details = (
            setup_cc_result.get("setup_cost_centres", [])
            if isinstance(setup_cc_result, dict)
            else setup_cc_result
        )
        coa_details = (
            coa_result.get("chart_of_accounts", [])
            if isinstance(coa_result, dict)
            else coa_result
        )

        logger.info(
            f"org_id={org_id}: fetched {len(cc_details)} cost centres, "
            f"{len(coa_details)} chart-of-accounts entries"
        )

        # Load stored mapping from DB (DB-only, no disk fallback)
        from auth.database import get_org_department_mapping, set_org_dept_warnings
        dept_mapping = get_org_department_mapping(org_id) or {}

        # Build account_number_to_name for drift validation
        account_number_to_name: Dict[str, str] = {
            str(a.get("Number", "")).strip(): (a.get("Name") or "")
            for a in coa_details
            if a.get("Number")
        }

        # Validate stored mapping against live data
        drift_warnings = _validate_mapping(dept_mapping, account_number_to_name)
        if drift_warnings:
            logger.warning(
                f"org_id={org_id}: {len(drift_warnings)} department mapping drift warning(s)"
            )
        # Persist warnings to DB so UI can show them without triggering a rebuild
        try:
            set_org_dept_warnings(org_id, drift_warnings)
        except Exception as e:
            logger.warning(f"org_id={org_id}: failed to persist dept warnings: {e}")

        cache = _build_cache(cc_details, coa_details, dept_mapping, drift_warnings)
        _cache_store[org_id] = cache
        _cache_timestamps[org_id] = time.monotonic()

        return cache


async def refresh_department_cache(mcp_executor: Any, org_id: int) -> "DepartmentCache":
    """Force-refresh the cache for a specific org (e.g., after mapping update)."""
    _cache_store.pop(org_id, None)
    _cache_timestamps.pop(org_id, None)
    return await get_department_cache(mcp_executor, org_id)


def invalidate_department_cache(org_id: int) -> None:
    """Evict the cached entry for an org (call after saving a new mapping)."""
    _cache_store.pop(org_id, None)
    _cache_timestamps.pop(org_id, None)


async def resolve_cc_instances_to_departments(
    mcp_executor: Any,
    cc_instance_pairs: List[Tuple[int, int]],
    org_id: int,
) -> Dict[int, str]:
    """
    Map CC instance IDs (from schedule References) to department names.

    Args:
        mcp_executor: MCPToolExecutor for making MCP tool calls.
        cc_instance_pairs: List of (job_id, cc_instance_id) tuples.
        org_id: The org whose cache to use.

    Returns:
        {cc_instance_id: department_name} for every instance that could be resolved.
    """
    if not cc_instance_pairs:
        return {}

    cache = await get_department_cache(mcp_executor, org_id)

    # Group by job_id to minimise API calls
    jobs: Dict[int, set] = defaultdict(set)
    for job_id, cc_id in cc_instance_pairs:
        jobs[job_id].add(cc_id)

    result: Dict[int, str] = {}

    for job_id, needed_cc_ids in jobs.items():
        try:
            sections_resp = await mcp_executor.call_tool(
                "get_job_sections", {"job_id": job_id}
            )
            section_list = (
                sections_resp.get("sections", [])
                if isinstance(sections_resp, dict)
                else sections_resp if isinstance(sections_resp, list) else []
            )
        except Exception as e:
            logger.warning(f"resolve_cc_instances: failed to get sections for job {job_id}: {e}")
            continue

        for section in section_list:
            section_id = section.get("ID")
            if not section_id:
                continue

            try:
                ccs_resp = await mcp_executor.call_tool(
                    "get_job_section_cost_centres",
                    {"job_id": job_id, "section_id": section_id},
                )
                cc_list = (
                    ccs_resp.get("cost_centres", [])
                    if isinstance(ccs_resp, dict)
                    else ccs_resp if isinstance(ccs_resp, list) else []
                )
            except Exception as e:
                logger.warning(
                    f"resolve_cc_instances: failed to get CCs for job {job_id} "
                    f"section {section_id}: {e}"
                )
                continue

            for cc in cc_list:
                instance_id = cc.get("ID")
                if instance_id not in needed_cc_ids:
                    continue

                cost_center_ref = cc.get("CostCenter") or {}
                type_id = (
                    cost_center_ref.get("ID")
                    if isinstance(cost_center_ref, dict)
                    else cost_center_ref
                )
                if type_id:
                    dept = cache.get_department_for_cost_centre(cc_id=type_id)
                    if dept:
                        result[instance_id] = dept

            # Early exit if all needed CCs for this job are resolved
            if needed_cc_ids.issubset(result.keys()):
                break

    logger.info(
        f"resolve_cc_instances: resolved {len(result)}/{len(cc_instance_pairs)} "
        f"CC instances to departments"
    )
    return result
