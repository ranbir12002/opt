import React, { useState, useMemo } from 'react';
import OptificialLogo from '../assets/Optificial_logo.svg';
import { secureFetch } from '../lib/api.js';

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8001';

export default function LoginPage({ onLogin, tenant }) {
  const [isRegister, setIsRegister] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  // Resolve branding: tenant-specific or default Optificial
  const brandLogo = tenant?.logo_url || OptificialLogo;
  const brandName = tenant?.name || 'Optificial.AI';
  const brandTagline = tenant?.tagline || 'AI-Powered Back Office';

  // Generate floating particles once
  const particles = useMemo(
    () =>
      Array.from({ length: 20 }, (_, i) => ({
        id: i,
        left: Math.random() * 100,
        delay: Math.random() * 8,
        duration: 6 + Math.random() * 10,
        size: 2 + Math.random() * 3,
      })),
    []
  );

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    const endpoint = isRegister ? '/api/auth/register' : '/api/auth/login';
    const body = isRegister
      ? { email, password, name, tenant_slug: tenant?.slug }
      : { email, password, tenant_slug: tenant?.slug };

    try {
      const res = await secureFetch(`${BACKEND_URL}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      const data = await res.json();
      onLogin(data.token, data.user);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-container">
      {/* Floating particles */}
      <div className="login-particles">
        {particles.map((p) => (
          <div
            key={p.id}
            className="particle"
            style={{
              left: `${p.left}%`,
              animationDelay: `${p.delay}s`,
              animationDuration: `${p.duration}s`,
              width: `${p.size}px`,
              height: `${p.size}px`,
            }}
          />
        ))}
      </div>

      <div className="login-card">
        {/* Logo / Branding */}
        <div className="login-logo">
          <img src={brandLogo} alt={brandName} className="login-logo-img" />
          <h1 className="login-title">{brandName}</h1>
          <p className="login-subtitle">{brandTagline}</p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="login-form">
          {isRegister && (
            <div className="login-field">
              <label>Name</label>
              <input
                type="text"
                placeholder="Your name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoComplete="name"
              />
            </div>
          )}

          <div className="login-field">
            <label>Email</label>
            <input
              type="email"
              placeholder="you@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
            />
          </div>

          <div className="login-field">
            <label>Password</label>
            <input
              type="password"
              placeholder="Enter password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={6}
              autoComplete={isRegister ? 'new-password' : 'current-password'}
            />
          </div>

          {error && <div className="login-error">{error}</div>}

          <button type="submit" className="login-btn" disabled={loading}>
            {loading
              ? 'Please wait...'
              : isRegister
                ? 'Create Account'
                : 'Sign In'}
          </button>
        </form>

        {/* Toggle login / register */}
        <div className="login-toggle">
          {isRegister ? (
            <span>
              Already have an account?{' '}
              <button type="button" onClick={() => { setIsRegister(false); setError(''); }}>
                Sign In
              </button>
            </span>
          ) : (
            <span>
              Don't have an account?{' '}
              <button type="button" onClick={() => { setIsRegister(true); setError(''); }}>
                Register
              </button>
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
