"""
Assisted Orchestration Strategy.

For LLMs (like GPT-3.5) that can handle tool calling but need
hints and guidance for complex multi-step orchestration.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.tools.executor import get_tool_executor
from src.utils import get_logger

from .base import BaseOrchestrator

logger = get_logger(__name__)


class AssistedOrchestrator(BaseOrchestrator):
    """
    Assisted orchestration strategy.
    
    This strategy helps weaker LLMs by:
    1. Adding step-by-step hints to the prompt
    2. Detecting common query patterns
    3. Suggesting tool call sequences
    4. Validating tool call logic
    
    Works best with: GPT-3.5-turbo, GPT-4-mini
    
    Example flow:
        User: "Create invoices for customer ABC"
        
        System adds hint:
        "To complete this task:
        1. First search for the customer to get their ID
        2. Then search for jobs for that customer
        3. Finally create invoices for each job"
        
        LLM follows the hints and makes correct calls.
    """
    
    def __init__(
        self,
        llm_provider: Any,
        tools: Optional[List[Dict[str, Any]]] = None
    ):
        """
        Initialize assisted orchestrator.
        
        Args:
            llm_provider: LLM provider instance
            tools: List of available tools
        """
        super().__init__(llm_provider, tools)
        self.tool_executor = get_tool_executor()
        
        if not llm_provider:
            raise ValueError("LLM provider required for assisted orchestration")
        
        logger.info("Assisted orchestrator ready")
    
    def _add_orchestration_hints(self, query: str) -> str:
        """
        Add helpful hints to the query based on detected patterns.
        
        Args:
            query: Original user query
        
        Returns:
            Enhanced query with hints
        """
        query_lower = query.lower()
        hints = []
        
        # Invoice creation pattern
        if "invoice" in query_lower and "create" in query_lower:
            if "customer" in query_lower:
                hints.append(
                    "To create invoices for a customer:\n"
                    "1. First use search_customers to find the customer ID\n"
                    "2. Then use search_jobs to get jobs for that customer\n"
                    "3. Finally create invoices for the jobs"
                )
        
        # Customer + jobs pattern
        elif "customer" in query_lower and "job" in query_lower:
            hints.append(
                "To find jobs for a customer:\n"
                "1. First use search_customers to get the customer ID\n"
                "2. Then use search_jobs with the customer_id filter"
            )
        
        # Job details pattern
        elif "job" in query_lower and any(word in query_lower for word in ["detail", "section", "cost"]):
            hints.append(
                "To get job details:\n"
                "1. Use search_jobs if you need to find the job first\n"
                "2. Use get_job_details for full information\n"
                "3. Use get_job_sections for section breakdown"
            )
        
        # Add hints if detected
        if hints:
            enhanced = f"{query}\n\n[Assistant Guidance]\n" + "\n\n".join(hints)
            logger.debug(f"Added orchestration hints to query")
            return enhanced
        
        return query
    
    async def orchestrate(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Orchestrate with assistance hints.
        """
        logger.info(f"Assisted orchestration for query: {query}")
        
        # Add hints to help LLM
        enhanced_query = self._add_orchestration_hints(query)
        
        # Build conversation
        messages = []
        
        # Add system message with guidance
        messages.append({
            "role": "system",
            "content": (
                "You are a helpful assistant for Simpro ERP. "
                "Follow the guidance steps carefully to complete tasks. "
                "Call tools one at a time and wait for results before proceeding."
            )
        })
        
        # Add context if provided
        if context and context.get("history"):
            messages.extend(context["history"])
        
        # Add enhanced query
        messages.append({
            "role": "user",
            "content": enhanced_query
        })
        
        # Track tool calls
        tool_calls_history = []
        max_iterations = 10
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1
            logger.debug(f"Iteration {iteration}")
            
            # Call LLM with tools
            response = await self.llm_provider.chat(
                messages=messages,
                tools=self.tools,
                temperature=0.0
            )
            
            # Check if LLM wants to use tools
            tool_calls = response.get("tool_calls", [])
            
            if not tool_calls:
                # LLM is done
                logger.info("Assisted orchestration complete")
                return {
                    "success": True,
                    "tool_calls": tool_calls_history,
                    "response": response.get("content"),
                    "strategy": "assisted",
                    "iterations": iteration
                }
            
            # Execute tool calls
            for tool_call in tool_calls:
                tool_name = tool_call.get("name")
                arguments = tool_call.get("arguments", {})
                
                logger.info(f"Executing tool: {tool_name}")
                
                # Execute tool
                result = await self.tool_executor.execute(
                    tool_name,
                    arguments
                )
                
                # Record tool call
                tool_calls_history.append({
                    "tool": tool_name,
                    "arguments": arguments,
                    "result": result
                })
                
                # Add tool result to conversation with helpful feedback
                messages.append({
                    "role": "assistant",
                    "content": f"Called {tool_name} - please wait for result"
                })
                
                messages.append({
                    "role": "user",
                    "content": f"Result: {str(result)}\n\nContinue with next step if needed."
                })
        
        # Max iterations reached
        logger.warning(f"Max iterations ({max_iterations}) reached")
        return {
            "success": False,
            "tool_calls": tool_calls_history,
            "response": "Maximum orchestration steps reached",
            "strategy": "assisted",
            "iterations": iteration,
            "error": "max_iterations_reached"
        }
    
    def get_strategy_name(self) -> str:
        return "assisted"