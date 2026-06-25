"""
LLM Native Orchestration Strategy.

For smart LLMs (Claude Sonnet/Opus, GPT-4.1) that can handle
multi-step orchestration natively without help.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.tools.executor import get_tool_executor
from src.utils import get_logger

from .base import BaseOrchestrator

logger = get_logger(__name__)


class LLMNativeOrchestrator(BaseOrchestrator):
    """
    Native LLM orchestration strategy.
    
    This strategy relies entirely on the LLM's native ability to:
    1. Understand the user query
    2. Decide which tools to call
    3. Chain multiple tool calls together
    4. Format the final response
    
    Works best with: Claude Sonnet 4, Claude Opus, GPT-4.1, GPT-4-turbo
    
    Example flow:
        User: "Create invoices for customer ABC"
        
        LLM automatically decides:
        1. search_customers(name="ABC") → customer_id=123
        2. search_jobs(customer_id=123) → [job1, job2, job3]
        3. create_invoice(job_id=job1.id)
        4. create_invoice(job_id=job2.id)
        5. create_invoice(job_id=job3.id)
        
        No code needed - LLM handles everything!
    """
    
    def __init__(
        self,
        llm_provider: Any,
        tools: Optional[List[Dict[str, Any]]] = None
    ):
        """
        Initialize native LLM orchestrator.
        
        Args:
            llm_provider: LLM provider instance (Claude or GPT-4.1)
            tools: List of available tools
        """
        super().__init__(llm_provider, tools)
        self.tool_executor = get_tool_executor()
        
        if not llm_provider:
            raise ValueError("LLM provider required for native orchestration")
        
        logger.info("LLM Native orchestrator ready")
    
    async def orchestrate(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Orchestrate using LLM's native capabilities.
        
        The LLM decides everything - we just execute what it asks for.
        """
        logger.info(f"Native orchestration for query: {query}")
        
        # Build conversation
        messages = []
        
        # Add context if provided
        if context and context.get("history"):
            messages.extend(context["history"])
        
        # Add user query
        messages.append({
            "role": "user",
            "content": query
        })
        
        # Track tool calls
        tool_calls_history = []
        max_iterations = 10  # Prevent infinite loops
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
                # LLM is done - return final response
                logger.info("LLM finished orchestration")
                return {
                    "success": True,
                    "tool_calls": tool_calls_history,
                    "response": response.get("content"),
                    "strategy": "llm_native",
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
                
                # Add tool result to conversation
                messages.append({
                    "role": "assistant",
                    "content": f"Called {tool_name}",
                    "tool_calls": [tool_call]
                })
                
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.get("id"),
                    "content": str(result)
                })
        
        # Max iterations reached
        logger.warning(f"Max iterations ({max_iterations}) reached")
        return {
            "success": False,
            "tool_calls": tool_calls_history,
            "response": "Maximum orchestration steps reached",
            "strategy": "llm_native",
            "iterations": iteration,
            "error": "max_iterations_reached"
        }
    
    def get_strategy_name(self) -> str:
        return "llm_native"