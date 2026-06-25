# backend/api/superadmin_routes.py
"""
Super-admin API — platform owner manages all tenants.

Auth: Bearer token matched against SUPERADMIN_TOKEN env var.
This is completely independent of the user JWT system.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from auth.database import (
    get_all_orgs,
    get_org_by_id,
    create_organization,
    update_organization,
    get_org_agent_plans,
    set_org_agent_plan,
    get_monthly_usage,
    get_monthly_agent_usage,
    get_usage_logs,
    create_org_admin_user,
    get_org_roles,
    seed_org_roles,
    PLAN_ORG_TOKEN_LIMITS,
    PLAN_AGENT_TOKEN_LIMITS,
    AGENT_NAMES,
    # Phase 5
    get_all_org_sops,
    delete_org_sop,
    # Phase 6
    get_platform_llm_config,
    get_all_platform_settings,
    set_platform_setting,
    # Department mapping
    get_org_department_mapping,
    set_org_department_mapping,
    get_org_dept_warnings,
    clear_org_dept_warnings,
    # User management
    get_org_members,
    get_user_by_id,
    set_user_password,
    get_role,
    ensure_admin_role,
    assign_member_role_atomic,
    set_membership_active,
    LastAdminError,
)
from auth.auth import hash_password, validate_password_policy

router = APIRouter(tags=["Superadmin"])

# ── Auth ─────────────────────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)


async def require_superadmin(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> None:
    superadmin_token = os.getenv("SUPERADMIN_TOKEN", "")
    if not superadmin_token:
        raise HTTPException(status_code=403, detail="Superadmin access required")

    token = request.cookies.get("superadmin_token")
    if not token and credentials:
        token = credentials.credentials

    if not token or token != superadmin_token:
        raise HTTPException(status_code=403, detail="Superadmin access required")


class SuperadminLoginRequest(BaseModel):
    token: str


@router.post("/login")
async def superadmin_login(response: Response, body: SuperadminLoginRequest):
    superadmin_token = os.getenv("SUPERADMIN_TOKEN", "")
    if not superadmin_token or body.token.strip() != superadmin_token:
        raise HTTPException(status_code=401, detail="Invalid superadmin token")

    is_prod = os.getenv("ENVIRONMENT", "development") != "development"
    response.set_cookie(
        key="superadmin_token",
        value=body.token.strip(),
        httponly=True,
        secure=is_prod,
        samesite="lax",
        max_age=24 * 3600,  # 1 day
    )
    return {"status": "ok"}


@router.post("/logout")
async def superadmin_logout(response: Response):
    response.delete_cookie("superadmin_token")
    return {"status": "ok"}


# ── Request models ────────────────────────────────────────────────────────────

class CreateOrgRequest(BaseModel):
    org_name: str
    org_slug: str
    simpro_api_url: Optional[str] = None
    simpro_access_token: Optional[str] = None
    simpro_company_id: Optional[int] = None
    plan_tier: str = "starter"
    agents: Optional[List[str]] = None   # If None → all agents enabled
    admin_email: Optional[str] = None


class UpdateOrgRequest(BaseModel):
    name: Optional[str] = None
    simpro_api_url: Optional[str] = None
    simpro_access_token: Optional[str] = None
    simpro_company_id: Optional[int] = None
    plan_tier: Optional[str] = None
    monthly_token_limit: Optional[int] = None
    is_active: Optional[bool] = None


class AgentPlanItem(BaseModel):
    agent_name: str
    is_enabled: bool
    monthly_token_limit: Optional[int] = None


class UpdateAgentsRequest(BaseModel):
    agents: List[AgentPlanItem]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _org_with_agents(org_id: int) -> Dict[str, Any]:
    """Build full org detail dict including agent plans."""
    org = get_org_by_id(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    plans = get_org_agent_plans(org_id)
    plan_map = {p["agent_name"]: p for p in plans}
    agents = []
    for name in AGENT_NAMES:
        p = plan_map.get(name)
        agents.append({
            "agent_name": name,
            "is_enabled": bool(p["is_enabled"]) if p else True,
            "monthly_token_limit": p["monthly_token_limit"] if p else None,
        })
    return {**org, "agent_plans": agents}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/orgs", dependencies=[Depends(require_superadmin)])
async def list_orgs() -> Dict[str, Any]:
    """List all tenant orgs with user count and current-month usage summary."""
    orgs = get_all_orgs()
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    result = []
    for org in orgs:
        usage = get_monthly_usage(org["id"], now_ist.year, now_ist.month)
        total_used = usage["total_input_tokens"] + usage["total_output_tokens"]
        limit = org.get("monthly_token_limit") or 0
        result.append({
            **org,
            "usage_this_month": total_used,
            "usage_pct": round(total_used / limit * 100, 1) if limit else 0,
        })
    return {"orgs": result}


@router.post("/orgs", dependencies=[Depends(require_superadmin)])
async def create_org(body: CreateOrgRequest) -> Dict[str, Any]:
    """
    Create a new tenant org, seed agent plans, and optionally create an admin user.
    Returns the new org + temp_password if admin_email was provided.
    """
    plan_tier = body.plan_tier if body.plan_tier in PLAN_ORG_TOKEN_LIMITS else "starter"
    org_limit = PLAN_ORG_TOKEN_LIMITS[plan_tier]

    org = create_organization(
        name=body.org_name,
        slug=body.org_slug,
        simpro_company_id=body.simpro_company_id,
        plan_name=plan_tier,
        monthly_token_limit=org_limit,
    )

    # Write simpro credentials + plan_tier (create_organization doesn't take these yet)
    update_organization(
        org["id"],
        simpro_api_url=body.simpro_api_url,
        simpro_access_token=body.simpro_access_token,
        plan_name=plan_tier,
    )

    # Seed agent plans
    enabled_agents = set(body.agents) if body.agents else set(AGENT_NAMES)
    agent_limit = PLAN_AGENT_TOKEN_LIMITS.get(plan_tier)
    for name in AGENT_NAMES:
        set_org_agent_plan(
            org["id"], name,
            is_enabled=(name in enabled_agents),
            monthly_token_limit=agent_limit,
        )

    # Seed Phase 4 roles for this org (safe even if org has no roles yet)
    existing_roles = get_org_roles(org["id"])
    if not existing_roles:
        role_ids = seed_org_roles(org["id"])
        admin_role_id = role_ids["admin_role_id"]
    else:
        admin_role_id = next((r["id"] for r in existing_roles if r["is_system"]), None)

    response: Dict[str, Any] = {"org": _org_with_agents(org["id"])}

    # Optionally create admin user
    if body.admin_email:
        # Check if user already exists — if so, just add membership
        from auth.database import get_user_by_email, create_org_membership
        existing = get_user_by_email(body.admin_email)
        if existing:
            create_org_membership(existing["id"], org["id"], role="admin", role_id=admin_role_id)
            response["admin"] = {"email": body.admin_email, "note": "Existing user linked as admin"}
        else:
            admin_info = create_org_admin_user(body.admin_email, org["id"])
            response["admin"] = admin_info

    return response


@router.get("/orgs/{org_id}", dependencies=[Depends(require_superadmin)])
async def get_org(org_id: int) -> Dict[str, Any]:
    """Get full tenant detail: org fields + agent plans."""
    return _org_with_agents(org_id)


@router.put("/orgs/{org_id}", dependencies=[Depends(require_superadmin)])
async def update_org(org_id: int, body: UpdateOrgRequest) -> Dict[str, Any]:
    """Update org fields (credentials, plan, limits, active flag)."""
    org = get_org_by_id(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")

    updated = update_organization(
        org_id,
        name=body.name,
        simpro_api_url=body.simpro_api_url,
        simpro_access_token=body.simpro_access_token,
        simpro_company_id=body.simpro_company_id,
        plan_name=body.plan_tier,
        monthly_token_limit=body.monthly_token_limit,
        is_active=body.is_active,
    )
    return {"org": updated}


@router.get("/orgs/{org_id}/usage", dependencies=[Depends(require_superadmin)])
async def org_usage(org_id: int) -> Dict[str, Any]:
    """Monthly token usage breakdown (org-level + per-agent) for any tenant."""
    org = get_org_by_id(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")

    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    org_usage_data = get_monthly_usage(org_id, now_ist.year, now_ist.month)
    total_used = org_usage_data["total_input_tokens"] + org_usage_data["total_output_tokens"]

    plans = {p["agent_name"]: p for p in get_org_agent_plans(org_id)}
    agents: Dict[str, Any] = {}
    for name in AGENT_NAMES:
        agent_data = get_monthly_agent_usage(org_id, name, now_ist.year, now_ist.month)
        used = agent_data["total_input_tokens"] + agent_data["total_output_tokens"]
        plan = plans.get(name)
        limit = plan["monthly_token_limit"] if plan else None
        agents[name] = {
            "used": used,
            "limit": limit,
            "is_enabled": bool(plan["is_enabled"]) if plan else True,
        }

    return {
        "org_id": org_id,
        "year": now_ist.year,
        "month": now_ist.month,
        "total_used": total_used,
        "monthly_limit": org.get("monthly_token_limit"),
        "agents": agents,
    }


@router.post("/orgs/{org_id}/agents", dependencies=[Depends(require_superadmin)])
async def update_org_agents(org_id: int, body: UpdateAgentsRequest) -> Dict[str, Any]:
    """Batch set per-agent is_enabled + monthly_token_limit for a tenant."""
    org = get_org_by_id(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")

    for item in body.agents:
        if item.agent_name not in AGENT_NAMES:
            raise HTTPException(status_code=400, detail=f"Unknown agent: {item.agent_name}")
        set_org_agent_plan(
            org_id, item.agent_name,
            is_enabled=item.is_enabled,
            monthly_token_limit=item.monthly_token_limit,
        )

    return {"org_id": org_id, "agent_plans": get_org_agent_plans(org_id)}


# ═══════════════════════════════════════════════════════════════════════════
# Phase 5: SOP endpoints for superadmin
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/orgs/{org_id}/sops", dependencies=[Depends(require_superadmin)])
async def get_tenant_sops(org_id: int) -> Dict[str, Any]:
    """View all custom SOP overrides for a tenant."""
    org = get_org_by_id(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    sops = get_all_org_sops(org_id)
    return {
        "org_id": org_id,
        "org_name": org["name"],
        "sops": [
            {
                "agent_name": s["agent_name"],
                "original_filename": s["original_filename"],
                "char_count": len(s["sop_text"]),
                "uploaded_at": s["uploaded_at"],
            }
            for s in sops
        ],
    }


@router.delete("/orgs/{org_id}/sops/{agent_name}", dependencies=[Depends(require_superadmin)])
async def reset_tenant_sop(org_id: int, agent_name: str) -> Dict[str, Any]:
    """Reset one agent SOP to default for a tenant."""
    org = get_org_by_id(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    if agent_name not in AGENT_NAMES:
        raise HTTPException(status_code=400, detail=f"Unknown agent '{agent_name}'")
    deleted = delete_org_sop(org_id, agent_name)
    if not deleted:
        raise HTTPException(status_code=404, detail="No custom SOP found for this agent")
    return {"org_id": org_id, "agent_name": agent_name, "reverted_to": "default"}


# ═══════════════════════════════════════════════════════════════════════════
# Phase 6: Platform-level LLM settings (global defaults)
# ═══════════════════════════════════════════════════════════════════════════

class LLMSlotInput(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None


class PlatformLLMRequest(BaseModel):
    primary: Optional[LLMSlotInput] = None
    complex: Optional[LLMSlotInput] = None


class OrgLLMRequest(BaseModel):
    use_platform_llm: Optional[bool] = None
    primary: Optional[LLMSlotInput] = None
    complex: Optional[LLMSlotInput] = None


def _mask_llm_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return LLM config with api_key replaced by api_key_set bool."""
    result = {}
    for slot_name in ("primary", "complex"):
        slot = config.get(slot_name, {})
        result[slot_name] = {
            "provider": slot.get("provider") or "",
            "model":    slot.get("model") or "",
            "api_key_set": bool(slot.get("api_key")),
        }
    return result


