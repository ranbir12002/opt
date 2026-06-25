import { useState, useMemo } from 'react'
import { api } from '../api.js'
import OptificialLogo from '../assets/Optificial_logo.svg'

export default function LoginPage({ onLogin }) {
  const [token, setToken] = useState('')
  const [show, setShow] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const particles = useMemo(() =>
    Array.from({ length: 20 }, (_, i) => ({
      id: i,
      left: Math.random() * 100,
      delay: Math.random() * 8,
      duration: 6 + Math.random() * 10,
      size: 2 + Math.random() * 3,
    })), []
  )

  async function handleSubmit(e) {
    e.preventDefault()
    if (!token.trim()) { setError('Enter the superadmin token'); return }
    setLoading(true)
    setError('')
    try {
      await api.login(token.trim())
      onLogin(token.trim())
    } catch {
      setError('Invalid token — check that SUPERADMIN_TOKEN in your backend .env matches what you entered.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-container">
      <div className="login-particles">
        {particles.map((p) => (
          <div key={p.id} className="particle" style={{
            left: `${p.left}%`,
            animationDelay: `${p.delay}s`,
            animationDuration: `${p.duration}s`,
            width: `${p.size}px`,
            height: `${p.size}px`,
          }} />
        ))}
      </div>

      <form className="login-card" onSubmit={handleSubmit}>
        <div className="login-logo">
          <img src={OptificialLogo} alt="Optificial" className="login-logo-img" />
          <h1 className="login-title">Optificial.AI</h1>
          <p className="login-subtitle">AI-Powered Back Office</p>
          <span className="login-badge">SUPER ADMIN</span>
        </div>

        {error && <div className="login-error" style={{ marginBottom: 16 }}>{error}</div>}

        <div className="login-form">
          <div className="login-field">
            <label>Superadmin Token</label>
            <div className="input-wrap">
              <input
                type={show ? 'text' : 'password'}
                placeholder="Enter your superadmin token"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                autoFocus
                style={{ paddingRight: 52 }}
              />
              <button type="button" className="login-show-btn" onClick={() => setShow(s => !s)} tabIndex={-1}>
                {show ? 'Hide' : 'Show'}
              </button>
            </div>
          </div>

          <div className="login-hint">
            <strong style={{ color: '#94a3b8' }}>Setup:</strong> Add <code>SUPERADMIN_TOKEN=your-token</code> to <code>Chatbox_mcp/.env</code> and restart the backend. Any characters are valid including <code>@</code> <code>!</code> <code>#</code> <code>$</code>.
          </div>

          <button type="submit" className="login-btn" disabled={loading}>
            {loading ? 'Verifying…' : 'Sign In'}
          </button>
        </div>
      </form>
    </div>
  )
}
