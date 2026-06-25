# backend/api/auth_routes.py
# Authentication endpoints: register, login, me, usage, agent plans
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Request, Response
from pydantic import BaseModel
from typing import List, Optional

from auth.auth import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    get_current_user,
    validate_password_policy,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
)
from auth.database import (
    get_user_by_email,
    create_user,
    create_organization,
    create_org_membership,
    get_user_org,
    get_monthly_usage,
    get_monthly_agent_usage,
    get_org_by_id,
    get_org_agent_plans,
    set_org_agent_plan,
    is_agent_enabled_for_org,
    get_usage_analytics,
    get_usage_logs,
    get_all_users,
    get_journal_entries,
    PLAN_AGENT_TOKEN_LIMITS,
    PLAN_ORG_TOKEN_LIMITS,
    AGENT_NAMES,
    # Phase 4
    create_org_role,
    get_org_roles,
    get_role,
    delete_org_role,
    set_role_permissions,
    get_role_permissions,
    seed_org_roles,
    get_org_members,
    create_org_member_user,
    set_user_password,
    ensure_admin_role,
    is_reserved_role_name,
    assign_member_role_atomic,
    set_membership_active,
    get_membership_by_email,
    LastAdminError,
    AGENT_OPERATIONS,
    # Phase 5
    get_org_sop,
    set_org_sop,
    delete_org_sop,
    get_all_org_sops,
    # Department mapping
    get_org_department_mapping,
    set_org_department_mapping,
    get_org_dept_warnings,
    clear_org_dept_warnings,
    # Phase 8 RTR & Blacklist
    add_refresh_token,
    get_refresh_token,
    mark_refresh_token_used,
    revoke_refresh_token,
    revoke_all_user_refresh_tokens,
    blacklist_access_token,
    # Subdomain branding
    get_org_by_slug,
)
from utils.capability_radar import compute_radar, identify_improvement_targets
from agents.registry import AGENT_REGISTRY

router = APIRouter(tags=["auth"])

# ── OPEN_REGISTRATION gate ──────────────────────────────────────────────────
# Set OPEN_REGISTRATION=true in .env to allow anyone to self-register.
# Defaults to false (invite-only) for production safety.
_OPEN_REGISTRATION = os.getenv("OPEN_REGISTRATION", "false").lower() == "true"


# ---- Request / Response models ----
class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str
    tenant_slug: Optional[str] = None


class AuthResponse(BaseModel):
    token: str
    user: dict


class AgentPlanRequest(BaseModel):
    agent_name: str
    is_enabled: bool
    monthly_token_limit: int | None = None


# ---- Public endpoints (no auth) ----
@router.get("/tenant-info/{slug}")
async def tenant_info(slug: str):
    """Public endpoint: return branding metadata for a tenant by slug.
    Used by the frontend to render branded login pages on subdomains.
    No authentication required — only safe, non-secret fields are returned.
    """
    org = get_org_by_slug(slug)
    if not org or not org.get("is_active", 1):
        raise HTTPException(status_code=404, detail="Organization not found")
    return {
        "slug": org["slug"],
        "name": org["name"],
        "logo_url": org.get("logo_url"),
        "primary_color": org.get("primary_color"),
        "tagline": org.get("tagline"),
    }


# ---- Helpers ----
def _slug_from_email(email: str) -> str:
    """Generate an org slug from an email domain."""
    domain = email.split("@")[-1] if "@" in email else email
    slug = re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")
    return slug or "default"


def _auto_create_org_for_user(user: dict, plan_tier: str = "starter") -> dict:
    """Create a default organization + membership + agent plans + roles for a new user."""
    slug = _slug_from_email(user["email"])
    # Append user id to avoid slug collisions
    slug = f"{slug}-{user['id']}"

    org_token_limit = PLAN_ORG_TOKEN_LIMITS.get(plan_tier, PLAN_ORG_TOKEN_LIMITS["starter"])

    org = create_organization(
        name=f"{user['name'] or user['email'].split('@')[0]}'s Organization",
        slug=slug,
        plan_name=plan_tier,
        monthly_token_limit=org_token_limit,
    )

    # Seed Phase 4 roles before creating membership (need admin role_id)
    role_ids = seed_org_roles(org["id"])

    create_org_membership(user["id"], org["id"], role="admin", role_id=role_ids["admin_role_id"])

    # Seed all agents as enabled with per-agent token limit for this plan tier
    agent_limit = PLAN_AGENT_TOKEN_LIMITS.get(plan_tier)  # None = no per-agent limit
    for agent_name in AGENT_NAMES:
        set_org_agent_plan(org["id"], agent_name, is_enabled=True, monthly_token_limit=agent_limit)

    return org