@router.get("/platform/llm", dependencies=[Depends(require_superadmin)])
async def get_platform_llm() -> Dict[str, Any]:
    """View global platform LLM defaults (api_key masked)."""
    config = get_platform_llm_config()
    return {
        "config": _mask_llm_config(config),
        "note": "These defaults apply to all orgs with 'Use Platform Global Key' enabled.",
    }


@router.put("/platform/llm", dependencies=[Depends(require_superadmin)])
async def update_platform_llm(body: PlatformLLMRequest) -> Dict[str, Any]:
    """Set global platform LLM defaults. All fields optional — only provided fields are updated."""
    if body.primary:
        if body.primary.provider is not None:
            set_platform_setting("llm_primary_provider", body.primary.provider)
        if body.primary.model is not None:
            set_platform_setting("llm_primary_model", body.primary.model)
        if body.primary.api_key is not None:
            set_platform_setting("llm_primary_api_key", body.primary.api_key)
    if body.complex:
        if body.complex.provider is not None:
            set_platform_setting("llm_complex_provider", body.complex.provider)
        if body.complex.model is not None:
            set_platform_setting("llm_complex_model", body.complex.model)
        if body.complex.api_key is not None:
            set_platform_setting("llm_complex_api_key", body.complex.api_key)
    config = get_platform_llm_config()
    return {"config": _mask_llm_config(config), "status": "updated"}


