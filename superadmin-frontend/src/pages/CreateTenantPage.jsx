import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'

const AGENT_NAMES = ['schedule', 'invoice', 'workorder', 'purchase_order']
const PLAN_TIERS = ['starter', 'growth', 'enterprise']

export default function CreateTenantPage() {
  const navigate = useNavigate()
  const [form, setForm] = useState({
    org_name: '', org_slug: '', simpro_api_url: '',
    simpro_access_token: '', simpro_company_id: '',
    plan_tier: 'starter', admin_email: '',
  })
  const [enabledAgents, setEnabledAgents] = useState(new Set(AGENT_NAMES))
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)

  function set(k, v) {
    setForm((f) => {
      const next = { ...f, [k]: v }
      if (k === 'org_name' && !f.org_slug) {
        next.org_slug = v.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
      }
      return next
    })
  }

  function toggleAgent(name) {
    setEnabledAgents((prev) => {
      const next = new Set(prev)
      next.has(name) ? next.delete(name) : next.add(name)
      return next
    })
  }

  async function handleSubmit(e) {
    e.preventDefault()
    if (!form.org_name || !form.org_slug) { setError('Name and slug are required'); return }
    setLoading(true); setError('')
    try {
      const data = await api.createOrg({
        org_name: form.org_name,
        org_slug: form.org_slug,
        simpro_api_url: form.simpro_api_url || null,
        simpro_access_token: form.simpro_access_token || null,
        simpro_company_id: form.simpro_company_id ? parseInt(form.simpro_company_id) : null,
        plan_tier: form.plan_tier,
        agents: [...enabledAgents],
        admin_email: form.admin_email || null,
      })
      setResult(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  if (result) {
    return (
      <>
        <div className="back-link" onClick={() => navigate('/tenants')}>← Back to tenants</div>
        <div className="page-header"><h1 className="page-title">Tenant Created</h1></div>
        <div className="alert alert-success">
          Tenant <strong>{result.org?.name}</strong> created successfully.
        </div>
        {result.admin?.temp_password && (
          <div className="card">
            <div className="card-title">Admin Credentials — Share with Client</div>
            <p style={{ color: '#64748b', fontSize: '0.85rem', marginBottom: 16 }}>
              This password cannot be retrieved again. Copy it now and send to the client.
            </p>
            <table style={{ width: 'auto' }}>
              <tbody>
                <tr className="no-hover">
                  <td style={{ paddingRight: 20, color: '#64748b', paddingBottom: 10 }}>Email</td>
                  <td style={{ fontWeight: 600 }}>{result.admin.email}</td>
                </tr>
                <tr className="no-hover">
                  <td style={{ paddingRight: 20, color: '#64748b' }}>Temp Password</td>
                  <td>
                    <code style={{ background: 'rgba(99,102,241,0.12)', color: '#818cf8', padding: '3px 10px', borderRadius: 6, fontSize: '0.9rem', letterSpacing: '0.05em' }}>
                      {result.admin.temp_password}
                    </code>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        )}
        {result.admin?.note && <div className="alert alert-info">{result.admin.note}</div>}
        <div style={{ display: 'flex', gap: 12 }}>
          <button className="btn btn-primary" onClick={() => navigate(`/tenants/${result.org?.id}`)}>View Tenant</button>
          <button className="btn btn-outline" onClick={() => navigate('/tenants')}>Back to List</button>
        </div>
      </>
    )
  }

  return (
    <>
      <div className="back-link" onClick={() => navigate('/tenants')}>← Back to tenants</div>
      <div className="page-header"><h1 className="page-title">New Tenant</h1></div>
      {error && <div className="alert alert-error">{error}</div>}

      <form onSubmit={handleSubmit}>
        <div className="card">
          <div className="card-title">Organisation Details</div>
          <div className="grid-2">
            <div className="form-group">
              <label className="form-label">Organisation Name *</label>
              <input className="form-input" value={form.org_name} onChange={(e) => set('org_name', e.target.value)} placeholder="Acme Plumbing" required />
            </div>
            <div className="form-group">
              <label className="form-label">Slug (URL identifier) *</label>
              <input className="form-input" value={form.org_slug} onChange={(e) => set('org_slug', e.target.value)} placeholder="acme-plumbing" required />
            </div>
            <div className="form-group">
              <label className="form-label">Admin Email <span style={{ color: '#475569' }}>(optional — creates a login)</span></label>
              <input className="form-input" type="email" value={form.admin_email} onChange={(e) => set('admin_email', e.target.value)} placeholder="admin@acmeplumbing.com" />
            </div>
            <div className="form-group">
              <label className="form-label">Plan Tier</label>
              <select className="form-select" value={form.plan_tier} onChange={(e) => set('plan_tier', e.target.value)}>
                {PLAN_TIERS.map((t) => <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>)}
              </select>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-title">Simpro Credentials</div>
          <div className="grid-2">
            <div className="form-group">
              <label className="form-label">Simpro API URL</label>
              <input className="form-input" value={form.simpro_api_url} onChange={(e) => set('simpro_api_url', e.target.value)} placeholder="https://company.simprosuite.com/api/v1.0" />
            </div>
            <div className="form-group">
              <label className="form-label">Simpro Company ID</label>
              <input className="form-input" type="number" value={form.simpro_company_id} onChange={(e) => set('simpro_company_id', e.target.value)} placeholder="5" />
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">Simpro Access Token</label>
            <input className="form-input" type="password" value={form.simpro_access_token} onChange={(e) => set('simpro_access_token', e.target.value)} placeholder="••••••••••••••••" />
          </div>
        </div>

        <div className="card">
          <div className="card-title">Agents to Enable</div>
          <div style={{ display: 'flex', gap: 28, flexWrap: 'wrap' }}>
            {AGENT_NAMES.map((name) => (
              <label key={name} className="toggle-wrap">
                <input type="checkbox" checked={enabledAgents.has(name)} onChange={() => toggleAgent(name)} />
                <span style={{ textTransform: 'capitalize', color: '#cbd5e1' }}>{name.replace('_', ' ')}</span>
              </label>
            ))}
          </div>
        </div>

        <button className="btn btn-primary" type="submit" disabled={loading}>
          {loading ? 'Creating…' : 'Create Tenant'}
        </button>
      </form>
    </>
  )
}