# ---- Endpoints ----
@router.post("/register", response_model=AuthResponse)
async def register(response: Response, body: RegisterRequest):
    if not _OPEN_REGISTRATION:
        raise HTTPException(
            status_code=403,
            detail="Registration is invite-only. Contact your administrator to get access.",
        )

    if not body.email or not body.password:
        raise HTTPException(status_code=400, detail="Email and password are required")

    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    existing = get_user_by_email(body.email)
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    hashed = hash_password(body.password)
    user = create_user(email=body.email, hashed_password=hashed, name=body.name)

    # Auto-create org + membership + default agent plans
    org = _auto_create_org_for_user(user)

    token = create_access_token({
        "sub": user["email"],
        "uid": user["id"],
        "oid": org["id"],
    })

    access_payload = decode_access_token(token)

    refresh_token = create_refresh_token({
        "sub": user["email"],
        "uid": user["id"],
        "oid": org["id"],
    })

    refresh_payload = decode_access_token(refresh_token)

    from datetime import datetime, timezone
    refresh_exp = datetime.fromtimestamp(refresh_payload["exp"], tz=timezone.utc)
    add_refresh_token(
        jti=refresh_payload["jti"],
        user_id=user["id"],
        expires_at=refresh_exp,
    )

    is_prod = os.getenv("ENVIRONMENT", "development") != "development"
    response.set_cookie(
        key="token",
        value=token,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
    )

    return {"token": token, "user": {**user, "org_id": org["id"]}}


@router.post("/login", response_model=AuthResponse)
async def login(response: Response, body: LoginRequest):
    user = get_user_by_email(body.email)
    if not user or not verify_password(body.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    org = None
    if body.tenant_slug:
        # Enforce subdomain boundary: user must belong to this specific tenant
        target_org = get_org_by_slug(body.tenant_slug)
        if not target_org:
            raise HTTPException(status_code=404, detail="Tenant not found")
            
        membership = get_membership_by_email(body.email, target_org["id"])
        if not membership:
            raise HTTPException(status_code=403, detail="You do not have access to this tenant.")
        if not membership.get("is_active", 1):
            raise HTTPException(status_code=403, detail="Your access to this tenant has been deactivated.")
        
        org = target_org
    else:
        # get_user_org returns first ACTIVE membership. If the user has memberships
        # but they're all deactivated, block login with a clear message.
        org = get_user_org(user["id"])
        if org is None:
            from auth.database import has_any_membership
            if has_any_membership(user["id"]):
                raise HTTPException(
                    status_code=403,
                    detail="Your access has been deactivated. Contact your administrator.",
                )

    if org and not org.get("is_active", 1):
        raise HTTPException(
            status_code=403,
            detail="Your organization has been deactivated. Contact support.",
        )

    token = create_access_token({
        "sub": user["email"],
        "uid": user["id"],
        "oid": org["id"] if org else None,
    })

    access_payload = decode_access_token(token)

    refresh_token = create_refresh_token({
        "sub": user["email"],
        "uid": user["id"],
        "oid": org["id"] if org else None,
    })

    refresh_payload = decode_access_token(refresh_token)

    from datetime import datetime, timezone
    refresh_exp = datetime.fromtimestamp(refresh_payload["exp"], tz=timezone.utc)
    add_refresh_token(
        jti=refresh_payload["jti"],
        user_id=user["id"],
        expires_at=refresh_exp,
    )

    is_prod = os.getenv("ENVIRONMENT", "development") != "development"
    response.set_cookie(
        key="token",
        value=token,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
    )

    return {
        "token": token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user["name"],
            "org_id": org["id"] if org else None,
        },
    }


class RefreshRequest(BaseModel):
    refresh_token: Optional[str] = None


