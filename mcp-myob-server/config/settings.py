"""
Configuration management for MCP MyOB Server.

Loads all configuration from environment variables using Pydantic Settings.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ===================================================================
    # MyOB OAuth2
    # ===================================================================
    MYOB_CLIENT_ID: str = Field(
        default="",
        description="MyOB API Key (client_id)"
    )

    MYOB_CLIENT_SECRET: str = Field(
        default="",
        description="MyOB API Secret (client_secret)"
    )

    MYOB_REDIRECT_URI: str = Field(
        default="http://localhost:8080/oauth/callback",
        description="OAuth2 redirect URI"
    )

    # ===================================================================
    # MyOB Company File
    # ===================================================================
    MYOB_CF_USERNAME: str = Field(
        default="Administrator",
        description="Company file admin username"
    )

    MYOB_CF_PASSWORD: str = Field(
        default="",
        description="Company file admin password"
    )

    MYOB_COMPANY_FILE_ID: Optional[str] = Field(
        default=None,
        description="Company file GUID (discovered or configured)"
    )

    MYOB_COMPANY_FILE_URI: Optional[str] = Field(
        default=None,
        description="Regional base URL (e.g. https://ar1.api.myob.com/accountright)"
    )

    # ===================================================================
    # MCP Server
    # ===================================================================
    MCP_SERVER_HOST: str = Field(
        default="0.0.0.0",
        description="Host to bind MCP server"
    )

    MCP_SERVER_PORT: int = Field(
        default=8010,
        ge=1024,
        le=65535,
        description="Port for MCP server (8010 to avoid conflict with Simpro on 8000)"
    )

    # ===================================================================
    # Rate Limiting
    # ===================================================================
    MYOB_RATE_LIMIT_RPS: int = Field(
        default=8,
        ge=1,
        le=20,
        description="MyOB API requests per second (limit is 8)"
    )

    MYOB_RATE_LIMIT_MAX_WAIT: float = Field(
        default=5.0,
        ge=0.5,
        le=30.0,
        description="Maximum seconds to wait for a rate limit slot"
    )

    MYOB_RATE_LIMIT_MAX_CONCURRENT: int = Field(
        default=6,
        ge=1,
        le=20,
        description="Maximum concurrent MyOB API requests"
    )

    # ===================================================================
    # Logging
    # ===================================================================
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Logging level"
    )

    LOG_FILE: str = Field(
        default="logs/mcp-myob-server.log",
        description="Log file path"
    )

    # ===================================================================
    # Pydantic Config
    # ===================================================================
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ===================================================================
    # Validators
    # ===================================================================
    @field_validator("MYOB_COMPANY_FILE_URI")
    @classmethod
    def strip_trailing_slash(cls, v: Optional[str]) -> Optional[str]:
        return v.rstrip("/") if v else v

    @field_validator("LOG_FILE")
    @classmethod
    def create_log_directory(cls, v: str) -> str:
        Path(v).parent.mkdir(parents=True, exist_ok=True)
        return v


# Global instance
settings = Settings()


def get_settings() -> Settings:
    return settings
