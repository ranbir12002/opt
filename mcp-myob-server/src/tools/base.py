"""
Base tool class for all MyOB MCP tools.

All tools inherit from BaseTool and implement:
- get_name() — tool name (e.g., "myob_search_customers")
- get_description() — LLM-facing description with API hints
- get_input_schema() — JSON Schema for tool inputs
- execute() — async implementation that calls MyOB API
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

from src.utils import get_logger
from src.myob.odata import smart_build_filter

logger = get_logger(__name__)


class BaseTool(ABC):
    """Abstract base class for MyOB MCP tools."""

    def __init__(self):
        self.name = self.get_name()
        self.description = self.get_description()
        self.input_schema = self.get_input_schema()
        logger.debug(f"Tool initialized: {self.name}")

    @abstractmethod
    def get_name(self) -> str:
        pass

    @abstractmethod
    def get_description(self) -> str:
        pass

    @abstractmethod
    def get_input_schema(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        pass

    def validate_arguments(self, arguments: Dict[str, Any]) -> None:
        """Basic validation against input schema."""
        schema = self.input_schema
        required = schema.get("required", [])
        for field in required:
            if field not in arguments:
                raise ValueError(f"Required field missing: {field}")

        properties = schema.get("properties", {})
        for field, value in arguments.items():
            if field in properties:
                prop = properties[field]
                expected_type = prop.get("type")
                if expected_type == "string" and not isinstance(value, str):
                    raise ValueError(f"Field '{field}' must be a string")
                elif expected_type == "integer" and not isinstance(value, int):
                    raise ValueError(f"Field '{field}' must be an integer")
                elif expected_type == "boolean" and not isinstance(value, bool):
                    raise ValueError(f"Field '{field}' must be a boolean")

                allowed = prop.get("enum")
                if allowed and value not in allowed:
                    raise ValueError(
                        f"Field '{field}' must be one of {allowed}, got '{value}'"
                    )

    async def __call__(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        self.validate_arguments(arguments)
        logger.info(f"Executing tool: {self.name}")
        result = await self.execute(arguments)
        logger.debug(f"Tool {self.name} completed")
        return result

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