@router.post("/refresh")
async def refresh(
    request: Request,
    response: Response,
    body: Optional[RefreshRequest] = None,
):
    refresh_token = None

    # 1. Try to read from cookies
    if request.cookies:
        refresh_token = request.cookies.get("refresh_token")

    # 2. Try to read from JSON body
    if not refresh_token and body:
        refresh_token = body.refresh_token

    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing refresh token",
        )

    try:
        from jose import jwt, JWTError
        from auth.auth import JWT_SECRET, JWT_ALGORITHM
        payload = jwt.decode(refresh_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        jti = payload.get("jti")
        email = payload.get("sub")
        token_type = payload.get("type")
        user_id = payload.get("uid")

        if token_type != "refresh" or not jti or not email or not user_id:
            raise HTTPException(status_code=401, detail="Invalid refresh token")

    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    # Query DB to check token status
    token_row = get_refresh_token(jti)
    if not token_row:
        raise HTTPException(status_code=401, detail="Refresh token not recognized")

    if token_row["is_revoked"]:
        raise HTTPException(status_code=401, detail="Refresh token has been revoked")

    # Replay/Reuse Detection:
    if token_row["is_used"]:
        # Revoke all tokens for this user
        revoke_all_user_refresh_tokens(user_id)
        # Clear cookies
        response.delete_cookie("token")
        response.delete_cookie("refresh_token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token reuse detected. All sessions revoked.",
        )

    # Validate expiration
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    db_exp = datetime.fromisoformat(token_row["expires_at"])
    if db_exp.tzinfo is None:
        db_exp = db_exp.replace(tzinfo=timezone.utc)

    if db_exp < now:
        raise HTTPException(status_code=401, detail="Refresh token has expired")

    # Mark old token as used
    mark_refresh_token_used(jti)

    # Generate new access and refresh tokens
    org = get_user_org(user_id)
    org_id = org["id"] if org else None

    new_access_token = create_access_token({
        "sub": email,
        "uid": user_id,
        "oid": org_id,
    })

    new_refresh_token = create_refresh_token({
        "sub": email,
        "uid": user_id,
        "oid": org_id,
    })

    from auth.auth import JWT_SECRET, JWT_ALGORITHM
    new_refresh_payload = jwt.decode(new_refresh_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    new_refresh_exp = datetime.fromtimestamp(new_refresh_payload["exp"], tz=timezone.utc)

    # Store new refresh token in DB with parent_jti link
    add_refresh_token(
        jti=new_refresh_payload["jti"],
        user_id=user_id,
        expires_at=new_refresh_exp,
        parent_jti=jti,
    )

    is_prod = os.getenv("ENVIRONMENT", "development") != "development"
    response.set_cookie(
        key="token",
        value=new_access_token,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    response.set_cookie(
        key="refresh_token",
        value=new_refresh_token,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
    )

    return {
        "token": new_access_token,
        "user": {
            "id": user_id,
            "email": email,
            "org_id": org_id,
        }
    }


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    current_user: Optional[dict] = Depends(get_current_user),
):
    token = None
    if request.cookies:
        token = request.cookies.get("token") or request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]

    if token:
        try:
            from jose import jwt
            from auth.auth import JWT_SECRET, JWT_ALGORITHM
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM], options={"verify_exp": False})
            jti = payload.get("jti")
            exp = payload.get("exp")
            uid = payload.get("uid")

            # Blacklist access token
            from datetime import datetime, timezone
            if jti and exp:
                blacklist_access_token(jti, datetime.fromtimestamp(exp, tz=timezone.utc))

            # Revoke all refresh tokens for user
            if uid:
                revoke_all_user_refresh_tokens(uid)
        except Exception:
            pass

    response.delete_cookie("token")
    response.delete_cookie("refresh_token")
    return {"status": "ok", "message": "Successfully logged out"}


@router.get("/me")
async def me(current_user: dict = Depends(get_current_user)):
    return current_user


@router.get("/usage")
async def usage(current_user: dict = Depends(get_current_user)):
    """Get monthly token usage + remaining budget for current org, including per-agent breakdown."""
    org_id = current_user.get("org_id")
    if not org_id:
        return {"total_input_tokens": 0, "total_output_tokens": 0, "monthly_limit": 0, "remaining": 0}

    org = get_org_by_id(org_id)
    now = datetime.now(timezone.utc)
    usage_data = get_monthly_usage(org_id, now.year, now.month)

    monthly_limit = org["monthly_token_limit"] if org else 10000000
    total_used = usage_data["total_input_tokens"] + usage_data["total_output_tokens"]
    remaining = max(0, monthly_limit - total_used)

    # Per-agent breakdown
    agent_plans = {p["agent_name"]: p for p in get_org_agent_plans(org_id)}
    agents_usage = {}
    for agent_name in AGENT_NAMES:
        agent_usage = get_monthly_agent_usage(org_id, agent_name, now.year, now.month)
        plan = agent_plans.get(agent_name)
        agent_used = agent_usage["total_input_tokens"] + agent_usage["total_output_tokens"]
        agent_limit = plan["monthly_token_limit"] if plan else None
        agents_usage[agent_name] = {
            "used": agent_used,
            "limit": agent_limit,
            "remaining": max(0, agent_limit - agent_used) if agent_limit is not None else None,
            "is_enabled": bool(plan["is_enabled"]) if plan else True,
        }

    return {
        **usage_data,
        "monthly_limit": monthly_limit,
        "remaining": remaining,
        "plan_name": org["plan_name"] if org else "free",
        "plan_tier": org.get("plan_name", "starter") if org else "starter",
        "agents": agents_usage,
    }


