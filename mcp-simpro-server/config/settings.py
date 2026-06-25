"""
Configuration management for MCP Simpro Server.

This module loads all configuration from environment variables
using Pydantic Settings for validation and type safety.
"""
import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    
    Environment variables are loaded from .env file if present.
    All settings have validation and sensible defaults.
    """
    
    # ===================================================================
    # LLM Configuration
    # ===================================================================
    LLM_PROVIDER: Literal["claude", "openai", "azure", "custom"] = Field(
        default="claude",
        description="LLM provider to use"
    )
    
    LLM_MODEL: str = Field(
        default="claude-sonnet-4-20250514",
        description="Specific model to use (depends on provider)"
    )
    
    LLM_API_KEY: Optional[str] = Field(
        default=None,
        description="API key for LLM provider (injected per-request from DB in multi-tenant mode)"
    )
    
    OPENAI_BASE_URL: Optional[str] = Field(
        default=None,
        description="Custom base URL for OpenAI (for Azure or proxies)"
    )
    
    # ===================================================================
    # Simpro API Configuration
    # ===================================================================
    SIMPRO_ACCESS_TOKEN: Optional[str] = Field(
        default=None,
        description="Simpro API access token (injected per-request from DB in multi-tenant mode)"
    )

    SIMPRO_COMPANY_ID: Optional[int] = Field(
        default=None,
        description="Simpro company ID (injected per-request from DB in multi-tenant mode)"
    )
    
    SIMPRO_REGION: Literal["au", "us", "uk"] = Field(
        default="au",
        description="Simpro region"
    )
    
    SIMPRO_BASE_URL: str = Field(
        default="https://api.simprogroup.com",
        description="Simpro API base URL"
    )
    
    # ===================================================================
    # Agent Service URLs
    # ===================================================================
    INVOICE_AGENT_URL: str = Field(
        default="http://localhost:8001",
        description="Invoice agent service URL"
    )
    
    WORKORDER_AGENT_URL: str = Field(
        default="http://localhost:8002",
        description="WorkOrder agent service URL"
    )
    
    EXTRACTOR_AGENT_URL: str = Field(
        default="http://localhost:8003",
        description="Document extractor service URL"
    )
    
    # ===================================================================
    # MCP Server Configuration
    # ===================================================================
    MCP_SERVER_HOST: str = Field(
        default="0.0.0.0",
        description="Host to bind MCP server to"
    )
    
    MCP_SERVER_PORT: int = Field(
        default=8000,
        ge=1024,
        le=65535,
        description="Port for MCP server"
    )
    
    # ===================================================================
    # Logging Configuration
    # ===================================================================
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Logging level"
    )
    
    LOG_FILE: str = Field(
        default="logs/mcp-server.log",
        description="Log file path"
    )
    
    # ===================================================================
    # Optional Features
    # ===================================================================
    ENABLE_CACHING: bool = Field(
        default=False,
        description="Enable response caching"
    )
    
    ENABLE_METRICS: bool = Field(
        default=True,
        description="Enable metrics collection"
    )

    # ===================================================================
    # Rate Limiting Configuration
    # ===================================================================
    SIMPRO_RATE_LIMIT_RPS: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Simpro API requests per second per tenant"
    )

    SIMPRO_RATE_LIMIT_MAX_WAIT: float = Field(
        default=5.0,
        ge=0.5,
        le=30.0,
        description="Maximum seconds to wait for a rate limit slot"
    )

    SIMPRO_RATE_LIMIT_MAX_CONCURRENT: int = Field(
        default=8,
        ge=1,
        le=50,
        description="Maximum concurrent Simpro API requests per tenant"
    )

    # ===================================================================
    # Pydantic Configuration
    # ===================================================================
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"  # Ignore extra env vars
    )
    
    # ===================================================================
    # Validators
    # ===================================================================
    @field_validator("SIMPRO_BASE_URL")
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        """Ensure base URL doesn't end with slash"""
        return v.rstrip("/")
    
    @field_validator("LOG_FILE")
    @classmethod
    def create_log_directory(cls, v: str) -> str:
        """Create log directory if it doesn't exist"""
        log_path = Path(v)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return v
    
    # ===================================================================
    # Helper Methods
    # ===================================================================
    def get_llm_capabilities(self) -> dict:
        """
        Load LLM capabilities from config file.
        
        Returns orchestration strategy and limits for current model.
        """
        config_path = Path(__file__).parent / "llm_capabilities.json"
        
        if not config_path.exists():
            # Default fallback
            return {
                "strategy": "llm_native",
                "max_tokens": 128000
            }
        
        with open(config_path) as f:
            capabilities = json.load(f)
        
        # Return capabilities for current model, or default
        return capabilities.get(
            self.LLM_MODEL,
            {"strategy": "llm_native", "max_tokens": 128000}
        )
    
    def get_agent_endpoints(self) -> dict:
        """
        Load agent endpoint configuration.
        
        Returns endpoint paths for each agent service.
        """
        config_path = Path(__file__).parent / "agent_endpoints.json"
        
        if not config_path.exists():
            # Default endpoints
            return {
                "invoice_agent": {
                    "create_invoice": "/api/create-invoice",
                    "health": "/health"
                },
                "workorder_agent": {
                    "create_workorder": "/api/create-workorder",
                    "health": "/health"
                },
                "extractor": {
                    "extract_document": "/api/extract",
                    "health": "/health"
                }
            }
        
        with open(config_path) as f:
            return json.load(f)


# ===================================================================
# Global Settings Instance
# ===================================================================
# This is the single instance used throughout the application
settings = Settings()


# ===================================================================
# Convenience Functions
# ===================================================================
def get_settings() -> Settings:
    """
    Get the global settings instance.
    
    Useful for dependency injection in FastAPI:
    
    @app.get("/")
    def index(settings: Settings = Depends(get_settings)):
        ...
    """
    return settings