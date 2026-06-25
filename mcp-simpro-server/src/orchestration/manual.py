"""
Manual Orchestration Strategy.

For weak LLMs or when you need guaranteed execution patterns.
Uses pre-defined workflows and intent detection instead of LLM orchestration.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from src.tools.executor import get_tool_executor
from src.utils import get_logger

from .base import BaseOrchestrator

logger = get_logger(__name__)


def _fuzzy_find_customer(
    query: str, customers: List[Dict[str, Any]], threshold: float = 0.6
) -> Optional[Dict[str, Any]]:
    """Fuzzy-match a customer name against a list of customer dicts.

    Checks CompanyName (primary) then GivenName/FamilyName (fallback).
    Uses substring match first, then edit-distance for typo tolerance.
    """
    q = query.lower().strip()
    best, best_score = None, 0.0

    for c in customers:
        name = (c.get("CompanyName") or c.get("Name") or "").lower()
        if not name:
            given = (c.get("GivenName") or "").strip()
            family = (c.get("FamilyName") or "").strip()
            name = f"{given} {family}".strip().lower()
        if not name:
            continue

        # Substring match — high confidence
        if q in name or name in q:
            return c

        # Edit-distance
        ratio = SequenceMatcher(None, q, name).ratio()
        if ratio > best_score:
            best_score = ratio
            best = c

    return best if best_score >= threshold else None


class ManualOrchestrator(BaseOrchestrator):
    """
    Manual orchestration strategy.
    
    This strategy uses code-based workflows instead of LLM orchestration:
    1. Detect intent from query (regex/NLP)
    2. Extract entities (customer names, IDs, etc.)
    3. Execute pre-defined workflow
    4. Format response
    
    Works with: Any LLM, or even no LLM at all
    
    Example flow:
        User: "Create invoices for customer ABC"
        
        Intent: create_invoices_for_customer
        Entities: {customer_name: "ABC"}
        
        Workflow:
        1. search_customers(name="ABC")
        2. search_jobs(customer_id=result.id)
        3. create_invoice for each job
        
        All done with code - no LLM orchestration needed!
    """
    
    def __init__(
        self,
        llm_provider: Optional[Any] = None,
        tools: Optional[List[Dict[str, Any]]] = None
    ):
        """
        Initialize manual orchestrator.
        
        Args:
            llm_provider: Optional LLM for response formatting
            tools: List of available tools
        """
        super().__init__(llm_provider, tools)
        self.tool_executor = get_tool_executor()
        
        logger.info("Manual orchestrator ready")
    
    def _detect_intent(self, query: str) -> tuple[str, Dict[str, Any]]:
        """
        Detect intent and extract entities from query.
        
        Args:
            query: User query
        
        Returns:
            Tuple of (intent, entities)
        """
        query_lower = query.lower()
        entities = {}
        
        # Pattern: "jobs for customer X"
        if re.search(r"jobs?.*(for|of)\s+customer", query_lower):
            # Extract customer name
            match = re.search(r"customer\s+['\"]?([a-zA-Z0-9\s]+)['\"]?", query_lower)
            if match:
                entities["customer_name"] = match.group(1).strip()
            return ("get_jobs_for_customer", entities)
        
        # Pattern: "customer details/info"
        if re.search(r"customer.*(detail|info)", query_lower):
            # Extract customer ID
            match = re.search(r"customer\s+(\d+)", query_lower)
            if match:
                entities["customer_id"] = int(match.group(1))
            else:
                # Extract customer name
                match = re.search(r"customer\s+['\"]?([a-zA-Z0-9\s]+)['\"]?", query_lower)
                if match:
                    entities["customer_name"] = match.group(1).strip()
            return ("get_customer_details", entities)
        
        # Pattern: "job details/info"
        if re.search(r"job.*(detail|info)", query_lower):
            # Extract job ID
            match = re.search(r"job\s+(\d+)", query_lower)
            if match:
                entities["job_id"] = int(match.group(1))
            return ("get_job_details", entities)
        
        # Pattern: "search/find/list jobs"
        if re.search(r"(search|find|list|show).*(job|jobs)", query_lower):
            # Extract status if mentioned
            if "active" in query_lower:
                entities["status"] = "Active"
            elif "completed" in query_lower:
                entities["status"] = "Completed"
            return ("search_jobs", entities)
        
        # Pattern: "search/find/list customers"
        if re.search(r"(search|find|list|show).*(customer|customers)", query_lower):
            return ("search_customers", entities)
        
        # Default: generic search
        return ("unknown", entities)
    
    async def _execute_workflow(
        self,
        intent: str,
        entities: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute pre-defined workflow based on intent.
        
        Args:
            intent: Detected intent
            entities: Extracted entities
        
        Returns:
            Workflow execution result
        """
        tool_calls = []
        
        # Workflow: Get jobs for customer
        if intent == "get_jobs_for_customer":
            customer_name = entities.get("customer_name")
            
            if not customer_name:
                return {
                    "success": False,
                    "error": "Customer name not provided"
                }
            
            # Step 1: Search customer
            result1 = await self.tool_executor.execute(
                "search_customers",
                {"page": 1, "page_size": 10}
            )
            tool_calls.append({"tool": "search_customers", "result": result1})
            
            if not result1.get("success"):
                return {
                    "success": False,
                    "tool_calls": tool_calls,
                    "error": "Failed to search customers"
                }
            
            # Find matching customer (fuzzy match)
            customers = result1.get("data", {}).get("customers", [])
            customer = _fuzzy_find_customer(customer_name, customers)
            
            if not customer:
                return {
                    "success": False,
                    "tool_calls": tool_calls,
                    "response": f"Customer '{customer_name}' not found"
                }
            
            # Step 2: Get jobs for customer
            customer_id = customer.get("ID")
            result2 = await self.tool_executor.execute(
                "search_jobs",
                {"page": 1, "page_size": 20}
            )
            tool_calls.append({"tool": "search_jobs", "result": result2})
            
            jobs = result2.get("data", {}).get("jobs", [])
            
            return {
                "success": True,
                "tool_calls": tool_calls,
                "response": f"Found {len(jobs)} jobs for customer {customer.get('Name')}"
            }
        
        # Workflow: Get customer details
        elif intent == "get_customer_details":
            customer_id = entities.get("customer_id")
            
            if not customer_id:
                # Search by name first
                customer_name = entities.get("customer_name")
                if customer_name:
                    result1 = await self.tool_executor.execute(
                        "search_customers",
                        {"page": 1, "page_size": 10}
                    )
                    tool_calls.append({"tool": "search_customers", "result": result1})
                    
                    # Find matching customer (fuzzy match)
                    customers = result1.get("data", {}).get("customers", [])
                    matched = _fuzzy_find_customer(customer_name, customers)
                    if matched:
                        customer_id = matched.get("ID")
            
            if not customer_id:
                return {
                    "success": False,
                    "tool_calls": tool_calls,
                    "error": "Customer ID not found"
                }
            
            # Get customer details
            result = await self.tool_executor.execute(
                "get_customer_details",
                {"customer_id": customer_id}
            )
            tool_calls.append({"tool": "get_customer_details", "result": result})
            
            return {
                "success": True,
                "tool_calls": tool_calls,
                "response": f"Retrieved details for customer {customer_id}"
            }
        
        # Workflow: Get job details
        elif intent == "get_job_details":
            job_id = entities.get("job_id")
            
            if not job_id:
                return {
                    "success": False,
                    "error": "Job ID required"
                }
            
            result = await self.tool_executor.execute(
                "get_job_details",
                {"job_id": job_id}
            )
            tool_calls.append({"tool": "get_job_details", "result": result})
            
            return {
                "success": True,
                "tool_calls": tool_calls,
                "response": f"Retrieved details for job {job_id}"
            }
        
        # Workflow: Search jobs
        elif intent == "search_jobs":
            arguments = {"page": 1, "page_size": 10}
            
            if "status" in entities:
                arguments["status"] = entities["status"]
            
            result = await self.tool_executor.execute(
                "search_jobs",
                arguments
            )
            tool_calls.append({"tool": "search_jobs", "result": result})
            
            jobs = result.get("data", {}).get("jobs", [])
            
            return {
                "success": True,
                "tool_calls": tool_calls,
                "response": f"Found {len(jobs)} jobs"
            }
        
        # Workflow: Search customers
        elif intent == "search_customers":
            result = await self.tool_executor.execute(
                "search_customers",
                {"page": 1, "page_size": 10}
            )
            tool_calls.append({"tool": "search_customers", "result": result})
            
            customers = result.get("data", {}).get("customers", [])
            
            return {
                "success": True,
                "tool_calls": tool_calls,
                "response": f"Found {len(customers)} customers"
            }
        
        # Unknown intent
        else:
            return {
                "success": False,
                "tool_calls": [],
                "error": f"Unknown intent: {intent}"
            }
    
    async def orchestrate(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Orchestrate using manual workflows.
        """
        logger.info(f"Manual orchestration for query: {query}")
        
        # Detect intent
        intent, entities = self._detect_intent(query)
        logger.info(f"Detected intent: {intent}, entities: {entities}")
        
        # Execute workflow
        result = await self._execute_workflow(intent, entities)
        result["strategy"] = "manual"
        result["intent"] = intent
        result["entities"] = entities
        
        return result
    
    def get_strategy_name(self) -> str:
        return "manual"