@router.get("/org/agents")
async def list_org_agents(current_user: dict = Depends(get_current_user)):
    """List agent plans for the current org."""
    org_id = current_user.get("org_id")
    if not org_id:
        return {"agents": []}

    plans = get_org_agent_plans(org_id)
    plan_map = {p["agent_name"]: p for p in plans}

    agents = []
    for name, entry in AGENT_REGISTRY.items():
        plan = plan_map.get(name)
        agents.append({
            "agent_name": name,
            "title": entry["title"],
            "is_enabled": bool(plan["is_enabled"]) if plan else True,
            "monthly_token_limit": plan["monthly_token_limit"] if plan else None,
        })
    return {"agents": agents}


@router.post("/org/agents")
async def update_org_agent(
    body: AgentPlanRequest,
    current_user: dict = Depends(get_current_user),
):
    """Enable/disable an agent for the current org (admin only)."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    org_id = current_user.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization linked to this account")

    if body.agent_name not in AGENT_REGISTRY:
        raise HTTPException(status_code=400, detail=f"Unknown agent: {body.agent_name}")

    set_org_agent_plan(org_id, body.agent_name, body.is_enabled, body.monthly_token_limit)
    return {"status": "ok", "agent_name": body.agent_name, "is_enabled": body.is_enabled}


@router.get("/analytics")
async def analytics(
    days: int = 7,
    current_user: dict = Depends(get_current_user),
):
    """
    Daily usage analytics: calls, tokens, cost, latency, clarification rounds.

    Query params:
        days: How many days to look back (default 7)

    Returns one row per (day, agent_name) with aggregated metrics.
    Hit from browser: GET /api/auth/analytics?days=30
    """
    org_id = current_user.get("org_id")
    rows = get_usage_analytics(org_id=org_id, days=days)

    # Also compute totals across all days
    totals = {
        "total_calls": sum(r["total_calls"] for r in rows),
        "total_input_tokens": sum(r["total_input_tokens"] for r in rows),
        "total_output_tokens": sum(r["total_output_tokens"] for r in rows),
        "total_cost_usd": round(sum(r["total_cost_usd"] or 0 for r in rows), 4),
        "total_clarification_rounds": sum(r["total_clarification_rounds"] or 0 for r in rows),
    }

    # Avg latency across all rows that have duration data
    durations = [r["avg_duration_ms"] for r in rows if r["avg_duration_ms"] and r["avg_duration_ms"] > 0]
    totals["avg_duration_ms"] = round(sum(durations) / len(durations)) if durations else 0

    return {"days": days, "totals": totals, "daily": rows}


@router.get("/logs")
async def usage_logs(
    days: int = 1,
    agent: str = None,
    limit: int = 100,
    offset: int = 0,
    current_user: dict = Depends(get_current_user),
):
    """
    Individual request-level logs for the current user's org.

    Query params:
        days:   How many days to look back (default 1 = today)
        agent:  Filter by agent name (optional, e.g. "schedule", "chat")
        limit:  Max records per page (default 100)
        offset: Pagination offset (default 0)

    Hit from browser: GET /api/auth/logs?days=1&agent=schedule&limit=50
    """
    org_id = current_user.get("org_id")
    return get_usage_logs(
        org_id=org_id,
        days=days,
        agent_name=agent,
        limit=limit,
        offset=offset,
    )


@router.get("/logs/all")
async def usage_logs_all(
    days: int = 1,
    agent: str = None,
    user_id: int = None,
    limit: int = 100,
    offset: int = 0,
    current_user: dict = Depends(get_current_user),
):
    """
    Admin-only: see logs across ALL users and orgs.

    Query params:
        days:    How many days to look back (default 1 = today)
        agent:   Filter by agent name (optional)
        user_id: Filter by specific user ID (optional)
        limit:   Max records per page (default 100)
        offset:  Pagination offset (default 0)

    Hit from browser: GET /api/auth/logs/all?days=7&user_id=3
    """
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    org_id = current_user.get("org_id")
    return get_usage_logs(
        org_id=org_id,
        days=days,
        agent_name=agent,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )


@router.get("/users")
async def list_users(
    current_user: dict = Depends(get_current_user),
):
    """
    Admin-only: list all users with org, role, and usage summary.

    Hit from browser: GET /api/auth/users
    """
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    org_id = current_user.get("org_id")
    return {"users": get_org_members(org_id)}


# ═══════════════════════════════════════════════════════════════════════════
# Decision Journal & Capability Radar
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/journal")
async def journal(
    days: int = 7,
    dimension: str = None,
    decision_type: str = None,
    outcome: str = None,
    limit: int = 100,
    offset: int = 0,
    current_user: dict = Depends(get_current_user),
):
    """
    Query decision journal entries with filters.

    Query params:
        days: How many days to look back (default 7)
        dimension: Filter by dimension (routing, disambiguation, auto_selection, tool_alignment)
        decision_type: Filter by type (intent_analysis, crossroads_*, pick_best_auto, etc.)
        outcome: Filter by outcome (success, failure, clarification, pending)
        limit/offset: Pagination

    Hit from browser: GET /api/auth/journal?days=7&dimension=routing
    """
    org_id = current_user.get("org_id")
    return get_journal_entries(
        org_id=org_id,
        days=days,
        dimension=dimension,
        decision_type=decision_type,
        outcome=outcome,
        limit=limit,
        offset=offset,
    )


@router.get("/radar")
async def radar(
    days: int = 30,
    current_user: dict = Depends(get_current_user),
):
    """
    Capability Radar: per-dimension scores computed from the decision journal.

    Returns scores 0-100 for each dimension (routing, disambiguation,
    auto_selection, tool_alignment, error_recovery) plus improvement targets
    sorted worst-first.

    Hit from browser: GET /api/auth/radar?days=30
    """
    org_id = current_user.get("org_id")
    result = compute_radar(org_id=org_id, days=days)
    result["improvement_targets"] = identify_improvement_targets(result)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Phase 4 — Member management + Custom RBAC
# ═══════════════════════════════════════════════════════════════════════════

# ── Pydantic models ──────────────────────────────────────────────────────────

class PermissionItem(BaseModel):
    agent_name: str
    operation: str
    is_allowed: bool = True


class CreateRoleRequest(BaseModel):
    name: str
    permissions: List[PermissionItem] = []


class UpdateRoleRequest(BaseModel):
    name: Optional[str] = None
    permissions: Optional[List[PermissionItem]] = None


class InviteMemberRequest(BaseModel):
    email: str
    name: str = ""
    role_id: int


class UpdateMemberRoleRequest(BaseModel):
    role_id: int


class SetMemberPasswordRequest(BaseModel):
    new_password: str


class ChangeOwnPasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ActivateMemberRequest(BaseModel):
    new_password: Optional[str] = None  # If set, also reset password on reactivate


# ── Helper ───────────────────────────────────────────────────────────────────

def _require_admin(current_user: dict) -> None:
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")


def _require_org(current_user: dict) -> int:
    org_id = current_user.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization linked to this account")
    return org_id


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return current_user


# ── Member endpoints ─────────────────────────────────────────────────────────

@router.get("/org/members")
async def list_members(current_user: dict = Depends(get_current_user)):
    """List all members of the current org."""
    org_id = _require_org(current_user)
    return {"members": get_org_members(org_id)}


@router.post("/org/members/invite")
async def invite_member(
    body: InviteMemberRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Admin: create a new user, add them to the org, return temp password.
    If a deactivated user with the same email already exists in this org,
    return a 409 with hints so the frontend can offer "Reactivate?".
    """
    _require_admin(current_user)
    org_id = _require_org(current_user)

    # Validate role belongs to this org
    role = get_role(body.role_id)
    if not role or role["org_id"] != org_id:
        raise HTTPException(status_code=400, detail="Invalid role for this organization")

    # Check if the email maps to an existing membership in THIS org.
    existing_membership = get_membership_by_email(body.email, org_id)
    if existing_membership:
        if existing_membership["is_active"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "A user with this email is already an active member of this organization.",
                    "kind": "active_member_exists",
                    "user_id": existing_membership["user_id"],
                },
            )
        # Deactivated → offer reactivation path.
        raise HTTPException(
            status_code=409,
            detail={
                "message": "A deactivated user with this email exists in this organization. Reactivate them instead.",
                "kind": "deactivated_member_exists",
                "user_id": existing_membership["user_id"],
                "email": existing_membership["email"],
            },
        )

    # No membership in THIS org. The email might still belong to a user in another org.
    existing_user = get_user_by_email(body.email)
    if existing_user:
        # Link them as a new member (reuse pattern from superadmin onboarding).
        from auth.database import create_org_membership
        create_org_membership(existing_user["id"], org_id, role=role["name"], role_id=body.role_id)
        return {
            "user_id": existing_user["id"],
            "email": existing_user["email"],
            "name": existing_user["name"],
            "role": role["name"],
            "linked_existing": True,
        }

    result = create_org_member_user(
        email=body.email,
        name=body.name,
        org_id=org_id,
        role=role["name"],
        role_id=body.role_id,
    )
    return result


