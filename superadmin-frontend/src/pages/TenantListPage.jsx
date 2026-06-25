import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'

function UsageBar({ used, limit }) {
  if (!limit) return <span className="badge badge-indigo">Unlimited</span>
  const pct = Math.min(100, Math.round((used / limit) * 100))
  const cls = pct >= 90 ? 'danger' : pct >= 70 ? 'warn' : ''
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <div className="progress-wrap" style={{ flex: 1 }}>
        <div className={`progress-bar ${cls}`} style={{ width: `${pct}%` }} />
      </div>
      <span style={{ fontSize: '0.78rem', color: '#64748b', whiteSpace: 'nowrap', minWidth: 32 }}>
        {pct}%
      </span>
    </div>
  )
}

export default function TenantListPage() {
  const [orgs, setOrgs] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const navigate = useNavigate()

  useEffect(() => {
    api.listOrgs()
      .then((data) => setOrgs(data.orgs || []))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="loader">Loading tenants…</div>
  if (error) return <div className="alert alert-error">{error}</div>

  return (
    <>
      <div className="page-header">
        <h1 className="page-title">Tenants</h1>
        <button className="btn btn-primary" onClick={() => navigate('/tenants/new')}>
          + New Tenant
        </button>
      </div>

      <div className="card">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Slug</th>
                <th>Plan</th>
                <th>Users</th>
                <th>Usage this month</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {orgs.length === 0 && (
                <tr className="no-hover">
                  <td colSpan={6} style={{ textAlign: 'center', color: '#64748b', padding: 40 }}>
                    No tenants yet. Create one to get started.
                  </td>
                </tr>
              )}
              {orgs.map((org) => (
                <tr key={org.id} onClick={() => navigate(`/tenants/${org.id}`)}>
                  <td style={{ fontWeight: 600 }}>
                    {org.name}
                    {org.dept_mapping_warnings && (
                      <span title="Department mapping drift detected" style={{ marginLeft: 6, color: '#fbbf24', fontSize: '0.85rem', cursor: 'default' }}>⚠</span>
                    )}
                  </td>
                  <td style={{ color: '#64748b', fontSize: '0.82rem', fontFamily: 'monospace' }}>{org.slug}</td>
                  <td>
                    <span className={`badge ${org.plan_tier === 'enterprise' ? 'badge-yellow' : org.plan_tier === 'growth' ? 'badge-green' : 'badge-indigo'}`}>
                      {(org.plan_tier || org.plan_name || 'starter').toUpperCase()}
                    </span>
                  </td>
                  <td style={{ color: '#94a3b8' }}>
                    {(() => {
                      const total = org.user_count ?? 0
                      const active = org.active_user_count ?? total
                      const inactive = total - active
                      if (inactive > 0) {
                        return (
                          <span title={`${active} active, ${inactive} inactive`}>
                            {active} <span style={{ color: '#64748b', fontSize: '0.78rem' }}>/ {total}</span>
                          </span>
                        )
                      }
                      return total
                    })()}
                  </td>
                  <td style={{ minWidth: 160 }}>
                    <UsageBar used={org.usage_this_month || 0} limit={org.monthly_token_limit} />
                  </td>
                  <td>
                    <span className={`badge ${org.is_active ? 'badge-green' : 'badge-red'}`}>
                      {org.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}
