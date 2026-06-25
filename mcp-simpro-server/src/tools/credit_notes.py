"""
Credit Note-related MCP tools.

Provides tools for viewing credit notes in Simpro.
"""
from __future__ import annotations

from typing import Any, Dict

from src.simpro.api.credit_notes import CreditNotesAPI
from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


class GetCreditNotesByInvoiceTool(BaseTool):
    """
    Tool for getting credit notes for a specific invoice.
    """
    
    def __init__(self):
        """Initialize get credit notes by invoice tool"""
        self.credit_notes_api = CreditNotesAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_credit_notes_by_invoice"
    
    def get_description(self) -> str:
        return """Get all credit notes for a specific invoice.
        
Use this tool when the user asks about credit notes for an invoice,
refunds, or adjustments to an invoice.

Examples:
- "Show me credit notes for invoice 12345"
- "Get refunds for invoice 67890"
- "What credit notes are applied to invoice 111?"
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "invoice_id": {
                    "type": "integer",
                    "description": "The ID of the invoice"
                }
            },
            "required": ["invoice_id"]
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get credit notes by invoice"""
        invoice_id = arguments["invoice_id"]
        
        # Call Simpro API
        result = await self.credit_notes_api.get_credit_notes_by_invoice(
            invoice_id=invoice_id
        )
        
        return {
            "credit_notes": result,
            "invoice_id": invoice_id
        }


class GetCreditNoteDetailsTool(BaseTool):
    """
    Tool for getting detailed information about a specific credit note.
    """
    
    def __init__(self):
        """Initialize get credit note details tool"""
        self.credit_notes_api = CreditNotesAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_credit_note_details"
    
    def get_description(self) -> str:
        return """Get detailed information about a specific credit note.
        
Use this tool when the user asks for details about a specific credit note,
wants to see credit note amount, or needs credit note information.

Examples:
- "Show me credit note 123 for invoice 456"
- "Get details for credit note 789"
- "What's the amount of credit note 111 on invoice 222?"
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "invoice_id": {
                    "type": "integer",
                    "description": "The ID of the invoice"
                },
                "credit_note_id": {
                    "type": "integer",
                    "description": "The ID of the credit note"
                }
            },
            "required": ["invoice_id", "credit_note_id"]
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get credit note details"""
        invoice_id = arguments["invoice_id"]
        credit_note_id = arguments["credit_note_id"]
        
        # Call Simpro API
        result = await self.credit_notes_api.get_credit_note_by_id(
            invoice_id=invoice_id,
            credit_note_id=credit_note_id
        )
        
        return {
            "credit_note": result,
            "invoice_id": invoice_id,
            "credit_note_id": credit_note_id
        }