@router.put("/org/members/{user_id}/role")
async def update_member_role(
    user_id: int,
    body: UpdateMemberRoleRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Admin: change any member's role, including their own.
    Self-demote is allowed when at least one other admin exists.
    Last-admin demote is blocked atomically.
    """
    _require_admin(current_user)
    org_id = _require_org(current_user)

    role = get_role(body.role_id)
    if not role or role["org_id"] != org_id:
        raise HTTPException(status_code=400, detail="Invalid role for this organization")

    # Map role to membership.role text: 'admin' if system role, else 'member'.
    role_text = "admin" if role.get("is_system") else "member"

    try:
        updated = assign_member_role_atomic(user_id, org_id, body.role_id, role_text)
    except LastAdminError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not updated:
        raise HTTPException(status_code=404, detail="Member not found in this organization")

    return {"status": "ok", "user_id": user_id, "role_id": body.role_id, "role_name": role["name"]}


@router.post("/org/members/{user_id}/deactivate")
async def deactivate_member(
    user_id: int,
    current_user: dict = Depends(get_current_user),
):
    """
    Admin: deactivate a member's access to this org. Reversible.
    Rules:
      - Nobody can deactivate themselves.
      - Last active admin cannot be deactivated (atomic guard).
      - All user data and history preserved.
    """
    _require_admin(current_user)
    org_id = _require_org(current_user)

    if user_id == current_user.get("id"):
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account.")

    members = get_org_members(org_id)
    target = next((m for m in members if m["id"] == user_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Member not found in this organization")

    if not target.get("is_active", 1):
        raise HTTPException(status_code=400, detail="User is already deactivated.")

    try:
        result = set_membership_active(
            user_id, org_id, is_active=False, actor_user_id=current_user.get("id"),
        )
    except LastAdminError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not result["updated"]:
        raise HTTPException(status_code=404, detail="Member not found in this organization")

    return {"status": "ok", "user_id": user_id, "is_active": False}


@router.post("/org/members/{user_id}/activate")
async def activate_member(
    user_id: int,
    body: ActivateMemberRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Admin: reactivate a previously deactivated member.
    Optionally resets password if new_password is provided.
    If their previous role was deleted while inactive, falls back to the first
    non-system role (typically 'member') and reports the fallback.
    """
    _require_admin(current_user)
    org_id = _require_org(current_user)

    members = get_org_members(org_id)
    target = next((m for m in members if m["id"] == user_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Member not found in this organization")

    if target.get("is_active", 1):
        raise HTTPException(status_code=400, detail="User is already active.")

    # Validate optional new password BEFORE making any DB changes.
    new_password = body.new_password
    if new_password is not None:
        err = validate_password_policy(new_password)
        if err:
            raise HTTPException(status_code=400, detail=err)

    result = set_membership_active(
        user_id, org_id, is_active=True, actor_user_id=current_user.get("id"),
    )
    if not result["updated"]:
        raise HTTPException(status_code=404, detail="Member not found in this organization")

    if new_password is not None:
        hashed = hash_password(new_password)
        set_user_password(user_id, hashed)

    return {
        "status": "ok",
        "user_id": user_id,
        "is_active": True,
        "role_fallback": result.get("role_fallback"),
        "password_reset": new_password is not None,
    }


@router.put("/org/members/{user_id}/password")
async def admin_set_member_password(
    user_id: int,
    body: SetMemberPasswordRequest,
    current_user: dict = Depends(get_current_user),
):
    """Admin: set another member's password. Member must belong to admin's org."""
    _require_admin(current_user)
    org_id = _require_org(current_user)

    # Confirm user belongs to this org
    members = get_org_members(org_id)
    if not any(m["id"] == user_id for m in members):
        raise HTTPException(status_code=404, detail="Member not found in this organization")

    err = validate_password_policy(body.new_password)
    if err:
        raise HTTPException(status_code=400, detail=err)

    hashed = hash_password(body.new_password)
    if not set_user_password(user_id, hashed):
        raise HTTPException(status_code=404, detail="User not found")
    return {"status": "ok", "user_id": user_id}


@router.put("/me/password")
async def change_own_password(
    body: ChangeOwnPasswordRequest,
    current_user: dict = Depends(get_current_user),
):
    """Any logged-in user: change their own password (current password required)."""
    user = get_user_by_email(current_user["email"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not verify_password(body.current_password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    err = validate_password_policy(body.new_password)
    if err:
        raise HTTPException(status_code=400, detail=err)

    hashed = hash_password(body.new_password)
    set_user_password(user["id"], hashed)
    return {"status": "ok"}


# ── Role endpoints ────────────────────────────────────────────────────────────

@router.get("/org/roles")
async def list_roles(current_user: dict = Depends(get_current_user)):
    """List all custom roles for the current org, with permission matrix."""
    org_id = _require_org(current_user)
    # Self-heal: every tenant must have a system 'admin' role.
    ensure_admin_role(org_id)
    roles = get_org_roles(org_id)
    # Attach permissions to each role
    for role in roles:
        role["permissions"] = get_role_permissions(role["id"])
    return {"roles": roles, "agent_operations": AGENT_OPERATIONS}


@router.post("/org/roles")
async def create_role(
    body: CreateRoleRequest,
    current_user: dict = Depends(get_current_user),
):
    """Admin: create a new role with permissions."""
    _require_admin(current_user)
    org_id = _require_org(current_user)

    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Role name cannot be empty")

    if is_reserved_role_name(body.name):
        raise HTTPException(
            status_code=400,
            detail="The name 'admin' is reserved. Choose a different role name.",
        )

    role = create_org_role(org_id, body.name)
    if body.permissions:
        set_role_permissions(role["id"], [p.model_dump() for p in body.permissions])
    role["permissions"] = get_role_permissions(role["id"])
    return role


@router.put("/org/roles/{role_id}")
async def update_role(
    role_id: int,
    body: UpdateRoleRequest,
    current_user: dict = Depends(get_current_user),
):
    """Admin: update role name and/or permissions."""
    _require_admin(current_user)
    org_id = _require_org(current_user)

    role = get_role(role_id)
    if not role or role["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Role not found")

    if role["is_system"]:
        raise HTTPException(status_code=400, detail="The built-in admin role cannot be modified")

    from auth.database import get_db
    conn = get_db()
    if body.name is not None:
        if is_reserved_role_name(body.name):
            conn.close()
            raise HTTPException(
                status_code=400,
                detail="The name 'admin' is reserved. Choose a different role name.",
            )
        conn.execute("UPDATE org_roles SET name = ? WHERE id = ?", (body.name.strip(), role_id))
        conn.commit()
    conn.close()

    if body.permissions is not None:
        set_role_permissions(role_id, [p.model_dump() for p in body.permissions])

    updated = get_role(role_id)
    updated["permissions"] = get_role_permissions(role_id)
    return updated


@router.delete("/org/roles/{role_id}")
async def delete_role(
    role_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Admin: delete a role (blocked if is_system or users assigned)."""
    _require_admin(current_user)
    org_id = _require_org(current_user)

    role = get_role(role_id)
    if not role or role["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Role not found")

    if role["is_system"]:
        raise HTTPException(status_code=400, detail="Cannot delete the built-in admin role")

    deleted = delete_org_role(role_id)
    if not deleted:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete this role — users are still assigned to it",
        )
    return {"status": "ok", "role_id": role_id}


@router.get("/org/roles/{role_id}/permissions")
async def get_role_perms(
    role_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Get permission matrix for a specific role."""
    org_id = _require_org(current_user)
    role = get_role(role_id)
    if not role or role["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Role not found")
    return {"role": role, "permissions": get_role_permissions(role_id)}


# ═══════════════════════════════════════════════════════════════════════════
# Phase 5: Per-Tenant SOP Overrides
# ═══════════════════════════════════════════════════════════════════════════

MAX_SOP_CHARS = 32_000  # ~4× largest existing SOP (~9 KB text)

_ALLOWED_SOP_EXTENSIONS = {".md", ".txt", ".docx"}


def _extract_sop_text(content: bytes, filename: str) -> str:
    """
    Parse uploaded file bytes to plain text.
    Supports .md and .txt (UTF-8) and .docx (python-docx).
    Raises ValueError (→ HTTP 422) if file exceeds MAX_SOP_CHARS.
    """
    ext = os.path.splitext(filename.lower())[1]
    if ext not in _ALLOWED_SOP_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type '{ext}'. Allowed: .md, .txt, .docx"
        )

    if ext == ".docx":
        try:
            from docx import Document
            import io
            doc = Document(io.BytesIO(content))
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception as e:
            raise ValueError(f"Failed to parse DOCX file: {e}")
    else:
        text = content.decode("utf-8", errors="replace")

    if len(text) > MAX_SOP_CHARS:
        raise ValueError(
            f"SOP file is too large ({len(text):,} chars). "
            f"Maximum allowed is {MAX_SOP_CHARS:,} characters. "
            f"Please trim the document and re-upload."
        )
    return text


@router.get("/org/sops")
async def list_sops(current_user: dict = Depends(get_current_user)):
    """List all custom SOP overrides for the current org."""
    org_id = _require_org(current_user)
    custom = {row["agent_name"]: row for row in get_all_org_sops(org_id)}
    result = []
    for agent_name in AGENT_NAMES:
        if agent_name in custom:
            row = custom[agent_name]
            result.append({
                "agent_name": agent_name,
                "status": "custom",
                "original_filename": row["original_filename"],
                "uploaded_at": row["uploaded_at"],
                "char_count": len(row["sop_text"]),
            })
        else:
            result.append({
                "agent_name": agent_name,
                "status": "default",
                "original_filename": None,
                "uploaded_at": None,
                "char_count": None,
            })
    return {"sops": result}


@router.post("/org/sops/{agent_name}")
async def upload_sop(
    agent_name: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """Admin: upload a custom SOP file (.md, .txt, .docx) for an agent."""
    _require_admin(current_user)
    org_id = _require_org(current_user)

    if agent_name not in AGENT_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown agent '{agent_name}'. Valid agents: {AGENT_NAMES}",
        )

    content = await file.read()
    filename = file.filename or ""
    try:
        sop_text = _extract_sop_text(content, filename)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    row = set_org_sop(
        org_id=org_id,
        agent_name=agent_name,
        sop_text=sop_text,
        original_filename=filename,
        uploaded_by_user_id=current_user.get("user_id"),
    )
    return {
        "status": "ok",
        "agent_name": agent_name,
        "original_filename": filename,
        "char_count": len(sop_text),
        "uploaded_at": row["uploaded_at"],
    }


@router.delete("/org/sops/{agent_name}")
async def delete_sop(
    agent_name: str,
    current_user: dict = Depends(get_current_user),
):
    """Admin: delete custom SOP override (reverts agent to default)."""
    _require_admin(current_user)
    org_id = _require_org(current_user)

    if agent_name not in AGENT_NAMES:
        raise HTTPException(status_code=400, detail=f"Unknown agent '{agent_name}'")

    deleted = delete_org_sop(org_id, agent_name)
    if not deleted:
        raise HTTPException(status_code=404, detail="No custom SOP found for this agent")
    return {"status": "ok", "agent_name": agent_name, "reverted_to": "default"}


@router.get("/org/sops/{agent_name}/text")
async def get_sop_text(
    agent_name: str,
    current_user: dict = Depends(get_current_user),
):
    """Get raw SOP text for preview (custom if set, otherwise empty)."""
    org_id = _require_org(current_user)
    if agent_name not in AGENT_NAMES:
        raise HTTPException(status_code=400, detail=f"Unknown agent '{agent_name}'")

    row = get_org_sop(org_id, agent_name)
    if not row:
        return {"agent_name": agent_name, "status": "default", "text": None}
    return {
        "agent_name": agent_name,
        "status": "custom",
        "text": row["sop_text"],
        "original_filename": row["original_filename"],
        "uploaded_at": row["uploaded_at"],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Department Mapping — tenant admin manages their org's account→dept mapping
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/org/department-mapping")
async def get_dept_mapping(current_user: dict = Depends(get_current_user)):
    """Get current department mapping + any drift warnings for the org."""
    org_id = _require_org(current_user)
    mapping = get_org_department_mapping(org_id) or {}
    warnings = get_org_dept_warnings(org_id)
    return {"mapping": mapping, "drift_warnings": warnings}


@router.put("/org/department-mapping")
async def save_dept_mapping(
    body: dict,
    current_user: dict = Depends(require_admin),
):
    """
    Save a new department mapping for the org. Invalidates the in-memory cache
    so the next request rebuilds from the new mapping.

    Body: {"mapping": {"Plumbing": ["4-1000"], "Roofing": ["4-2000"]}}
    """
    org_id = _require_org(current_user)
    mapping = body.get("mapping")
    if not isinstance(mapping, dict):
        raise HTTPException(status_code=400, detail="'mapping' must be a JSON object")

    set_org_department_mapping(org_id, mapping)

    # Evict cache so next request picks up the new mapping
    from utils.department_cache import invalidate_department_cache
    invalidate_department_cache(org_id)

    return {"status": "ok", "org_id": org_id, "department_count": len(mapping)}


@router.delete("/org/department-mapping/warnings")
async def dismiss_dept_warnings(current_user: dict = Depends(get_current_user)):
    """Dismiss / acknowledge drift warnings for the org."""
    org_id = _require_org(current_user)
    clear_org_dept_warnings(org_id)
    return {"status": "ok", "org_id": org_id}


@router.post("/org/department-mapping/validate")
async def validate_dept_mapping(current_user: dict = Depends(require_admin)):
    """
    Force-rebuild the department cache for this org and return fresh drift warnings.
    Useful after editing the mapping or when Simpro chart of accounts may have changed.
    """
    from utils.mcp_tool_client import MCPToolClient
    from utils.mcp_executor import MCPToolExecutor
    from utils.department_cache import refresh_department_cache
    from auth.database import get_org_by_id

    org_id = _require_org(current_user)
    org = get_org_by_id(org_id)
    if not org or not org.get("simpro_api_url") or not org.get("simpro_access_token"):
        raise HTTPException(
            status_code=400,
            detail="Simpro credentials not configured for this org — cannot validate mapping.",
        )

    client = MCPToolClient(
        simpro_token=org["simpro_access_token"],
        simpro_url=org["simpro_api_url"],
        simpro_company_id=org.get("simpro_company_id"),
    )
    executor = MCPToolExecutor(
        tool_registry=client,
        company_id=org.get("simpro_company_id") or 0,
    )
    try:
        cache = await refresh_department_cache(executor, org_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to rebuild department cache: {e}")

    return {
        "status": "ok",
        "org_id": org_id,
        "departments": len(cache.department_names),
        "cost_centres_mapped": len(cache.cc_id_to_dept),
        "drift_warnings": cache.drift_warnings,
    }
