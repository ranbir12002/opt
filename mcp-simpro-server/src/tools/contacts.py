"""
Contact-related MCP tools.

Provides tools for searching and viewing contacts/people in Simpro.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.simpro.api.contacts import ContactsAPI
from src.simpro_api_reference import get_api_hint
from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


class SearchContactsTool(BaseTool):
    """
    Tool for searching contacts in Simpro.
    """
    
    def __init__(self):
        """Initialize search contacts tool"""
        self.contacts_api = ContactsAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "search_contacts"
    
    def get_description(self) -> str:
        return f"""Search for contacts/people in Simpro with optional filters.

Use this tool ONLY when the user wants contact information (phone, email, address).
Do NOT use this tool to look up a person before querying schedules, jobs, or invoices
— those tools already include staff/person names in their results.

Filterable fields (use in 'filters' param): GivenName, FamilyName, CompanyName,
Email, Phone, Type

Examples:
- "Show me all contacts" → no filters
- "What is John Smith's phone number?" → search: "John Smith"
- "Find contact details for John Smith" → filters: {{"GivenName": "%John%", "FamilyName": "%Smith%"}}

{get_api_hint("search_operators", "pagination")}
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Search term for filtering contacts"
                }
            }
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute contact search with auto-pagination to fetch all pages."""
        page_size = arguments.get("page_size", 250)
        search = arguments.get("search")

        filters = self.extract_filters(arguments)

        all_contacts: List[Dict[str, Any]] = []
        current_page = 1
        while True:
            result = await self.contacts_api.get_contacts(
                page=current_page,
                page_size=page_size,
                search=search,
                **filters
            )
            if isinstance(result, list):
                all_contacts.extend(result)
                if len(result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"search_contacts: fetched {len(all_contacts)} contacts across {current_page} page(s)")

        return {
            "contacts": all_contacts,
            "total_fetched": len(all_contacts),
            "pages_fetched": current_page
        }


class GetContactDetailsTool(BaseTool):
    """
    Tool for getting detailed information about a specific contact.
    """
    
    def __init__(self):
        """Initialize get contact details tool"""
        self.contacts_api = ContactsAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_contact_details"
    
    def get_description(self) -> str:
        return """Get detailed information about a specific contact by ID.
        
Use this tool when the user asks for details about a specific contact,
wants to see contact information, phone numbers, or email addresses.

Examples:
- "Show me details for contact 12345"
- "What's the phone number for contact 67890?"
- "Get information about contact ID 111"
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "contact_id": {
                    "type": "integer",
                    "description": "The ID of the contact to retrieve"
                }
            },
            "required": ["contact_id"]
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get contact details"""
        contact_id = arguments["contact_id"]
        
        # Call Simpro API
        result = await self.contacts_api.get_contact_by_id(contact_id=contact_id)
        
        return {
            "contact": result,
            "contact_id": contact_id
        }