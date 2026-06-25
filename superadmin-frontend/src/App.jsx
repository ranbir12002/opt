import { useState, useEffect, useMemo } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useNavigate, useLocation, useSearchParams } from 'react-router-dom'
import { api, clearAuthCookie } from './api.js'
import OptificialLogo from './assets/Optificial_logo.svg'
import LoginPage from './pages/LoginPage.jsx'
import TenantListPage from './pages/TenantListPage.jsx'
import TenantDetailPage from './pages/TenantDetailPage.jsx'
import CreateTenantPage from './pages/CreateTenantPage.jsx'
import PlatformSettingsPage from './pages/PlatformSettingsPage.jsx'

function TopNav({ onLogout }) {
  const navigate = useNavigate()
  const location = useLocation()

  const links = [
    { label: 'Tenants', path: '/tenants' },
    { label: 'New Tenant', path: '/tenants/new' },
    { label: 'Platform Settings', path: '/platform' },
  ]

  return (
    <nav className="top-nav">
      <div className="nav-container">
        <div className="nav-left">
          <div className="nav-logo" onClick={() => navigate('/tenants')} style={{ cursor: 'pointer' }}>
            <img src={OptificialLogo} alt="Optificial" className="nav-logo-img" />
            <span className="nav-logo-text">Optificial.AI</span>
            <span className="nav-logo-badge">ADMIN</span>
          </div>
          <div className="nav-links">
            {links.map((l) => (
              <button
                key={l.path}
                className={`nav-link ${location.pathname === l.path || (l.path === '/tenants' && location.pathname.startsWith('/tenants/') && location.pathname !== '/tenants/new' && !location.pathname.startsWith('/tenants/new')) ? 'active' : ''}`}
                onClick={() => navigate(l.path)}
              >
                {l.label}
              </button>
            ))}
          </div>
        </div>
        <div className="nav-right">
          <button className="nav-logout-btn" onClick={onLogout}>
            Logout
          </button>
        </div>
      </div>
    </nav>
  )
}

function AuthenticatedApp({ onLogout }) {
  return (
    <>
      <TopNav onLogout={onLogout} />
      <div className="page-shell">
        <div className="page-content">
          <Routes>
            <Route path="/tenants" element={<TenantListPage />} />
            <Route path="/tenants/new" element={<CreateTenantPage />} />
            <Route path="/tenants/:id" element={<TenantDetailPage />} />
            <Route path="/platform" element={<PlatformSettingsPage />} />
            <Route path="*" element={<Navigate to="/tenants" replace />} />
          </Routes>
        </div>
      </div>
    </>
  )
}

function RequireAuth({ authed, children }) {
  const location = useLocation()
  if (!authed) {
    const redirectUrl = encodeURIComponent(location.pathname + location.search)
    return <Navigate to={`/login?redirect=${redirectUrl}`} replace />
  }
  return children
}

function LoginRoute({ authed, onLogin }) {
  const [searchParams] = useSearchParams()
  if (authed) {
    const redirectPath = searchParams.get('redirect') || '/tenants'
    return <Navigate to={redirectPath} replace />
  }
  return <LoginPage onLogin={onLogin} />
}

export default function App() {
  const [authed, setAuthed] = useState(null)

  useEffect(() => {
    api.checkToken()
      .then(() => setAuthed(true))
      .catch(() => setAuthed(false))
  }, [])

  if (authed === null) {
    return (
      <div className="login-container">
        <div className="login-card" style={{ textAlign: 'center', padding: '60px 40px' }}>
          <img src={OptificialLogo} alt="Optificial" style={{ width: 48, height: 48, marginBottom: 16 }} />
          <p style={{ color: '#64748b' }}>Loading…</p>
        </div>
      </div>
    )
  }

  function handleLogin() {
    setAuthed(true)
  }

  async function handleLogout() {
    await clearAuthCookie()
    setAuthed(false)
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginRoute authed={authed} onLogin={handleLogin} />} />
        <Route path="/*" element={
          <RequireAuth authed={authed}>
            <AuthenticatedApp onLogout={handleLogout} />
          </RequireAuth>
        } />
      </Routes>
    </BrowserRouter>
  )
}
