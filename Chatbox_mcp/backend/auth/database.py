# backend/auth/database.py
# Database storage for authentication, organizations, and usage tracking
import os
import re
import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker
from auth.models import Base
from auth import crypto

# ═══════════════════════════════════════════════════════════════════════════
# Plan tier constants
# ═══════════════════════════════════════════════════════════════════════════

# Per-agent monthly token limits per plan tier (None = no per-agent limit)
PLAN_AGENT_TOKEN_LIMITS: Dict[str, Optional[int]] = {
    "starter":    50_000,
    "growth":     250_000,
    "enterprise": None,   # custom — set manually per org
}

# Org-level monthly token limits per plan tier
PLAN_ORG_TOKEN_LIMITS: Dict[str, int] = {
    "starter":    200_000,    # 4 agents × 50k
    "growth":     1_000_000,  # 4 agents × 250k
    "enterprise": 10_000_000, # custom default
}

# Agents available in the platform
AGENT_NAMES = ["schedule", "invoice", "workorder", "purchase_order"]

# ═══════════════════════════════════════════════════════════════════════════
# Engine and Session initialization
# ═══════════════════════════════════════════════════════════════════════════
DATABASE_URL = os.getenv("DATABASE_URL", "")
USE_RDS_IAM_AUTH = os.getenv("RDS_IAM_AUTH", "").lower() == "true"

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable must be set to a valid PostgreSQL connection string")

if USE_RDS_IAM_AUTH:
    import urllib.parse
    import boto3
    import psycopg2

    # Parse DB connection info from DATABASE_URL
    url = urllib.parse.urlparse(DATABASE_URL)
    db_user = url.username
    db_host = url.hostname
    db_port = url.port or 5432
    db_name = url.path.lstrip('/')

    # Try to extract region from database host or default to us-east-1
    aws_region = os.getenv("AWS_REGION")
    if not aws_region and db_host:
        parts = db_host.split('.')
        if len(parts) >= 3 and parts[-3] == 'rds':
            aws_region = parts[-4]
    aws_region = aws_region or "us-east-1"

    def rds_iam_connect():
        # Dynamically generate IAM token for database authentication
        rds_client = boto3.client('rds', region_name=aws_region)
        token = rds_client.generate_db_auth_token(
            DBHostname=db_host,
            Port=db_port,
            DBUsername=db_user,
            Region=aws_region
        )
        return psycopg2.connect(
            host=db_host,
            port=db_port,
            user=db_user,
            password=token,
            database=db_name,
            sslmode="require"
        )

    # Connect using custom connection creator function
    engine = create_engine(
        "postgresql://",
        creator=rds_iam_connect,
        pool_size=10,
        max_overflow=20,
        pool_recycle=600,  # Recycle within 10 mins (RDS token expires in 15 mins)
        pool_pre_ping=True
    )
else:
    # Standard connection (password-based)
    engine = create_engine(
        DATABASE_URL,
        pool_size=10,
        max_overflow=20,
        pool_recycle=1800,
        pool_pre_ping=True
    )

SessionLocal = sessionmaker(bind=engine)


# ═══════════════════════════════════════════════════════════════════════════
# Compatibility wrappers
# ═══════════════════════════════════════════════════════════════════════════

class SqlRow:
    def __init__(self, mapping):
        self._mapping = dict(mapping)

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._mapping.values())[key]
        return self._mapping[key]

    def get(self, key, default=None):
        return self._mapping.get(key, default)

    def keys(self):
        return self._mapping.keys()

    def values(self):
        return self._mapping.values()

    def __iter__(self):
        return iter(self._mapping.keys())


class SqlResult:
    def __init__(self, res):
        self._res = res
        self.rowcount = res.rowcount

    def fetchone(self):
        row = self._res.fetchone()
        if not row:
            return None
        return SqlRow(row._mapping)

    def fetchall(self):
        rows = self._res.fetchall()
        return [SqlRow(r._mapping) for r in rows]

    @property
    def lastrowid(self):
        if hasattr(self, '_pg_lastrowid') and self._pg_lastrowid is not None:
            return self._pg_lastrowid
        try:
            if self._res.inserted_primary_key:
                return self._res.inserted_primary_key[0]
        except Exception:
            pass
        return getattr(self._res, 'lastrowid', None)


class SqlAlchemyConnection:
    def __init__(self, conn):
        self._conn = conn
        self._trans = conn.begin()
        self.rowcount = 0

    def execute(self, sql: str, params=None):
        # Convert positional ? parameters to named parameters (:p0, :p1, ...)
        if not params:
            param_dict = {}
        elif isinstance(params, (tuple, list)):
            param_dict = {}
            count = 0
            def repl(match):
                nonlocal count
                name = f"p{count}"
                param_dict[name] = params[count]
                count += 1
                return f":{name}"
            sql = re.sub(r'\?', repl, sql)
        elif isinstance(params, dict):
            param_dict = params
        else:
            sql = re.sub(r'\?', ":p0", sql)
            param_dict = {"p0": params}

        # Auto-append RETURNING id to get the inserted primary key
        is_insert = sql.strip().upper().startswith("INSERT")
        if is_insert and "RETURNING" not in sql.upper():
            sql += " RETURNING id"

        res = self._conn.execute(text(sql), param_dict)
        self.rowcount = res.rowcount
        result = SqlResult(res)

        # Fetch the returned id
        if is_insert:
            try:
                row = res.fetchone()
                result._pg_lastrowid = row[0] if row else None
            except Exception:
                result._pg_lastrowid = None
        return result

    def commit(self):
        self._trans.commit()
        self._trans = self._conn.begin()

    def rollback(self):
        self._trans.rollback()
        self._trans = self._conn.begin()

    def close(self):
        try:
            self._trans.rollback()
        except Exception:
            pass
        self._conn.close()


def get_db() -> SqlAlchemyConnection:
    conn = engine.connect()
    return SqlAlchemyConnection(conn)


def init_db():
    # 1. Create tables using SQLAlchemy models
    Base.metadata.create_all(engine)

    conn = get_db()
    # 2. Seed platform settings with default LLM values
    _seed_platform_settings(conn)
    # 3. Run the one-time encryption migration for organization credentials
    _migrate_encrypt_credentials(conn)
    conn.commit()
    conn.close()


def _migrate_encrypt_credentials(conn):
    """
    Scan all organizations and encrypt plaintext secrets in-place.
    """
    rows = conn.execute(
        "SELECT id, simpro_access_token, llm_api_key, llm_complex_api_key, llm_stt_api_key FROM organizations"
    ).fetchall()
    
    migrated_count = 0
    for r in rows:
        org_id = r["id"]
        updates = {}
        for col in ["simpro_access_token", "llm_api_key", "llm_complex_api_key", "llm_stt_api_key"]:
            val = r[col]
            if val and not crypto.is_encrypted(val):
                updates[col] = crypto.encrypt(val)
        
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            params = list(updates.values()) + [org_id]
            conn.execute(f"UPDATE organizations SET {set_clause} WHERE id = ?", params)
            migrated_count += 1
            
    if migrated_count > 0:
        print(f"🔑 Encrypted credentials for {migrated_count} organization(s) during startup migration.")

