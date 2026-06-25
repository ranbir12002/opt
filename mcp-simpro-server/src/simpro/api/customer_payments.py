"""
Customer Payments API wrapper for Simpro.

Handles all customer payment-related API calls.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.simpro.client import get_simpro_client
from src.utils import get_logger

logger = get_logger(__name__)


class CustomerPaymentsAPI:
    """
    API wrapper for Simpro customer payments endpoints.
    
    Provides methods for:
    - Getting customer payments
    - Getting payment details
    - Creating payments
    """
    
    def __init__(self):
        """Initialize Customer Payments API"""
        logger.debug("Customer Payments API initialized")
    
    async def get_customer_payments(
        self,
        page: int = 1,
        page_size: int = 250,
        columns: Optional[str] = None,
        **filters
    ) -> list[Dict[str, Any]]:
        """
        Get list of customer payments.

        Args:
            page: Page number (1-based)
            page_size: Number of payments per page
            columns: Comma-separated columns to include
            **filters: Additional Simpro API filters (passed as query params)

        Returns:
            List of customer payments
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching customer payments (page={page}, page_size={page_size})")

        endpoint = f"/v1.0/companies/{company_id}/customerPayments/"

        params = {
            "page": page,
            "pageSize": page_size,
            **filters,
        }

        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result

    async def get_customer_payment_by_id(
        self,
        payment_id: int,
        columns: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a specific customer payment by ID.
        
        Args:
            payment_id: Payment ID
            columns: Optional columns to include
        
        Returns:
            Payment data
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching customer payment {payment_id}")

        endpoint = f"/v1.0/companies/{company_id}/customerPayments/{payment_id}"
        
        params = {}
        if columns:
            params["columns"] = columns
        
        result = await client.get(endpoint, params=params)
        return result

    async def create_customer_payment(
        self,
        payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create a new customer payment.
        
        Args:
            payload: Payment data including Payment details and Invoices
        
        Returns:
            Created payment data
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Creating customer payment")

        endpoint = f"/v1.0/companies/{company_id}/customerPayments/"
        result = await client.post(endpoint, json=payload)
        return result
    
    async def apply_payment_to_invoice(
        self,
        invoice_id: int,
        amount: float,
        payment_method_id: int,
        deposit_account: str,
        date: str,
        reference: Optional[str] = None,
        details: Optional[str] = None,
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Apply a payment to a specific invoice (convenience method).
        
        Args:
            invoice_id: Invoice ID to apply payment to
            amount: Payment amount
            payment_method_id: Payment method ID
            deposit_account: Deposit account code (e.g., "1-1106")
            date: Payment date (YYYY-MM-DD)
            reference: Optional reference/check number
            details: Optional payment details
            notes: Optional notes
        
        Returns:
            Created payment data
        """
        logger.info(f"Applying payment of ${amount} to invoice {invoice_id}")
        
        payload = {
            "Payment": {
                "PaymentMethod": payment_method_id,
                "DepositAccount": deposit_account,
                "Date": date,
                "FinanceCharge": 0,
                "CheckNo": reference or f"INV-{invoice_id}",
                "Details": details or f"Payment of ${amount:.2f} to invoice {invoice_id}",
            },
            "Invoices": [
                {"Invoice": invoice_id, "Amount": amount}
            ],
            "Notes": notes or f"Payment ${amount:.2f} applied to invoice {invoice_id}",
        }
        
        return await self.create_customer_payment(payload)