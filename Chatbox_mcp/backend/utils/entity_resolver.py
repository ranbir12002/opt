# backend/utils/entity_resolver.py
"""
Central Simpro Entity Resolver — THE SINGLE GATEWAY for name → ID resolution.

All agents MUST use this class for resolving user-provided entity names to
Simpro IDs. Do NOT write inline name matching (substring, .lower() ==, etc.)
in agent code. Every resolve method uses fuzzy matching with typo tolerance,
auto-selection, and LLM disambiguation.

Covered entity types:
    - Staff (employees + contractors)  →  resolve_staff()
    - Jobs                             →  resolve_job()
    - Sections                         →  resolve_section()
    - Cost Centres                     →  resolve_cost_centre()
    - Contractors                      →  resolve_contractor()
    - Customers                        →  resolve_customer()
    - Cost Centre Types (departments)  →  resolve_cost_centre_type()
    - Departments (via cost centre)    →  resolve_department()
    - Company IDs                      →  resolve_company_id()
    - Batch (multi-field, phased)      →  resolve_batch()

Usage:
    from utils.entity_resolver import EntityResolver, ResolutionError, AmbiguousResolutionError
    resolver = EntityResolver(mcp_executor, llm_chat=llm_chat)

    staff      = await resolver.resolve_staff(name="Stephen")
    job        = await resolver.resolve_job(name="Main St")
    section    = await resolver.resolve_section(job_id=20990, name="Electrical")
    cc         = await resolver.resolve_cost_centre(job_id=20990, section_id=1, name="Wiring")
    contractor = await resolver.resolve_contractor(name="Allister")
    customer   = await resolver.resolve_customer(name="ABC Construction")
    cc_type    = await resolver.resolve_cost_centre_type(name="Plumbing")
    dept       = await resolver.resolve_department(name="Plumbing")
    dept       = await resolver.resolve_department(cost_centre_id=42)

Adding a new entity type:
    1. Add a resolve_X() method to EntityResolver following the pattern:
       - Fetch candidates via mcp_executor.call_tool(...)
       - Call fuzzy_match_entities() from utils.fuzzy_match
       - Call self._pick_best() for auto-selection / ambiguity handling
    2. Update this docstring to list the new type above.
    3. Do NOT add inline name matching in agent code — always centralise here.
"""

from __future__ import annotations
import logging
from typing import Any, Callable, Dict, List, Optional

from utils.decision_journal import record_decision

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Resolution Exceptions (shared across all agents)
# ═══════════════════════════════════════════════════════════════════════════

class ResolutionError(Exception):
    """No matches found for a name/ID."""
    def __init__(self, message: str, partial_data: Optional[Dict[str, Any]] = None):
        self.partial_data = partial_data or {}
        super().__init__(message)


class AmbiguousResolutionError(Exception):
    """Multiple matches found — user must choose."""
    def __init__(self, field: str, value: str, matches: List[Dict[str, Any]], message: str = ""):
        self.field = field
        self.value = value
        self.matches = matches  # [{"id": 1, "name": "..."}]
        self.message = message or f"Multiple matches for {field}: {value}"
        super().__init__(self.message)


class MissingFieldError(Exception):
    """Required field not provided — user must specify."""
    def __init__(self, field: str, message: str, **context):
        self.field = field
        self.message = message
        self.context = context  # Additional context (e.g., available options)
        super().__init__(message)


class ValidationError(Exception):
    """General validation error."""
    pass


class BatchedClarificationError(Exception):
    """Multiple independent fields need user clarification.

    Raised by resolve_batch() when two or more independent resolution
    steps each need user input (AmbiguousResolutionError or MissingFieldError).
    Callers can unpack ``self.errors`` to build individual clarification items.
    """
    def __init__(
        self,
        errors: List[Exception],
        partial_resolved: Optional[Dict[str, Any]] = None,
    ):
        self.errors = errors  # [AmbiguousResolutionError | MissingFieldError, ...]
        self.partial_resolved = partial_resolved or {}
        super().__init__(f"{len(errors)} fields need clarification")


# ═══════════════════════════════════════════════════════════════════════════
# EntityResolver
# ═══════════════════════════════════════════════════════════════════════════