# ═══════════════════════════════════════════════════════════════════════════
# Phase 6: Per-org LLM config (toggle + org-specific slots)
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/orgs/{org_id}/llm", dependencies=[Depends(require_superadmin)])
async def get_org_llm(org_id: int) -> Dict[str, Any]:
    """View org's LLM config: toggle state + effective config (api_key masked)."""
    org = get_org_by_id(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")

    use_platform = bool(org.get("use_platform_llm", 1))
    platform = get_platform_llm_config()

    # Org-specific slots (raw, masked)
    org_specific = {
        "primary": {
            "provider": org.get("llm_provider") or "",
            "model":    org.get("llm_model") or "",
            "api_key_set": bool(org.get("llm_api_key")),
        },
        "complex": {
            "provider": org.get("llm_complex_provider") or "",
            "model":    org.get("llm_complex_model") or "",
            "api_key_set": bool(org.get("llm_complex_api_key")),
        },
    }

    # Effective config (what actually gets used)
    if use_platform:
        effective = _mask_llm_config(platform)
    else:
        effective = {
            "primary": {
                "provider": org.get("llm_provider") or platform["primary"]["provider"],
                "model":    org.get("llm_model")    or platform["primary"]["model"],
                "api_key_set": bool(org.get("llm_api_key") or platform["primary"]["api_key"]),
            },
            "complex": {
                "provider": org.get("llm_complex_provider") or platform["complex"]["provider"],
                "model":    org.get("llm_complex_model")    or platform["complex"]["model"],
                "api_key_set": bool(org.get("llm_complex_api_key") or platform["complex"]["api_key"]),
            },
        }

    return {
        "org_id": org_id,
        "use_platform_llm": use_platform,
        "effective": effective,
        "org_specific": org_specific,
    }


@router.put("/orgs/{org_id}/llm", dependencies=[Depends(require_superadmin)])
async def update_org_llm(org_id: int, body: OrgLLMRequest) -> Dict[str, Any]:
    """Set per-org LLM toggle and/or org-specific key slots."""
    org = get_org_by_id(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")

    kwargs: Dict[str, Any] = {}
    if body.use_platform_llm is not None:
        kwargs["use_platform_llm"] = body.use_platform_llm
    if body.primary:
        if body.primary.provider is not None:
            kwargs["llm_provider"] = body.primary.provider
        if body.primary.model is not None:
            kwargs["llm_model"] = body.primary.model
        if body.primary.api_key is not None:
            kwargs["llm_api_key"] = body.primary.api_key
    if body.complex:
        if body.complex.provider is not None:
            kwargs["llm_complex_provider"] = body.complex.provider
        if body.complex.model is not None:
            kwargs["llm_complex_model"] = body.complex.model
        if body.complex.api_key is not None:
            kwargs["llm_complex_api_key"] = body.complex.api_key

    if kwargs:
        update_organization(org_id, **kwargs)

    # Return updated state
    updated_org = get_org_by_id(org_id)
    use_platform = bool(updated_org.get("use_platform_llm", 1))
    return {
        "org_id": org_id,
        "use_platform_llm": use_platform,
        "status": "updated",
    }


@router.delete("/orgs/{org_id}/llm", dependencies=[Depends(require_superadmin)])
async def clear_org_llm(org_id: int) -> Dict[str, Any]:
    """Clear all org-specific LLM overrides and flip toggle back to platform global."""
    org = get_org_by_id(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")

    update_organization(
        org_id,
        use_platform_llm=True,
        llm_provider="",
        llm_model="",
        llm_api_key="",
        llm_complex_provider="",
        llm_complex_model="",
        llm_complex_api_key="",
    )
    return {"org_id": org_id, "use_platform_llm": True, "status": "cleared"}


# ═══════════════════════════════════════════════════════════════════════════
# Phase 7: Cross-org usage logs (replaces the vulnerable /api/auth/logs/all)
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/logs", dependencies=[Depends(require_superadmin)])
async def superadmin_logs(
    days: int = 1,
    agent: str = None,
    user_id: int = None,
    org_id: int = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Cross-org usage logs — superadmin only.

    Query params:
        days:    How many days to look back (default 1)
        agent:   Filter by agent name (optional)
        user_id: Filter by specific user ID (optional)
        org_id:  Filter by specific org ID (optional — omit for all orgs)
        limit:   Max records (default 100)
        offset:  Pagination offset (default 0)
    """
    return get_usage_logs(
        org_id=org_id,
        days=days,
        agent_name=agent,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Department Mapping (superadmin view/set per org)
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/orgs/{org_id}/department-mapping", dependencies=[Depends(require_superadmin)])
async def superadmin_get_dept_mapping(org_id: int) -> Dict[str, Any]:
    """View current department mapping + drift warnings for an org."""
    org = get_org_by_id(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    mapping = get_org_department_mapping(org_id) or {}
    warnings = get_org_dept_warnings(org_id)
    return {"org_id": org_id, "mapping": mapping, "drift_warnings": warnings}


@router.put("/orgs/{org_id}/department-mapping", dependencies=[Depends(require_superadmin)])
async def superadmin_update_dept_mapping(org_id: int, body: dict) -> Dict[str, Any]:
    """
    Set department mapping for an org and invalidate its cache.
    Body: {"mapping": {"Plumbing": ["4-1000"], "Roofing": ["4-2000"]}}
    """
    org = get_org_by_id(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    mapping = body.get("mapping")
    if not isinstance(mapping, dict):
        raise HTTPException(status_code=400, detail="'mapping' must be a JSON object")

    set_org_department_mapping(org_id, mapping)

    from utils.department_cache import invalidate_department_cache
    invalidate_department_cache(org_id)

    return {"status": "ok", "org_id": org_id, "department_count": len(mapping)}


@router.delete("/orgs/{org_id}/department-mapping/warnings", dependencies=[Depends(require_superadmin)])
async def superadmin_dismiss_dept_warnings(org_id: int) -> Dict[str, Any]:
    """Dismiss drift warnings for an org."""
    org = get_org_by_id(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    clear_org_dept_warnings(org_id)
    return {"status": "ok", "org_id": org_id}


# ═══════════════════════════════════════════════════════════════════════════
# Tenant user management (list / change password / change role / delete)
# ═══════════════════════════════════════════════════════════════════════════

class ChangePasswordRequest(BaseModel):
    new_password: str


class ChangeRoleRequest(BaseModel):
    role_id: int


class ActivateUserRequest(BaseModel):
    new_password: Optional[str] = None


def _ensure_org(org_id: int) -> Dict[str, Any]:
    org = get_org_by_id(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    return org


def _ensure_member(org_id: int, user_id: int) -> Dict[str, Any]:
    """Ensure user_id is a member of org_id. Returns the member row."""
    members = get_org_members(org_id)
    member = next((m for m in members if m["id"] == user_id), None)
    if not member:
        raise HTTPException(status_code=404, detail="User is not a member of this org")
    return member


@router.get("/orgs/{org_id}/users", dependencies=[Depends(require_superadmin)])
async def list_tenant_users(org_id: int) -> Dict[str, Any]:
    """List all users of a tenant with role info."""
    _ensure_org(org_id)
    members = get_org_members(org_id)
    return {"org_id": org_id, "users": members}


@router.get("/orgs/{org_id}/roles", dependencies=[Depends(require_superadmin)])
async def list_tenant_roles(org_id: int) -> Dict[str, Any]:
    """List all roles for a tenant org (used by superadmin to promote/demote users)."""
    _ensure_org(org_id)
    # Self-heal: every tenant must have a system 'admin' role.
    ensure_admin_role(org_id)
    return {"org_id": org_id, "roles": get_org_roles(org_id)}


@router.post("/orgs/{org_id}/users/{user_id}/password", dependencies=[Depends(require_superadmin)])
async def change_tenant_user_password(
    org_id: int,
    user_id: int,
    body: ChangePasswordRequest,
) -> Dict[str, Any]:
    """Set a tenant user's password. Superadmin-only."""
    _ensure_org(org_id)
    _ensure_member(org_id, user_id)

    err = validate_password_policy(body.new_password)
    if err:
        raise HTTPException(status_code=400, detail=err)

    hashed = hash_password(body.new_password)
    if not set_user_password(user_id, hashed):
        raise HTTPException(status_code=404, detail="User not found")
    return {"status": "ok", "user_id": user_id}


@router.put("/orgs/{org_id}/users/{user_id}/role", dependencies=[Depends(require_superadmin)])
async def change_tenant_user_role(
    org_id: int,
    user_id: int,
    body: ChangeRoleRequest,
) -> Dict[str, Any]:
    """Promote/demote a tenant user. Last-admin demotion is blocked atomically."""
    _ensure_org(org_id)
    _ensure_member(org_id, user_id)

    new_role = get_role(body.role_id)
    if not new_role or new_role["org_id"] != org_id:
        raise HTTPException(status_code=400, detail="Invalid role for this organization")

    role_text = "admin" if bool(new_role.get("is_system")) else "member"
    try:
        updated = assign_member_role_atomic(user_id, org_id, body.role_id, role_text)
    except LastAdminError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not updated:
        raise HTTPException(status_code=404, detail="Membership not found")
    return {"status": "ok", "user_id": user_id, "role_id": body.role_id, "role": role_text}


@router.post("/orgs/{org_id}/users/{user_id}/deactivate", dependencies=[Depends(require_superadmin)])
async def deactivate_tenant_user(org_id: int, user_id: int) -> Dict[str, Any]:
    """Deactivate a tenant user (reversible). Last-active-admin guard is atomic."""
    _ensure_org(org_id)
    member = _ensure_member(org_id, user_id)

    if not member.get("is_active", 1):
        raise HTTPException(status_code=400, detail="User is already deactivated.")

    try:
        # actor_user_id=None signals "deactivated by superadmin" (no user record).
        result = set_membership_active(user_id, org_id, is_active=False, actor_user_id=None)
    except LastAdminError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not result["updated"]:
        raise HTTPException(status_code=404, detail="User not found")
    return {"status": "ok", "user_id": user_id, "is_active": False}


@router.post("/orgs/{org_id}/users/{user_id}/activate", dependencies=[Depends(require_superadmin)])
async def activate_tenant_user(
    org_id: int,
    user_id: int,
    body: ActivateUserRequest,
) -> Dict[str, Any]:
    """Reactivate a tenant user. Optionally resets their password."""
    _ensure_org(org_id)
    member = _ensure_member(org_id, user_id)

    if member.get("is_active", 1):
        raise HTTPException(status_code=400, detail="User is already active.")

    new_password = body.new_password
    if new_password is not None:
        err = validate_password_policy(new_password)
        if err:
            raise HTTPException(status_code=400, detail=err)

    result = set_membership_active(user_id, org_id, is_active=True, actor_user_id=None)
    if not result["updated"]:
        raise HTTPException(status_code=404, detail="User not found")

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


@router.post("/orgs/{org_id}/department-mapping/validate", dependencies=[Depends(require_superadmin)])
async def superadmin_validate_dept_mapping(org_id: int) -> Dict[str, Any]:
    """
    Force-rebuild the department cache for an org and return fresh drift warnings.
    Org must have Simpro credentials configured.
    """
    from utils.mcp_tool_client import MCPToolClient
    from utils.mcp_executor import MCPToolExecutor
    from utils.department_cache import refresh_department_cache

    org = get_org_by_id(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    if not org.get("simpro_api_url") or not org.get("simpro_access_token"):
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