def _seed_platform_settings(conn):
    """
    Seed platform_settings with default LLM values from .env if not already set.
    Safe to call repeatedly — only inserts missing keys (never overwrites existing).
    (Phase 6)
    """
    import os
    defaults = {
        "llm_primary_provider": os.getenv("LLM_PROVIDER", "openai"),
        "llm_primary_model":    os.getenv("LLM_MODEL", "gpt-4.1-mini"),
        "llm_primary_api_key":  os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or "",
        "llm_complex_provider": "",
        "llm_complex_model":    "",
        "llm_complex_api_key":  "",
        "llm_stt_api_key":      os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "",
    }
    for key, value in defaults.items():
        existing = conn.execute(
            "SELECT key FROM platform_settings WHERE key = ?", (key,)
        ).fetchone()
        if not existing:
            # Explicitly append 'RETURNING key' so the custom wrapper skips appending 'RETURNING id'
            conn.execute(
                "INSERT INTO platform_settings (key, value) VALUES (?, ?) RETURNING key", (key, value)
            )


# ═══════════════════════════════════════════════════════════════════════════
# User queries (existing)
# ═══════════════════════════════════════════════════════════════════════════

def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    row = conn.execute(
        "SELECT id, email, hashed_password, name FROM users WHERE email = ?",
        (email.lower().strip(),),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def create_user(email: str, hashed_password: str, name: str = "") -> Dict[str, Any]:
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO users (email, hashed_password, name) VALUES (?, ?, ?)",
        (email.lower().strip(), hashed_password, name.strip()),
    )
    user_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {"id": user_id, "email": email.lower().strip(), "name": name.strip()}


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    row = conn.execute(
        "SELECT id, email, name FROM users WHERE id = ?", (user_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def set_user_password(user_id: int, hashed_password: str) -> bool:
    """Update a user's hashed_password. Returns True if a row was updated."""
    conn = get_db()
    cursor = conn.execute(
        "UPDATE users SET hashed_password = ? WHERE id = ?",
        (hashed_password, user_id),
    )
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def delete_user_completely(user_id: int) -> bool:
    """Remove user + all their org_memberships. Usage history is preserved."""
    conn = get_db()
    conn.execute("DELETE FROM org_memberships WHERE user_id = ?", (user_id,))
    cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def get_all_users() -> List[Dict[str, Any]]:
    """List all users with their org and role info."""
    conn = get_db()
    rows = conn.execute("""
        SELECT
            u.id,
            u.email,
            u.name,
            CAST(u.created_at + INTERVAL '5 hours 30 minutes' AS TIMESTAMP) AS created_at_ist,
            o.id   AS org_id,
            o.name AS org_name,
            o.plan_name,
            om.role,
            COALESCE(stats.total_requests, 0)  AS total_requests,
            COALESCE(stats.total_tokens, 0)     AS total_tokens,
            ROUND(COALESCE(stats.total_cost_usd, 0), 4) AS total_cost_usd,
            stats.last_active_ist
        FROM users u
        LEFT JOIN org_memberships om ON om.user_id = u.id
        LEFT JOIN organizations o ON o.id = om.org_id
        LEFT JOIN (
            SELECT
                user_id,
                COUNT(*)                          AS total_requests,
                SUM(input_tokens + output_tokens) AS total_tokens,
                SUM(estimated_cost_usd)           AS total_cost_usd,
                CAST(MAX(created_at) + INTERVAL '5 hours 30 minutes' AS TIMESTAMP) AS last_active_ist
            FROM usage_records
            GROUP BY user_id
        ) stats ON stats.user_id = u.id
        ORDER BY u.id
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════
# Organization queries
# ═══════════════════════════════════════════════════════════════════════════

def _decrypt_org_fields(org: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not org:
        return org
    org = dict(org)
    for field in ["simpro_access_token", "llm_api_key", "llm_complex_api_key", "llm_stt_api_key"]:
        if field in org and org[field]:
            org[field] = crypto.decrypt(org[field])
    return org


def get_org_by_id(org_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM organizations WHERE id = ?", (org_id,)
    ).fetchone()
    conn.close()
    return _decrypt_org_fields(dict(row)) if row else None


def get_org_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    """Look up an org by its URL slug. Returns public branding fields only (no secrets)."""
    conn = get_db()
    row = conn.execute(
        "SELECT id, name, slug, logo_url, primary_color, tagline, is_active FROM organizations WHERE slug = ?",
        (slug,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def get_all_orgs() -> List[Dict[str, Any]]:
    """List all orgs with active + total user counts. Used by superadmin dashboard."""
    conn = get_db()
    rows = conn.execute("""
        SELECT
            o.*,
            COUNT(om.user_id) AS user_count,
            COALESCE(SUM(CASE WHEN COALESCE(om.is_active, 1) = 1 THEN 1 ELSE 0 END), 0)
                AS active_user_count,
            CAST(o.created_at + INTERVAL '5 hours 30 minutes' AS TIMESTAMP) AS created_at_ist
        FROM organizations o
        LEFT JOIN org_memberships om ON om.org_id = o.id
        GROUP BY o.id
        ORDER BY o.created_at DESC
    """).fetchall()
    conn.close()
    return [_decrypt_org_fields(dict(r)) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════
# Platform settings (Phase 6 — global LLM defaults)
# ═══════════════════════════════════════════════════════════════════════════

def get_platform_setting(key: str) -> Optional[str]:
    conn = get_db()
    row = conn.execute("SELECT value FROM platform_settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def set_platform_setting(key: str, value: str) -> None:
    conn = get_db()
    conn.execute(
        "INSERT INTO platform_settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_all_platform_settings() -> Dict[str, str]:
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM platform_settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def get_platform_llm_config() -> Dict[str, Any]:
    """
    Returns resolved global LLM config from platform_settings.
    {"primary": {provider, model, api_key}, "complex": {provider, model, api_key}}
    Complex slot falls back to primary if not configured.
    """
    settings = get_all_platform_settings()
    primary = {
        "provider": settings.get("llm_primary_provider") or "openai",
        "model":    settings.get("llm_primary_model") or "gpt-4.1-mini",
        "api_key":  settings.get("llm_primary_api_key") or "",
    }
    complex_provider = settings.get("llm_complex_provider") or ""
    complex_model    = settings.get("llm_complex_model") or ""
    complex_api_key  = settings.get("llm_complex_api_key") or ""
    # Complex slot — falls back to primary fields for any missing values
    complex_slot = {
        "provider": complex_provider or primary["provider"],
        "model":    complex_model    or primary["model"],
        "api_key":  complex_api_key  or primary["api_key"],
    }
    return {"primary": primary, "complex": complex_slot}


def get_org_department_mapping(org_id: int) -> Optional[Dict]:
    """Return parsed department_mapping JSON for an org, or None if not set."""
    conn = get_db()
    row = conn.execute(
        "SELECT department_mapping FROM organizations WHERE id = ?", (org_id,)
    ).fetchone()
    conn.close()
    if not row or not row[0]:
        return None
    try:
        import json as _json
        return _json.loads(row[0])
    except Exception:
        return None


def set_org_department_mapping(org_id: int, mapping: Dict) -> None:
    """Persist department_mapping JSON for an org."""
    import json as _json
    conn = get_db()
    conn.execute(
        "UPDATE organizations SET department_mapping = ? WHERE id = ?",
        (_json.dumps(mapping), org_id),
    )
    conn.commit()
    conn.close()


def get_org_dept_warnings(org_id: int) -> List[str]:
    """Return stored drift warnings for an org (empty list if none)."""
    conn = get_db()
    row = conn.execute(
        "SELECT dept_mapping_warnings FROM organizations WHERE id = ?", (org_id,)
    ).fetchone()
    conn.close()
    if not row or not row[0]:
        return []
    try:
        import json as _json
        return _json.loads(row[0]) or []
    except Exception:
        return []


def set_org_dept_warnings(org_id: int, warnings: List[str]) -> None:
    """Persist drift warnings for an org."""
    import json as _json
    conn = get_db()
    conn.execute(
        "UPDATE organizations SET dept_mapping_warnings = ? WHERE id = ?",
        (_json.dumps(warnings) if warnings else None, org_id),
    )
    conn.commit()
    conn.close()


def clear_org_dept_warnings(org_id: int) -> None:
    """Dismiss / clear drift warnings for an org."""
    conn = get_db()
    conn.execute(
        "UPDATE organizations SET dept_mapping_warnings = NULL WHERE id = ?", (org_id,)
    )
    conn.commit()
    conn.close()


def get_orgs_needing_dept_migration() -> List[Dict[str, Any]]:
    """
    Return orgs that have Simpro credentials but no department_mapping yet.
    Used for the one-time auto-migration on startup.
    """
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, name, simpro_api_url, simpro_access_token, simpro_company_id
        FROM organizations
        WHERE department_mapping IS NULL
          AND simpro_api_url IS NOT NULL
          AND simpro_access_token IS NOT NULL
          AND is_active = 1
        """
    ).fetchall()
    conn.close()
    return [_decrypt_org_fields(dict(r)) for r in rows]


def update_organization(
    org_id: int,
    name: Optional[str] = None,
    simpro_api_url: Optional[str] = None,
    simpro_access_token: Optional[str] = None,
    simpro_company_id: Optional[int] = None,
    plan_name: Optional[str] = None,
    monthly_token_limit: Optional[int] = None,
    is_active: Optional[bool] = None,
    # Phase 6 — per-org LLM config
    use_platform_llm: Optional[bool] = None,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    llm_api_key: Optional[str] = None,
    llm_complex_provider: Optional[str] = None,
    llm_complex_model: Optional[str] = None,
    llm_complex_api_key: Optional[str] = None,
    llm_stt_api_key: Optional[str] = None,
    # Department mapping
    department_mapping: Optional[str] = None,
    # Branding (subdomain portal)
    logo_url: Optional[str] = None,
    primary_color: Optional[str] = None,
    tagline: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Update one or more fields on an org. Only non-None args are written."""
    fields: Dict[str, Any] = {}
    if name is not None:
        fields["name"] = name
    if simpro_api_url is not None:
        fields["simpro_api_url"] = simpro_api_url
    if simpro_access_token is not None:
        fields["simpro_access_token"] = crypto.encrypt(simpro_access_token) if simpro_access_token else None
    if simpro_company_id is not None:
        fields["simpro_company_id"] = simpro_company_id
    if plan_name is not None:
        fields["plan_name"] = plan_name
    if monthly_token_limit is not None:
        fields["monthly_token_limit"] = monthly_token_limit
    if is_active is not None:
        fields["is_active"] = int(is_active)
    if use_platform_llm is not None:
        fields["use_platform_llm"] = int(use_platform_llm)
    if llm_provider is not None:
        fields["llm_provider"] = llm_provider or None
    if llm_model is not None:
        fields["llm_model"] = llm_model or None
    if llm_api_key is not None:
        fields["llm_api_key"] = crypto.encrypt(llm_api_key) if llm_api_key else None
    if llm_complex_provider is not None:
        fields["llm_complex_provider"] = llm_complex_provider or None
    if llm_complex_model is not None:
        fields["llm_complex_model"] = llm_complex_model or None
    if llm_complex_api_key is not None:
        fields["llm_complex_api_key"] = crypto.encrypt(llm_complex_api_key) if llm_complex_api_key else None
    if llm_stt_api_key is not None:
        fields["llm_stt_api_key"] = crypto.encrypt(llm_stt_api_key) if llm_stt_api_key else None
    if department_mapping is not None:
        fields["department_mapping"] = department_mapping or None
    if logo_url is not None:
        fields["logo_url"] = logo_url or None
    if primary_color is not None:
        fields["primary_color"] = primary_color or None
    if tagline is not None:
        fields["tagline"] = tagline or None

    if not fields:
        return get_org_by_id(org_id)

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [org_id]
    conn = get_db()
    conn.execute(f"UPDATE organizations SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return get_org_by_id(org_id)


def create_org_admin_user(email: str, org_id: int) -> Dict[str, Any]:
    """
    Create a new user with a random temp password and link them to org as admin.
    Returns {user_id, email, temp_password} — caller must surface temp_password to superadmin.
    """
    import secrets
    import string
    alphabet = string.ascii_letters + string.digits
    temp_password = "".join(secrets.choice(alphabet) for _ in range(16))

    from passlib.context import CryptContext
    _pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
    hashed = _pwd.hash(temp_password)

    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO users (email, hashed_password, name) VALUES (?, ?, ?)",
        (email.lower().strip(), hashed, email.split("@")[0]),
    )
    user_id = cursor.lastrowid
    # Look up the system admin role for this org
    admin_role = conn.execute(
        "SELECT id FROM org_roles WHERE org_id = ? AND is_system = 1 LIMIT 1", (org_id,)
    ).fetchone()
    role_id = admin_role["id"] if admin_role else None
    conn.execute(
        "INSERT INTO org_memberships (user_id, org_id, role, role_id, is_active) VALUES (?, ?, 'admin', ?, 1)",
        (user_id, org_id, role_id),
    )
    conn.commit()
    conn.close()
    return {"user_id": user_id, "email": email.lower().strip(), "temp_password": temp_password}


def get_user_org(user_id: int) -> Optional[Dict[str, Any]]:
    """Get the primary ACTIVE org membership for a user (first active membership)."""
    conn = get_db()
    row = conn.execute("""
        SELECT o.*, om.role
        FROM organizations o
        JOIN org_memberships om ON o.id = om.org_id
        WHERE om.user_id = ?
          AND COALESCE(om.is_active, 1) = 1
        ORDER BY om.created_at ASC
        LIMIT 1
    """, (user_id,)).fetchone()
    conn.close()
    return _decrypt_org_fields(dict(row)) if row else None


def get_membership_by_email(email: str, org_id: int) -> Optional[Dict[str, Any]]:
    """Fetch (user_id, is_active) for a user with the given email in the given org, or None."""
    conn = get_db()
    row = conn.execute(
        """SELECT u.id AS user_id, u.email,
                  COALESCE(om.is_active, 1) AS is_active,
                  om.role, om.role_id
           FROM users u
           JOIN org_memberships om ON om.user_id = u.id
           WHERE LOWER(u.email) = LOWER(?) AND om.org_id = ?
           LIMIT 1""",
        (email.strip(), org_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def has_any_membership(user_id: int) -> bool:
    """True if the user has any membership at all (active or inactive)."""
    conn = get_db()
    row = conn.execute(
        "SELECT 1 FROM org_memberships WHERE user_id = ? LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return row is not None


def create_organization(
    name: str,
    slug: str,
    simpro_company_id: Optional[int] = None,
    plan_name: str = "free",
    monthly_token_limit: int = 10000000,
) -> Dict[str, Any]:
    conn = get_db()
    cursor = conn.execute(
        """INSERT INTO organizations
           (name, slug, simpro_company_id, plan_name, monthly_token_limit, is_active, use_platform_llm)
           VALUES (?, ?, ?, ?, ?, 1, 1)""",
        (name, slug, simpro_company_id, plan_name, monthly_token_limit),
    )
    org_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {"id": org_id, "name": name, "slug": slug, "plan_name": plan_name}


def create_org_membership(user_id: int, org_id: int, role: str = "member", role_id: Optional[int] = None) -> Dict[str, Any]:
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO org_memberships (user_id, org_id, role, role_id, is_active) VALUES (?, ?, ?, ?, 1)",
        (user_id, org_id, role, role_id),
    )
    membership_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {"id": membership_id, "user_id": user_id, "org_id": org_id, "role": role, "role_id": role_id}


# ═══════════════════════════════════════════════════════════════════════════
# Phase 4: Custom RBAC — Roles + Permissions
# ═══════════════════════════════════════════════════════════════════════════

# All valid operations across all agents
AGENT_OPERATIONS = {
    "schedule":       ["query", "create", "update", "delete", "lock", "unlock"],
    "invoice":        ["query", "create", "update", "delete"],
    "workorder":      ["query", "create", "update", "delete"],
    "purchase_order": ["query", "create", "update", "delete"],
}


def create_org_role(org_id: int, name: str, is_system: bool = False) -> Dict[str, Any]:
    """Create a new role for an org. Returns the created role dict."""
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO org_roles (org_id, name, is_system) VALUES (?, ?, ?)",
        (org_id, name.strip(), int(is_system)),
    )
    role_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {"id": role_id, "org_id": org_id, "name": name.strip(), "is_system": int(is_system)}


def get_org_roles(org_id: int) -> List[Dict[str, Any]]:
    """List all roles for an org, with user count per role."""
    conn = get_db()
    rows = conn.execute("""
        SELECT r.*, COUNT(om.user_id) AS user_count
        FROM org_roles r
        LEFT JOIN org_memberships om ON om.role_id = r.id
        WHERE r.org_id = ?
        GROUP BY r.id
        ORDER BY r.is_system DESC, r.created_at ASC
    """, (org_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_role(role_id: int) -> Optional[Dict[str, Any]]:
    """Get a single role by ID."""
    conn = get_db()
    row = conn.execute("SELECT * FROM org_roles WHERE id = ?", (role_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_org_role(role_id: int) -> bool:
    """
    Delete a role. Returns False (and does not delete) if:
    - is_system=1 (built-in admin role)
    - any users are still assigned to it
    """
    conn = get_db()
    role = conn.execute("SELECT * FROM org_roles WHERE id = ?", (role_id,)).fetchone()
    if not role:
        conn.close()
        return False
    if role["is_system"]:
        conn.close()
        return False
    user_count = conn.execute(
        "SELECT COUNT(*) FROM org_memberships WHERE role_id = ?", (role_id,)
    ).fetchone()[0]
    if user_count > 0:
        conn.close()
        return False
    conn.execute("DELETE FROM role_agent_permissions WHERE role_id = ?", (role_id,))
    conn.execute("DELETE FROM org_roles WHERE id = ?", (role_id,))
    conn.commit()
    conn.close()
    return True


def set_role_permissions(role_id: int, permissions: List[Dict[str, Any]]) -> None:
    """
    Replace all permissions for a role.
    permissions: [{"agent_name": "schedule", "operation": "create", "is_allowed": True}, ...]
    """
    conn = get_db()
    conn.execute("DELETE FROM role_agent_permissions WHERE role_id = ?", (role_id,))
    for p in permissions:
        conn.execute(
            """INSERT INTO role_agent_permissions (role_id, agent_name, operation, is_allowed)
               VALUES (?, ?, ?, ?)""",
            (role_id, p["agent_name"], p["operation"], int(p.get("is_allowed", True))),
        )
    conn.commit()
    conn.close()


def get_role_permissions(role_id: int) -> List[Dict[str, Any]]:
    """Get all permission rows for a role."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM role_agent_permissions WHERE role_id = ?", (role_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def seed_admin_role_permissions(role_id: int) -> None:
    """Grant all operations across all agents to the admin role."""
    permissions = [
        {"agent_name": agent, "operation": op, "is_allowed": True}
        for agent, ops in AGENT_OPERATIONS.items()
        for op in ops
    ]
    set_role_permissions(role_id, permissions)


def seed_default_member_permissions(role_id: int) -> None:
    """Grant query-only for all agents to a default member role."""
    permissions = [
        {"agent_name": agent, "operation": "query", "is_allowed": True}
        for agent in AGENT_OPERATIONS
    ]
    set_role_permissions(role_id, permissions)


def ensure_admin_role(org_id: int) -> Dict[str, Any]:
    """
    Ensure an org has a system 'admin' role with full permissions.
    - If a system role already exists: returns it (no-op).
    - Else: creates 'admin' (is_system=1), seeds full permissions, and backfills
      any org_memberships with role='admin' or NULL role_id to point at it.
    Idempotent.
    """
    conn = get_db()
    existing = conn.execute(
        "SELECT * FROM org_roles WHERE org_id = ? AND is_system = 1 LIMIT 1",
        (org_id,),
    ).fetchone()
    conn.close()

    if existing:
        return dict(existing)

    admin_role = create_org_role(org_id, "admin", is_system=True)
    seed_admin_role_permissions(admin_role["id"])

    # Backfill memberships: any user marked role='admin' or with no role_id should
    # now point at the new admin role.
    conn = get_db()
    conn.execute(
        """UPDATE org_memberships
           SET role_id = ?
           WHERE org_id = ?
             AND (role_id IS NULL OR role = 'admin')""",
        (admin_role["id"], org_id),
    )
    conn.commit()
    conn.close()
    return admin_role


def seed_org_roles(org_id: int, admin_user_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Create the built-in 'admin' and 'member' roles for a new org.
    Optionally link admin_user_id to the admin role.
    Returns {"admin_role_id": int, "member_role_id": int}.
    """
    admin_role = create_org_role(org_id, "admin", is_system=True)
    seed_admin_role_permissions(admin_role["id"])

    member_role = create_org_role(org_id, "member", is_system=False)
    seed_default_member_permissions(member_role["id"])

    if admin_user_id is not None:
        conn = get_db()
        conn.execute(
            "UPDATE org_memberships SET role_id = ? WHERE user_id = ? AND org_id = ?",
            (admin_role["id"], admin_user_id, org_id),
        )
        conn.commit()
        conn.close()

    return {"admin_role_id": admin_role["id"], "member_role_id": member_role["id"]}


# ═══════════════════════════════════════════════════════════════════════════
# Phase 4: Member management
# ═══════════════════════════════════════════════════════════════════════════

def get_org_members(org_id: int) -> List[Dict[str, Any]]:
    """List all members of an org (active + inactive) with role + deactivation info."""
    conn = get_db()
    rows = conn.execute("""
        SELECT
            u.id,
            u.email,
            u.name,
            om.role,
            om.role_id,
            COALESCE(om.is_active, 1) AS is_active,
            CAST(om.deactivated_at + INTERVAL '5 hours 30 minutes' AS TIMESTAMP) AS deactivated_at_ist,
            om.deactivated_by_user_id,
            db.email AS deactivated_by_email,
            r.name AS role_name,
            r.is_system AS role_is_system,
            CAST(om.created_at + INTERVAL '5 hours 30 minutes' AS TIMESTAMP) AS joined_at_ist
        FROM users u
        JOIN org_memberships om ON om.user_id = u.id
        LEFT JOIN org_roles r ON r.id = om.role_id
        LEFT JOIN users db ON db.id = om.deactivated_by_user_id
        WHERE om.org_id = ?
        ORDER BY COALESCE(om.is_active, 1) DESC, om.created_at ASC
    """, (org_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_membership(user_id: int, org_id: int) -> Optional[Dict[str, Any]]:
    """Fetch a single membership row including is_active."""
    conn = get_db()
    row = conn.execute(
        """SELECT user_id, org_id, role, role_id,
                  COALESCE(is_active, 1) AS is_active,
                  deactivated_at, deactivated_by_user_id
           FROM org_memberships
           WHERE user_id = ? AND org_id = ?""",
        (user_id, org_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def set_membership_active(
    user_id: int,
    org_id: int,
    is_active: bool,
    actor_user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Atomically activate or deactivate a membership.

    On deactivate: enforces last-active-admin guard. Stamps deactivated_at + actor.
    On activate:   clears audit fields. If role_id points to a deleted role,
                   falls back to the system 'admin' role if their text role is
                   'admin', otherwise to any non-system role; reports the fallback
                   in the returned dict.

    Returns: {"updated": bool, "role_fallback": Optional[str], "role_id": int}
    Raises LastAdminError if deactivation would leave zero active admins.
    """
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        membership = conn.execute(
            """SELECT role, role_id, COALESCE(is_active, 1) AS is_active
               FROM org_memberships WHERE user_id = ? AND org_id = ?""",
            (user_id, org_id),
        ).fetchone()
        if membership is None:
            conn.execute("ROLLBACK")
            return {"updated": False, "role_fallback": None, "role_id": None}

        already_active = bool(membership["is_active"])
        if already_active == bool(is_active):
            # No state change needed.
            conn.execute("ROLLBACK")
            return {"updated": False, "role_fallback": None, "role_id": membership["role_id"]}

        if not is_active:
            # Deactivating: block if this is the only active admin.
            if membership["role"] == "admin":
                active_admin_count = conn.execute(
                    """SELECT COUNT(*) FROM org_memberships
                       WHERE org_id = ? AND role = 'admin' AND COALESCE(is_active, 1) = 1""",
                    (org_id,),
                ).fetchone()[0]
                if active_admin_count <= 1:
                    conn.execute("ROLLBACK")
                    raise LastAdminError(
                        "Cannot deactivate the last active admin. "
                        "Promote another user to admin first."
                    )
            conn.execute(
                """UPDATE org_memberships
                   SET is_active = 0,
                       deactivated_at = CURRENT_TIMESTAMP,
                       deactivated_by_user_id = ?
                   WHERE user_id = ? AND org_id = ?""",
                (actor_user_id, user_id, org_id),
            )
            conn.execute("COMMIT")
            return {"updated": True, "role_fallback": None, "role_id": membership["role_id"]}

        # Reactivating: check role still exists; fall back if needed.
        role_fallback = None
        new_role_id = membership["role_id"]
        new_role_text = membership["role"]
        if new_role_id is not None:
            role_row = conn.execute(
                "SELECT id, name, is_system FROM org_roles WHERE id = ? AND org_id = ?",
                (new_role_id, org_id),
            ).fetchone()
            if role_row is None:
                # Role was deleted while inactive. Fall back to a non-system member role.
                fallback_row = conn.execute(
                    """SELECT id, name FROM org_roles
                       WHERE org_id = ? AND is_system = 0
                       ORDER BY id ASC LIMIT 1""",
                    (org_id,),
                ).fetchone()
                if fallback_row:
                    new_role_id = fallback_row["id"]
                    new_role_text = "member"
                    role_fallback = fallback_row["name"]
                else:
                    # No fallback available — leave role_id null, role text 'member'.
                    new_role_id = None
                    new_role_text = "member"
                    role_fallback = "member"

        conn.execute(
            """UPDATE org_memberships
               SET is_active = 1,
                   deactivated_at = NULL,
                   deactivated_by_user_id = NULL,
                   role_id = ?,
                   role = ?
               WHERE user_id = ? AND org_id = ?""",
            (new_role_id, new_role_text, user_id, org_id),
        )
        conn.execute("COMMIT")
        return {"updated": True, "role_fallback": role_fallback, "role_id": new_role_id}
    except LastAdminError:
        raise
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def assign_member_role(user_id: int, org_id: int, role_id: int, role_name: str) -> bool:
    """Update a member's role_id and role text in org_memberships."""
    conn = get_db()
    cursor = conn.execute(
        "UPDATE org_memberships SET role_id = ?, role = ? WHERE user_id = ? AND org_id = ?",
        (role_id, role_name, user_id, org_id),
    )
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


class LastAdminError(Exception):
    """Raised when an action would leave the org with zero admins."""


def assign_member_role_atomic(
    user_id: int, org_id: int, new_role_id: int, new_role_name: str,
) -> bool:
    """
    Atomically change a member's role with last-active-admin protection.
    Raises LastAdminError if demoting this user would leave the org without an
    active admin.
    Returns True if the membership was updated.
    """
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            """SELECT role, COALESCE(is_active, 1) AS is_active
               FROM org_memberships WHERE user_id = ? AND org_id = ?""",
            (user_id, org_id),
        ).fetchone()
        if current is None:
            conn.execute("ROLLBACK")
            return False

        is_currently_admin = current["role"] == "admin"
        is_new_role_admin = new_role_name == "admin"

        # Only protect against losing the last admin if this user is currently
        # contributing to the active-admin count.
        if is_currently_admin and not is_new_role_admin and current["is_active"]:
            admin_count = conn.execute(
                """SELECT COUNT(*) FROM org_memberships
                   WHERE org_id = ? AND role = 'admin' AND COALESCE(is_active, 1) = 1""",
                (org_id,),
            ).fetchone()[0]
            if admin_count <= 1:
                conn.execute("ROLLBACK")
                raise LastAdminError(
                    "Cannot demote the last admin. Promote another user to admin first."
                )

        cursor = conn.execute(
            "UPDATE org_memberships SET role_id = ?, role = ? WHERE user_id = ? AND org_id = ?",
            (new_role_id, new_role_name, user_id, org_id),
        )
        conn.execute("COMMIT")
        return cursor.rowcount > 0
    except LastAdminError:
        raise
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def delete_user_atomic(user_id: int, org_id: int) -> bool:
    """
    Atomically delete a user (full account + all memberships) with last-admin protection
    scoped to the given org. Raises LastAdminError if this user is the last admin of the org.
    """
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        membership = conn.execute(
            "SELECT role FROM org_memberships WHERE user_id = ? AND org_id = ?",
            (user_id, org_id),
        ).fetchone()
        if membership is None:
            conn.execute("ROLLBACK")
            return False

        if membership["role"] == "admin":
            admin_count = conn.execute(
                "SELECT COUNT(*) FROM org_memberships WHERE org_id = ? AND role = 'admin'",
                (org_id,),
            ).fetchone()[0]
            if admin_count <= 1:
                conn.execute("ROLLBACK")
                raise LastAdminError(
                    "Cannot delete the last admin. Promote another user to admin first."
                )

        conn.execute("DELETE FROM org_memberships WHERE user_id = ?", (user_id,))
        cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.execute("COMMIT")
        return cursor.rowcount > 0
    except LastAdminError:
        raise
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


# Reserved-name regex: blocks "admin", "Admin", "ADMIN", "admin-2", "admin_3", "admin 4", etc.
# but allows real role names like "Site Admin" or "Admin Assistant" that just contain "admin"
# as a word.
_ADMIN_NAME_PATTERN = None
def is_reserved_role_name(name: str) -> bool:
    import re as _re
    global _ADMIN_NAME_PATTERN
    if _ADMIN_NAME_PATTERN is None:
        _ADMIN_NAME_PATTERN = _re.compile(r"^[\s_\-]*admin[\s_\-]*\d*$", _re.IGNORECASE)
    return bool(_ADMIN_NAME_PATTERN.match((name or "").strip()))


def remove_org_member(user_id: int, org_id: int) -> bool:
    """Remove a user from an org."""
    conn = get_db()
    cursor = conn.execute(
        "DELETE FROM org_memberships WHERE user_id = ? AND org_id = ?",
        (user_id, org_id),
    )
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def get_org_admin_count(org_id: int) -> int:
    """Count ACTIVE admins in an org."""
    conn = get_db()
    count = conn.execute(
        """SELECT COUNT(*) FROM org_memberships
           WHERE org_id = ? AND role = 'admin' AND COALESCE(is_active, 1) = 1""",
        (org_id,),
    ).fetchone()[0]
    conn.close()
    return count


def create_org_member_user(email: str, name: str, org_id: int, role: str = "member", role_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Create a new user and add them to an org with the given role.
    Returns {user_id, email, name, role, temp_password}.
    """
    import secrets
    import string
    alphabet = string.ascii_letters + string.digits
    temp_password = "".join(secrets.choice(alphabet) for _ in range(16))

    from passlib.context import CryptContext
    _pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
    hashed = _pwd.hash(temp_password)

    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO users (email, hashed_password, name) VALUES (?, ?, ?)",
        (email.lower().strip(), hashed, name.strip() or email.split("@")[0]),
    )
    user_id = cursor.lastrowid
    conn.execute(
        "INSERT INTO org_memberships (user_id, org_id, role, role_id, is_active) VALUES (?, ?, ?, ?, 1)",
        (user_id, org_id, role, role_id),
    )
    conn.commit()
    conn.close()
    return {
        "user_id": user_id,
        "email": email.lower().strip(),
        "name": name.strip() or email.split("@")[0],
        "role": role,
        "temp_password": temp_password,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Phase 4: Operation permission check
# ═══════════════════════════════════════════════════════════════════════════

def is_operation_allowed_for_user(user_id: int, org_id: int, agent_name: str, operation: str) -> bool:
    """
    Check if user's role permits this agent+operation combo.
    - Returns False if the membership is deactivated.
    - Falls back to True if no role_id set (backwards compat for existing users).
    - Falls back to True if no permission row exists (allow by default).
    - 'admin' text role always passes (for users created before Phase 4 roles).
    """
    conn = get_db()
    row = conn.execute(
        """SELECT role, role_id, COALESCE(is_active, 1) AS is_active
           FROM org_memberships WHERE user_id = ? AND org_id = ?""",
        (user_id, org_id),
    ).fetchone()
    conn.close()

    if not row:
        return True  # not in org — let other checks handle this

    # Deactivated membership: deny all operations.
    if not row["is_active"]:
        return False

    # Legacy admin text role → always allowed
    if row["role"] == "admin":
        return True

    role_id = row["role_id"]
    if role_id is None:
        return True  # no role assigned yet → backwards compat

    # Check permission row
    conn = get_db()
    perm = conn.execute(
        """SELECT is_allowed FROM role_agent_permissions
           WHERE role_id = ? AND agent_name = ? AND operation = ?""",
        (role_id, agent_name, operation),
    ).fetchone()
    conn.close()

    if perm is None:
        return True  # no explicit row → allow by default
    return bool(perm["is_allowed"])


# ═══════════════════════════════════════════════════════════════════════════
# Agent plan queries
# ═══════════════════════════════════════════════════════════════════════════

def get_org_agent_plans(org_id: int) -> List[Dict[str, Any]]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM org_agent_plans WHERE org_id = ?", (org_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def is_agent_enabled_for_org(org_id: int, agent_name: str) -> bool:
    """Check if a specific agent is enabled for an org. Defaults to True if no row exists."""
    conn = get_db()
    row = conn.execute(
        "SELECT is_enabled FROM org_agent_plans WHERE org_id = ? AND agent_name = ?",
        (org_id, agent_name),
    ).fetchone()
    conn.close()
    if row is None:
        return True  # no explicit restriction = allowed
    return bool(row["is_enabled"])


def get_org_agent_plan(org_id: int, agent_name: str) -> Optional[Dict[str, Any]]:
    """Get a single org_agent_plans row for (org_id, agent_name). Returns None if not found."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM org_agent_plans WHERE org_id = ? AND agent_name = ?",
        (org_id, agent_name),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def set_org_agent_plan(
    org_id: int,
    agent_name: str,
    is_enabled: bool = True,
    monthly_token_limit: Optional[int] = None,
) -> None:
    conn = get_db()
    conn.execute(
        """INSERT INTO org_agent_plans (org_id, agent_name, is_enabled, monthly_token_limit)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(org_id, agent_name) DO UPDATE SET
               is_enabled = excluded.is_enabled,
               monthly_token_limit = excluded.monthly_token_limit""",
        (org_id, agent_name, int(is_enabled), monthly_token_limit),
    )
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Usage tracking queries
# ═══════════════════════════════════════════════════════════════════════════

def get_monthly_usage(org_id: int, year: int, month: int) -> Dict[str, int]:
    """Get total token usage for an org in a specific month."""
    conn = get_db()
    start = f"{year}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1}-01-01"
    else:
        end = f"{year}-{month + 1:02d}-01"

    row = conn.execute("""
        SELECT
            COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
            COALESCE(SUM(output_tokens), 0) AS total_output_tokens
        FROM usage_records
        WHERE org_id = ?
          AND CAST(created_at + INTERVAL '5 hours 30 minutes' AS TIMESTAMP) >= ?
          AND CAST(created_at + INTERVAL '5 hours 30 minutes' AS TIMESTAMP) < ?
    """, (org_id, start, end)).fetchone()
    conn.close()
    return {
        "total_input_tokens": row["total_input_tokens"],
        "total_output_tokens": row["total_output_tokens"],
    }


def get_monthly_agent_usage(org_id: int, agent_name: str, year: int, month: int) -> Dict[str, int]:
    """Get total token usage for a specific agent within a month for this org."""
    conn = get_db()
    start = f"{year}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1}-01-01"
    else:
        end = f"{year}-{month + 1:02d}-01"

    row = conn.execute("""
        SELECT
            COALESCE(SUM(input_tokens), 0)  AS total_input_tokens,
            COALESCE(SUM(output_tokens), 0) AS total_output_tokens
        FROM usage_records
        WHERE org_id = ?
          AND agent_name = ?
          AND CAST(created_at + INTERVAL '5 hours 30 minutes' AS TIMESTAMP) >= ?
          AND CAST(created_at + INTERVAL '5 hours 30 minutes' AS TIMESTAMP) < ?
    """, (org_id, agent_name, start, end)).fetchone()
    conn.close()
    return {
        "total_input_tokens": row["total_input_tokens"],
        "total_output_tokens": row["total_output_tokens"],
    }


def get_usage_analytics(
    org_id: Optional[int] = None,
    days: int = 7,
) -> List[Dict[str, Any]]:
    """
    Daily usage analytics: calls, tokens, cost, avg latency, clarification rounds.

    Returns one row per (day, agent_name) with aggregated metrics.
    Useful for dashboards and performance tracking over time.
    """
    conn = get_db()
    # Use IST (+05:30) so dates align with local time, not UTC
    query = """
        SELECT
            CAST(created_at + INTERVAL '5 hours 30 minutes' AS DATE) AS day,
            agent_name,
            COUNT(*)                    AS total_calls,
            SUM(input_tokens)           AS total_input_tokens,
            SUM(output_tokens)          AS total_output_tokens,
            ROUND(SUM(estimated_cost_usd), 6) AS total_cost_usd,
            ROUND(AVG(duration_ms))     AS avg_duration_ms,
            MAX(duration_ms)            AS max_duration_ms,
            MIN(CASE WHEN duration_ms > 0 THEN duration_ms END) AS min_duration_ms,
            SUM(clarification_rounds)   AS total_clarification_rounds
        FROM usage_records
        WHERE created_at >= CAST(CURRENT_TIMESTAMP + INTERVAL '5 hours 30 minutes' + CAST(? AS INTERVAL) AS DATE)
    """
    params: list = [f"-{days} days"]

    if org_id is not None:
        query += " AND org_id = ?"
        params.append(org_id)

    query += " GROUP BY day, agent_name ORDER BY day, agent_name"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_usage_logs(
    org_id: Optional[int] = None,
    days: int = 1,
    agent_name: Optional[str] = None,
    user_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Individual request-level usage logs with pagination.

    Returns each usage_record row so you can see per-request
    duration, tokens, cost, model, and timestamp.
    """
    conn = get_db()
    query = """
        SELECT
            ur.id,
            ur.org_id,
            ur.user_id,
            u.email  AS user_email,
            u.name   AS user_name,
            ur.agent_name,
            ur.input_tokens,
            ur.output_tokens,
            (ur.input_tokens + ur.output_tokens) AS total_tokens,
            ur.model_name,
            ROUND(ur.estimated_cost_usd, 6) AS estimated_cost_usd,
            ur.request_path,
            ur.duration_ms,
            ur.clarification_rounds,
            CAST(ur.created_at + INTERVAL '5 hours 30 minutes' AS TIMESTAMP) AS created_at_ist
        FROM usage_records ur
        LEFT JOIN users u ON u.id = ur.user_id
        WHERE ur.created_at >= CAST(CURRENT_TIMESTAMP + INTERVAL '5 hours 30 minutes' + CAST(? AS INTERVAL) AS DATE)
    """
    params: list = [f"-{days} days"]

    if org_id is not None:
        query += " AND ur.org_id = ?"
        params.append(org_id)

    if agent_name:
        query += " AND ur.agent_name = ?"
        params.append(agent_name)

    if user_id is not None:
        query += " AND ur.user_id = ?"
        params.append(user_id)

    # Count total before pagination
    count_query = f"SELECT COUNT(*) FROM ({query})"
    total = conn.execute(count_query, params).fetchone()[0]

    query += " ORDER BY ur.created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "records": [dict(r) for r in rows],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Decision Journal queries
# ═══════════════════════════════════════════════════════════════════════════

def get_journal_entries(
    org_id: Optional[int] = None,
    days: int = 7,
    dimension: Optional[str] = None,
    decision_type: Optional[str] = None,
    outcome: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """Paginated decision journal entries with filters."""
    conn = get_db()
    query = """
        SELECT *,
            CAST(created_at + INTERVAL '5 hours 30 minutes' AS TIMESTAMP) AS created_at_ist
        FROM decision_journal
        WHERE created_at >= CAST(CURRENT_TIMESTAMP + INTERVAL '5 hours 30 minutes' + CAST(? AS INTERVAL) AS DATE)
    """
    params: list = [f"-{days} days"]

    if org_id is not None:
        query += " AND org_id = ?"
        params.append(org_id)
    if dimension:
        query += " AND dimension = ?"
        params.append(dimension)
    if decision_type:
        query += " AND decision_type = ?"
        params.append(decision_type)
    if outcome:
        query += " AND outcome = ?"
        params.append(outcome)

    count_query = f"SELECT COUNT(*) FROM ({query})"
    total = conn.execute(count_query, params).fetchone()[0]

    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return {"total": total, "limit": limit, "offset": offset, "entries": [dict(r) for r in rows]}


def get_capability_radar_data(
    org_id: Optional[int] = None,
    days: int = 30,
) -> List[Dict[str, Any]]:
    """
    Aggregate decision outcomes by dimension for capability radar.
    Returns one row per (dimension, outcome) with counts and averages.
    """
    conn = get_db()
    query = """
        SELECT
            dimension,
            outcome,
            COUNT(*) AS count,
            ROUND(AVG(confidence), 3) AS avg_confidence,
            ROUND(AVG(duration_ms)) AS avg_duration_ms
        FROM decision_journal
        WHERE created_at >= CAST(CURRENT_TIMESTAMP + INTERVAL '5 hours 30 minutes' + CAST(? AS INTERVAL) AS DATE)
    """
    params: list = [f"-{days} days"]

    if org_id is not None:
        query += " AND org_id = ?"
        params.append(org_id)

    query += " GROUP BY dimension, outcome ORDER BY dimension, outcome"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def log_usage(
    org_id: int,
    user_id: int,
    agent_name: str = "chat",
    input_tokens: int = 0,
    output_tokens: int = 0,
    model_name: str = "",
    estimated_cost_usd: float = 0.0,
    request_path: str = "",
    duration_ms: int = 0,
    clarification_rounds: int = 0,
) -> None:
    """Append a usage record. Fire-and-forget — errors are logged, not raised."""
    try:
        # Auto-compute cost if caller didn't provide one
        if estimated_cost_usd == 0.0 and (input_tokens or output_tokens):
            estimated_cost_usd = estimate_cost(model_name, input_tokens, output_tokens)

        conn = get_db()
        conn.execute(
            """INSERT INTO usage_records
               (org_id, user_id, agent_name, input_tokens, output_tokens,
                model_name, estimated_cost_usd, request_path,
                duration_ms, clarification_rounds)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (org_id, user_id, agent_name, input_tokens, output_tokens,
             model_name, estimated_cost_usd, request_path,
             duration_ms, clarification_rounds),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # usage logging should never break the request


# ═══════════════════════════════════════════════════════════════════════════
# Cost estimation
# ═══════════════════════════════════════════════════════════════════════════
# Prices are USD per 1M tokens. Update when pricing changes.
# Source: https://openai.com/api/pricing / https://docs.anthropic.com/en/docs/about-claude/models

_MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # OpenAI  (per 1M tokens)
    "gpt-4.1-mini":              {"input": 0.40,  "output": 1.60},
    "gpt-4.1-mini-2025-04-14":   {"input": 0.40,  "output": 1.60},
    "gpt-4.1":                   {"input": 2.00,  "output": 8.00},
    "gpt-4.1-nano":              {"input": 0.10,  "output": 0.40},
    "gpt-4o":                    {"input": 2.50,  "output": 10.00},
    "gpt-4o-mini":               {"input": 0.15,  "output": 0.60},
    "gpt-4-turbo":               {"input": 10.00, "output": 30.00},
    "o3-mini":                   {"input": 1.10,  "output": 4.40},
    # Anthropic  (per 1M tokens)
    "claude-sonnet-4-20250514":  {"input": 3.00,  "output": 15.00},
    "claude-haiku-4-5-20251001": {"input": 0.80,  "output": 4.00},
    "claude-opus-4-6":           {"input": 15.00, "output": 75.00},
    # Google Gemini
    "gemini-2.0-flash":          {"input": 0.10,  "output": 0.40},
    "gemini-2.5-pro-preview":    {"input": 1.25,  "output": 10.00},
}

# Fallback: if model not found, assume gpt-4.1-mini pricing
_DEFAULT_PRICING = {"input": 0.40, "output": 1.60}


def estimate_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """
    Estimate cost in USD from model name and token counts.

    Matches model names with prefix matching so "gpt-4.1-mini-2025-04-14"
    still matches the "gpt-4.1-mini" pricing entry.
    """
    pricing = _MODEL_PRICING.get(model_name)

    # Try prefix matching if exact match fails
    if not pricing:
        for key, val in _MODEL_PRICING.items():
            if model_name.startswith(key) or key.startswith(model_name):
                pricing = val
                break

    if not pricing:
        pricing = _DEFAULT_PRICING

    cost = (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000
    return round(cost, 6)


# ═══════════════════════════════════════════════════════════════════════════
# Phase 5: Per-tenant SOP overrides
# ═══════════════════════════════════════════════════════════════════════════

def get_org_sop(org_id: int, agent_name: str) -> Optional[Dict[str, Any]]:
    """Get custom SOP for an org+agent. Returns None if not set (use default)."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM org_sop_overrides WHERE org_id = ? AND agent_name = ?",
        (org_id, agent_name),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def set_org_sop(
    org_id: int,
    agent_name: str,
    sop_text: str,
    original_filename: str = "",
    uploaded_by_user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Upsert a custom SOP for an org+agent. Returns the stored row."""
    conn = get_db()
    conn.execute(
        """INSERT INTO org_sop_overrides
               (org_id, agent_name, sop_text, original_filename, uploaded_by_user_id)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(org_id, agent_name) DO UPDATE SET
               sop_text = excluded.sop_text,
               original_filename = excluded.original_filename,
               uploaded_by_user_id = excluded.uploaded_by_user_id,
               uploaded_at = CURRENT_TIMESTAMP""",
        (org_id, agent_name, sop_text, original_filename, uploaded_by_user_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM org_sop_overrides WHERE org_id = ? AND agent_name = ?",
        (org_id, agent_name),
    ).fetchone()
    conn.close()
    return dict(row)


def delete_org_sop(org_id: int, agent_name: str) -> bool:
    """Delete custom SOP override (reverts agent to default). Returns True if row existed."""
    conn = get_db()
    cursor = conn.execute(
        "DELETE FROM org_sop_overrides WHERE org_id = ? AND agent_name = ?",
        (org_id, agent_name),
    )
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def get_all_org_sops(org_id: int) -> List[Dict[str, Any]]:
    """List all custom SOP overrides for an org."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM org_sop_overrides WHERE org_id = ? ORDER BY agent_name",
        (org_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════
# Refresh Token & Blacklist helpers (Phase 8 BE-5)
# ═══════════════════════════════════════════════════════════════════════════

def add_refresh_token(jti: str, user_id: int, expires_at: datetime, parent_jti: Optional[str] = None) -> None:
    conn = get_db()
    expires_str = expires_at.isoformat()
    conn.execute(
        """INSERT INTO refresh_tokens (jti, user_id, expires_at, parent_jti)
           VALUES (?, ?, ?, ?)""",
        (jti, user_id, expires_str, parent_jti)
    )
    conn.commit()
    conn.close()


def get_refresh_token(jti: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM refresh_tokens WHERE jti = ?", (jti,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def mark_refresh_token_used(jti: str) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE refresh_tokens SET is_used = 1 WHERE jti = ?", (jti,)
    )
    conn.commit()
    conn.close()


def revoke_refresh_token(jti: str) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE refresh_tokens SET is_revoked = 1 WHERE jti = ?", (jti,)
    )
    conn.commit()
    conn.close()


def revoke_all_user_refresh_tokens(user_id: int) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE refresh_tokens SET is_revoked = 1 WHERE user_id = ?", (user_id,)
    )
    conn.commit()
    conn.close()


def blacklist_access_token(jti: str, expires_at: datetime) -> None:
    conn = get_db()
    expires_str = expires_at.isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO token_blacklist (token_jti, expires_at) VALUES (?, ?)",
        (jti, expires_str)
    )
    conn.commit()
    conn.close()


def is_token_blacklisted(jti: str) -> bool:
    conn = get_db()
    row = conn.execute(
        "SELECT 1 FROM token_blacklist WHERE token_jti = ?", (jti,)
    ).fetchone()
    conn.close()
    return row is not None


# Auto-init on import
init_db()