class EntityResolver:
    """
    Central resolver for Simpro entities.

    Provides consistent name → ID resolution with:
    - Fuzzy matching (substring → word-level → edit-distance)
    - Auto-selection when a single clear match exists
    - Crossroads LLM disambiguation when multiple matches exist
    - Structured exceptions for user clarification
    """

    def __init__(self, mcp_executor: Any, llm_chat: Optional[Callable] = None, org_id: int = 0):
        """
        Args:
            mcp_executor: MCPToolExecutor instance for calling Simpro API tools
            llm_chat: Optional LLM chat function for crossroads disambiguation
            org_id: Organisation ID — used to key the per-org department cache
        """
        self.mcp_executor = mcp_executor
        self.llm_chat = llm_chat
        self.org_id = org_id

    # ──────────────────────────────────────────────────────────────────────
    # Staff Resolution
    # ──────────────────────────────────────────────────────────────────────

    async def resolve_staff(
        self,
        name: Optional[str] = None,
        staff_id: Optional[int] = None,
        row_num: int = 0,
    ) -> Dict[str, Any]:
        """
        Resolve staff name → {id, name}.

        3-stage pipeline: API wildcard → fuzzy match → post-filter.
        Search order: list_employees → list_contractors (in parallel).

        Returns:
            {"id": int, "name": str}
        """
        if staff_id:
            return {"id": int(staff_id), "name": name or ""}

        if not name:
            raise MissingFieldError(
                field="StaffName",
                message=f"Row {row_num}: Staff not specified",
            )

        import asyncio
        from utils.fuzzy_match import fuzzy_match_entities, deduplicate_matches

        query = name.lower().strip()
        all_matches = []

        # Fetch candidates from ALL staff tools in parallel
        # Stage 1: API wildcard search narrows the dataset
        staff_tools = await self._get_staff_tools()

        async def _fetch_and_match(tool_name):
            try:
                candidates = await self._fetch_staff_candidates(tool_name, search_query=name)
                extra_fields = ["ContactName"] if tool_name == "list_contractors" else None
                return fuzzy_match_entities(
                    query, candidates,
                    extra_name_fields=extra_fields,
                    source=tool_name,
                )
            except Exception as e:
                logger.warning(f"Row {row_num}: Tool {tool_name} failed: {e}")
                return []

        tool_results = await asyncio.gather(*[_fetch_and_match(t) for t in staff_tools])
        for tool_matches in tool_results:
            all_matches.extend(tool_matches)

        matches = deduplicate_matches(all_matches)
        logger.info(f"Row {row_num}: {len(matches)} staff matched '{name}' (top: {matches[0]['score'] if matches else 0})")

        if not matches:
            raise ResolutionError(f"Row {row_num}: No staff found matching '{name}'")

        return await self._pick_best(
            matches=matches,
            entity_type="staff",
            field="StaffName",
            query=name,
            row_num=row_num,
        )

    async def _get_staff_tools(self) -> List[str]:
        """Get available staff resolution tools in priority order.

        Only employees and contractors are valid for schedule/workorder/invoice
        staff resolution. Contacts are customers/people — never used for staff lookup.
        """
        available = set(await self.mcp_executor.get_available_tools())
        preferred = ["list_employees", "list_contractors"]
        tools = [t for t in preferred if t in available]
        if not tools:
            logger.warning("No known staff resolution tools found in registry")
        return tools

    @staticmethod
    def _normalize_staff_names(staff_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Normalize Name fields that may be nested dicts into flat strings.

        Simpro sometimes returns Name as {"GivenName": ..., "FamilyName": ...}
        instead of a flat string. This would crash fuzzy matching (.strip() on a dict).
        """
        for s in staff_list:
            name = s.get("Name")
            if isinstance(name, dict):
                given = (name.get("GivenName") or "").strip()
                family = (name.get("FamilyName") or "").strip()
                s["Name"] = f"{given} {family}".strip()
        return staff_list

    async def _fetch_staff_candidates(
        self, tool_name: str, search_query: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch staff candidates with API wildcard search + fallback.

        Stage 1: Use the longest word from search_query as a wildcard filter
        to narrow results at the API level. If the filtered search returns 0
        results (e.g., typo in the keyword), fall back to fetching all.
        """
        # Extract longest word for API wildcard
        search_keyword = None
        if search_query:
            words = [w for w in search_query.strip().split() if len(w) >= 2]
            if words:
                search_keyword = max(words, key=len)

        if tool_name == "list_employees":
            if search_keyword:
                logger.info(f"Searching employees with keyword '{search_keyword}' (full query: '{search_query}')")
                result = await self.mcp_executor.call_tool("list_employees", {
                    "page_size": 250,
                    "columns": "ID,Name",
                    "filters": {"Name": f"%{search_keyword}%"},
                })
                employees = result.get("employees", [])
                if employees:
                    logger.info(f"Filtered employee search returned {len(employees)} results")
                    return self._normalize_staff_names(employees)
                # Fallback: wildcard returned nothing (possible typo) — fetch all
                logger.info(f"Filtered search for '{search_keyword}' returned 0 employees, falling back to full list")

            result = await self.mcp_executor.call_tool("list_employees", {
                "page_size": 250,
                "columns": "ID,Name"  # ONLY valid columns — NOT GivenName/FamilyName
            })
            return self._normalize_staff_names(result.get("employees", []))

        elif tool_name == "list_contractors":
            if search_keyword:
                logger.info(f"Searching contractors with keyword '{search_keyword}' (full query: '{search_query}')")
                # Search both Name and ContactName — contractors may match on either
                result = await self.mcp_executor.call_tool("list_contractors", {
                    "page_size": 250,
                    "columns": "ID,Name,ContactName",
                    "filters": {"Name": f"%{search_keyword}%"},
                })
                contractors_by_name = result.get("contractors", [])

                result2 = await self.mcp_executor.call_tool("list_contractors", {
                    "page_size": 250,
                    "columns": "ID,Name,ContactName",
                    "filters": {"ContactName": f"%{search_keyword}%"},
                })
                contractors_by_contact = result2.get("contractors", [])

                # Merge and deduplicate by ID
                seen_ids = set()
                merged = []
                for c in contractors_by_name + contractors_by_contact:
                    if c.get("ID") not in seen_ids:
                        seen_ids.add(c["ID"])
                        merged.append(c)

                if merged:
                    logger.info(f"Filtered contractor search returned {len(merged)} results")
                    return self._normalize_staff_names(merged)
                # Fallback: wildcard returned nothing — fetch all
                logger.info(f"Filtered search for '{search_keyword}' returned 0 contractors, falling back to full list")

            result = await self.mcp_executor.call_tool("list_contractors", {
                "page_size": 250,
                "columns": "ID,Name,ContactName"
            })
            return self._normalize_staff_names(result.get("contractors", []))

        elif tool_name == "search_contacts":
            result = await self.mcp_executor.call_tool("search_contacts", {
                "page_size": 250
            })
            contacts = result.get("contacts", [])
            # Normalize: build "Name" from GivenName + FamilyName
            for c in contacts:
                given = (c.get("GivenName") or "").strip()
                family = (c.get("FamilyName") or "").strip()
                c["Name"] = f"{given} {family}".strip()
            return contacts

        logger.warning(f"Unknown staff tool '{tool_name}', skipping")
        return []

    # ──────────────────────────────────────────────────────────────────────
    # Job Resolution
    # ──────────────────────────────────────────────────────────────────────

    async def resolve_job(
        self,
        name: Optional[str] = None,
        job_id: Optional[int] = None,
        site_name: Optional[str] = None,
        row_num: int = 0,
    ) -> Dict[str, Any]:
        """
        Resolve job name or site name → {id, name}.

        Uses broad wildcard search (longest word) + client-side fuzzy matching
        to handle typos (e.g., "nubeena cresent" → "Nubeena Crescent").

        Returns:
            {"id": int, "name": str}
        """
        if job_id:
            return {"id": int(job_id), "name": name or ""}

        if name:
            return await self._search_job_fuzzy(
                query=name,
                filter_field="Name",
                error_field="JobName",
                match_field="Name",
                row_num=row_num,
            )

        if site_name:
            return await self._search_job_fuzzy(
                query=site_name,
                filter_field="Site.Name",
                error_field="SiteName",
                match_field="Site",
                row_num=row_num,
            )

        raise MissingFieldError(
            field="JobID",
            message=f"Row {row_num}: Please specify JobID, JobName, or SiteName",
        )

    async def _search_job_fuzzy(
        self,
        query: str,
        filter_field: str,
        error_field: str,
        match_field: str,
        row_num: int = 0,
    ) -> Dict[str, Any]:
        """
        3-stage job resolution: API wildcard → fuzzy match → post-filter.

        Stage 1 — API wildcard search:
            Extract the longest word from the query as the search keyword
            (e.g., "nubeena cresent" → "nubeena") and search Simpro with
            a broad wildcard (e.g., Site.Name=%nubeena%).

        Stage 2 — Fuzzy match:
            Score all API results against the full query to handle typos
            and rank by relevance.

        Stage 3 — Post-filter:
            Before showing clarification, remove candidates that don't
            contain all query words as whole words (e.g., "Jacksonia Drive"
            removed for query "jackson street").

        Args:
            query: User's search term (e.g., "nubeena cresent")
            filter_field: Simpro filter key (e.g., "Name" or "Site.Name")
            error_field: Field name for error messages (e.g., "JobName" or "SiteName")
            match_field: Which field to fuzzy-match against ("Name" or "Site" for nested site name)
        """
        from utils.fuzzy_match import fuzzy_match_entities, post_filter_matches

        # Build a list of progressively broader search terms from the query.
        # For "Haines Street, Cranbourne East" we try:
        #   1. "Haines Street, Cranbourne East" (full query)
        #   2. "Haines Street"  /  "Cranbourne East" (comma-separated segments, longest first)
        # This avoids the old bug of using only the single longest word ("Cranbourne").
        import re
        search_terms: list[str] = []
        # Full query with punctuation cleaned (commas become spaces)
        full_cleaned = re.sub(r"[^\w\s]", " ", query).strip()
        full_cleaned = re.sub(r"\s+", " ", full_cleaned)
        if full_cleaned:
            search_terms.append(full_cleaned)
        # Comma-delimited segments, longest first (e.g., "Haines Street", "Cranbourne East")
        segments = [s.strip() for s in query.split(",") if s.strip()]
        if len(segments) > 1:
            segments.sort(key=len, reverse=True)
            for seg in segments:
                seg_clean = re.sub(r"[^\w\s]", " ", seg).strip()
                seg_clean = re.sub(r"\s+", " ", seg_clean)
                if seg_clean and seg_clean not in search_terms:
                    search_terms.append(seg_clean)
        # Final fallback: longest word (original behaviour)
        words = full_cleaned.split() if full_cleaned else query.strip().split()
        longest_word = max(words, key=len) if words else query.strip()
        if longest_word and longest_word not in search_terms:
            search_terms.append(longest_word)

        items: list[dict] = []
        used_term = search_terms[0]
        for term in search_terms:
            logger.info(f"Row {row_num}: Searching {filter_field} with '{term}' (original: '{query}')")
            result = await self.mcp_executor.call_tool("search_jobs", {
                "filters": {filter_field: f"%{term}%"},
                "page_size": 250,
            })
            items = result.get("jobs", [])
            used_term = term
            if items:
                break  # found results, no need to broaden

        if not items:
            raise ResolutionError(f"Row {row_num}: No job found for {error_field} '{query}'")

        # Single result — return directly
        if len(items) == 1:
            return {"id": items[0]["ID"], "name": self._build_job_label(items[0])}

        # Normalize candidates: extract the name field we're matching against
        for item in items:
            if match_field == "Site":
                site = item.get("Site")
                item["_match_name"] = site.get("Name", "") if isinstance(site, dict) else (site or "")
            else:
                item["_match_name"] = item.get(match_field, "")

        # ── Stage 2: Fuzzy match ──
        matches = fuzzy_match_entities(
            query=query,
            candidates=items,
            name_field="_match_name",
            id_field="ID",
        )

        if not matches:
            # Fuzzy match found nothing — post-filter and show candidates
            filtered = post_filter_matches(query, items, name_field="_match_name")
            raise AmbiguousResolutionError(
                field=error_field,
                value=query,
                matches=[{"id": i["ID"], "name": self._build_job_label(i)} for i in filtered],
                message=f"Row {row_num}: Multiple jobs found but none closely match '{query}'",
            )

        # Auto-select if clear winner
        if (
            len(matches) == 1
            or (matches[0]["score"] >= 70 and (len(matches) == 1 or matches[0]["score"] > matches[1]["score"] + 15))
        ):
            winner = matches[0]
            winner_item = next((i for i in items if i["ID"] == winner["id"]), items[0])
            logger.info(f"Row {row_num}: Fuzzy-resolved '{query}' → Job {winner['id']} (score={winner['score']})")
            return {"id": winner["id"], "name": self._build_job_label(winner_item)}

        # ── Stage 3: Post-filter before clarification ──
        # Build label-enriched list from fuzzy matches, then post-filter
        matched_items = []
        for m in matches:
            item = next((i for i in items if i["ID"] == m["id"]), None)
            if item:
                matched_items.append(item)
        filtered = post_filter_matches(query, matched_items, name_field="_match_name")

        raise AmbiguousResolutionError(
            field=error_field,
            value=query,
            matches=[{"id": i["ID"], "name": self._build_job_label(i)} for i in filtered],
            message=f"Row {row_num}: Multiple jobs match '{query}'",
        )

    @staticmethod
    def _build_job_label(item: Dict[str, Any], context: str = "Job") -> str:
        """Build a human-readable label for a job/quote match."""
        name = item.get("Name", "")
        site = ""
        if isinstance(item.get("Site"), dict):
            site = item["Site"].get("Name", "")
        elif isinstance(item.get("Site"), str):
            site = item["Site"]

        parts = [f"{context} {item['ID']}"]
        if name:
            parts.append(name)
        if site:
            parts.append(f"@ {site}")
        return " - ".join(parts)

    # ──────────────────────────────────────────────────────────────────────
    # Section Resolution
    # ──────────────────────────────────────────────────────────────────────

    async def resolve_section(
        self,
        job_id: int,
        name: Optional[str] = None,
        section_id: Optional[int] = None,
        cost_centre_id: Optional[int] = None,
        cost_centre_name: Optional[str] = None,
        context: str = "job",
        row_num: int = 0,
    ) -> Dict[str, Any]:
        """
        Resolve section within a job/quote → {id, name}.

        Strategies:
        1. Use section_id if provided
        2. Match by name
        3. Infer via cost_centre_name (scan sections' CCs, auto-select if unique)
        4. Reverse-lookup via cost_centre_id
        5. Auto-select if only one section

        Args:
            job_id: Parent job/quote ID
            cost_centre_name: CC name hint for cross-phase inference
            context: "job" or "quote" (determines which tools to call)
        """
        if section_id:
            return {"id": int(section_id), "name": name or ""}

        # Fetch all sections
        sections = await self._fetch_sections(job_id, context)

        # Strategy: name matching (fuzzy)
        if name:
            from utils.fuzzy_match import fuzzy_match_entities, deduplicate_matches

            matches = fuzzy_match_entities(name, sections, source="get_job_sections")
            matches = deduplicate_matches(matches)
            logger.info(
                f"Row {row_num}: {len(matches)} sections matched '{name}' "
                f"(top: {matches[0]['score'] if matches else 0})"
            )

            if not matches:
                raise ResolutionError(f"Row {row_num}: No section matching '{name}'")

            return await self._pick_best(
                matches=matches,
                entity_type="section",
                field="SectionName",
                query=name,
                row_num=row_num,
            )

        # Strategy: infer section from cost centre NAME (cross-phase inference)
        # When user provides a CC name but no section name, scan all sections'
        # cost centres to find which section(s) contain a matching CC.
        if cost_centre_name and not name and len(sections) > 1:
            inferred = await self._infer_section_from_cc_name(
                job_id, sections, cost_centre_name, context, row_num,
            )
            if inferred is not None:
                return inferred

        # Strategy: reverse-lookup via cost centre ID
        if cost_centre_id:
            found = await self.find_section_for_cost_centre(job_id, int(cost_centre_id), context, row_num)
            if found is not None:
                return {"id": found, "name": ""}

        # Strategy: auto-select if only one
        if not sections:
            raise ResolutionError(f"Row {row_num}: {context.title()} {job_id} has no sections")

        if len(sections) == 1:
            logger.info(f"Row {row_num}: Auto-selected only section: '{sections[0].get('Name', '')}' (ID={sections[0]['ID']})")
            return {"id": sections[0]["ID"], "name": sections[0].get("Name", "")}

        # Multiple sections, no name/CC — always ask user
        candidates = [{"id": s["ID"], "name": s.get("Name", f"Section {s['ID']}")} for s in sections]
        logger.info(f"Row {row_num}: {len(sections)} sections found — asking user to clarify")

        section_names = ", ".join(s.get("Name", f"Section {s['ID']}") for s in sections[:5])
        raise MissingFieldError(
            field="SectionName",
            message=f"Row {row_num}: {context.title()} {job_id} has {len(sections)} sections. Please select one: {section_names}",
            options=candidates,
            parent_id=job_id,
            context=context,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Cost Centre Resolution
    # ──────────────────────────────────────────────────────────────────────

    async def resolve_cost_centre(
        self,
        job_id: int,
        section_id: int,
        name: Optional[str] = None,
        cost_centre_id: Optional[int] = None,
        context: str = "job",
        row_num: int = 0,
    ) -> Dict[str, Any]:
        """
        Resolve cost centre within a job section → {id, name}.

        Args:
            context: "job" or "quote" (determines which tools to call)
        """
        if cost_centre_id:
            return {"id": int(cost_centre_id), "name": name or ""}

        # Fetch cost centres
        cost_centres = await self._fetch_cost_centres(job_id, section_id, context)

        if name:
            from utils.fuzzy_match import fuzzy_match_entities, deduplicate_matches

            matches = fuzzy_match_entities(name, cost_centres, source="get_job_section_cost_centres")
            matches = deduplicate_matches(matches)
            logger.info(
                f"Row {row_num}: {len(matches)} cost centres matched '{name}' "
                f"(top: {matches[0]['score'] if matches else 0})"
            )

            if not matches:
                cc_options = [{"id": cc["ID"], "name": cc.get("Name", f"Cost Centre {cc['ID']}")} for cc in cost_centres]
                raise ResolutionError(
                    f"Row {row_num}: No cost centre matching '{name}'",
                    partial_data={"available_cost_centres": cc_options},
                )

            return await self._pick_best(
                matches=matches,
                entity_type="cost_centre",
                field="CostCentreName",
                query=name,
                row_num=row_num,
            )

        # Auto-select if single cost centre
        if not cost_centres:
            raise ResolutionError(f"Row {row_num}: Section {section_id} has no cost centres")

        if len(cost_centres) == 1:
            logger.info(f"Row {row_num}: Auto-selected only cost centre: ID={cost_centres[0]['ID']}")
            return {"id": cost_centres[0]["ID"], "name": cost_centres[0].get("Name", "")}

        # Multiple cost centres — always ask user
        cc_options = [{"id": cc["ID"], "name": cc.get("Name", f"Cost Centre {cc['ID']}")} for cc in cost_centres]
        logger.info(f"Row {row_num}: {len(cost_centres)} cost centres found — asking user to clarify")

        cc_names = ", ".join(cc.get("Name", f"Cost Centre {cc['ID']}") for cc in cost_centres[:5])
        raise MissingFieldError(
            field="CostCentreName",
            message=f"Row {row_num}: Section has {len(cost_centres)} cost centres. Please select one: {cc_names}",
            options=cc_options,
            parent_id=job_id,
            section_id=section_id,
            context=context,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Section Reverse-Lookup
    # ──────────────────────────────────────────────────────────────────────

    async def find_section_for_cost_centre(
        self,
        job_id: int,
        cost_centre_id: int,
        context: str = "job",
        row_num: int = 0,
    ) -> Optional[int]:
        """
        Find which section contains a specific cost centre ID.

        Fetches all sections' cost centres in parallel (not N+1 sequential).

        Returns:
            section_id if found, None otherwise
        """
        import asyncio
        sections = await self._fetch_sections(job_id, context)
        logger.info(f"Row {row_num}: Searching {len(sections)} sections for CostCentreID={cost_centre_id}")

        if not sections:
            return None

        async def _check_section(section):
            try:
                cost_centres = await self._fetch_cost_centres(job_id, section["ID"], context)
                if any(cc["ID"] == cost_centre_id for cc in cost_centres):
                    return section["ID"]
            except Exception as e:
                logger.warning(f"Row {row_num}: Section {section['ID']} query failed: {e}")
            return None

        # Check all sections in parallel
        results = await asyncio.gather(*[_check_section(s) for s in sections])
        for section_id in results:
            if section_id is not None:
                logger.info(f"Row {row_num}: Found CostCentreID={cost_centre_id} in Section {section_id}")
                return section_id

        return None

    # ──────────────────────────────────────────────────────────────────────
    # Cross-Phase Section Inference (from CC name)
    # ──────────────────────────────────────────────────────────────────────

    async def _infer_section_from_cc_name(
        self,
        job_id: int,
        sections: List[Dict[str, Any]],
        cost_centre_name: str,
        context: str = "job",
        row_num: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """
        Infer which section to use by scanning all sections' cost centres
        for a fuzzy match on the cost centre name.

        Returns section dict {id, name} if exactly one section contains
        a matching CC, None otherwise (falls through to other strategies).
        """
        import asyncio
        from utils.fuzzy_match import fuzzy_match_entities

        logger.info(
            f"Row {row_num}: Cross-phase inference — scanning sections "
            f"for CC name '{cost_centre_name}'"
        )

        async def _scan_section(section):
            """Return (section, best_match_score) if CC name matches in this section."""
            try:
                cost_centres = await self._fetch_cost_centres(job_id, section["ID"], context)
                if not cost_centres:
                    return None
                matches = fuzzy_match_entities(
                    cost_centre_name, cost_centres,
                    source="get_job_section_cost_centres",
                )
                if matches and matches[0]["score"] >= self._MIN_MATCH_SCORE:
                    return (section, matches[0]["score"])
            except Exception as e:
                logger.warning(f"Row {row_num}: CC scan for section {section['ID']} failed: {e}")
            return None

        results = await asyncio.gather(*[_scan_section(s) for s in sections])
        hits = [r for r in results if r is not None]

        if len(hits) == 1:
            section, score = hits[0]
            logger.info(
                f"Row {row_num}: Cross-phase inference — CC '{cost_centre_name}' "
                f"found only in section '{section.get('Name', '')}' "
                f"(ID={section['ID']}, score={score}) → auto-selected"
            )
            return {"id": section["ID"], "name": section.get("Name", "")}

        if len(hits) > 1:
            # CC name matches in multiple sections — pick best score if clear winner
            hits.sort(key=lambda h: h[1], reverse=True)
            if hits[0][1] > hits[1][1] + 15:
                section, score = hits[0]
                logger.info(
                    f"Row {row_num}: Cross-phase inference — CC '{cost_centre_name}' "
                    f"best match in section '{section.get('Name', '')}' "
                    f"(ID={section['ID']}, score={score}, 2nd={hits[1][1]}) → auto-selected"
                )
                return {"id": section["ID"], "name": section.get("Name", "")}
            logger.info(
                f"Row {row_num}: Cross-phase inference — CC '{cost_centre_name}' "
                f"found in {len(hits)} sections with close scores, falling through"
            )

        return None

    # ──────────────────────────────────────────────────────────────────────
    # Contractor Resolution
    # ──────────────────────────────────────────────────────────────────────

    async def resolve_contractor(
        self,
        name: Optional[str] = None,
        contractor_id: Optional[int] = None,
        row_num: int = 0,
    ) -> Dict[str, Any]:
        """
        Resolve contractor name → {id, name}.

        3-stage pipeline: API wildcard → fuzzy match → _pick_best.
        Uses _fetch_staff_candidates (shared with resolve_staff) for consistent
        wildcard search + fallback behaviour.
        """
        if contractor_id:
            return {"id": int(contractor_id), "name": name or ""}

        if not name:
            raise MissingFieldError(
                field="ContractorName",
                message=f"Row {row_num}: Either contractor_name or contractor_id is required",
            )

        from utils.fuzzy_match import fuzzy_match_entities, deduplicate_matches

        # Stage 1: API wildcard search (with fallback to fetch-all)
        contractors = await self._fetch_staff_candidates("list_contractors", search_query=name)
        if not contractors:
            raise ResolutionError(f"Row {row_num}: No contractors found in system")

        # Stage 2: Fuzzy match
        matches = fuzzy_match_entities(
            name, contractors,
            extra_name_fields=["ContactName"],
            source="list_contractors",
        )
        matches = deduplicate_matches(matches)
        logger.info(
            f"Row {row_num}: {len(matches)} contractors matched '{name}' "
            f"(top: {matches[0]['score'] if matches else 0})"
        )

        if not matches:
            raise ResolutionError(f"Row {row_num}: No contractor found matching '{name}'")

        return await self._pick_best(
            matches=matches,
            entity_type="contractor",
            field="ContractorName",
            query=name,
            row_num=row_num,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Customer Resolution
    # ──────────────────────────────────────────────────────────────────────

    async def resolve_customer(
        self,
        name: Optional[str] = None,
        customer_id: Optional[int] = None,
        row_num: int = 0,
    ) -> Dict[str, Any]:
        """
        Resolve customer name → {id, name}.

        3-stage pipeline: API wildcard → fuzzy match → _pick_best.
        """
        if customer_id:
            return {"id": int(customer_id), "name": name or ""}

        if not name:
            raise MissingFieldError(
                field="CustomerName",
                message=f"Row {row_num}: Customer name or ID is required",
            )

        from utils.fuzzy_match import fuzzy_match_entities, deduplicate_matches

        # Stage 1: API wildcard search on CompanyName
        customers = await self._fetch_customers(name)
        if not customers:
            raise ResolutionError(f"Row {row_num}: No customers found in system")

        # Stage 2: Fuzzy match — CompanyName is primary, GivenName/FamilyName are extras
        matches = fuzzy_match_entities(
            name, customers,
            name_field="CompanyName",
            extra_name_fields=["GivenName", "FamilyName"],
            source="search_customers",
        )
        matches = deduplicate_matches(matches)
        logger.info(
            f"Row {row_num}: {len(matches)} customers matched '{name}' "
            f"(top: {matches[0]['score'] if matches else 0})"
        )

        if not matches:
            raise ResolutionError(f"Row {row_num}: No customer found matching '{name}'")

        return await self._pick_best(
            matches=matches,
            entity_type="customer",
            field="CustomerName",
            query=name,
            row_num=row_num,
        )

    async def _fetch_customers(self, search_query: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Fetch customer candidates with API wildcard search + fallback.

        Uses the longest word from search_query as a wildcard on CompanyName.
        Falls back to fetching all if filtered search returns 0.
        """
        search_keyword = None
        if search_query:
            words = [w for w in search_query.strip().split() if len(w) >= 2]
            if words:
                search_keyword = max(words, key=len)

        if search_keyword:
            logger.info(f"Searching customers with keyword '{search_keyword}' (full query: '{search_query}')")
            result = await self.mcp_executor.call_tool("search_customers", {
                "page_size": 250,
                "filters": {"CompanyName": f"%{search_keyword}%"},
            })
            customers = result.get("customers", [])
            if customers:
                logger.info(f"Filtered customer search returned {len(customers)} results")
                return customers
            logger.info(f"Filtered search for '{search_keyword}' returned 0 customers, falling back to full list")

        result = await self.mcp_executor.call_tool("search_customers", {"page_size": 250})
        return result.get("customers", [])

    # ──────────────────────────────────────────────────────────────────────
    # Cost Centre Type (Department) Resolution
    # ──────────────────────────────────────────────────────────────────────

    async def resolve_cost_centre_type(
        self,
        name: Optional[str] = None,
        type_id: Optional[int] = None,
        row_num: int = 0,
    ) -> Dict[str, Any]:
        """
        Resolve cost centre type (department) name → {id, name}.

        Uses get_cost_centre_types with fuzzy matching.
        """
        if type_id:
            return {"id": int(type_id), "name": name or ""}

        if not name:
            raise MissingFieldError(
                field="CostCentreType",
                message=f"Row {row_num}: Cost centre type name or ID is required",
            )

        from utils.fuzzy_match import fuzzy_match_entities, deduplicate_matches

        result = await self.mcp_executor.call_tool(
            "get_cost_centre_types", {"columns": "ID,Name", "page_size": 250}
        )
        type_list = result.get("cost_centre_types", result) if isinstance(result, dict) else result
        if not isinstance(type_list, list):
            type_list = []
        if not type_list:
            raise ResolutionError(f"Row {row_num}: No cost centre types found in system")

        matches = fuzzy_match_entities(name, type_list, source="get_cost_centre_types")
        matches = deduplicate_matches(matches)
        logger.info(
            f"Row {row_num}: {len(matches)} cost centre types matched '{name}' "
            f"(top: {matches[0]['score'] if matches else 0})"
        )

        if not matches:
            raise ResolutionError(f"Row {row_num}: No cost centre type matching '{name}'")

        return await self._pick_best(
            matches=matches,
            entity_type="cost_centre_type",
            field="CostCentreType",
            query=name,
            row_num=row_num,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Department Resolution (via Setup Cost Centres + Chart of Accounts)
    # ──────────────────────────────────────────────────────────────────────

    async def resolve_department(
        self,
        name: Optional[str] = None,
        cost_centre_id: Optional[int] = None,
        cost_centre_name: Optional[str] = None,
        row_num: int = 0,
    ) -> Dict[str, Any]:
        """
        Resolve department via the cost centre -> IncomeAccountNo -> chart of accounts chain.

        Bidirectional:
        (a) Given a cost centre (ID or name), find its department.
        (b) Given a department name, find the cost centres in it.

        Args:
            name: Department name to search for (returns cost centres list)
            cost_centre_id: Setup cost centre ID to find department for
            cost_centre_name: Setup cost centre name to find department for
            row_num: Row number for error messages

        Returns:
            If cost_centre_id/cost_centre_name given:
                {"department": str, "cost_centre_id": int, "cost_centre_name": str}
            If name given:
                {"department": str, "cost_centres": [{id, name, income_account_no}, ...]}

        Raises:
            ResolutionError: No department found
            AmbiguousResolutionError: Multiple departments match fuzzy query
            MissingFieldError: No parameters provided
        """
        from utils.department_cache import get_department_cache
        from utils.fuzzy_match import fuzzy_match_entities, deduplicate_matches

        cache = await get_department_cache(self.mcp_executor, org_id=self.org_id)

        # ── Direction A: Cost centre -> Department ──
        if cost_centre_id or cost_centre_name:
            dept = cache.get_department_for_cost_centre(
                cc_id=cost_centre_id,
                cc_name=cost_centre_name,
            )
            if dept:
                logger.info(
                    f"Row {row_num}: Resolved department for CC "
                    f"{cost_centre_id or cost_centre_name} -> '{dept}'"
                )
                return {
                    "department": dept,
                    "cost_centre_id": cost_centre_id,
                    "cost_centre_name": cost_centre_name or "",
                }
            raise ResolutionError(
                f"Row {row_num}: No department found for cost centre "
                f"{cost_centre_id or cost_centre_name}"
            )

        # ── Direction B: Department name -> Cost centres ──
        if name:
            # Try exact match first (case-insensitive)
            cost_centres = cache.get_cost_centres_for_department(name)
            if cost_centres:
                return {
                    "department": name,
                    "cost_centres": cost_centres,
                }

            # Fuzzy match against known department names
            dept_candidates = [
                {"ID": i, "Name": d}
                for i, d in enumerate(cache.list_departments())
            ]
            if not dept_candidates:
                raise ResolutionError(
                    f"Row {row_num}: No departments configured in the system"
                )

            matches = fuzzy_match_entities(
                name, dept_candidates, source="department_cache"
            )
            matches = deduplicate_matches(matches)

            if not matches:
                raise ResolutionError(
                    f"Row {row_num}: No department matching '{name}'"
                )

            result = await self._pick_best(
                matches=matches,
                entity_type="department",
                field="DepartmentName",
                query=name,
                row_num=row_num,
            )

            # Get the actual department name from the match
            dept_name = result["name"]
            cost_centres = cache.get_cost_centres_for_department(dept_name)
            return {
                "department": dept_name,
                "cost_centres": cost_centres,
            }

        raise MissingFieldError(
            field="Department",
            message=(
                f"Row {row_num}: Provide department name, "
                "cost_centre_id, or cost_centre_name"
            ),
        )

    # ──────────────────────────────────────────────────────────────────────
    # Company ID Resolution
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def resolve_company_id(
        group_rows: list,
        defaults: Optional[Dict[str, Any]] = None,
        hints: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """
        Resolve company ID from system hints, Excel rows, or LLM defaults.

        Priority:
        1. System hints/env (SIMPRO_COMPANY_ID) — most reliable
        2. Explicit company_id from rows
        3. LLM defaults — least reliable (may hallucinate)
        """
        # 1) Hints from backend (env var / user profile)
        for key in ("CompanyID", "company_id", "companyID"):
            if key in (hints or {}) and hints[key] is not None:
                try:
                    val = int(hints[key])
                    logger.info(f"CompanyID={val} (from system hints/env)")
                    return val
                except (TypeError, ValueError):
                    pass

        # 2) Explicit company_id from rows
        for r in group_rows:
            cid = getattr(r, "company_id", None) if hasattr(r, "company_id") else (r.get("company_id") if isinstance(r, dict) else None)
            if cid is not None:
                logger.info(f"CompanyID={cid} (from row data)")
                return int(cid)

        # 3) LLM defaults (least reliable)
        d = defaults or {}
        for key in ("CompanyID", "company_id", "companyID"):
            if key in d and d[key] is not None:
                try:
                    val = int(d[key])
                    logger.warning(f"CompanyID={val} (from LLM defaults — verify correctness)")
                    return val
                except (TypeError, ValueError):
                    pass

        return None

    # ──────────────────────────────────────────────────────────────────────
    # Batched Resolution (resolve independent fields in one round)
    # ──────────────────────────────────────────────────────────────────────

    async def resolve_batch(
        self,
        tasks: List[Dict[str, Any]],
        row_num: int = 0,
    ) -> Dict[str, Any]:
        """
        Resolve multiple fields in dependency-phase order, collecting
        clarification errors from independent fields so they can be
        presented to the user in a single round.

        Args:
            tasks: list of resolution task dicts, each containing:
                - ``key``        : output key name (e.g. ``"staff_id"``)
                - ``resolve``    : async callable ``(resolved) -> {"id": …, "name": …}``
                                   Receives the partially-resolved dict so dependent
                                   tasks can read earlier results.
                - ``depends_on`` : list of ``key`` names that must be resolved first.
                                   Empty list = independent (Phase 0).
            row_num: row number for logging.

        Returns:
            Dict mapping each ``key`` → its resolved value (usually an int id).

        Raises:
            AmbiguousResolutionError / MissingFieldError  – if exactly one
                field needs clarification (backward-compatible single-error path).
            BatchedClarificationError – if two or more independent fields
                need clarification.
            ResolutionError / ValidationError – propagated immediately (hard errors).
        """
        resolved: Dict[str, Any] = {}
        collected_errors: List[Exception] = []

        # ── Build phases by topological depth ──
        # Phase 0 = no deps, Phase 1 = depends only on Phase-0 keys, etc.
        remaining = list(tasks)
        phases: List[List[Dict[str, Any]]] = []
        assigned_keys: set = set()

        while remaining:
            phase = [
                t for t in remaining
                if all(dep in assigned_keys for dep in t.get("depends_on", []))
            ]
            if not phase:
                # Circular or unresolvable deps — treat rest as next phase
                phase = remaining
                remaining = []
            else:
                remaining = [t for t in remaining if t not in phase]
            phases.append(phase)
            assigned_keys.update(t["key"] for t in phase)

        # ── Execute phases (independent tasks within a phase run concurrently) ──
        import asyncio
        for phase_idx, phase in enumerate(phases):
            phase_errors: List[Exception] = []

            # Filter to tasks whose dependencies are met
            runnable = []
            for task in phase:
                key = task["key"]
                deps = task.get("depends_on", [])
                if any(d not in resolved for d in deps):
                    logger.info(
                        f"Row {row_num}: Skipping '{key}' — "
                        f"dependency {[d for d in deps if d not in resolved]} unresolved"
                    )
                    continue
                runnable.append(task)

            # Run all tasks in this phase concurrently
            async def _run_task(task):
                key = task["key"]
                try:
                    result = await task["resolve"](resolved)
                    if isinstance(result, dict) and "id" in result:
                        return (key, result["id"], None)
                    return (key, result, None)
                except (AmbiguousResolutionError, MissingFieldError) as e:
                    logger.info(
                        f"Row {row_num}: '{key}' needs clarification, "
                        f"continuing with remaining fields"
                    )
                    return (key, None, e)
                # ResolutionError / ValidationError propagate immediately

            if runnable:
                task_results = await asyncio.gather(*[_run_task(t) for t in runnable])
                for key, value, error in task_results:
                    if error is not None:
                        phase_errors.append(error)
                    elif value is not None:
                        resolved[key] = value

            if phase_errors:
                collected_errors.extend(phase_errors)
                # Don't continue to later phases — their deps aren't met
                break

        # ── Raise collected errors ──
        if collected_errors:
            record_decision(
                dimension="disambiguation",
                decision_type="batched_clarification",
                decision_value=f"needs_{len(collected_errors)}_clarifications",
                confidence=0.0,
                reasoning=f"Batch has {len(collected_errors)} unresolved fields, partial: {list(resolved.keys())}",
                context={"partial_resolved": list(resolved.keys()), "error_count": len(collected_errors)},
                outcome="clarification",
            )
            if len(collected_errors) == 1:
                raise collected_errors[0]
            raise BatchedClarificationError(
                errors=collected_errors,
                partial_resolved=resolved,
            )

        return resolved

    # ──────────────────────────────────────────────────────────────────────
    # Internal Helpers
    # ──────────────────────────────────────────────────────────────────────

    async def _fetch_sections(self, parent_id: int, context: str = "job") -> List[Dict[str, Any]]:
        """Fetch all sections for a job/quote."""
        tool = f"get_{context}_sections"
        result = await self.mcp_executor.call_tool(tool, {f"{context}_id": parent_id})
        sections = result.get("sections", result) if isinstance(result, dict) else result
        return sections if isinstance(sections, list) else []

    async def _fetch_cost_centres(self, parent_id: int, section_id: int, context: str = "job") -> List[Dict[str, Any]]:
        """Fetch all cost centres for a section."""
        tool = f"get_{context}_section_cost_centres"
        result = await self.mcp_executor.call_tool(tool, {
            f"{context}_id": parent_id,
            "section_id": section_id,
        })
        cost_centres = result.get("cost_centres", result) if isinstance(result, dict) else result
        return cost_centres if isinstance(cost_centres, list) else []

    # Minimum score to be considered a real match (not noise).
    # Typo-tier scores (≤48) are too unreliable to show as options.
    _MIN_MATCH_SCORE = 50

    async def _pick_best(
        self,
        matches: List[Dict[str, Any]],
        entity_type: str,
        field: str,
        query: str,
        row_num: int = 0,
    ) -> Dict[str, Any]:
        """
        Auto-select if exactly ONE clear winner (score >= 70, significantly
        better than second). If zero viable candidates, try LLM disambiguation
        as a rescue. If multiple viable candidates, ALWAYS ask the user.

        Matches below _MIN_MATCH_SCORE are filtered out to prevent garbage
        results (e.g., "nick" matching "rick" at score 48) from polluting
        the clarification dropdown.
        """
        # Filter out low-confidence noise
        viable = [m for m in matches if m["score"] >= self._MIN_MATCH_SCORE]

        # ── No viable candidates: try LLM disambiguation as rescue ──
        if not viable:
            logger.info(f"Row {row_num}: No {entity_type} above threshold for '{query}' — trying LLM rescue")
            disambiguated = await self._disambiguate(
                entity_type=entity_type,
                query=query,
                candidates=matches[:5],
                row_num=row_num,
            )
            if disambiguated:
                record_decision(
                    dimension="disambiguation",
                    decision_type="llm_rescue",
                    decision_value=str(disambiguated["id"]),
                    confidence=0.70,
                    reasoning=f"LLM rescued '{query}' → {disambiguated['name']} (ID={disambiguated['id']}, no viable candidates above threshold)",
                    context={"entity_type": entity_type, "field": field, "row_num": row_num, "match_count": len(matches)},
                    outcome="success",
                )
                return disambiguated
            raise ResolutionError(f"Row {row_num}: No {entity_type} found matching '{query}'")

        # ── Single clear winner: score >= 90 (exact/near-exact name match) ──
        # Only auto-proceed when the match is genuinely unambiguous:
        # the query IS the name, or the query is a whole word in the name,
        # AND there's a clear score gap over second place.
        # Scores below 90 (substring, all-words, typo tiers) always go to the
        # user — the LLM can hallucinate or pick wrong names, so we never
        # silently act on a fuzzy match.
        if len(viable) == 1 and viable[0]["score"] >= 90:
            logger.info(f"Row {row_num}: Auto-resolved '{query}' → {viable[0]['name']} (ID={viable[0]['id']}, via {viable[0]['source']})")
            record_decision(
                dimension="auto_selection",
                decision_type="pick_best_auto",
                decision_value=str(viable[0]["id"]),
                confidence=viable[0]["score"] / 100.0,
                reasoning=f"Auto-resolved '{query}' to {viable[0]['name']} (score={viable[0]['score']}, entity={entity_type})",
                context={"entity_type": entity_type, "field": field, "row_num": row_num, "match_count": len(viable)},
                outcome="success",
            )
            return {"id": viable[0]["id"], "name": viable[0]["name"]}

        # ── Multiple viable candidates: use LLM to narrow the list ──
        # LLM acts as a shortlisting step only — it narrows many candidates
        # down to the top 1-3 most likely matches. The user ALWAYS sees the
        # final shortlist and must confirm. We never auto-proceed based on
        # LLM output alone, because LLM can hallucinate or pick wrong names.
        #
        # Run LLM when: there are distinguishable names and LLM is available.
        # No count cap — even 50 candidates go through LLM shortlisting
        # (we pass up to 15 to keep the prompt manageable).
        unique_names = {m["name"].strip().lower() for m in viable[:15]}
        if len(unique_names) > 1 and self.llm_chat:
            logger.info(
                f"Row {row_num}: {len(viable)} {entity_type}(s) match '{query}' "
                f"(scores: {[m['score'] for m in viable[:5]]}) — using LLM to shortlist"
            )
            shortlisted = await self._shortlist(
                entity_type=entity_type,
                query=query,
                candidates=viable[:15],
                row_num=row_num,
            )
            if shortlisted:
                # LLM narrowed the list — show only shortlisted to user
                record_decision(
                    dimension="disambiguation",
                    decision_type="llm_shortlist",
                    decision_value="needs_clarification",
                    confidence=0.75,
                    reasoning=(
                        f"LLM shortlisted '{query}' from {len(viable)} → {len(shortlisted)} candidates: "
                        f"{[m['name'] for m in shortlisted]}"
                    ),
                    context={
                        "entity_type": entity_type, "field": field,
                        "row_num": row_num, "original_count": len(viable),
                        "shortlisted": [m["name"] for m in shortlisted],
                    },
                    outcome="clarification",
                )
                raise AmbiguousResolutionError(
                    field=field,
                    value=query,
                    matches=[{"id": m["id"], "name": m["name"]} for m in shortlisted],
                    message=f"Row {row_num}: Multiple {entity_type} match '{query}'",
                )
            # LLM couldn't narrow — fall through to show top 5 to user

        # ── Fall through: ask the user with top 5 ──
        logger.info(
            f"Row {row_num}: {len(viable)} {entity_type}(s) match '{query}' "
            f"(scores: {[m['score'] for m in viable[:5]]}) — asking user to clarify"
        )
        record_decision(
            dimension="disambiguation",
            decision_type="ambiguous_resolution",
            decision_value="needs_clarification",
            confidence=viable[0]["score"] / 100.0 if viable else 0.0,
            reasoning=f"Multiple {entity_type} match '{query}': {[m['name'] for m in viable[:5]]}",
            context={"entity_type": entity_type, "field": field, "row_num": row_num, "top_matches": len(viable)},
            outcome="clarification",
        )
        raise AmbiguousResolutionError(
            field=field,
            value=query,
            matches=[{"id": m["id"], "name": m["name"]} for m in viable[:5]],
            message=f"Row {row_num}: Multiple {entity_type} match '{query}'",
        )

    async def _shortlist(
        self,
        entity_type: str,
        query: str,
        candidates: List[Dict[str, Any]],
        row_num: int = 0,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Ask the LLM to narrow a list of fuzzy-matched candidates down to the
        top 1-3 most likely matches for the user query.

        The LLM acts as a shortlisting step ONLY — it never auto-confirms.
        The user always sees the final shortlist and must pick.

        Returns:
            List of {"id": int, "name": str} (1-3 items), ordered best-first.
            None if the LLM cannot meaningfully narrow the list.
        """
        if not self.llm_chat:
            return None

        try:
            # Build numbered candidate list for the LLM
            candidate_lines = []
            for i, c in enumerate(candidates, 1):
                line = f"{i}. ID={c['id']}, name=\"{c['name']}\""
                if c.get("source"):
                    line += f" ({c['source']})"
                candidate_lines.append(line)

            prompt = (
                f"A user typed: \"{query}\"\n"
                f"The following {entity_type}s were fuzzy-matched as possible candidates:\n"
                + "\n".join(candidate_lines)
                + "\n\n"
                f"Return the numbers (1-based) of the top 1 to 3 candidates whose names "
                f"best match what the user likely meant, ordered best-first. "
                f"Consider spelling mistakes, abbreviations, and partial names. "
                f"If multiple candidates are equally plausible, include all of them. "
                f"If you cannot confidently identify ANY good match, return an empty list.\n"
                f"Respond with JSON only: {{\"shortlist\": [<number>, ...]}} "
                f"where numbers are 1-based indices into the list above."
            )

            response = self.llm_chat([{"role": "user", "content": prompt}], temperature=0.0)
            raw = (response or "").strip()

            # Parse JSON response
            import json, re as _re
            json_match = _re.search(r'\{.*\}', raw, _re.DOTALL)
            if not json_match:
                logger.warning(f"Row {row_num}: LLM shortlist response not JSON: {raw[:200]}")
                return None

            parsed = json.loads(json_match.group())
            indices = parsed.get("shortlist", [])
            if not isinstance(indices, list) or not indices:
                logger.info(f"Row {row_num}: LLM returned empty shortlist for '{query}'")
                return None

            # Map indices back to candidates (1-based)
            shortlisted = []
            for idx in indices:
                if isinstance(idx, int) and 1 <= idx <= len(candidates):
                    c = candidates[idx - 1]
                    shortlisted.append({"id": c["id"], "name": c["name"]})

            if not shortlisted:
                return None

            logger.info(
                f"Row {row_num}: LLM shortlisted '{query}': "
                f"{len(candidates)} → {[m['name'] for m in shortlisted]}"
            )
            return shortlisted

        except Exception as e:
            logger.warning(f"Row {row_num}: {entity_type} LLM shortlisting failed: {e}")
            return None

    async def _disambiguate(
        self,
        entity_type: str,
        query: str,
        candidates: List[Dict[str, Any]],
        row_num: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """
        Legacy single-pick disambiguation — used only for the no-viable-candidates
        rescue path where we must pick one or raise ResolutionError.

        Returns:
            {"id": int, "name": str} if resolved, None if LLM can't decide
        """
        if not self.llm_chat:
            return None

        try:
            candidate_lines = []
            for i, c in enumerate(candidates[:5], 1):
                line = f"{i}. ID={c['id']}, name=\"{c['name']}\""
                if c.get("source"):
                    line += f" ({c['source']})"
                candidate_lines.append(line)

            prompt = (
                f"A user typed: \"{query}\"\n"
                f"None of these {entity_type} candidates scored high enough automatically:\n"
                + "\n".join(candidate_lines)
                + "\n\n"
                f"Pick the single best match (1-based index), or 0 if none are reasonable.\n"
                f"Respond with JSON only: {{\"pick\": <number>}}"
            )

            response = self.llm_chat([{"role": "user", "content": prompt}], temperature=0.0)
            raw = (response or "").strip()

            import json, re as _re
            json_match = _re.search(r'\{.*\}', raw, _re.DOTALL)
            if not json_match:
                return None

            parsed = json.loads(json_match.group())
            pick = parsed.get("pick", 0)
            if isinstance(pick, int) and 1 <= pick <= len(candidates[:5]):
                c = candidates[pick - 1]
                logger.info(f"Row {row_num}: LLM rescue picked '{c['name']}' (ID={c['id']}) for '{query}'")
                return {"id": c["id"], "name": c["name"]}

        except Exception as e:
            logger.warning(f"Row {row_num}: {entity_type} LLM rescue failed: {e}")

        return None
