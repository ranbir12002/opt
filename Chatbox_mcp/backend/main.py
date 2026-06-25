from dotenv import load_dotenv
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
# Load environment variables from .env
load_dotenv(dotenv_path=Path(__file__).parent.parent / '.env', override=False)
print(f"OPENAI_API_KEY: {'SET' if os.getenv('OPENAI_API_KEY') else 'NOT SET'}")
print(f"LLM_MODEL: {os.getenv('LLM_MODEL', 'NOT SET')}")

# ── Centralized logging: console + rotating file ──────────────────────────
_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_LOG_FILE = _LOG_DIR / "backend.log"
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

_fmt = logging.Formatter(
    "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# File handler: 5 MB per file, keep 5 rotations (25 MB total max)
_fh = RotatingFileHandler(
    str(_LOG_FILE), maxBytes=5_000_000, backupCount=5, encoding="utf-8"
)
_fh.setFormatter(_fmt)
_fh.setLevel(_LOG_LEVEL)

# Console handler (utf-8 safe for Windows emoji issues)
_ch = logging.StreamHandler(sys.stdout)
_ch.setFormatter(_fmt)
_ch.setLevel(_LOG_LEVEL)

logging.basicConfig(level=_LOG_LEVEL, handlers=[_ch, _fh])

# Quiet noisy third-party loggers
for _noisy in ("httpx", "httpcore", "watchfiles"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# Append root directory to sys.path so `rag`, `backend`, etc., work
sys.path.append(os.path.dirname(__file__))

# ✅ UPDATED: Import new chat endpoint (MCP-integrated)
from api.chat import router as chat_router

# ✅ NEW: Import analysis endpoint (for Node.js to call)
from api.analysis import router as analysis_router

# ✅ KEEP: Presenter router (still needed for rendering)
from presenter_router import router as presenter_router

# Auth router
from api.auth_routes import router as auth_router

# Personality router
from api.personality_api import router as personality_router

# Agent handoff router (MCP → Agent bridge)
from api.agent_handoff import router as agent_handoff_router

# Super-admin router (platform owner manages all tenants)
from api.superadmin_routes import router as superadmin_router

# ✅ OPTIONAL: Keep old endpoints as fallback (can remove later)
# from endpoints import chat_execute

app = FastAPI(
    title="Chatbox MCP Backend",
    description="Python backend for Chatbox with MCP integration",
    version="2.0.0"
)

# ✅ UPDATED: CORS for frontend + Node.js MCP client + tenant subdomains
# BASE_DOMAIN controls which production subdomains are accepted (e.g. *.optificial.ai)
_BASE_DOMAIN = os.getenv("BASE_DOMAIN", "optificial.ai")
# Escape dots for regex, build pattern: https?://<anything>.optificial.ai(:port)?
_domain_re = _BASE_DOMAIN.replace(".", r"\.")
_CORS_ORIGIN_REGEX = rf"^(https?://localhost(:\d+)?|https?://([a-z0-9\-]+\.)?{_domain_re}(:\d+)?)$"

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)




# ✅ NEW: Register MCP-integrated chat endpoint
app.include_router(chat_router, prefix="/api", tags=["Chat"])

# ✅ NEW: Register analysis endpoint (for query complexity)
app.include_router(analysis_router, prefix="/api", tags=["Analysis"])

# ✅ KEEP: Presenter router (for rendering results)
app.include_router(presenter_router, prefix="/api", tags=["Presenter"])

# Auth router
app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])

# Personality router
app.include_router(personality_router, prefix="/api", tags=["Personality"])

# Agent handoff router (MCP tool-calling loop → Python agents)
app.include_router(agent_handoff_router, prefix="/api", tags=["Agent Handoff"])

# Super-admin router (platform management — protected by SUPERADMIN_TOKEN)
app.include_router(superadmin_router, prefix="/api/superadmin", tags=["Superadmin"])

# ✅ OPTIONAL: Keep old chat_execute as backup (can remove after testing)
# app.include_router(chat_execute.router, prefix="/api/legacy", tags=["Legacy"])


