import { useState, useEffect } from 'react'
import { api } from '../api.js'

export default function PlatformSettingsPage() {
  const [config, setConfig] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [form, setForm] = useState({
    primary_provider: '', primary_model: '', primary_api_key: '',
    complex_provider: '', complex_model: '', complex_api_key: '',
  })
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    api.getPlatformLlm()
      .then((data) => {
        setConfig(data)
        setForm({
          primary_provider: data.config?.primary?.provider || '',
          primary_model:    data.config?.primary?.model    || '',
          primary_api_key:  '',
          complex_provider: data.config?.complex?.provider || '',
          complex_model:    data.config?.complex?.model    || '',
          complex_api_key:  '',
        })
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  async function save(e) {
    e.preventDefault()
    setSaving(true); setSaved(false)
    try {
      await api.updatePlatformLlm({
        primary: {
          provider: form.primary_provider || undefined,
          model:    form.primary_model    || undefined,
          api_key:  form.primary_api_key  || undefined,
        },
        complex: {
          provider: form.complex_provider || undefined,
          model:    form.complex_model    || undefined,
          api_key:  form.complex_api_key  || undefined,
        },
      })
      const fresh = await api.getPlatformLlm()
      setConfig(fresh)
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="loader">Loading…</div>

  return (
    <>
      <div className="page-header">
        <h1 className="page-title">Platform Settings</h1>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      <div className="card">
        <div className="card-title">Global LLM Configuration</div>
        <p style={{ color: '#64748b', fontSize: '0.85rem', marginBottom: 20 }}>
          These defaults apply to all orgs with "Use Platform Global Key" enabled.
          Updating these keys rotates credentials for all such orgs immediately — no redeploy needed.
        </p>

        <form onSubmit={save}>
          {/* Primary LLM */}
          <div style={{ color: '#94a3b8', fontWeight: 600, fontSize: '0.82rem', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Primary LLM <span style={{ color: '#64748b', fontWeight: 400, textTransform: 'none' }}>(standard calls)</span>
          </div>
          <div className="grid-3" style={{ marginBottom: 24 }}>
            <div className="form-group">
              <label className="form-label">Provider</label>
              <select className="form-select" value={form.primary_provider}
                onChange={(e) => setForm((f) => ({ ...f, primary_provider: e.target.value }))}>
                <option value="">-- Select --</option>
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
              </select>
              {config?.config?.primary?.provider && (
                <div style={{ color: '#475569', fontSize: '0.78rem', marginTop: 4 }}>
                  Current: {config.config.primary.provider}
                </div>
              )}
            </div>
            <div className="form-group">
              <label className="form-label">Model</label>
              <input className="form-input" value={form.primary_model} placeholder="e.g. gpt-4.1-mini"
                onChange={(e) => setForm((f) => ({ ...f, primary_model: e.target.value }))} />
              {config?.config?.primary?.model && (
                <div style={{ color: '#475569', fontSize: '0.78rem', marginTop: 4 }}>
                  Current: {config.config.primary.model}
                </div>
              )}
            </div>
            <div className="form-group">
              <label className="form-label">
                API Key{' '}
                {config?.config?.primary?.api_key_set && (
                  <span style={{ color: '#475569' }}>(●●●●●● SET)</span>
                )}
              </label>
              <input className="form-input" type="password" value={form.primary_api_key}
                placeholder="Enter new key to update"
                onChange={(e) => setForm((f) => ({ ...f, primary_api_key: e.target.value }))} />
            </div>
          </div>

          {/* Complex LLM */}
          <div style={{ color: '#94a3b8', fontWeight: 600, fontSize: '0.82rem', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Complex LLM <span style={{ color: '#64748b', fontWeight: 400, textTransform: 'none' }}>(high-reasoning calls — falls back to Primary if not set)</span>
          </div>
          <div className="grid-3" style={{ marginBottom: 24 }}>
            <div className="form-group">
              <label className="form-label">Provider</label>
              <select className="form-select" value={form.complex_provider}
                onChange={(e) => setForm((f) => ({ ...f, complex_provider: e.target.value }))}>
                <option value="">Same as Primary</option>
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
              </select>
              {config?.config?.complex?.provider && (
                <div style={{ color: '#475569', fontSize: '0.78rem', marginTop: 4 }}>
                  Current: {config.config.complex.provider}
                </div>
              )}
            </div>
            <div className="form-group">
              <label className="form-label">Model</label>
              <input className="form-input" value={form.complex_model} placeholder="e.g. claude-sonnet-4-6"
                onChange={(e) => setForm((f) => ({ ...f, complex_model: e.target.value }))} />
              {config?.config?.complex?.model && (
                <div style={{ color: '#475569', fontSize: '0.78rem', marginTop: 4 }}>
                  Current: {config.config.complex.model}
                </div>
              )}
            </div>
            <div className="form-group">
              <label className="form-label">
                API Key{' '}
                {config?.config?.complex?.api_key_set && (
                  <span style={{ color: '#475569' }}>(●●●●●● SET)</span>
                )}
              </label>
              <input className="form-input" type="password" value={form.complex_api_key}
                placeholder="Enter new key to update"
                onChange={(e) => setForm((f) => ({ ...f, complex_api_key: e.target.value }))} />
            </div>
          </div>

          <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
            <button className="btn btn-primary" type="submit" disabled={saving}>
              {saving ? 'Saving…' : 'Save Platform LLM Config'}
            </button>
            {saved && <span className="badge badge-green">Saved</span>}
          </div>
        </form>
      </div>
    </>
  )
}
