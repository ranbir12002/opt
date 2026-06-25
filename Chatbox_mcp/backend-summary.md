# Backend Summary

## Onboarding and user setup
- The backend uses FastAPI and SQLite for auth and tenant data.
- `backend/api/auth_routes.py` handles registration and login.
- `backend/auth/auth.py` creates and validates JWT tokens using `JWT_SECRET` and `HS256`.
- Registration is gated by the `OPEN_REGISTRATION` environment variable.
- When a new user is created, the backend automatically creates:
  - a `users` row,
  - a tenant org in `organizations`,
  - an `org_memberships` link,
  - default per-agent settings in `org_agent_plans`.

## Database structure
- `backend/auth/database.py` contains the SQL schema and access functions.
- Key tables:
  - `users`: email, hashed password, name, created_at , user_id.
  - `organizations`: tenant metadata including `simpro_company_id`, `simpro_api_url`, `simpro_access_token`, plan tier, and monthly token limits.
  - `org_memberships`: connects users to organizations with roles.
  - `org_agent_plans`: per-org, per-agent enablement and token limits.
  - `usage_records`: logs token usage and estimated costs.
- This schema is stored in `backend/auth/users.db` by default.

## Simpro credential storage
- Simpro credentials are stored in the `organizations` table.
- Relevant columns:
  - `simpro_company_id`
  - `simpro_api_url`
  - `simpro_access_token`
- The backend uses `update_organization()` to store these fields.
- `backend/api/superadmin_routes.py` exposes superadmin endpoints to create/update orgs, including writing Simpro URL and access token.
- Runtime retrieval is done in `backend/api/chat.py` via `_get_org_simpro_credentials(org_id)`, which loads the org row and returns:
  - `simpro_token` from `simpro_access_token`,
  - `simpro_url` from `simpro_api_url`,
  - `simpro_company_id`.
- Agents and tool runners use those values when calling the Simpro MCP backend.

## MYOB handling
- The repository references a MYOB server URL in `backend/main.py`:
  - `MYOB_SERVER_URL` environment variable or `http://localhost:8010` default.
- There is no database column for MYOB credentials in the current schema.
- There is no evidence of MYOB token persistence in `backend/auth/database.py` or API routes.
- Therefore MYOB integration appears to rely on a separate service URL, not stored tenant tokens here.

## What the backend currently supports
- Auth login and JWT-based session validation.
- Tenant/org creation and membership management.
- Simpro credential persistence per org.
- Per-org/monthly token limits and usage logging.
- Superadmin org management, including updating Simpro credentials.

## Important detail
- `backend/auth/database.py` also supports planned future features like role-based access control, tenant SOP overrides, and per-org LLM configuration.
- The active onboarding path is primarily through user registration + automatic org creation in `backend/api/auth_routes.py`.
