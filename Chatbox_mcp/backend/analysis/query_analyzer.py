"""
Query Analyzer - Determines query complexity for LLM routing.

Analyzes user queries and assigns complexity scores:
- Simple: Basic lookups, single entity queries
- Medium: Multi-step queries, some context needed
- Complex: Multi-entity orchestration, reasoning required
"""
from __future__ import annotations

import re
from typing import Dict, Literal
from enum import Enum


class QueryComplexity(str, Enum):
    """Query complexity levels"""
    SIMPLE = "simple"      # GPT-3.5 can handle
    MEDIUM = "medium"      # GPT-4 recommended
    COMPLEX = "complex"    # Claude Sonnet 4 recommended


class QueryAnalyzer:
    """
    Analyzes queries to determine their complexity.
    
    This helps route queries to appropriate LLMs:
    - Simple → GPT-3.5 (cheap, fast)
    - Medium → GPT-4.1 (balanced)
    - Complex → Claude Sonnet 4 (best reasoning)
    """
    
    # Keywords that indicate complexity
    COMPLEX_KEYWORDS = [
        "analyze", "compare", "evaluate", "calculate", "optimize",
        "summarize", "recommend", "strategy", "forecast", "trend",
        "relationship", "correlation", "impact", "consequence"
    ]
    
    MEDIUM_KEYWORDS = [
        "create", "update", "modify", "change", "generate",
        "for all", "for each", "multiple", "batch", "bulk"
    ]
    
    # Multi-step indicators
    MULTI_STEP_PATTERNS = [
        r"and then",
        r"after that",
        r"next",
        r"followed by",
        r"for each .+ (create|update|get)",
        r"all .+ that (have|are|were)",
    ]
    
    # Simple query patterns
    SIMPLE_PATTERNS = [
        r"^(get|show|find|list|search|view) (the |a |an )?(\w+)( \d+)?$",
        r"^what is( the)? status of",
        r"^show me( the)?",
        r"^list( all)?",
    ]
    
    def __init__(self):
        """Initialize query analyzer"""
        pass
    
    def analyze(self, query: str) -> Dict:
        """
        Analyze query and return complexity assessment.
        
        Args:
            query: User query string
        
        Returns:
            {
                "complexity": "simple" | "medium" | "complex",
                "confidence": 0.0-1.0,
                "reasoning": "Why this complexity was chosen",
                "features": {
                    "entity_count": int,
                    "has_multi_step": bool,
                    "has_complex_keywords": bool,
                    "has_conditions": bool
                }
            }
        """
        query_lower = query.lower().strip()
        
        # Extract features
        features = self._extract_features(query_lower)
        
        # Calculate complexity
        complexity, confidence, reasoning = self._calculate_complexity(
            query_lower, features
        )
        
        return {
            "complexity": complexity.value,
            "confidence": confidence,
            "reasoning": reasoning,
            "features": features
        }
    
    def _extract_features(self, query: str) -> Dict:
        """Extract query features for analysis"""
        
        # Count entities mentioned
        entity_keywords = ["job", "customer", "invoice", "quote", "site"]
        entity_count = sum(1 for kw in entity_keywords if kw in query)
        
        # Check for multi-step indicators
        has_multi_step = any(
            re.search(pattern, query, re.IGNORECASE)
            for pattern in self.MULTI_STEP_PATTERNS
        )
        
        # Check for complex operations
        has_complex_keywords = any(kw in query for kw in self.COMPLEX_KEYWORDS)
        
        # Check for medium complexity operations
        has_medium_keywords = any(kw in query for kw in self.MEDIUM_KEYWORDS)
        
        # Check for conditions (filtering, criteria)
        has_conditions = any(word in query for word in [
            "where", "with", "that have", "that are", "if", "when"
        ])
        
        # Check for batch operations
        has_batch = any(word in query for word in [
            "all", "every", "each", "multiple", "batch"
        ])
        
        return {
            "entity_count": entity_count,
            "has_multi_step": has_multi_step,
            "has_complex_keywords": has_complex_keywords,
            "has_medium_keywords": has_medium_keywords,
            "has_conditions": has_conditions,
            "has_batch": has_batch,
            "query_length": len(query.split())
        }
    
    def _calculate_complexity(
        self,
        query: str,
        features: Dict
    ) -> tuple[QueryComplexity, float, str]:
        """
        Calculate complexity level based on features.
        
        Returns:
            (complexity_level, confidence_score, reasoning)
        """
        score = 0.0
        reasons = []
        
        # Simple query patterns
        if any(re.match(pattern, query, re.IGNORECASE) for pattern in self.SIMPLE_PATTERNS):
            return (
                QueryComplexity.SIMPLE,
                0.9,
                "Simple lookup pattern detected"
            )
        
        # Scoring system
        if features["has_complex_keywords"]:
            score += 3.0
            reasons.append("complex operation keywords")
        
        if features["has_multi_step"]:
            score += 2.5
            reasons.append("multi-step workflow")
        
        if features["entity_count"] >= 3:
            score += 2.0
            reasons.append(f"{features['entity_count']} entities involved")
        
        if features["has_batch"]:
            score += 1.5
            reasons.append("batch operation")
        
        if features["has_medium_keywords"]:
            score += 1.0
            reasons.append("medium complexity operation")
        
        if features["has_conditions"]:
            score += 0.5
            reasons.append("conditional logic")
        
        if features["query_length"] > 20:
            score += 1.0
            reasons.append("long query")
        
        # Determine complexity level
        if score >= 4.0:
            complexity = QueryComplexity.COMPLEX
            confidence = min(0.95, 0.7 + (score - 4.0) * 0.05)
            reason = f"Complex query: {', '.join(reasons)}"
        
        elif score >= 2.0:
            complexity = QueryComplexity.MEDIUM
            confidence = 0.75 + (score - 2.0) * 0.05
            reason = f"Medium complexity: {', '.join(reasons)}"
        
        else:
            complexity = QueryComplexity.SIMPLE
            confidence = 0.8
            reason = f"Simple query: {', '.join(reasons) if reasons else 'basic lookup'}"
        
        return (complexity, confidence, reason)
    
    def recommend_llm(self, query: str) -> Dict:
        """
        Analyze query and recommend which LLM to use.
        
        Returns:
            {
                "recommended_llm": "claude-sonnet-4" | "gpt-4" | "gpt-3.5-turbo",
                "fallback_llm": "...",
                "complexity": "simple" | "medium" | "complex",
                "reasoning": "..."
            }
        """
        analysis = self.analyze(query)
        complexity = analysis["complexity"]
        
        if complexity == "complex":
            return {
                "recommended_llm": "claude-sonnet-4-20250514",
                "fallback_llm": "gpt-4.1",
                "complexity": complexity,
                "reasoning": f"{analysis['reasoning']} - Using Claude for best reasoning"
            }
        
        elif complexity == "medium":
            return {
                "recommended_llm": "gpt-4.1",
                "fallback_llm": "gpt-3.5-turbo",
                "complexity": complexity,
                "reasoning": f"{analysis['reasoning']} - Using GPT-4.1 for balanced performance"
            }
        
        else:  # simple
            return {
                "recommended_llm": "gpt-3.5-turbo",
                "fallback_llm": "gpt-4.1",
                "complexity": complexity,
                "reasoning": f"{analysis['reasoning']} - Using GPT-3.5 for speed and cost"
            }


# Global analyzer instance
_analyzer = QueryAnalyzer()


def analyze_query(query: str) -> Dict:
    """
    Convenience function to analyze a query.
    
    Args:
        query: User query string
    
    Returns:
        Analysis result dict
    """
    return _analyzer.analyze(query)


def recommend_llm(query: str) -> Dict:
    """
    Convenience function to get LLM recommendation.
    
    Args:
        query: User query string
    
    Returns:
        LLM recommendation dict
    """
    return _analyzer.recommend_llm(query)