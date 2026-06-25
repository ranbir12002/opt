#mcp-simpro-server/src/tools

"""
Base tool class for all MCP tools.

All Simpro tools inherit from this class.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, Dict

from src.simpro_api_reference import get_api_hint
from src.utils import get_logger

logger = get_logger(__name__)

# ── Universal filters definition (auto-injected into list tools) ───
_FILTERS_SCHEMA = {
    "type": "object",
    "description": (
        "Simpro API filters. Most response fields can be used as a filter — "
        "including nested fields via dot notation (e.g. Staff.Type, Customer.CompanyName). "
        "Values support operators: %keyword%, gt(), lt(), between(), in(), ne(). "
        "Multiple filters are ANDed. If a field is unsupported, it is auto-applied as post-filter."
    ),
    "additionalProperties": {"type": "string"},
}

# ── Smart filter helpers ────────────────────────────────────────────
# Regex matching Simpro filter operators — these should NOT be auto-wrapped.
_OPERATOR_RE = re.compile(
    r"^(%.*%|gt\(|lt\(|ge\(|le\(|between\(|in\(|!in\(|ne\()",
    re.IGNORECASE,
)

# Known text/name filter keys where wildcard wrapping is safe.
# Status, stage, boolean, date, and ID fields are excluded.
_TEXT_FILTER_KEYS = {
    "Site.Name", "Customer.CompanyName", "Name", "CompanyName",
    "Description", "Notes", "OrderNo", "Reference",
    "GivenName", "FamilyName", "DisplayName",
    "Contractor.CompanyName", "Vendor.CompanyName",
}


def safe_decode_filters(filters: Dict[str, Any]) -> Dict[str, Any]:
    """
    Safely decode filter values without corrupting Simpro ``%`` wildcards.

    Only applies ``unquote()`` when a value contains ``%25`` (the URL encoding
    of ``%``), indicating genuine URL-encoded content.  Raw ``%`` characters
    used as Simpro wildcards (e.g., ``%Beveridge%``) are left untouched.

    This replaces the bare ``unquote()`` pattern that corrupted wildcard values
    like ``%Beveridge%`` (where ``%Be`` was misinterpreted as hex byte 0xBE).
    """
    from urllib.parse import unquote

    return {
        k: unquote(v) if isinstance(v, str) and "%25" in v else v
        for k, v in filters.items()
    }


def smart_wrap_filters(filters: Dict[str, Any]) -> Dict[str, Any]:
    """
    Safety net: auto-wrap plain-text filter values in %...% wildcards.

    Only wraps values that are strings, belong to known text fields,
    and don't already use a Simpro operator or wildcard.  This catches
    cases where the LLM passes an exact address like "1 bloomfield avenue"
    instead of "%bloomfield%".
    """
    wrapped = {}
    for key, value in filters.items():
        if (
            isinstance(value, str)
            and key in _TEXT_FILTER_KEYS
            and not _OPERATOR_RE.match(value)
        ):
            wrapped[key] = f"%{value}%"
            logger.info(f"smart_wrap_filters: auto-wrapped {key}='{value}' → '%{value}%'")
        else:
            wrapped[key] = value
    return wrapped


class BaseTool(ABC):
    """
    Abstract base class for MCP tools.

    All tools must implement:
    - name: Tool name
    - description: Tool description for LLM
    - input_schema: JSON schema for tool inputs
    - execute: Async function to execute the tool

    Set ``_supports_filters = False`` on mutation tools (create/update/delete)
    that should NOT receive the universal ``filters`` parameter.
    """

    # Override to False on tools that should NOT get the universal filters param.
    # By default, auto-detected from tool name: create_/update_/delete_ and
    # *_details/*_detail tools are excluded.
    _supports_filters: bool | None = None  # None = auto-detect

    def __init__(self):
        """Initialize tool — auto-injects filters param + hint for list tools."""
        self.name = self.get_name()
        self.description = self.get_description()
        self.input_schema = self.get_input_schema()

        # Auto-detect whether this tool should get filters
        should_have_filters = self._supports_filters
        if should_have_filters is None:
            # Mutation tools and single-entity detail tools don't need filters
            should_have_filters = not (
                self.name.startswith(("create_", "update_", "delete_"))
                or self.name.endswith(("_details", "_detail"))
            )

        if should_have_filters:
            props = self.input_schema.setdefault("properties", {})
            if "filters" not in props:
                props["filters"] = _FILTERS_SCHEMA

            # Append the universal response-field filtering hint to description
            hint = get_api_hint("nested_filters")
            if hint and hint not in self.description:
                self.description = f"{self.description}\n\n{hint}"

        logger.debug(f"Tool initialized: {self.name} (filters={'yes' if should_have_filters else 'no'})")
    
    @abstractmethod
    def get_name(self) -> str:
        """
        Get tool name.
        
        Returns:
            Tool name (e.g., "search_jobs")
        """
        pass
    
    @abstractmethod
    def get_description(self) -> str:
        """
        Get tool description.
        
        This is shown to the LLM so it knows when to use this tool.
        Be clear and concise.
        
        Returns:
            Tool description
        """
        pass
    
    @abstractmethod
    def get_input_schema(self) -> Dict[str, Any]:
        """
        Get JSON schema for tool inputs.
        
        Returns:
            JSON schema dict
        
        Example:
            {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Job status filter"
                    },
                    "customer_id": {
                        "type": "integer",
                        "description": "Filter by customer ID"
                    }
                },
                "required": ["status"]
            }
        """
        pass
    
    @abstractmethod
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the tool.
        
        Args:
            arguments: Tool arguments (validated against input_schema)
        
        Returns:
            Tool execution result
        
        Example:
            async def execute(self, arguments):
                status = arguments.get("status")
                jobs = await self.api.get_jobs(status=status)
                return {"jobs": jobs, "count": len(jobs)}
        """
        pass
    
    # ── Universal filter extraction ──────────────────────────────────
    @staticmethod
    def extract_filters(arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract, decode, and smart-wrap filters from tool arguments.

        Call this in any tool's ``execute()`` to get clean filters ready to
        spread into URL params via ``**filters``.
        """
        raw = arguments.get("filters", {})
        if not isinstance(raw, dict):
            raw = {}
        return smart_wrap_filters(safe_decode_filters(raw))

    def validate_arguments(self, arguments: Dict[str, Any]) -> None:
        """
        Validate arguments against input schema.
        
        Basic validation - can be overridden for custom validation.
        
        Args:
            arguments: Arguments to validate
        
        Raises:
            ValueError: If validation fails
        """
        schema = self.input_schema
        required = schema.get("required", [])
        
        # Check required fields
        for field in required:
            if field not in arguments:
                raise ValueError(f"Required field missing: {field}")
        
        # Check field types and enum constraints
        properties = schema.get("properties", {})
        for field, value in arguments.items():
            if field in properties:
                prop = properties[field]
                expected_type = prop.get("type")
                if expected_type == "string" and not isinstance(value, str):
                    raise ValueError(f"Field '{field}' must be a string")
                elif expected_type == "integer" and not isinstance(value, int):
                    raise ValueError(f"Field '{field}' must be an integer")
                elif expected_type == "number" and not isinstance(value, (int, float)):
                    raise ValueError(f"Field '{field}' must be a number")
                elif expected_type == "boolean" and not isinstance(value, bool):
                    raise ValueError(f"Field '{field}' must be a boolean")

                # Validate enum values
                allowed = prop.get("enum")
                if allowed and value not in allowed:
                    raise ValueError(
                        f"Field '{field}' must be one of {allowed}, got '{value}'"
                    )
    
    async def __call__(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call the tool (convenience method).
        
        Args:
            arguments: Tool arguments
        
        Returns:
            Tool execution result
        """
        # Validate arguments
        self.validate_arguments(arguments)
        
        # Execute tool
        logger.info(f"Executing tool: {self.name}")
        result = await self.execute(arguments)
        logger.debug(f"Tool {self.name} completed")
        
        return result
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert tool to dictionary format for MCP.
        
        Returns:
            Tool dict
        """
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema
        }