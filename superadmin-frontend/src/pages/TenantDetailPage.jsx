import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../api.js'

const PLAN_TIERS = ['starter', 'growth', 'enterprise']
const AGENT_NAMES = ['schedule', 'invoice', 'workorder', 'purchase_order']

const PASSWORD_HELP = 'Min 8 characters, one uppercase, one lowercase, one digit, one special character.'

function passwordPolicyError(pw) {
  if (!pw || pw.length < 8) return PASSWORD_HELP
  if (!/[A-Z]/.test(pw)) return PASSWORD_HELP
  if (!/[a-z]/.test(pw)) return PASSWORD_HELP
  if (!/\d/.test(pw)) return PASSWORD_HELP
  if (!/[^A-Za-z0-9]/.test(pw)) return PASSWORD_HELP
  return ''
}

function ChangePasswordModal({ user, onClose, onSubmit }) {
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [showPw, setShowPw] = useState(false)
  const [showPw2, setShowPw2] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const policyErr = pw ? passwordPolicyError(pw) : ''
  const matchErr = (pw && pw2 && pw !== pw2) ? 'Passwords do not match' : ''
  const canSubmit = pw && pw === pw2 && !policyErr

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    if (!canSubmit) return
    setSubmitting(true)
    try {
      await onSubmit(pw)
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(2,6,23,0.7)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
      }}
    >
      <div className="card" style={{ width: '100%', maxWidth: 460, margin: 0 }}>
        <div className="card-title" style={{ marginBottom: 8 }}>Change Password</div>
        <p style={{ color: '#64748b', fontSize: '0.85rem', marginTop: 0, marginBottom: 16 }}>
          For <strong style={{ color: '#cbd5e1' }}>{user?.email}</strong>
        </p>

        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label className="form-label">New Password</label>
            <div style={{ display: 'flex', gap: 6 }}>
              <input
                className="form-input"
                type={showPw ? 'text' : 'password'}
                value={pw}
                onChange={(e) => setPw(e.target.value)}
                autoFocus
                style={{ flex: 1 }}
              />
              <button
                type="button"
                className="btn"
                onClick={() => setShowPw((v) => !v)}
                style={{ background: '#1e293b', color: '#94a3b8', border: '1px solid #334155', padding: '0 12px' }}
              >
                {showPw ? 'Hide' : 'Show'}
              </button>
            </div>
          </div>

          <div className="form-group">
            <label className="form-label">Confirm Password</label>
            <div style={{ display: 'flex', gap: 6 }}>
              <input
                className="form-input"
                type={showPw2 ? 'text' : 'password'}
                value={pw2}
                onChange={(e) => setPw2(e.target.value)}
                style={{ flex: 1 }}
              />
              <button
                type="button"
                className="btn"
                onClick={() => setShowPw2((v) => !v)}
                style={{ background: '#1e293b', color: '#94a3b8', border: '1px solid #334155', padding: '0 12px' }}
              >
                {showPw2 ? 'Hide' : 'Show'}
              </button>
            </div>
          </div>

          {(policyErr || matchErr) && (
            <div style={{ color: '#fbbf24', fontSize: '0.8rem', marginBottom: 12 }}>
              {matchErr || policyErr}
            </div>
          )}
          {!policyErr && !matchErr && (
            <div style={{ color: '#64748b', fontSize: '0.78rem', marginBottom: 12 }}>{PASSWORD_HELP}</div>
          )}

          {error && <div className="alert alert-error" style={{ marginBottom: 12 }}>{error}</div>}

          <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={onClose}
              style={{ background: '#1e293b', color: '#94a3b8', border: '1px solid #334155' }}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={!canSubmit || submitting}>
              {submitting ? 'Saving…' : 'Save Password'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function ReactivateUserModal({ user, onClose, onSubmit }) {
  const [resetPassword, setResetPassword] = useState(true)
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [showPw, setShowPw] = useState(false)
  const [showPw2, setShowPw2] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const policyErr = (resetPassword && pw) ? passwordPolicyError(pw) : ''
  const matchErr = (resetPassword && pw && pw2 && pw !== pw2) ? 'Passwords do not match' : ''
  const canSubmit = resetPassword ? (pw && pw === pw2 && !policyErr) : true

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    if (!canSubmit) return
    setSubmitting(true)
    try {
      await onSubmit(resetPassword ? pw : null)
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(2,6,23,0.7)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
      }}
    >
      <div className="card" style={{ width: '100%', maxWidth: 460, margin: 0 }}>
        <div className="card-title" style={{ marginBottom: 8 }}>Reactivate User</div>
        <p style={{ color: '#64748b', fontSize: '0.85rem', marginTop: 0, marginBottom: 16 }}>
          Reactivating <strong style={{ color: '#cbd5e1' }}>{user?.email}</strong> restores their previous role and history.
        </p>

        <form onSubmit={handleSubmit}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14, color: '#cbd5e1', fontSize: '0.88rem' }}>
            <input
              type="checkbox"
              checked={resetPassword}
              onChange={(e) => setResetPassword(e.target.checked)}
            />
            <span>Set a new password (recommended if user may have forgotten the old one)</span>
          </label>

          {resetPassword && (
            <>
              <div className="form-group">
                <label className="form-label">New Password</label>
                <div style={{ display: 'flex', gap: 6 }}>
                  <input className="form-input" type={showPw ? 'text' : 'password'}
                    value={pw} onChange={(e) => setPw(e.target.value)} autoFocus style={{ flex: 1 }} />
                  <button type="button" className="btn" onClick={() => setShowPw((v) => !v)}
                    style={{ background: '#1e293b', color: '#94a3b8', border: '1px solid #334155', padding: '0 12px' }}>
                    {showPw ? 'Hide' : 'Show'}
                  </button>
                </div>
              </div>

              <div className="form-group">
                <label className="form-label">Confirm Password</label>
                <div style={{ display: 'flex', gap: 6 }}>
                  <input className="form-input" type={showPw2 ? 'text' : 'password'}
                    value={pw2} onChange={(e) => setPw2(e.target.value)} style={{ flex: 1 }} />
                  <button type="button" className="btn" onClick={() => setShowPw2((v) => !v)}
                    style={{ background: '#1e293b', color: '#94a3b8', border: '1px solid #334155', padding: '0 12px' }}>
                    {showPw2 ? 'Hide' : 'Show'}
                  </button>
                </div>
              </div>

              {(policyErr || matchErr) && (
                <div style={{ color: '#fbbf24', fontSize: '0.8rem', marginBottom: 12 }}>{matchErr || policyErr}</div>
              )}
              {!policyErr && !matchErr && (
                <div style={{ color: '#64748b', fontSize: '0.78rem', marginBottom: 12 }}>{PASSWORD_HELP}</div>
              )}
            </>
          )}

          {error && <div className="alert alert-error" style={{ marginBottom: 12 }}>{error}</div>}

          <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={onClose}
              style={{ background: '#1e293b', color: '#94a3b8', border: '1px solid #334155' }}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={!canSubmit || submitting}>
              {submitting ? 'Reactivating…' : 'Reactivate'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}