# Agent status endpoint
@app.get("/api/agents/status", tags=["Agents"])
async def agents_status():
    """Return the list of registered agents and connected MCP servers."""
    from agents.registry import AGENT_REGISTRY

    agents = []
    for key, info in AGENT_REGISTRY.items():
        if info.get("enabled"):
            agents.append({
                "id": key,
                "title": info["title"],
                "responsibility": info["responsibility"],
                "status": "active",
            })

    # Check MCP servers
    services = []
    mcp_client_url = os.getenv("MCP_CLIENT_URL", "http://localhost:3001")
    simpro_url = os.getenv("MCP_SERVER_URL", "http://localhost:8000")
    myob_url = os.getenv("MYOB_SERVER_URL", "http://localhost:8010")

    service_defs = [
        ("MCP Client", mcp_client_url, "LLM orchestration and tool routing"),
        ("Simpro Server", simpro_url, "Simpro ERP API tools"),
        ("MyOB Server", myob_url, "MyOB AccountRight API tools"),
    ]

    from utils.http_pool import get_health_pool
    hc = get_health_pool()
    for name, url, desc in service_defs:
        try:
            resp = await hc.get(f"{url}/health", timeout=3)
            status = "active" if resp.status_code == 200 else "degraded"
        except Exception:
            status = "offline"
        services.append({"name": name, "url": url, "description": desc, "status": status})

    total_active = len(agents) + sum(1 for s in services if s["status"] == "active")

    return {
        "agents": agents,
        "services": services,
        "total_active": total_active,
    }


# ✅ NEW: Health check endpoint
@app.get("/health", tags=["Health"])
async def health_check():
    """
    Health check endpoint.
    
    Returns:
        - Backend status
        - MCP client connectivity
        - Environment info
    """
    mcp_client_url = os.getenv("MCP_CLIENT_URL", "http://localhost:3001")
    mcp_status = "unknown"
    
    # Check if MCP client is reachable
    try:
        from utils.http_pool import get_health_pool
        hc = get_health_pool()
        response = await hc.get(f"{mcp_client_url}/health")
        if response.status_code == 200:
            mcp_status = "healthy"
        else:
            mcp_status = "unhealthy"
    except Exception:
        mcp_status = "unreachable"
    
    return {
        "status": "healthy",
        "service": "chatbox-backend",
        "version": "2.0.0",
        "mcp_client": {
            "url": mcp_client_url,
            "status": mcp_status
        },
        "environment": {
            "python_version": sys.version,
            "mode": os.getenv("ENVIRONMENT", "development")
        }
    }


# ✅ NEW: Root endpoint with API documentation
@app.get("/", tags=["Root"])
async def root():
    """
    Root endpoint with service information.
    """
    return {
        "service": "Chatbox MCP Backend",
        "version": "2.0.0",
        "description": "Python backend with MCP integration",
        "features": [
            "MCP-integrated chat endpoint",
            "Query complexity analysis",
            "Agent support (invoice, etc.)",
            "Result presentation",
            "Session management"
        ],
        "endpoints": {
            "chat": "POST /api/chat",
            "analysis": "POST /api/analyze-query",
            "presenter": "POST /api/present",
            "health": "GET /health",
            "docs": "GET /docs"
        },
        "documentation": "/docs"
    }


