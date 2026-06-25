# backend/auth/auth.py
# Password hashing + JWT token creation / verification
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext

from auth.database import (
    get_user_by_email,
    get_user_org,
    get_membership,
    has_any_membership,
    is_token_blacklisted,
)


# ---- Password policy ----
PASSWORD_POLICY_MESSAGE = (
    "Password must be at least 8 characters and include "
    "an uppercase letter, a lowercase letter, a digit, and a special character."
)


def validate_password_policy(password: str) -> Optional[str]:
    """Return None if password is valid, else an error message."""
    if not password or len(password) < 8:
        return PASSWORD_POLICY_MESSAGE
    if not re.search(r"[A-Z]", password):
        return PASSWORD_POLICY_MESSAGE
    if not re.search(r"[a-z]", password):
        return PASSWORD_POLICY_MESSAGE
    if not re.search(r"\d", password):
        return PASSWORD_POLICY_MESSAGE
    if not re.search(r"[^A-Za-z0-9]", password):
        return PASSWORD_POLICY_MESSAGE
    return None

# ---- Config ----
JWT_SECRET = os.getenv("JWT_SECRET", "optificial-dev-secret-change-in-prod")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7

# ---- Password hashing ----
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ---- JWT ----
def create_access_token(data: Dict[str, Any], expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    to_encode["exp"] = expire
    if "jti" not in to_encode:
        to_encode["jti"] = str(uuid.uuid4())
    to_encode["type"] = "access"
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(data: Dict[str, Any], expires_days: int = REFRESH_TOKEN_EXPIRE_DAYS) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=expires_days)
    to_encode["exp"] = expire
    if "jti" not in to_encode:
        to_encode["jti"] = str(uuid.uuid4())
    to_encode["type"] = "refresh"
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


# ---- FastAPI dependency ----
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Dict[str, Any]:
    """FastAPI dependency — extracts and validates the JWT from cookies or Authorization header."""
    token: Optional[str] = None

    # 1. Try to read from cookies (key: 'token' or 'access_token')
    if request.cookies:
        token = request.cookies.get("token") or request.cookies.get("access_token")

    # 2. Fallback to Authorization Bearer header
    if not token and credentials:
        token = credentials.credentials

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated — missing token in cookie or header",
        )

    try:
        payload = decode_access_token(token)
        jti: Optional[str] = payload.get("jti")
        email: Optional[str] = payload.get("sub")
        token_type: Optional[str] = payload.get("type", "access")

        if token_type != "access":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

        if email is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

        if jti and is_token_blacklisted(jti):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked")

    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user = get_user_by_email(email)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    org = None
    token_org_id = payload.get("oid")

    if token_org_id:
        from auth.database import get_org_by_id
        org = get_org_by_id(token_org_id)
        if org:
            membership = get_membership(user["id"], org["id"])
            if membership:
                org["role"] = membership.get("role")
            else:
                org = None
    else:
        # Fallback: get_user_org returns the first ACTIVE membership.
        org = get_user_org(user["id"])

    # If the user has memberships but none are active, they're deactivated everywhere.
    if org is None and has_any_membership(user["id"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your access has been deactivated. Contact your administrator.",
        )

    # Block login if the org has been deactivated by superadmin
    if org and not org.get("is_active", 1):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your organization has been deactivated. Please contact support.",
        )

    # Belt-and-suspenders: explicitly verify the membership row is active.
    if org:
        membership = get_membership(user["id"], org["id"])
        if membership is None or not membership.get("is_active", 1):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your access to this organisation has been deactivated. Contact your administrator.",
            )

    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "org_id": org["id"] if org else None,
        "org_name": org["name"] if org else None,
        "simpro_company_id": org["simpro_company_id"] if org else None,
        "role": org.get("role", "member") if org else "member",
        "plan_tier": org.get("plan_name", "starter") if org else "starter",
    }
