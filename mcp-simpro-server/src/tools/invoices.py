#mcp-simpro-server/src/tools
"""
Invoice-related MCP tools.

Provides tools for searching and viewing invoices in Simpro.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.simpro.api.invoices import InvoicesAPI
from src.simpro_api_reference import get_api_hint
from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


class SearchInvoicesTool(BaseTool):
    """
    Tool for searching invoices in Simpro.
    """
    
    def __init__(self):
        """Initialize search invoices tool"""
        self.invoices_api = InvoicesAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "search_invoices"
    
    def get_description(self) -> str:
        return f"""Search for invoices in Simpro with optional filters.

Use this tool when the user asks about invoices, billing, payments,
or wants to find invoices.

Filterable fields (use in 'filters' param): IsPaid, Type, Status, DateIssued,
Jobs.ID, Customer.ID, Customer.CompanyName, Total.ExTax, Total.IncTax,
Total.BalanceDue, RecurringInvoice.ID

IMPORTANT: Invoices do NOT have a Site field. To find invoices for a site/address:
1. First use search_jobs with filters: {{"Site.Name": "%keyword%"}} to get Job IDs
2. Then use search_invoices with filters: {{"Jobs.ID": "in(id1,id2,...)"}}

Examples:
- "Show me all unpaid invoices" → is_paid: "false"
- "Unpaid invoices for job 20821" → is_paid: "false", filters: {{"Jobs.ID": "20821"}}
- "Invoices for customer 690" → filters: {{"Customer.ID": "690"}}
- "Invoices for Smith" → filters: {{"Customer.CompanyName": "%Smith%"}}
- "Progress invoices" → filters: {{"Type": "ProgressInvoice"}}
- "Invoices from January" → filters: {{"DateIssued": "between(2026-01-01,2026-01-31)"}}
- "Unpaid invoices over $1000" → is_paid: "false", filters: {{"Total.BalanceDue": "gt(1000)"}}

{get_api_hint("search_operators", "pagination")}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "is_paid": {
                    "type": "string",
                    "description": "Filter by paid status: 'true' for paid, 'false' for unpaid. Shorthand for filters.IsPaid."
                }
            }
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute invoice search with auto-pagination to fetch all pages."""
        page_size = arguments.get("page_size", 250)
        is_paid = arguments.get("is_paid")

        filters = self.extract_filters(arguments)

        # Auto-paginate to fetch all results
        all_invoices: List[Dict[str, Any]] = []
        current_page = 1
        while True:
            result = await self.invoices_api.get_invoices(
                page=current_page,
                page_size=page_size,
                is_paid=is_paid,
                **filters,
            )
            if isinstance(result, list):
                all_invoices.extend(result)
                if len(result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"search_invoices: fetched {len(all_invoices)} invoices across {current_page} page(s)")

        return {
            "invoices": all_invoices,
            "total_fetched": len(all_invoices),
            "pages_fetched": current_page,
            "filter": f"paid={is_paid}" if is_paid else "all"
        }


class GetInvoiceDetailsTool(BaseTool):
    """
    Tool for getting detailed information about a specific invoice.
    """
    
    def __init__(self):
        """Initialize get invoice details tool"""
        self.invoices_api = InvoicesAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_invoice_details"
    
    def get_description(self) -> str:
        return f"""Get detailed information about a specific invoice by ID.

Use this tool when the user asks for details about a specific invoice,
wants to see invoice information, or needs to check an invoice's status.

{get_api_hint("display_all")}

