"""
One-time migration: create orgs + memberships + agent plans for existing users.

Run from the backend directory:
    python -m scripts.migrate_existing_users

Safe to run multiple times — skips users that already have an org.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Ensure backend root is on sys.path
backend_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_root))

from auth.database import (
    get_db,
    get_user_org,
    create_organization,
    create_org_membership,
    set_org_agent_plan,
)
from agents.registry import AGENT_REGISTRY


def _slug_from_email(email: str) -> str:
    domain = email.split("@")[-1] if "@" in email else email
    slug = re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")
    return slug or "default"


def migrate():
    conn = get_db()
    users = conn.execute("SELECT id, email, name FROM users").fetchall()
    conn.close()

    migrated = 0
    skipped = 0

    for user in users:
        user = dict(user)
        existing_org = get_user_org(user["id"])
        if existing_org:
            skipped += 1
            continue

        slug = f"{_slug_from_email(user['email'])}-{user['id']}"
        org_name = f"{user['name'] or user['email'].split('@')[0]}'s Organization"

        org = create_organization(
            name=org_name,
            slug=slug,
            plan_name="trial",
            monthly_token_limit=10000000,
        )

        create_org_membership(user["id"], org["id"], role="admin")

        for agent_name in AGENT_REGISTRY:
            set_org_agent_plan(org["id"], agent_name, is_enabled=True)

        migrated += 1
        print(f"  + User {user['id']} ({user['email']}) → Org {org['id']} ({slug})")

    print(f"\nDone. Migrated: {migrated}, Skipped (already had org): {skipped}")


if __name__ == "__main__":
    print("=== Migrating existing users to multi-tenant model ===\n")
    migrate()
