# backend/api/analysis.py
"""
Query Analysis API

Provides endpoints for analyzing query complexity.
Called by Node.js MCP client for smart routing decisions.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from analysis.query_analyzer import recommend_llm, analyze_query

router = APIRouter()


class AnalyzeRequest(BaseModel):
    query: str


class AnalysisResponse(BaseModel):
    complexity: str
    confidence: float
    reasoning: str
    recommended_llm: str
    fallback_llm: str


@router.post("/analyze-query", response_model=AnalysisResponse)
async def analyze_query_endpoint(request: AnalyzeRequest):
    """
    Analyze query complexity and recommend appropriate LLM.
    
    This endpoint is called by the Node.js MCP client to determine
    which LLM should handle the query based on complexity.
    
    Returns:
        {
            "complexity": "simple" | "medium" | "complex",
            "confidence": 0.0-1.0,
            "reasoning": "Explanation of decision",
            "recommended_llm": "claude-sonnet-4" | "gpt-4.1" | "gpt-3.5-turbo",
            "fallback_llm": "..."
        }
    """
    recommendation = recommend_llm(request.query)
    
    return {
        "complexity": recommendation["complexity"],
        "confidence": 0.85,  # Placeholder - you can enhance this
        "reasoning": recommendation["reasoning"],
        "recommended_llm": recommendation["recommended_llm"],
        "fallback_llm": recommendation["fallback_llm"]
    }