Examples:
- "Show me details for invoice 12345" → display=None (basic info only)
- "What was invoiced on invoice 67890?" → display='all'
- "What's the status of invoice 111?" → display=None
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "invoice_id": {
                    "type": "integer",
                    "description": "The ID of the invoice to retrieve"
                },
                "display": {
                    "type": "string",
                    "description": "Set to 'all' to include all subresources (line items, cost centres) in one call. Omit for basic invoice info only.",
                    "enum": ["all"]
                }
            },
            "required": ["invoice_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get invoice details"""
        invoice_id = arguments["invoice_id"]
        display = arguments.get("display")

        # Call Simpro API
        result = await self.invoices_api.get_invoice_by_id(
            invoice_id=invoice_id,
            display=display
        )
        
        return {
            "invoice": result,
            "invoice_id": invoice_id
        }
    
class CreateInvoiceTool(BaseTool):
    """
    Tool for creating invoices in Simpro.
    
    Accepts invoice body from invoice agent and POSTs to Simpro API:
    /api/v1.0/companies/{companyID}/invoices/
    """
    
    def __init__(self):
        """Initialize create invoice tool"""
        self.invoices_api = InvoicesAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "create_invoice"
    
    def get_description(self) -> str:
        return f"""Create a new invoice in Simpro.

This tool accepts a complete invoice body and POSTs it to Simpro.

Required fields: Type, Jobs, DateIssued, Stage, PerItem
- Type: "TaxInvoice" | "Deposit" | "ProgressInvoice" | "RequestForClaim"
- Jobs: [<job_id>]
- Stage: "Approved" | "Pending"
- CostCenters: [{{"ID": <id>, "Claim": {{"Percent": <n>}}, "Items": [{{"ID": <id>, "Quantity": <n>}}]}}]

Returns the created invoice with ID from Simpro.

{get_api_hint("mutation_errors")}
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "integer",
                    "description": "Simpro company ID"
                },
                "invoice_data": {
                    "type": "object",
                    "description": "Complete invoice body matching Simpro API format",
                    "properties": {
                        "Type": {
                            "type": "string",
                            "enum": ["TaxInvoice", "Deposit", "ProgressInvoice", "RequestForClaim"],
                            "description": "Type of invoice"
                        },
                        "Jobs": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "Job IDs to invoice"
                        },
                        "DateIssued": {
                            "type": "string",
                            "format": "date",
                            "description": "Issue date (YYYY-MM-DD)"
                        },
                        "PaymentTermID": {
                            "type": "integer",
                            "description": "Payment term ID (optional)"
                        },
                        "PaymentTerms": {
                            "type": "object",
                            "properties": {
                                "Days": {"type": "integer"},
                                "Type": {"type": "string"},
                                "DueDate": {"type": "string", "format": "date"}
                            }
                        },
                        "ProgressClaimNumber": {
                            "type": "integer",
                            "description": "Progress claim number"
                        },
                        "Stage": {
                            "type": "string",
                            "enum": ["Approved", "Pending"],
                            "description": "Invoice stage"
                        },
                        "PerItem": {
                            "type": "boolean",
                            "description": "Per-item (true) or consolidated (false)"
                        },
                        "OrderNo": {
                            "type": "string",
                            "description": "Optional order number"
                        },
                        "LatePaymentFee": {
                            "type": "boolean",
                            "description": "Apply late payment fee"
                        },
                        "CISDeductionRate": {
                            "type": "number",
                            "description": "CIS deduction rate (UK/Ireland)"
                        },
                        "AccountingCategory": {
                            "type": "integer",
                            "description": "Accounting category ID"
                        },
                        "Status": {
                            "type": "integer",
                            "description": "Invoice status code ID"
                        },
                        "AutoAdjustStatus": {
                            "type": "boolean",
                            "description": "Auto-adjust status"
                        },
                        "Description": {
                            "type": "string",
                            "description": "Invoice description (supports HTML)"
                        },
                        "Notes": {
                            "type": "string",
                            "description": "Invoice footnote (supports HTML)"
                        },
                        "Retainage": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "JobID": {"type": "integer"},
                                    "ExTax": {"type": "number"},
                                    "IncTax": {"type": "number"}
                                }
                            }
                        },
                        "CostCenters": {
                            "type": "array",
                            "description": "Cost centers with claims or items",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "ID": {
                                        "type": "integer",
                                        "description": "Cost center ID"
                                    },
                                    "Claim": {
                                        "type": "object",
                                        "properties": {
                                            "ExTax": {"type": "number"},
                                            "IncTax": {"type": "number"},
                                            "Percent": {"type": "number"}
                                        }
                                    },
                                    "Items": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "ID": {"type": "integer"},
                                                "Quantity": {"type": "number"}
                                            }
                                        }
                                    }
                                },
                                "required": ["ID"]
                            }
                        }
                    },
                    "required": ["Type", "Jobs", "DateIssued", "Stage", "PerItem"]
                }
            },
            "required": ["company_id", "invoice_data"]
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute invoice creation.
        
        Args:
            arguments: {
                "company_id": int,
                "invoice_data": {...invoice body...}
            }
        
        Returns:
            Created invoice data from Simpro API
        """
        company_id = arguments.get("company_id")
        invoice_data = arguments.get("invoice_data", {})
        
        job_id = invoice_data.get("Jobs", [None])[0] if invoice_data.get("Jobs") else None
        
        logger.info(f"Creating invoice for CompanyID={company_id}, JobID={job_id}")
        logger.info(f"  Type: {invoice_data.get('Type')}")
        logger.info(f"  Stage: {invoice_data.get('Stage')}")
        logger.info(f"  PerItem: {invoice_data.get('PerItem')}")
        logger.info(f"  CostCenters: {len(invoice_data.get('CostCenters', []))}")
        
        try:
            # Call Simpro API
            result = await self.invoices_api.create_invoice(
                company_id=company_id,
                invoice_data=invoice_data
            )

            invoice_id = result.get("ID")

            # Detect empty/incomplete response from Simpro.
            # This typically happens when a cost centre is already 100% claimed
            # or other business-rule rejections that Simpro signals by returning
            # an empty (or ID-less) response instead of an HTTP error.
            if not invoice_id or not result or result == {}:
                cost_centres = invoice_data.get("CostCenters", [])
                cc_ids = [cc.get("ID") for cc in cost_centres if cc.get("ID")]

                logger.warning(
                    f"⚠️ Simpro returned empty/no-ID response for "
                    f"JobID={job_id}, CostCentres={cc_ids}. "
                    f"Likely already fully claimed or business-rule rejection."
                )

                return {
                    "success": True,
                    "status": "warning",
                    "warning": (
                        f"Simpro accepted the request but returned no invoice ID. "
                        f"This usually means the cost centre(s) {cc_ids} in Job {job_id} "
                        f"are already fully claimed (100%) or a business rule prevented creation."
                    ),
                    "invoice": result,
                    "invoice_id": None,
                    "job_id": job_id,
                    "cost_centre_ids": cc_ids,
                }

            logger.info(f"✅ Invoice created: ID={invoice_id}")

            return {
                "success": True,
                "status": "created",
                "invoice": result,
                "invoice_id": invoice_id,
                "job_id": job_id
            }

        except Exception as e:
            logger.error(f"❌ Failed to create invoice: {e}")
            return {
                "success": False,
                "status": "failed",
                "error": str(e),
                "job_id": job_id
            }


class UpdateInvoiceTool(BaseTool):
    """
    Tool for updating (PATCHing) an existing invoice in Simpro.
    """

    def __init__(self):
        """Initialize update invoice tool"""
        self.invoices_api = InvoicesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "update_invoice"

    def get_description(self) -> str:
        return f"""Update an existing invoice in Simpro (partial update via PATCH).

Only include the fields you want to change — Simpro merges them with existing data.

{get_api_hint("mutation_errors", "patch_semantics")}

Updatable fields include:
- Type: "TaxInvoice" | "Deposit" | "ProgressInvoice" | "RequestForClaim"
- DateIssued: "YYYY-MM-DD"
- Stage: "Approved" | "Pending"
- PerItem: true | false
- OrderNo, Description, Notes (supports HTML)
- PaymentTermID, LatePaymentFee, CISDeductionRate
- AccountingCategory, Status, AutoAdjustStatus
- CostCenters: [{{ID, Claim: {{ExTax, IncTax, Percent}}, Items: [{{ID, Quantity}}]}}]
- Retainage: [{{JobID, ExTax, IncTax}}]

Examples:
- "Change invoice 12345 stage to Pending"
- "Update invoice 67890 description"
- "Change the date on invoice 111 to 2026-03-01"
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "invoice_id": {
                    "type": "integer",
                    "description": "The ID of the invoice to update"
                },
                "invoice_data": {
                    "type": "object",
                    "description": "Partial invoice body — only include fields to update",
                    "properties": {
                        "Type": {
                            "type": "string",
                            "enum": ["TaxInvoice", "Deposit", "ProgressInvoice", "RequestForClaim"]
                        },
                        "DateIssued": {"type": "string", "format": "date"},
                        "PaymentTermID": {"type": "integer"},
                        "Stage": {"type": "string", "enum": ["Approved", "Pending"]},
                        "PerItem": {"type": "boolean"},
                        "OrderNo": {"type": "string"},
                        "LatePaymentFee": {"type": "boolean"},
                        "CISDeductionRate": {"type": ["number", "null"]},
                        "AccountingCategory": {"type": "integer"},
                        "Status": {"type": "integer"},
                        "AutoAdjustStatus": {"type": "boolean"},
                        "Description": {"type": "string"},
                        "Notes": {"type": "string"},
                        "CostCenters": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "ID": {"type": "integer"},
                                    "Claim": {
                                        "type": "object",
                                        "properties": {
                                            "ExTax": {"type": "number"},
                                            "IncTax": {"type": "number"},
                                            "Percent": {"type": "number"}
                                        }
                                    },
                                    "Items": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "ID": {"type": "integer"},
                                                "Quantity": {"type": "number"}
                                            }
                                        }
                                    }
                                },
                                "required": ["ID"]
                            }
                        },
                        "Retainage": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "JobID": {"type": "number"},
                                    "ExTax": {"type": "number"},
                                    "IncTax": {"type": "number"}
                                }
                            }
                        }
                    }
                }
            },
            "required": ["invoice_id", "invoice_data"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute update invoice"""
        invoice_id = arguments["invoice_id"]
        invoice_data = arguments["invoice_data"]

        logger.info(f"Updating invoice {invoice_id} with fields: {list(invoice_data.keys())}")

        await self.invoices_api.update_invoice(
            invoice_id=invoice_id,
            invoice_data=invoice_data,
        )

        return {
            "success": True,
            "message": "Invoice updated successfully",
            "invoice_id": invoice_id,
            "updated_fields": list(invoice_data.keys()),
        }


class DeleteInvoiceTool(BaseTool):
    """
    Tool for deleting an invoice in Simpro.
    """

    def __init__(self):
        """Initialize delete invoice tool"""
        self.invoices_api = InvoicesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "delete_invoice"

    def get_description(self) -> str:
        return f"""Delete an existing invoice from Simpro.

Permanently removes an invoice. Returns success on deletion, 404 if not found.

IMPORTANT: Always confirm with the user before deleting an invoice.

{get_api_hint("mutation_errors")}

Examples:
- "Delete invoice 12345"
- "Remove invoice 67890"
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "invoice_id": {
                    "type": "integer",
                    "description": "The ID of the invoice to delete"
                }
            },
            "required": ["invoice_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute delete invoice"""
        invoice_id = arguments["invoice_id"]

        logger.info(f"Deleting invoice {invoice_id}")

        await self.invoices_api.delete_invoice(invoice_id=invoice_id)

        return {
            "success": True,
            "message": "Invoice deleted successfully",
            "invoice_id": invoice_id
        }