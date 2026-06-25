"""
Setup API wrapper for Simpro.

Handles setup and configuration-related API calls.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.simpro.client import get_simpro_client
from src.utils import get_logger

logger = get_logger(__name__)


class SetupAPI:
    """
    API wrapper for Simpro setup endpoints.
    
    Provides methods for:
    - Getting labor rates
    - Getting teams
    - Getting other setup/configuration data
    """
    
    def __init__(self):
        """Initialize Setup API"""
        logger.debug("Setup API initialized")
    
    async def get_labor_rates(
        self,
        page: int = 1,
        page_size: int = 250,
        columns: Optional[str] = None
    ) -> list[Dict[str, Any]]:
        """
        Get list of labor rates.
        
        Args:
            page: Page number (1-based)
            page_size: Number of labor rates per page
            columns: Optional columns to include
        
        Returns:
            List of labor rates
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching labor rates (page={page}, page_size={page_size})")

        endpoint = f"/v1.0/companies/{company_id}/setup/labourRates/"
        
        params = {
            "page": page,
            "pageSize": page_size
        }
        
        if columns:
            params["columns"] = columns
        
        result = await client.get(endpoint, params=params)
        return result

    async def get_labor_rate_by_id(
        self,
        labor_rate_id: int,
        columns: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a specific labor rate by ID.
        
        Args:
            labor_rate_id: Labor rate ID
            columns: Optional columns to include
        
        Returns:
            Labor rate data
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching labor rate {labor_rate_id}")

        endpoint = f"/v1.0/companies/{company_id}/setup/labourRates/{labor_rate_id}"
        
        params = {}
        if columns:
            params["columns"] = columns
        
        result = await client.get(endpoint, params=params)
        return result

    async def get_teams(
        self,
        page: int = 1,
        page_size: int = 250,
        columns: Optional[str] = None
    ) -> list[Dict[str, Any]]:
        """
        Get list of teams.
        
        Args:
            page: Page number (1-based)
            page_size: Number of teams per page
            columns: Optional columns to include
        
        Returns:
            List of teams
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching teams (page={page}, page_size={page_size})")

        endpoint = f"/v1.0/companies/{company_id}/setup/teams/"
        
        params = {
            "page": page,
            "pageSize": page_size
        }
        
        if columns:
            params["columns"] = columns
        
        result = await client.get(endpoint, params=params)
        return result

    async def get_team_by_id(
        self,
        team_id: int,
        columns: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a specific team by ID.

        Args:
            team_id: Team ID
            columns: Optional columns to include

        Returns:
            Team data
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching team {team_id}")

        endpoint = f"/v1.0/companies/{company_id}/setup/teams/{team_id}"

        params = {}
        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result

    # ── Setup Cost Centres (Account-linked) ──────────────────────────────

    async def get_setup_cost_centres(
        self,
        page: int = 1,
        page_size: int = 250,
        columns: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        """
        Get list of setup cost centres (account-linked definitions).

        Endpoint: /setup/accounts/costCenters/
        NOTE: Different from /jobCostCenters/ (cost centre types).

        Args:
            page: Page number (1-based)
            page_size: Number of items per page
            columns: Optional comma-separated columns (e.g. "ID,Name,IncomeAccountNo")

        Returns:
            List of setup cost centres
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching setup cost centres (page={page}, page_size={page_size})")

        endpoint = f"/v1.0/companies/{company_id}/setup/accounts/costCenters/"

        params = {"page": page, "pageSize": page_size}
        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result

    async def get_setup_cost_centre_by_id(
        self,
        cost_center_id: int,
        columns: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get details for a specific setup cost centre.

        Endpoint: /setup/accounts/costCenters/{costCenterID}

        Args:
            cost_center_id: Setup cost centre ID
            columns: Optional comma-separated columns

        Returns:
            Cost centre details {ID, Name, IncomeAccountNo, ExpenseAccountNo,
            AccrualRevAccountNo, DeferralRevAccountNo, MonthlySalesBudget,
            MonthlyExpenditureBudget, Archived, IsMembershipCostCenter, Rates}
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching setup cost centre {cost_center_id}")

        endpoint = f"/v1.0/companies/{company_id}/setup/accounts/costCenters/{cost_center_id}"

        params = {}
        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result

    # ── Chart of Accounts ────────────────────────────────────────────────

    async def get_chart_of_accounts(
        self,
        page: int = 1,
        page_size: int = 250,
        columns: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        """
        Get list of chart of accounts entries.

        Endpoint: /setup/accounts/chartOfAccounts/

        Args:
            page: Page number (1-based)
            page_size: Number of items per page
            columns: Optional comma-separated columns (e.g. "ID,Name,Number,Type")

        Returns:
            List of chart of accounts entries
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching chart of accounts (page={page}, page_size={page_size})")

        endpoint = f"/v1.0/companies/{company_id}/setup/accounts/chartOfAccounts/"

        params = {"page": page, "pageSize": page_size}
        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result

    async def get_chart_of_accounts_by_id(
        self,
        account_id: int,
        columns: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get details for a specific chart of accounts entry.

        Endpoint: /setup/accounts/chartOfAccounts/{accountID}

        Args:
            account_id: Chart of accounts entry ID
            columns: Optional comma-separated columns

        Returns:
            Account details {ID, Name, Number, Type, Archived}
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching chart of accounts entry {account_id}")

        endpoint = f"/v1.0/companies/{company_id}/setup/accounts/chartOfAccounts/{account_id}"

        params = {}
        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result