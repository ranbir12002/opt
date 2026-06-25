// src/lib/useTenant.js — Subdomain detection + tenant branding fetch
import { useState, useEffect } from 'react';

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8001';
const BASE_DOMAIN = import.meta.env.VITE_BASE_DOMAIN || 'optificial.ai';

/**
 * Extract the subdomain slug from the current hostname.
 *
 * Examples:
 *   acme.optificial.ai  → "acme"
 *   app.optificial.ai   → "app"
 *   optificial.ai       → null   (bare domain — default portal)
 *   localhost            → null   (local dev — default portal)
 *   localhost:5173       → null
 *
 * @returns {string|null}
 */
function getSubdomainSlug() {
  const hostname = window.location.hostname; // e.g. "acme.optificial.ai"

  // Local development — no subdomain routing
  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    return null;
  }

  // Strip the base domain to get the subdomain prefix
  // e.g. "acme.optificial.ai" → "acme"
  if (hostname.endsWith(`.${BASE_DOMAIN}`)) {
    const sub = hostname.slice(0, -(BASE_DOMAIN.length + 1)); // remove ".optificial.ai"
    // Ignore multi-level subs like "www" or "api"
    if (sub && !sub.includes('.') && sub !== 'www' && sub !== 'api' && sub !== 'app') {
      return sub;
    }
  }

  return null;
}

/**
 * React hook: detect tenant subdomain and fetch branding.
 *
 * @returns {{ tenant: object|null, slug: string|null, loading: boolean, error: string|null }}
 */
export function useTenant() {
  const [tenant, setTenant] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const slug = getSubdomainSlug();

  useEffect(() => {
    if (!slug) {
      // No subdomain → default Optificial branding
      setLoading(false);
      return;
    }

    let cancelled = false;

    fetch(`${BACKEND_URL}/api/auth/tenant-info/${encodeURIComponent(slug)}`)
      .then((res) => {
        if (!res.ok) throw new Error('Organization not found');
        return res.json();
      })
      .then((data) => {
        if (!cancelled) setTenant(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [slug]);

  return { tenant, slug, loading, error };
}
