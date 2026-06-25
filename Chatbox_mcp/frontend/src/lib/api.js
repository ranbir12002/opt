// src/lib/api.js — Centralized secure fetch for Chatbox frontend
// Auth: HttpOnly cookie (sent automatically via credentials: 'include')

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8001'

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
 * Safe, short `detail` strings from the API are preserved (e.g. "User not found").
 * Everything else is replaced with a generic message keyed to the HTTP status.
 */
function sanitizeError(status, rawBody) {
  const fallback = STATUS_MESSAGES[status] || `Something went wrong (HTTP ${status}). Please try again.`

  const detail = rawBody?.detail
  if (typeof detail === 'string' && isSafeDetail(detail)) {
    return detail
  }
  if (Array.isArray(detail)) {
    return 'Validation failed. Please check your input and try again.'
  }
  return fallback
}

/**
 * Core secure fetch wrapper.
 * - Always sends `credentials: 'include'` (HttpOnly cookie).
 * - Sanitizes error responses before surfacing to the UI.
 * - Network failures get a clean user-facing message.
 *
 * @param {string} url – Full URL to fetch
 * @param {RequestInit} opts – Standard fetch options (method, body, headers, etc.)
 * @returns {Promise<Response>} – The raw Response (caller handles body parsing)
 */
export async function secureFetch(url, opts = {}) {
  let res
  try {
    res = await fetch(url, {
      ...opts,
      credentials: 'include',
    })
  } catch (networkErr) {
    if (import.meta.env.DEV) console.error('[api] Network error:', networkErr)
    throw new Error('Unable to reach the server. Please check your connection and try again.')
  }

  // Intercept 401 Unauthorized to attempt token refresh
  if (res.status === 401 && !url.includes('/api/auth/refresh') && !url.includes('/api/auth/login')) {
    try {
      const refreshRes = await fetch(`${BACKEND_URL}/api/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
      })
      if (refreshRes.ok) {
        // Retry the original request
        res = await fetch(url, {
          ...opts,
          credentials: 'include',
        })
      }
    } catch (refreshErr) {
      if (import.meta.env.DEV) console.error('[api] Token refresh failed:', refreshErr)
    }
  }

  if (!res.ok) {
    const rawBody = await res.json().catch(() => ({}))
    if (import.meta.env.DEV) console.error(`[api] ${opts.method || 'GET'} ${url} → ${res.status}`, rawBody)
    const err = new Error(sanitizeError(res.status, rawBody))
    err.status = res.status
    err.detail = rawBody?.detail
    throw err
  }

  return res
}

/**
 * JSON API helper for /api/auth/* paths (used by AdminPanel).
 * Drop-in replacement for the old `apiFetch(path, token, opts)`.
 * The `token` parameter is kept for call-site compatibility but ignored.
 *
 * @param {string} path – Path under /api/auth, e.g. '/org/members'
 * @param {string} _token – (ignored, kept for backward compat)
 * @param {RequestInit} opts – fetch options (method, body, headers)
 * @returns {Promise<any>} – Parsed JSON response
 */
export async function authFetch(path, _token, opts = {}) {
  const res = await secureFetch(`${BACKEND_URL}/api/auth${path}`, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ...(opts.headers || {}),
    },
  })
  return res.json()
}

/**
 * Ask the backend to clear the HttpOnly auth cookie.
 * Call this from the logout handler instead of localStorage.removeItem().
 */
export async function clearAuthCookie() {
  await fetch(`${BACKEND_URL}/api/auth/logout`, {
    method: 'POST',
    credentials: 'include',
  })
}