function UsersSection({ orgId }) {
  const [users, setUsers] = useState([])
  const [roles, setRoles] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [pwUser, setPwUser] = useState(null)
  const [reactivateUser, setReactivateUser] = useState(null)

  function load() {
    setLoading(true); setError('')
    Promise.all([api.listOrgUsers(orgId), api.getOrgRoles(orgId)])
      .then(([u, r]) => { setUsers(u.users || []); setRoles(r.roles || []) })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [orgId])

  const adminRole = roles.find((r) => r.is_system)
  const memberRole = roles.find((r) => !r.is_system)

  async function handleRoleChange(user, newRoleIdStr) {
    setError('')
    const newRoleId = parseInt(newRoleIdStr)
    const newRole = roles.find((r) => r.id === newRoleId)
    if (!newRole) return

    const wasAdmin = user.role === 'admin'
    const willBeAdmin = !!newRole.is_system

    if (!wasAdmin && willBeAdmin) {
      if (!window.confirm(`Promote ${user.email} to admin? They will gain full administrative access for this tenant.`)) {
        load()  // revert dropdown
        return
      }
    } else if (wasAdmin && !willBeAdmin) {
      if (!window.confirm(`Demote ${user.email} from admin to "${newRole.name}"? They will lose admin privileges.`)) {
        load()
        return
      }
    }

    try {
      await api.setUserRole(orgId, user.id, newRoleId)
      load()
    } catch (e) {
      setError(e.message)
      load()  // revert dropdown to server-side truth
    }
  }

  async function handleDeactivate(user) {
    if (!window.confirm(
      `Deactivate ${user.email}?\n\n`
      + `They will lose access immediately. You can reactivate later — role, password, and history are preserved.`
    )) return
    setError('')
    try {
      await api.deactivateOrgUser(orgId, user.id)
      load()
    } catch (e) { setError(e.message) }
  }

  async function handleReactivateSubmit(newPassword) {
    if (!reactivateUser) return
    const result = await api.activateOrgUser(orgId, reactivateUser.id, newPassword)
    setReactivateUser(null)
    load()
    if (result?.role_fallback) {
      setError(`Reactivated. Note: previous role no longer exists; reassigned to "${result.role_fallback}".`)
    }
  }

  async function handlePasswordSubmit(newPassword) {
    await api.setUserPassword(orgId, pwUser.id, newPassword)
    setPwUser(null)
  }

  if (loading) return <div style={{ color: '#64748b', fontSize: '0.85rem' }}>Loading users…</div>
  if (error && users.length === 0) return <div className="alert alert-error">{error}</div>

  const activeAdminCount = users.filter((u) => u.role === 'admin' && u.is_active !== 0).length

  return (
    <>
      {error && <div className="alert alert-error" style={{ marginBottom: 12 }}>{error}</div>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Email</th>
              <th>Name</th>
              <th>Role</th>
              <th>Joined</th>
              <th style={{ minWidth: 280 }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {users.length === 0 && (
              <tr className="no-hover">
                <td colSpan={5} style={{ textAlign: 'center', color: '#64748b', padding: 30 }}>
                  No users yet for this tenant.
                </td>
              </tr>
            )}
            {users.map((u) => {
              const isInactive = u.is_active === 0
              const isAdmin = u.role === 'admin'
              const isLastActiveAdmin = isAdmin && !isInactive && activeAdminCount <= 1
              return (
                <tr key={u.id} className="no-hover" style={isInactive ? { opacity: 0.55 } : undefined}>
                  <td style={{ fontWeight: 500 }}>
                    {u.email}
                    {isAdmin && (
                      <span className="badge badge-yellow" style={{ marginLeft: 8, fontSize: '0.7rem' }}>ADMIN</span>
                    )}
                    {isInactive && (
                      <span className="badge" style={{ marginLeft: 8, fontSize: '0.7rem', background: '#64748b33', color: '#94a3b8' }}>INACTIVE</span>
                    )}
                    {isInactive && u.deactivated_at_ist && (
                      <div style={{ fontSize: '0.7rem', color: '#64748b', marginTop: 2 }}>
                        Deactivated {u.deactivated_at_ist.split(' ')[0]}
                        {u.deactivated_by_email ? ` by ${u.deactivated_by_email}` : ' by superadmin'}
                      </div>
                    )}
                  </td>
                  <td style={{ color: '#94a3b8' }}>{u.name || '—'}</td>
                  <td>
                    {isInactive ? (
                      <span style={{ color: '#94a3b8' }}>
                        {u.role || '—'}
                        <span style={{ display: 'block', fontSize: '0.7rem', color: '#64748b', marginTop: 2 }}>
                          Reactivate to change role
                        </span>
                      </span>
                    ) : adminRole && memberRole ? (
                      <select
                        className="form-select"
                        style={{ padding: '4px 8px', fontSize: '0.82rem', minWidth: 110 }}
                        value={u.role_id || (isAdmin ? adminRole.id : memberRole.id)}
                        onChange={(e) => handleRoleChange(u, e.target.value)}
                      >
                        <option value={adminRole.id}>admin</option>
                        <option value={memberRole.id}>member</option>
                      </select>
                    ) : (
                      <span style={{ color: '#94a3b8' }}>{u.role || '—'}</span>
                    )}
                  </td>
                  <td style={{ fontSize: '0.8rem', color: '#64748b' }}>
                    {u.joined_at_ist ? u.joined_at_ist.split(' ')[0] : '—'}
                  </td>
                  <td>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      {!isInactive && (
                        <button
                          className="btn"
                          onClick={() => setPwUser(u)}
                          style={{ background: '#1e293b', color: '#cbd5e1', border: '1px solid #334155', padding: '4px 10px', fontSize: '0.78rem' }}
                        >
                          Change Password
                        </button>
                      )}
                      {isInactive ? (
                        <button
                          className="btn btn-primary"
                          onClick={() => setReactivateUser(u)}
                          style={{ padding: '4px 10px', fontSize: '0.78rem' }}
                        >
                          Reactivate
                        </button>
                      ) : (
                        <button
                          className="btn"
                          onClick={() => handleDeactivate(u)}
                          disabled={isLastActiveAdmin}
                          title={isLastActiveAdmin ? 'Cannot deactivate the last active admin. Promote another user first.' : ''}
                          style={{
                            background: isLastActiveAdmin ? '#1e293b' : '#7f1d1d',
                            color: isLastActiveAdmin ? '#475569' : '#fecaca',
                            border: `1px solid ${isLastActiveAdmin ? '#334155' : '#991b1b'}`,
                            padding: '4px 10px', fontSize: '0.78rem',
                            cursor: isLastActiveAdmin ? 'not-allowed' : 'pointer',
                          }}
                        >
                          Deactivate
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {pwUser && (
        <ChangePasswordModal
          user={pwUser}
          onClose={() => setPwUser(null)}
          onSubmit={handlePasswordSubmit}
        />
      )}

      {reactivateUser && (
        <ReactivateUserModal
          user={reactivateUser}
          onClose={() => setReactivateUser(null)}
          onSubmit={handleReactivateSubmit}
        />
      )}
    </>
  )
}

function UsageSection({ orgId }) {
  const [usage, setUsage] = useState(null)
  useEffect(() => { api.getOrgUsage(orgId).then(setUsage).catch(() => {}) }, [orgId])

  if (!usage) return <div style={{ color: '#64748b', fontSize: '0.85rem' }}>Loading usage…</div>

  const pct = usage.monthly_limit ? Math.round((usage.total_used / usage.monthly_limit) * 100) : 0

  return (
    <>
      <div className="grid-3" style={{ marginBottom: 20 }}>
        <div className="stat-card">
          <div className="stat-label">Tokens Used ({usage.month}/{usage.year})</div>
          <div className="stat-value">{(usage.total_used || 0).toLocaleString()}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Monthly Limit</div>
          <div className="stat-value">{usage.monthly_limit ? usage.monthly_limit.toLocaleString() : '∞'}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Usage</div>
          <div className="stat-value" style={{ color: pct >= 90 ? '#f87171' : pct >= 70 ? '#fbbf24' : '#f1f5f9' }}>{pct}%</div>
        </div>
      </div>

      <table>
        <thead>
          <tr>
            <th>Agent</th>
            <th>Tokens Used</th>
            <th>Agent Limit</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {AGENT_NAMES.map((name) => {
            const a = usage.agents?.[name] || {}
            const agentPct = a.limit ? Math.round((a.used / a.limit) * 100) : 0
            return (
              <tr key={name} className="no-hover">
                <td style={{ textTransform: 'capitalize', fontWeight: 500 }}>{name.replace('_', ' ')}</td>
                <td style={{ color: '#94a3b8' }}>{(a.used || 0).toLocaleString()}</td>
                <td>{a.limit ? a.limit.toLocaleString() : <span className="badge badge-indigo">No limit</span>}</td>
                <td>
                  <span className={`badge ${a.is_enabled ? 'badge-green' : 'badge-red'}`}>
                    {a.is_enabled ? 'Enabled' : 'Disabled'}
                  </span>
                  {a.limit > 0 && agentPct >= 100 && (
                    <span className="badge badge-red" style={{ marginLeft: 6 }}>Limit reached</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </>
  )
}

export default function TenantDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()

  const [org, setOrg] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [orgForm, setOrgForm] = useState({})
  const [orgSaving, setOrgSaving] = useState(false)
  const [orgSaved, setOrgSaved] = useState(false)
  const [agentPlans, setAgentPlans] = useState([])
  const [agentSaving, setAgentSaving] = useState(false)
  const [agentSaved, setAgentSaved] = useState(false)

  // Phase 6 — LLM config state
  const [llmConfig, setLlmConfig] = useState(null)
  const [llmForm, setLlmForm] = useState({
    use_platform_llm: true,
    primary_provider: '', primary_model: '', primary_api_key: '',
    complex_provider: '', complex_model: '', complex_api_key: '',
  })
  const [llmSaving, setLlmSaving] = useState(false)
  const [llmSaved, setLlmSaved] = useState(false)
  const [llmClearing, setLlmClearing] = useState(false)

  // Department mapping state
  const [deptMapping, setDeptMapping] = useState(null)
  const [deptWarnings, setDeptWarnings] = useState([])
  const [deptRows, setDeptRows] = useState([])       // [{dept: '', accounts: ''}]
  const [deptSaving, setDeptSaving] = useState(false)
  const [deptSaved, setDeptSaved] = useState(false)
  const [deptValidating, setDeptValidating] = useState(false)
  const [deptDismissing, setDeptDismissing] = useState(false)

  function deptMappingToRows(mapping) {
    if (!mapping || typeof mapping !== 'object') return []
    return Object.entries(mapping).map(([dept, accounts]) => ({
      dept,
      accounts: Array.isArray(accounts) ? accounts.join(', ') : String(accounts),
    }))
  }

  function deptRowsToMapping(rows) {
    const mapping = {}
    for (const { dept, accounts } of rows) {
      if (!dept.trim()) continue
      mapping[dept.trim()] = accounts.split(',').map((a) => a.trim()).filter(Boolean)
    }
    return mapping
  }

  useEffect(() => {
    Promise.all([api.getOrg(id), api.getOrgLlm(id), api.getDeptMapping(id).catch(() => ({ mapping: null, drift_warnings: [] }))])
      .then(([data, llmData, deptData]) => {
        setOrg(data)
        setOrgForm({
          name: data.name || '',
          simpro_api_url: data.simpro_api_url || '',
          simpro_access_token: '',
          simpro_company_id: data.simpro_company_id || '',
          plan_tier: data.plan_tier || 'starter',
          monthly_token_limit: data.monthly_token_limit || '',
          is_active: data.is_active !== 0,
        })
        setAgentPlans((data.agent_plans || []).map((p) => ({ ...p })))
        setLlmConfig(llmData)
        setLlmForm({
          use_platform_llm: llmData.use_platform_llm !== false,
          primary_provider: llmData.org_specific?.primary?.provider || '',
          primary_model: llmData.org_specific?.primary?.model || '',
          primary_api_key: '',
          complex_provider: llmData.org_specific?.complex?.provider || '',
          complex_model: llmData.org_specific?.complex?.model || '',
          complex_api_key: '',
        })
        setDeptMapping(deptData.mapping)
        setDeptWarnings(deptData.drift_warnings || [])
        setDeptRows(deptMappingToRows(deptData.mapping))
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [id])

  async function saveOrg(e) {
    e.preventDefault()
    setOrgSaving(true); setOrgSaved(false)
    try {
      await api.updateOrg(id, {
        name: orgForm.name || undefined,
        simpro_api_url: orgForm.simpro_api_url || undefined,
        simpro_access_token: orgForm.simpro_access_token || undefined,
        simpro_company_id: orgForm.simpro_company_id ? parseInt(orgForm.simpro_company_id) : undefined,
        plan_tier: orgForm.plan_tier || undefined,
        monthly_token_limit: orgForm.monthly_token_limit ? parseInt(orgForm.monthly_token_limit) : undefined,
        is_active: orgForm.is_active,
      })
      setOrgSaved(true)
      setTimeout(() => setOrgSaved(false), 3000)
    } catch (e) {
      setError(e.message)
    } finally {
      setOrgSaving(false)
    }
  }

  async function saveAgents() {
    setAgentSaving(true); setAgentSaved(false)
    try {
      await api.updateOrgAgents(id, agentPlans.map((p) => ({
        agent_name: p.agent_name,
        is_enabled: p.is_enabled,
        monthly_token_limit: p.monthly_token_limit ? parseInt(p.monthly_token_limit) : null,
      })))
      setAgentSaved(true)
      setTimeout(() => setAgentSaved(false), 3000)
    } catch (e) {
      setError(e.message)
    } finally {
      setAgentSaving(false)
    }
  }

  function updateAgent(name, field, value) {
    setAgentPlans((prev) => prev.map((p) => p.agent_name === name ? { ...p, [field]: value } : p))
  }

  async function saveLlm(e) {
    e.preventDefault()
    setLlmSaving(true); setLlmSaved(false)
    try {
      const body = { use_platform_llm: llmForm.use_platform_llm }
      if (!llmForm.use_platform_llm) {
        body.primary = {
          provider: llmForm.primary_provider || undefined,
          model: llmForm.primary_model || undefined,
          api_key: llmForm.primary_api_key || undefined,
        }
        body.complex = {
          provider: llmForm.complex_provider || undefined,
          model: llmForm.complex_model || undefined,
          api_key: llmForm.complex_api_key || undefined,
        }
      }
      await api.updateOrgLlm(id, body)
      const fresh = await api.getOrgLlm(id)
      setLlmConfig(fresh)
      setLlmSaved(true)
      setTimeout(() => setLlmSaved(false), 3000)
    } catch (e) {
      setError(e.message)
    } finally {
      setLlmSaving(false)
    }
  }

  async function clearLlm() {
    if (!window.confirm('Clear org-specific LLM config and revert to platform global?')) return
    setLlmClearing(true)
    try {
      await api.clearOrgLlm(id)
      const fresh = await api.getOrgLlm(id)
      setLlmConfig(fresh)
      setLlmForm((f) => ({ ...f, use_platform_llm: true, primary_provider: '', primary_model: '', primary_api_key: '', complex_provider: '', complex_model: '', complex_api_key: '' }))
    } catch (e) {
      setError(e.message)
    } finally {
      setLlmClearing(false)
    }
  }

  async function saveDeptMapping() {
    setDeptSaving(true); setDeptSaved(false)
    try {
      const mapping = deptRowsToMapping(deptRows)
      await api.updateDeptMapping(id, mapping)
      setDeptMapping(mapping)
      setDeptSaved(true)
      setTimeout(() => setDeptSaved(false), 3000)
    } catch (e) {
      setError(e.message)
    } finally {
      setDeptSaving(false)
    }
  }

  async function validateDeptMapping() {
    setDeptValidating(true)
    try {
      const result = await api.validateDeptMapping(id)
      setDeptWarnings(result.drift_warnings || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setDeptValidating(false)
    }
  }

  async function dismissDeptWarnings() {
    setDeptDismissing(true)
    try {
      await api.dismissDeptWarnings(id)
      setDeptWarnings([])
    } catch (e) {
      setError(e.message)
    } finally {
      setDeptDismissing(false)
    }
  }

  if (loading) return <div className="loader">Loading…</div>
  if (error && !org) return <div className="alert alert-error">{error}</div>

  return (
    <>
      <div className="back-link" onClick={() => navigate('/tenants')}>← Back to tenants</div>

      <div className="page-header">
        <h1 className="page-title">{org?.name}</h1>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <span className={`badge ${org?.plan_tier === 'enterprise' ? 'badge-yellow' : org?.plan_tier === 'growth' ? 'badge-green' : 'badge-indigo'}`} style={{ fontSize: '0.82rem', padding: '4px 12px' }}>
            {(org?.plan_tier || 'starter').toUpperCase()}
          </span>
          <span className={`badge ${org?.is_active ? 'badge-green' : 'badge-red'}`} style={{ fontSize: '0.82rem', padding: '4px 12px' }}>
            {org?.is_active ? 'Active' : 'Inactive'}
          </span>
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {/* Org settings */}
      <div className="card">
        <div className="card-title">Organisation Settings</div>
        <form onSubmit={saveOrg}>
          <div className="grid-2">
            <div className="form-group">
              <label className="form-label">Organisation Name</label>
              <input className="form-input" value={orgForm.name} onChange={(e) => setOrgForm((f) => ({ ...f, name: e.target.value }))} />
            </div>
            <div className="form-group">
              <label className="form-label">Plan Tier</label>
              <select className="form-select" value={orgForm.plan_tier} onChange={(e) => setOrgForm((f) => ({ ...f, plan_tier: e.target.value }))}>
                {PLAN_TIERS.map((t) => <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>)}
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Monthly Token Limit (org-level)</label>
              <input className="form-input" type="number" value={orgForm.monthly_token_limit} onChange={(e) => setOrgForm((f) => ({ ...f, monthly_token_limit: e.target.value }))} placeholder="1000000" />
            </div>
            <div className="form-group">
              <label className="form-label">Simpro Company ID</label>
              <input className="form-input" type="number" value={orgForm.simpro_company_id} onChange={(e) => setOrgForm((f) => ({ ...f, simpro_company_id: e.target.value }))} />
            </div>
            <div className="form-group">
              <label className="form-label">Simpro API URL</label>
              <input className="form-input" value={orgForm.simpro_api_url} onChange={(e) => setOrgForm((f) => ({ ...f, simpro_api_url: e.target.value }))} placeholder="https://…" />
            </div>
            <div className="form-group">
              <label className="form-label">Simpro Access Token <span style={{ color: '#475569' }}>(blank = keep existing)</span></label>
              <input className="form-input" type="password" value={orgForm.simpro_access_token} onChange={(e) => setOrgForm((f) => ({ ...f, simpro_access_token: e.target.value }))} placeholder="Enter new token to update" />
            </div>
          </div>
          <div className="form-group" style={{ marginTop: 4 }}>
            <label className="toggle-wrap">
              <input type="checkbox" checked={orgForm.is_active} onChange={(e) => setOrgForm((f) => ({ ...f, is_active: e.target.checked }))} />
              <span style={{ color: '#94a3b8', fontSize: '0.88rem' }}>Organisation Active <span style={{ color: '#475569' }}>(unchecking blocks all user logins)</span></span>
            </label>
          </div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 20 }}>
            <button className="btn btn-primary" type="submit" disabled={orgSaving}>{orgSaving ? 'Saving…' : 'Save Changes'}</button>
            {orgSaved && <span className="badge badge-green">Saved</span>}
          </div>
        </form>
      </div>

      {/* Agent plans */}
      <div className="card">
        <div className="card-title">Agent Plans</div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Agent</th>
                <th>Enabled</th>
                <th>Monthly Token Limit</th>
              </tr>
            </thead>
            <tbody>
              {agentPlans.map((p) => (
                <tr key={p.agent_name} className="no-hover">
                  <td style={{ textTransform: 'capitalize', fontWeight: 500 }}>{p.agent_name.replace('_', ' ')}</td>
                  <td>
                    <input type="checkbox" checked={Boolean(p.is_enabled)} onChange={(e) => updateAgent(p.agent_name, 'is_enabled', e.target.checked)} />
                  </td>
                  <td>
                    <input
                      className="form-input"
                      type="number"
                      style={{ width: 180 }}
                      value={p.monthly_token_limit ?? ''}
                      placeholder="No limit"
                      onChange={(e) => updateAgent(p.agent_name, 'monthly_token_limit', e.target.value)}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 18 }}>
          <button className="btn btn-primary" onClick={saveAgents} disabled={agentSaving}>{agentSaving ? 'Saving…' : 'Save Agent Plans'}</button>
          {agentSaved && <span className="badge badge-green">Saved</span>}
        </div>
      </div>

      {/* LLM Configuration */}
      <div className="card">
        <div className="card-title">LLM Configuration</div>
        <p style={{ color: '#64748b', fontSize: '0.85rem', marginBottom: 16 }}>
          Primary: standard calls (intent analysis, planning). Complex: high-reasoning agent calls. If Complex is not set, Primary is used for all calls.
        </p>
        <form onSubmit={saveLlm}>
          <div className="form-group" style={{ marginBottom: 16 }}>
            <label className="toggle-wrap">
              <input type="checkbox" checked={llmForm.use_platform_llm}
                onChange={(e) => setLlmForm((f) => ({ ...f, use_platform_llm: e.target.checked }))} />
              <span style={{ color: '#94a3b8', fontSize: '0.88rem' }}>
                Use Platform Global Key{' '}
                <span style={{ color: '#475569' }}>(when ON, org inherits the platform default LLM)</span>
              </span>
            </label>
          </div>

          {llmForm.use_platform_llm && llmConfig?.effective && (
            <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 6, padding: '12px 16px', marginBottom: 16 }}>
              <p style={{ color: '#64748b', fontSize: '0.82rem', margin: 0 }}>
                Using platform default — Primary: <strong style={{ color: '#94a3b8' }}>{llmConfig.effective.primary?.provider || '?'} / {llmConfig.effective.primary?.model || '?'}</strong>
                {llmConfig.effective.primary?.api_key_set ? ' ✓ Key set' : ' ⚠ No key'}
              </p>
            </div>
          )}

          {!llmForm.use_platform_llm && (
            <>
              <div style={{ color: '#94a3b8', fontWeight: 600, fontSize: '0.82rem', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Primary LLM</div>
              <div className="grid-3" style={{ marginBottom: 16 }}>
                <div className="form-group">
                  <label className="form-label">Provider</label>
                  <select className="form-select" value={llmForm.primary_provider}
                    onChange={(e) => setLlmForm((f) => ({ ...f, primary_provider: e.target.value }))}>
                    <option value="">Platform Default</option>
                    <option value="openai">OpenAI</option>
                    <option value="anthropic">Anthropic</option>
                  </select>
                </div>
                <div className="form-group">
                  <label className="form-label">Model</label>
                  <input className="form-input" value={llmForm.primary_model} placeholder="e.g. gpt-4.1-mini"
                    onChange={(e) => setLlmForm((f) => ({ ...f, primary_model: e.target.value }))} />
                </div>
                <div className="form-group">
                  <label className="form-label">
                    API Key{' '}
                    {llmConfig?.org_specific?.primary?.api_key_set && (
                      <span style={{ color: '#475569' }}>(●●●●●● SET)</span>
                    )}
                  </label>
                  <input className="form-input" type="password" value={llmForm.primary_api_key}
                    placeholder="Enter new key to update"
                    onChange={(e) => setLlmForm((f) => ({ ...f, primary_api_key: e.target.value }))} />
                </div>
              </div>

              <div style={{ color: '#94a3b8', fontWeight: 600, fontSize: '0.82rem', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Complex LLM <span style={{ color: '#475569', fontWeight: 400, textTransform: 'none' }}>(optional — falls back to Primary if not set)</span>
              </div>
              <div className="grid-3" style={{ marginBottom: 16 }}>
                <div className="form-group">
                  <label className="form-label">Provider</label>
                  <select className="form-select" value={llmForm.complex_provider}
                    onChange={(e) => setLlmForm((f) => ({ ...f, complex_provider: e.target.value }))}>
                    <option value="">Same as Primary</option>
                    <option value="openai">OpenAI</option>
                    <option value="anthropic">Anthropic</option>
                  </select>
                </div>
                <div className="form-group">
                  <label className="form-label">Model</label>
                  <input className="form-input" value={llmForm.complex_model} placeholder="e.g. claude-sonnet-4-6"
                    onChange={(e) => setLlmForm((f) => ({ ...f, complex_model: e.target.value }))} />
                </div>
                <div className="form-group">
                  <label className="form-label">
                    API Key{' '}
                    {llmConfig?.org_specific?.complex?.api_key_set && (
                      <span style={{ color: '#475569' }}>(●●●●●● SET)</span>
                    )}
                  </label>
                  <input className="form-input" type="password" value={llmForm.complex_api_key}
                    placeholder="Enter new key to update"
                    onChange={(e) => setLlmForm((f) => ({ ...f, complex_api_key: e.target.value }))} />
                </div>
              </div>
            </>
          )}

          <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 4 }}>
            <button className="btn btn-primary" type="submit" disabled={llmSaving}>{llmSaving ? 'Saving…' : 'Save LLM Config'}</button>
            <button className="btn" type="button" onClick={clearLlm} disabled={llmClearing}
              style={{ background: '#1e293b', color: '#94a3b8', border: '1px solid #334155' }}>
              {llmClearing ? 'Clearing…' : 'Clear Org Config'}
            </button>
            {llmSaved && <span className="badge badge-green">Saved</span>}
          </div>
        </form>
      </div>

      {/* Department Mapping */}
      <div className="card">
        <div className="card-title">Department Mapping</div>
        <p style={{ color: '#64748b', fontSize: '0.85rem', marginBottom: 16 }}>
          Maps department names to Simpro chart-of-accounts numbers. Auto-built from live Simpro data on first deployment.
        </p>

        {deptWarnings.length > 0 && (
          <div style={{ background: '#451a03', border: '1px solid #92400e', borderRadius: 6, padding: '10px 14px', marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
            <div>
              <div style={{ color: '#fbbf24', fontWeight: 600, fontSize: '0.85rem', marginBottom: 4 }}>Mapping drift detected</div>
              {deptWarnings.map((w, i) => (
                <div key={i} style={{ color: '#fcd34d', fontSize: '0.82rem' }}>{w}</div>
              ))}
            </div>
            <button className="btn" onClick={dismissDeptWarnings} disabled={deptDismissing}
              style={{ background: '#92400e', color: '#fef3c7', border: 'none', fontSize: '0.78rem', padding: '4px 10px', whiteSpace: 'nowrap', flexShrink: 0 }}>
              {deptDismissing ? 'Dismissing…' : 'Dismiss'}
            </button>
          </div>
        )}

        {deptMapping === null ? (
          <div style={{ color: '#64748b', fontSize: '0.85rem', marginBottom: 16 }}>
            No mapping configured. Add Simpro credentials and the mapping will be auto-built on next backend startup.
          </div>
        ) : (
          <div className="table-wrap" style={{ marginBottom: 16 }}>
            <table>
              <thead>
                <tr>
                  <th>Department Name</th>
                  <th>Account Numbers (comma-separated)</th>
                  <th style={{ width: 60 }}></th>
                </tr>
              </thead>
              <tbody>
                {deptRows.map((row, i) => (
                  <tr key={i} className="no-hover">
                    <td>
                      <input className="form-input" value={row.dept}
                        onChange={(e) => setDeptRows((prev) => prev.map((r, j) => j === i ? { ...r, dept: e.target.value } : r))} />
                    </td>
                    <td>
                      <input className="form-input" value={row.accounts}
                        onChange={(e) => setDeptRows((prev) => prev.map((r, j) => j === i ? { ...r, accounts: e.target.value } : r))} />
                    </td>
                    <td>
                      <button className="btn" onClick={() => setDeptRows((prev) => prev.filter((_, j) => j !== i))}
                        style={{ background: 'transparent', color: '#f87171', border: 'none', padding: '4px 8px', fontSize: '1rem' }}>×</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <button className="btn" onClick={() => setDeptRows((prev) => [...prev, { dept: '', accounts: '' }])}
            style={{ background: '#1e293b', color: '#94a3b8', border: '1px solid #334155' }}>
            + Add Row
          </button>
          <button className="btn btn-primary" onClick={saveDeptMapping} disabled={deptSaving}>
            {deptSaving ? 'Saving…' : 'Save Mapping'}
          </button>
          <button className="btn" onClick={validateDeptMapping} disabled={deptValidating}
            style={{ background: '#1e293b', color: '#94a3b8', border: '1px solid #334155' }}>
            {deptValidating ? 'Validating…' : 'Validate Now'}
          </button>
          {deptSaved && <span className="badge badge-green">Saved</span>}
        </div>
      </div>

      {/* Users */}
      <div className="card">
        <div className="card-title">Users</div>
        <p style={{ color: '#64748b', fontSize: '0.85rem', marginBottom: 16 }}>
          Manage tenant users. Promote/demote roles, change passwords, or remove access. Every tenant must keep at least one admin.
        </p>
        <UsersSection orgId={id} />
      </div>

      {/* Usage */}
      <div className="card">
        <div className="card-title">Usage This Month</div>
        <UsageSection orgId={id} />
      </div>
    </>
  )
}
