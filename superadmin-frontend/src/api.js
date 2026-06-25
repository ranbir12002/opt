// src/api.js — all calls to /api/superadmin/*
// Auth: HttpOnly cookie (set by backend on login, sent automatically via credentials: 'include')

const BASE = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8001'

// ── Status-code → user-friendly message map ──────────────────────────
const STATUS_MESSAGES = {
  400: 'The request was invalid. Please check your input and try again.',
  401: 'Your session has expired. Please log in again.',
  403: 'You do not have permission to perform this action.',
  404: 'The requested resource was not found.',
  409: 'This operation conflicts with the current state. Please refresh and retry.',
  422: 'The submitted data is invalid. Please review and correct it.',
  429: 'Too many requests. Please wait a moment and try again.',
  500: 'An unexpected server error occurred. Please try again later.',
  502: 'The server is temporarily unreachable. Please try again shortly.',
  503: 'The service is currently unavailable. Please try again later.',
  504: 'The server took too long to respond. Please try again.',
}

/**
 * Returns true if a backend detail string is safe to show to users.
 * Blocks stack traces, file paths, SQL fragments, and overly long messages.
 */
function isSafeDetail(detail) {
  if (typeof detail !== 'string' || detail.length > 200) return false
  const UNSAFE = /traceback|stack|\.py|\.js|\/usr\/|\\\\|SQL|SELECT |INSERT |UPDATE |DELETE |errno|ECONNREFUSED|node_modules/i
  return !UNSAFE.test(detail)
}

/**
 * Sanitize a raw backend error into a clean, user-facing message.
 * Safe, short `detail` strings from the API are preserved (e.g. "Org not found").
 * Everything else is replaced with a generic message keyed to the HTTP status.
 */
function sanitizeError(status, rawBody) {
  const fallback = STATUS_MESSAGES[status] || `Something went wrong (HTTP ${status}). Please try again.`

  // rawBody may be { detail: "..." } or { detail: [...] } or something else entirely
  const detail = rawBody?.detail
  if (typeof detail === 'string' && isSafeDetail(detail)) {
    return detail
  }
  // FastAPI validation errors come as an array of objects
  if (Array.isArray(detail)) {
    return 'Validation failed. Please check your input and try again.'
  }
  return fallback
}

async function request(method, path, body) {
  const opts = {
    method,
    credentials: 'include',                   // ← send HttpOnly cookie automatically
    headers: {
      'Content-Type': 'application/json',
    },
  }
  if (body !== undefined) opts.body = JSON.stringify(body)

  let res
  try {
    res = await fetch(`${BASE}/api/superadmin${path}`, opts)
  } catch (networkErr) {
    // Network failure, DNS error, CORS block, offline, etc.
    if (import.meta.env.DEV) console.error('[api] Network error:', networkErr)
    throw new Error('Unable to reach the server. Please check your connection and try again.')
  }

  if (!res.ok) {
    const rawBody = await res.json().catch(() => ({}))
    if (import.meta.env.DEV) console.error(`[api] ${method} ${path} → ${res.status}`, rawBody)
    throw new Error(sanitizeError(res.status, rawBody))
  }

  return res.json()
}

/**
 * Ask the backend to clear the HttpOnly auth cookie.
 * Call this from the logout handler instead of localStorage.removeItem().
 */
export async function clearAuthCookie() {
  await fetch(`${BASE}/api/superadmin/logout`, {
    method: 'POST',
    credentials: 'include',
  })
}

export const api = {
  // Auth check — just hit /orgs to see if cookie is valid
  checkToken: () => request('GET', '/orgs'),

  // Login — POST token to backend to set cookie
  login: (token) => request('POST', '/login', { token }),

  // Orgs
  listOrgs: () => request('GET', '/orgs'),
  getOrg: (id) => request('GET', `/orgs/${id}`),
  createOrg: (data) => request('POST', '/orgs', data),
  updateOrg: (id, data) => request('PUT', `/orgs/${id}`, data),
  getOrgUsage: (id) => request('GET', `/orgs/${id}/usage`),
  updateOrgAgents: (id, agents) => request('POST', `/orgs/${id}/agents`, { agents }),

  // Phase 6 — Platform LLM global defaults
  getPlatformLlm: () => request('GET', '/platform/llm'),
  updatePlatformLlm: (data) => request('PUT', '/platform/llm', data),

  // Phase 6 — Per-org LLM config
  getOrgLlm: (id) => request('GET', `/orgs/${id}/llm`),
  updateOrgLlm: (id, data) => request('PUT', `/orgs/${id}/llm`, data),
  clearOrgLlm: (id) => request('DELETE', `/orgs/${id}/llm`),

  // Department mapping
  getDeptMapping: (id) => request('GET', `/orgs/${id}/department-mapping`),
  updateDeptMapping: (id, mapping) => request('PUT', `/orgs/${id}/department-mapping`, { mapping }),
  validateDeptMapping: (id) => request('POST', `/orgs/${id}/department-mapping/validate`),
  dismissDeptWarnings: (id) => request('DELETE', `/orgs/${id}/department-mapping/warnings`),

  // Tenant user management
  listOrgUsers: (id) => request('GET', `/orgs/${id}/users`),
  setUserPassword: (orgId, userId, newPassword) =>
    request('POST', `/orgs/${orgId}/users/${userId}/password`, { new_password: newPassword }),
  setUserRole: (orgId, userId, roleId) =>
    request('PUT', `/orgs/${orgId}/users/${userId}/role`, { role_id: roleId }),
  deactivateOrgUser: (orgId, userId) =>
    request('POST', `/orgs/${orgId}/users/${userId}/deactivate`),
  activateOrgUser: (orgId, userId, newPassword) =>
    request('POST', `/orgs/${orgId}/users/${userId}/activate`, { new_password: newPassword || null }),

  // Roles for an org (used for promote/demote dropdown)
  getOrgRoles: (id) => request('GET', `/orgs/${id}/roles`),
}
