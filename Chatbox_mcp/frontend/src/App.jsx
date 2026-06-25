import { useState, useEffect } from 'react';
import ChatBox from './components/ChatBox';
import LoginPage from './components/LoginPage';
import OptificialLogo from './assets/Optificial_logo.svg';
import { secureFetch, clearAuthCookie } from './lib/api.js';
import { useTenant } from './lib/useTenant.js';

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8001';

export default function App() {
  const [user, setUser] = useState(null);
  const [checking, setChecking] = useState(true);

  // Subdomain → tenant branding (public, no auth)
  const { tenant, loading: tenantLoading } = useTenant();

  // On mount: validate session via HttpOnly cookie
  useEffect(() => {
    secureFetch(`${BACKEND_URL}/api/auth/me`)
      .then((r) => r.json())
      .then((u) => setUser(u))
      .catch(() => {})  // No valid session — stay on login
      .finally(() => setChecking(false));
  }, []);

  // Apply tenant brand color as CSS custom property on <html>
  useEffect(() => {
    if (tenant?.primary_color) {
      document.documentElement.style.setProperty('--brand-primary', tenant.primary_color);
    }
    return () => {
      document.documentElement.style.removeProperty('--brand-primary');
    };
  }, [tenant]);

  const handleLogin = (_token, userData) => {
    // Token is now set as an HttpOnly cookie by the backend.
    // Show the app immediately with the login payload …
    setUser(userData);
    // … then refresh from /me so role/org info always comes from the same
    // authoritative source as the on-mount check.  The login response does
    // NOT include `role`, so without this the admin-only UI (Manage Team)
    // stays hidden until a full page reload.
    secureFetch(`${BACKEND_URL}/api/auth/me`)
      .then((r) => r.json())
      .then((u) => setUser(u))
      .catch(() => {});
  };

  const handleLogout = async () => {
    await clearAuthCookie();
    setUser(null);
  };

  // Loading state while checking token (or tenant branding)
  if (checking || tenantLoading) {
    return (
      <div className="login-container">
        <div className="login-card" style={{ textAlign: 'center', padding: '60px 40px' }}>
          <div className="login-logo-icon">
            <img src={tenant?.logo_url || OptificialLogo} alt="Logo" className="login-logo-img" />
          </div>
          <p style={{ color: '#94a3b8', marginTop: '16px' }}>Loading...</p>
        </div>
      </div>
    );
  }

  if (!user) {
    return <LoginPage onLogin={handleLogin} tenant={tenant} />;
  }

  return (
    <ChatBox
      user={user}
      token={null} // Auth handled via HttpOnly cookie
      onUserChange={setUser}
      onLogout={handleLogout}
    />
  );
}
