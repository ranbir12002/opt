# backend/auth/models.py
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, Text, DateTime, Float, ForeignKey, UniqueConstraint, func, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Organization(Base):
    __tablename__ = 'organizations'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    simpro_company_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    simpro_api_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    simpro_access_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    plan_name: Mapped[str] = mapped_column(String(50), nullable=False, server_default="free")
    monthly_token_limit: Mapped[int] = mapped_column(Integer, nullable=False, server_default="10000000")
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    use_platform_llm: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    llm_provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    llm_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    llm_api_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    llm_complex_provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    llm_complex_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    llm_complex_api_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    llm_stt_api_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    department_mapping: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dept_mapping_warnings: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Branding (subdomain portal)
    logo_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    primary_color: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # e.g. "#6366f1"
    tagline: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class OrgMembership(Base):
    __tablename__ = 'org_memberships'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False, server_default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    role_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey('org_roles.id', ondelete='SET NULL'), nullable=True, index=True)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    deactivated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    deactivated_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    __table_args__ = (UniqueConstraint('user_id', 'org_id', name='uq_user_org'),)


class OrgAgentPlan(Base):
    __tablename__ = 'org_agent_plans'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_enabled: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    monthly_token_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    __table_args__ = (UniqueConstraint('org_id', 'agent_name', name='uq_org_agent'),)


class UsageRecord(Base):
    __tablename__ = 'usage_records'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False, server_default="chat")
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    model_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.0")
    request_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    clarification_rounds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    __table_args__ = (
        Index('idx_usage_records_org_created', 'org_id', 'created_at'),
    )


class DecisionJournal(Base):
    __tablename__ = 'decision_journal'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    org_id: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    dimension: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    decision_type: Mapped[str] = mapped_column(String(100), nullable=False)
    decision_value: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.0")
    reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")
    context_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="{}")
    outcome: Mapped[str] = mapped_column(String(50), nullable=False, server_default="pending")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    __table_args__ = (
        Index('idx_journal_org_dim_created', 'org_id', 'dimension', 'created_at'),
    )


class RequestTrace(Base):
    __tablename__ = 'request_traces'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    org_id: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    intent: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")
    agent: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="")
    action: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.0")
    tool_sequence: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="[]")
    tool_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    outcome: Mapped[str] = mapped_column(String(50), nullable=False, server_default="pending")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    message_preview: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    __table_args__ = (
        Index('idx_traces_org_created', 'org_id', 'created_at'),
    )


class OrgRole(Base):
    __tablename__ = 'org_roles'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_system: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    __table_args__ = (UniqueConstraint('org_id', 'name', name='uq_org_role'),)


class RoleAgentPermission(Base):
    __tablename__ = 'role_agent_permissions'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_id: Mapped[int] = mapped_column(Integer, ForeignKey('org_roles.id', ondelete='CASCADE'), nullable=False, index=True)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    is_allowed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __table_args__ = (UniqueConstraint('role_id', 'agent_name', 'operation', name='uq_role_perm'),)


class OrgSopOverride(Base):
    __tablename__ = 'org_sop_overrides'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    sop_text: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, default="")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    uploaded_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    __table_args__ = (UniqueConstraint('org_id', 'agent_name', name='uq_org_sop'),)


class PlatformSetting(Base):
    __tablename__ = 'platform_settings'
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class RefreshToken(Base):
    __tablename__ = 'refresh_tokens'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    jti: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    parent_jti: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_revoked: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    expires_at: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TokenBlacklist(Base):
    __tablename__ = 'token_blacklist'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_jti: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    expires_at: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