async def _run_dept_mapping_migration():
    """
    One-time migration: for each org with Simpro credentials but no stored
    department_mapping, call Simpro to fetch chart-of-accounts + setup cost
    centres, then auto-build and persist the mapping.

    Guarded by 'department_mapping IS NULL' — safe to call on every startup.
    """
    from auth.database import get_orgs_needing_dept_migration, set_org_department_mapping
    from utils.mcp_tool_client import MCPToolClient
    from utils.mcp_executor import MCPToolExecutor
    import asyncio

    orgs = get_orgs_needing_dept_migration()
    if not orgs:
        print("  Dept mapping migration: all orgs already configured — skipping")
        return

    print(f"  Dept mapping migration: {len(orgs)} org(s) need initial mapping build")

    for org in orgs:
        org_id = org["id"]
        org_name = org.get("name", f"org {org_id}")
        try:
            client = MCPToolClient(
                simpro_token=org["simpro_access_token"],
                simpro_url=org["simpro_api_url"],
                simpro_company_id=org.get("simpro_company_id"),
            )
            executor = MCPToolExecutor(
                tool_registry=client,
                company_id=org.get("simpro_company_id") or 0,
            )

            setup_cc_result, coa_result = await asyncio.gather(
                executor.call_tool("get_setup_cost_centres", {"columns": "ID,Name,IncomeAccountNo"}),
                executor.call_tool("get_chart_of_accounts", {"columns": "ID,Name,Number"}),
            )

            cc_details = (
                setup_cc_result.get("setup_cost_centres", [])
                if isinstance(setup_cc_result, dict) else setup_cc_result or []
            )
            coa_details = (
                coa_result.get("chart_of_accounts", [])
                if isinstance(coa_result, dict) else coa_result or []
            )

            # Build mapping: account_name -> [account_number]
            # Groups cost centres by chart-of-accounts name as the department label
            acct_no_to_name = {
                str(a.get("Number", "")).strip(): (a.get("Name") or "")
                for a in coa_details if a.get("Number")
            }
            mapping: dict = {}
            for cc in cc_details:
                income_acct = cc.get("IncomeAccountNo")
                if not income_acct:
                    continue
                acct_str = str(income_acct).strip()
                dept_name = acct_no_to_name.get(acct_str)
                if dept_name:
                    if dept_name not in mapping:
                        mapping[dept_name] = []
                    if acct_str not in mapping[dept_name]:
                        mapping[dept_name].append(acct_str)

            set_org_department_mapping(org_id, mapping)
            print(
                f"  Dept mapping migration: org '{org_name}' ({org_id}) — "
                f"{len(mapping)} departments built from {len(coa_details)} accounts"
            )
        except Exception as e:
            print(
                f"  ⚠️  Dept mapping migration: org '{org_name}' ({org_id}) failed — {e}"
            )


# ✅ OPTIONAL: Startup event for initialization
@app.on_event("startup")
async def startup_event():
    """
    Initialize services on startup.
    """
    print("\n" + "="*60)
    print("  Chatbox MCP Backend - Starting")
    print("="*60)
    print(f"  Port: 8001 (default)")
    print(f"  Docs: http://localhost:8001/docs")
    print(f"  Health: http://localhost:8001/health")
    print("="*60 + "\n")
    
    # Check MCP client connectivity
    mcp_url = os.getenv("MCP_CLIENT_URL", "http://localhost:3001")
    print(f"[INFO] MCP Client URL: {mcp_url}")
    
    try:
        from utils.http_pool import get_health_pool
        hc = get_health_pool()
        response = await hc.get(f"{mcp_url}/health")
        if response.status_code == 200:
            print(f"[INFO] MCP Client is healthy")
        else:
            print(f"[WARN] MCP Client returned status {response.status_code}")
    except Exception as e:
        print(f"[WARN] MCP Client not reachable: {e}")
        print(f"   Make sure Node.js MCP client is running on {mcp_url}")
    
    # One-time department mapping migration: for every org with Simpro credentials
    # but no stored department_mapping, fetch live chart-of-accounts and build the
    # mapping automatically. Guarded by "department_mapping IS NULL" so it only
    # runs once per org.
    try:
        await _run_dept_mapping_migration()
    except Exception as e:
        print(f"[WARN] Department mapping migration failed: {e}")

    # Launch background session cleanup task (evicts expired clarification sessions)
    import asyncio
    from api.chat import _cleanup_expired_sessions
    asyncio.create_task(_cleanup_expired_sessions())
    print("  Session cleanup task started (TTL=600s, interval=60s)")

    print("\n[SUCCESS] Backend ready!\n")


# ✅ OPTIONAL: Shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    """
    Cleanup on shutdown.
    """
    from utils.http_pool import close_all as close_http_pools
    from utils.mcp_tool_client import get_mcp_tool_client

    await close_http_pools()
    await get_mcp_tool_client().close()
    print("\n[INFO] Chatbox MCP Backend shutting down...\n")


# ── Entry point ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8001"))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=True,
        reload_includes=["*.py"],
        reload_excludes=["logs/*", "__pycache__/*"],
    )