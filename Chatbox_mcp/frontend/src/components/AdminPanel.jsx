// src/components/AdminPanel.jsx — Phase 4 Tenant Admin Panel
import { useState, useEffect } from 'react';
import { authFetch as apiFetch, secureFetch } from '../lib/api.js';

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8001';

const PASSWORD_HELP = 'Min 8 characters, one uppercase, one lowercase, one digit, one special character.';

function passwordPolicyError(pw) {
  if (!pw || pw.length < 8) return PASSWORD_HELP;
  if (!/[A-Z]/.test(pw)) return PASSWORD_HELP;
  if (!/[a-z]/.test(pw)) return PASSWORD_HELP;
  if (!/\d/.test(pw)) return PASSWORD_HELP;
  if (!/[^A-Za-z0-9]/.test(pw)) return PASSWORD_HELP;
  return '';
}

// Shared modal: when `requireCurrent` is true, asks for current password (self-change).
function ChangePasswordModal({ title, subtitle, requireCurrent, onClose, onSubmit }) {
  const [current, setCurrent] = useState('');
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [showCurrent, setShowCurrent] = useState(false);
  const [showPw, setShowPw] = useState(false);
  const [showPw2, setShowPw2] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const policyErr = pw ? passwordPolicyError(pw) : '';
  const matchErr = (pw && pw2 && pw !== pw2) ? 'Passwords do not match' : '';
  const canSubmit = pw && pw === pw2 && !policyErr && (!requireCurrent || current);

  async function handleSubmit(e) {
    e.preventDefault();
    setError('');
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      await onSubmit({ current, newPassword: pw });
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  const inputWrap = { display: 'flex', gap: 6, alignItems: 'stretch' };
  const eyeBtn = {
    background: 'var(--surface-2, #1e293b)', color: 'var(--text-secondary, #94a3b8)',
    border: '1px solid var(--border, #334155)', padding: '0 12px', borderRadius: 6, cursor: 'pointer',
    fontSize: '0.78rem',
  };

  return (
    <div
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(2,6,23,0.7)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 2000,
      }}
    >
      <div className="admin-panel" style={{ width: '100%', maxWidth: 460, padding: 20, height: 'auto' }}>
        <div className="admin-panel-header" style={{ marginBottom: 12 }}>
          <span className="admin-panel-title">{title || 'Change Password'}</span>
          <button className="admin-panel-close" onClick={onClose}>✕</button>
        </div>
        {subtitle && (
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', marginTop: 0, marginBottom: 14 }}>
            {subtitle}
          </p>
        )}

        <form onSubmit={handleSubmit}>
          {requireCurrent && (
            <div style={{ marginBottom: 12 }}>
              <label className="admin-label" style={{ display: 'block', marginBottom: 4 }}>Current Password</label>
              <div style={inputWrap}>
                <input
                  className="admin-input"
                  type={showCurrent ? 'text' : 'password'}
                  value={current}
                  onChange={(e) => setCurrent(e.target.value)}
                  autoFocus
                  style={{ flex: 1 }}
                />
                <button type="button" style={eyeBtn} onClick={() => setShowCurrent((v) => !v)}>
                  {showCurrent ? 'Hide' : 'Show'}
                </button>
              </div>
            </div>
          )}

          <div style={{ marginBottom: 12 }}>
            <label className="admin-label" style={{ display: 'block', marginBottom: 4 }}>New Password</label>
            <div style={inputWrap}>
              <input
                className="admin-input"
                type={showPw ? 'text' : 'password'}
                value={pw}
                onChange={(e) => setPw(e.target.value)}
                autoFocus={!requireCurrent}
                style={{ flex: 1 }}
              />
              <button type="button" style={eyeBtn} onClick={() => setShowPw((v) => !v)}>
                {showPw ? 'Hide' : 'Show'}
              </button>
            </div>
          </div>

          <div style={{ marginBottom: 12 }}>
            <label className="admin-label" style={{ display: 'block', marginBottom: 4 }}>Confirm Password</label>
            <div style={inputWrap}>
              <input
                className="admin-input"
                type={showPw2 ? 'text' : 'password'}
                value={pw2}
                onChange={(e) => setPw2(e.target.value)}
                style={{ flex: 1 }}
              />
              <button type="button" style={eyeBtn} onClick={() => setShowPw2((v) => !v)}>
                {showPw2 ? 'Hide' : 'Show'}
              </button>
            </div>
          </div>

          {(policyErr || matchErr) ? (
            <div style={{ color: '#fbbf24', fontSize: '0.8rem', marginBottom: 12 }}>{matchErr || policyErr}</div>
          ) : (
            <div style={{ color: 'var(--text-secondary)', fontSize: '0.78rem', marginBottom: 12 }}>{PASSWORD_HELP}</div>
          )}

          {error && <div className="admin-error" style={{ marginBottom: 12 }}>{error}</div>}

          <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
            <button type="button" className="admin-btn-sm admin-btn-outline" onClick={onClose}>Cancel</button>
            <button type="submit" className="admin-btn-sm admin-btn-primary" disabled={!canSubmit || submitting}>
              {submitting ? 'Saving…' : 'Save Password'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

const AGENT_LABELS = {
  schedule: 'Schedule',
  invoice: 'Invoice',
  workorder: 'Work Order',
  purchase_order: 'Purchase Order',
};

const OP_LABELS = {
  query: 'View',
  create: 'Create',
  update: 'Update',
  delete: 'Delete',
  lock: 'Lock',
  unlock: 'Unlock',
};

// apiFetch is now imported from '../lib/api.js' (as authFetch → apiFetch)
// It uses HttpOnly cookies (credentials: 'include') and sanitizes errors.

// Reactivate modal: optional new password (default checked).
function ReactivateModal({ user, onClose, onSubmit }) {
  const [resetPassword, setResetPassword] = useState(true);
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [showPw2, setShowPw2] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const policyErr = (resetPassword && pw) ? passwordPolicyError(pw) : '';
  const matchErr = (resetPassword && pw && pw2 && pw !== pw2) ? 'Passwords do not match' : '';
  const canSubmit = resetPassword
    ? (pw && pw === pw2 && !policyErr)
    : true;

  async function handleSubmit(e) {
    e.preventDefault();
    setError('');
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      await onSubmit({ newPassword: resetPassword ? pw : null });
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  const inputWrap = { display: 'flex', gap: 6, alignItems: 'stretch' };
  const eyeBtn = {
    background: 'var(--surface-2, #1e293b)', color: 'var(--text-secondary, #94a3b8)',
    border: '1px solid var(--border, #334155)', padding: '0 12px', borderRadius: 6, cursor: 'pointer',
    fontSize: '0.78rem',
  };

  return (
    <div
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(2,6,23,0.7)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 2000,
      }}
    >
      <div className="admin-panel" style={{ width: '100%', maxWidth: 460, padding: 20, height: 'auto' }}>
        <div className="admin-panel-header" style={{ marginBottom: 12 }}>
          <span className="admin-panel-title">Reactivate User</span>
          <button className="admin-panel-close" onClick={onClose}>✕</button>
        </div>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', marginTop: 0, marginBottom: 14 }}>
          Reactivating <strong style={{ color: 'var(--text-primary)' }}>{user?.email}</strong> will restore
          their previous role and history. Password and all data are preserved.
        </p>

        <form onSubmit={handleSubmit}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14, fontSize: '0.88rem', color: 'var(--text-primary)' }}>
            <input
              type="checkbox"
              checked={resetPassword}
              onChange={(e) => setResetPassword(e.target.checked)}
            />
            <span>Set a new password (recommended if user may have forgotten the old one)</span>
          </label>

          {resetPassword && (
            <>
              <div style={{ marginBottom: 12 }}>
                <label className="admin-label" style={{ display: 'block', marginBottom: 4 }}>New Password</label>
                <div style={inputWrap}>
                  <input
                    className="admin-input"
                    type={showPw ? 'text' : 'password'}
                    value={pw}
                    onChange={(e) => setPw(e.target.value)}
                    autoFocus
                    style={{ flex: 1 }}
                  />
                  <button type="button" style={eyeBtn} onClick={() => setShowPw((v) => !v)}>
                    {showPw ? 'Hide' : 'Show'}
                  </button>
                </div>
              </div>

              <div style={{ marginBottom: 12 }}>
                <label className="admin-label" style={{ display: 'block', marginBottom: 4 }}>Confirm Password</label>
                <div style={inputWrap}>
                  <input
                    className="admin-input"
                    type={showPw2 ? 'text' : 'password'}
                    value={pw2}
                    onChange={(e) => setPw2(e.target.value)}
                    style={{ flex: 1 }}
                  />
                  <button type="button" style={eyeBtn} onClick={() => setShowPw2((v) => !v)}>
                    {showPw2 ? 'Hide' : 'Show'}
                  </button>
                </div>
              </div>

              {(policyErr || matchErr) ? (
                <div style={{ color: '#fbbf24', fontSize: '0.8rem', marginBottom: 12 }}>{matchErr || policyErr}</div>
              ) : (
                <div style={{ color: 'var(--text-secondary)', fontSize: '0.78rem', marginBottom: 12 }}>{PASSWORD_HELP}</div>
              )}
            </>
          )}

          {error && <div className="admin-error" style={{ marginBottom: 12 }}>{error}</div>}

          <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
            <button type="button" className="admin-btn-sm admin-btn-outline" onClick={onClose}>Cancel</button>
            <button type="submit" className="admin-btn-sm admin-btn-primary" disabled={!canSubmit || submitting}>
              {submitting ? 'Reactivating…' : 'Reactivate'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ─── Members Tab ─────────────────────────────────────────────────────────────

function MembersTab({ token, roles, currentUser, onUserChange }) {
  const [members, setMembers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showInvite, setShowInvite] = useState(false);
  const [inviteForm, setInviteForm] = useState({ email: '', name: '', role_id: '' });
  const [inviting, setInviting] = useState(false);
  const [newCreds, setNewCreds] = useState(null);
  const [pwUser, setPwUser] = useState(null);
  const [reactivateUser, setReactivateUser] = useState(null);

  const isAdmin = currentUser?.role === 'admin';
  // Active admin count for last-admin guards in the UI.
  const adminCount = members.filter((m) => m.role === 'admin' && m.is_active !== 0).length;

  // Refresh local user state from the backend after role changes.
  function refreshMe() {
    if (!onUserChange) return Promise.resolve();
    return apiFetch('/me', token).then((u) => onUserChange(u)).catch(() => {});
  }

  const loadMembers = () => {
    setLoading(true);
    apiFetch('/org/members', token)
      .then(d => setMembers(d.members || []))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { loadMembers(); }, []);

  async function handleInvite(e) {
    e.preventDefault();
    if (!inviteForm.email || !inviteForm.role_id) { setError('Email and role are required'); return; }
    setInviting(true); setError('');
    try {
      const result = await apiFetch('/org/members/invite', token, {
        method: 'POST',
        body: JSON.stringify({ ...inviteForm, role_id: parseInt(inviteForm.role_id) }),
      });
      // Existing user linked: just refresh, no temp password to display.
      if (result.linked_existing) {
        setError(`User ${result.email} already had an account in another organisation and was linked here.`);
      } else if (result.temp_password) {
        setNewCreds(result);
      }
      setInviteForm({ email: '', name: '', role_id: '' });
      setShowInvite(false);
      loadMembers();
    } catch (e) {
      // Handle "deactivated user exists" 409: offer to reactivate instead.
      if (e.status === 409 && e.detail && typeof e.detail === 'object'
          && e.detail.kind === 'deactivated_member_exists') {
        if (confirm(
          `${e.detail.email} exists in this organisation but is deactivated.\n\n`
          + `Reactivate them instead?`
        )) {
          setReactivateUser({ id: e.detail.user_id, email: e.detail.email });
          setShowInvite(false);
          setInviteForm({ email: '', name: '', role_id: '' });
        }
      } else {
        setError(e.message);
      }
    }
    finally { setInviting(false); }
  }

  async function handleRoleChange(member, newRoleIdStr) {
    setError('');
    const newRoleId = parseInt(newRoleIdStr);
    const newRole = roles.find((r) => r.id === newRoleId);
    if (!newRole) return;

    const wasAdmin = member.role === 'admin';
    const willBeAdmin = !!newRole.is_system;

    // Confirmation dialogs for high-impact role changes.
    if (!wasAdmin && willBeAdmin) {
      if (!confirm(`Promote ${member.email} to admin? They will gain full administrative access.`)) {
        return;
      }
    } else if (wasAdmin && !willBeAdmin) {
      const isSelf = member.id === currentUser?.id;
      const msg = isSelf
        ? `Demote yourself from admin to "${newRole.name}"? You will immediately lose admin privileges.`
        : `Demote ${member.email} from admin to "${newRole.name}"? They will lose admin privileges.`;
      if (!confirm(msg)) return;
    }

    try {
      await apiFetch(`/org/members/${member.id}/role`, token, {
        method: 'PUT',
        body: JSON.stringify({ role_id: newRoleId }),
      });
      // If the changed user is the current user, refresh /me so the UI reflects the new role.
      if (member.id === currentUser?.id) {
        await refreshMe();
      }
      loadMembers();
    } catch (e) { setError(e.message); }
  }

  async function handleDeactivate(member) {
    if (member.id === currentUser?.id) {
      setError('You cannot deactivate your own account.');
      return;
    }
    if (!confirm(
      `Deactivate ${member.email}?\n\n`
      + `They will lose access immediately. You can reactivate them later — their role, password, and history are preserved.`
    )) return;
    setError('');
    try {
      await apiFetch(`/org/members/${member.id}/deactivate`, token, { method: 'POST' });
      loadMembers();
    } catch (e) { setError(e.message); }
  }

  async function handleReactivate({ newPassword }) {
    if (!reactivateUser) return;
    const result = await apiFetch(`/org/members/${reactivateUser.id}/activate`, token, {
      method: 'POST',
      body: JSON.stringify({ new_password: newPassword || null }),
    });
    setReactivateUser(null);
    loadMembers();
    if (result.role_fallback) {
      setError(`Reactivated. Note: their previous role no longer exists; reassigned to "${result.role_fallback}".`);
    }
  }

  async function handlePasswordSubmit({ current, newPassword }) {
    if (!pwUser) return;
    const isSelf = pwUser.id === currentUser?.id;
    if (isSelf) {
      await apiFetch('/me/password', token, {
        method: 'PUT',
        body: JSON.stringify({ current_password: current, new_password: newPassword }),
      });
    } else {
      await apiFetch(`/org/members/${pwUser.id}/password`, token, {
        method: 'PUT',
        body: JSON.stringify({ new_password: newPassword }),
      });
    }
    setPwUser(null);
  }

  return (
    <div className="admin-tab-content">
      {error && <div className="admin-error">{error}</div>}

      {newCreds && (
        <div className="admin-success-box">
          <div style={{ fontWeight: 600, marginBottom: 8 }}>User created — share credentials</div>
          <div style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', marginBottom: 12 }}>
            This password cannot be retrieved again. Copy and send manually.
          </div>
          <div className="admin-cred-row"><span>Email</span><code>{newCreds.email}</code></div>
          <div className="admin-cred-row"><span>Password</span><code className="admin-temp-pass">{newCreds.temp_password}</code></div>
          <button className="admin-btn-sm admin-btn-outline" style={{ marginTop: 10 }} onClick={() => setNewCreds(null)}>Dismiss</button>
        </div>
      )}

      <div className="admin-section-header">
        <span>Members ({members.length})</span>
        <button className="admin-btn-sm admin-btn-primary" onClick={() => setShowInvite(v => !v)}>
          {showInvite ? 'Cancel' : '+ Invite Member'}
        </button>
      </div>

      {showInvite && (
        <form className="admin-invite-form" onSubmit={handleInvite}>
          <input
            className="admin-input"
            placeholder="Email *"
            type="email"
            value={inviteForm.email}
            onChange={e => setInviteForm(f => ({ ...f, email: e.target.value }))}
            required
          />
          <input
            className="admin-input"
            placeholder="Full name"
            value={inviteForm.name}
            onChange={e => setInviteForm(f => ({ ...f, name: e.target.value }))}
          />
          <select
            className="admin-select"
            value={inviteForm.role_id}
            onChange={e => setInviteForm(f => ({ ...f, role_id: e.target.value }))}
            required
          >
            <option value="">Select role *</option>
            {roles.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
          <button className="admin-btn-sm admin-btn-primary" type="submit" disabled={inviting}>
            {inviting ? 'Creating…' : 'Create & Get Password'}
          </button>
        </form>
      )}

      {loading ? (
        <div className="admin-loading">Loading members…</div>
      ) : (
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr><th>Member</th><th>Role</th><th>Joined</th><th></th></tr>
            </thead>
            <tbody>
              {members.map(m => {
                const isSelf = m.id === currentUser?.id;
                const isMemberAdmin = m.role === 'admin';
                const isInactive = m.is_active === 0;

                // Password change: own row always (active only); admin → others (active only).
                const canChangePassword = !isInactive && (isAdmin || isSelf);

                // Role select disabled for inactive (reactivate first), or sole-admin self.
                const canChangeRole = isAdmin && !isInactive && (
                  !isSelf || !isMemberAdmin || adminCount > 1
                );

                // Deactivate: admin → other active member (any role; atomic last-admin guard server-side).
                const canDeactivate = isAdmin && !isSelf && !isInactive;
                // Activate: admin → other inactive member.
                const canActivate = isAdmin && !isSelf && isInactive;

                return (
                  <tr key={m.id} style={isInactive ? { opacity: 0.55 } : undefined}>
                    <td>
                      <div className="admin-member-name">
                        {m.name || m.email.split('@')[0]}
                        {isMemberAdmin && (
                          <span className="admin-pill" style={{ marginLeft: 8, background: '#fbbf2433', color: '#fbbf24', padding: '1px 6px', borderRadius: 4, fontSize: '0.65rem', fontWeight: 600 }}>
                            ADMIN
                          </span>
                        )}
                        {isInactive && (
                          <span className="admin-pill" style={{ marginLeft: 8, background: '#64748b33', color: '#94a3b8', padding: '1px 6px', borderRadius: 4, fontSize: '0.65rem', fontWeight: 600 }}>
                            INACTIVE
                          </span>
                        )}
                        {isSelf && (
                          <span style={{ marginLeft: 6, color: 'var(--text-secondary)', fontSize: '0.7rem' }}>(you)</span>
                        )}
                      </div>
                      <div className="admin-member-email">{m.email}</div>
                      {isInactive && m.deactivated_at_ist && (
                        <div style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', marginTop: 2 }}>
                          Deactivated {m.deactivated_at_ist.split(' ')[0]}
                          {m.deactivated_by_email ? ` by ${m.deactivated_by_email}` : ''}
                        </div>
                      )}
                    </td>
                    <td>
                      {canChangeRole ? (
                        <select
                          className="admin-select-inline"
                          value={m.role_id || ''}
                          onChange={e => handleRoleChange(m, e.target.value)}
                          title={isSelf && isMemberAdmin && adminCount <= 1
                            ? 'You are the only admin. Promote another user first.'
                            : ''}
                        >
                          {roles.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
                        </select>
                      ) : (
                        <span style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
                          {m.role_name || m.role || '—'}
                          {isSelf && isMemberAdmin && adminCount <= 1 && !isInactive && (
                            <span style={{ display: 'block', fontSize: '0.7rem', color: 'var(--text-tertiary, #64748b)', marginTop: 2 }}>
                              Sole admin — promote another user to change role
                            </span>
                          )}
                          {isInactive && (
                            <span style={{ display: 'block', fontSize: '0.7rem', color: 'var(--text-tertiary, #64748b)', marginTop: 2 }}>
                              Reactivate to change role
                            </span>
                          )}
                        </span>
                      )}
                    </td>
                    <td style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                      {m.joined_at_ist ? m.joined_at_ist.split(' ')[0] : '—'}
                    </td>
                    <td>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        {canChangePassword && (
                          <button
                            className="admin-btn-sm admin-btn-outline"
                            onClick={() => setPwUser(m)}
                          >
                            {isSelf ? 'Change My Password' : 'Change Password'}
                          </button>
                        )}
                        {canDeactivate && (
                          <button
                            className="admin-btn-sm admin-btn-danger"
                            onClick={() => handleDeactivate(m)}
                          >
                            Deactivate
                          </button>
                        )}
                        {canActivate && (
                          <button
                            className="admin-btn-sm admin-btn-primary"
                            onClick={() => setReactivateUser(m)}
                          >
                            Reactivate
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {pwUser && (
        <ChangePasswordModal
          title="Change Password"
          subtitle={
            pwUser.id === currentUser?.id
              ? 'Change your own password.'
              : `For ${pwUser.email}`
          }
          requireCurrent={pwUser.id === currentUser?.id}
          onClose={() => setPwUser(null)}
          onSubmit={handlePasswordSubmit}
        />
      )}

      {reactivateUser && (
        <ReactivateModal
          user={reactivateUser}
          onClose={() => setReactivateUser(null)}
          onSubmit={handleReactivate}
        />
      )}
    </div>
  );
}

// ─── Permission Matrix ────────────────────────────────────────────────────────

function PermMatrix({ agentOps, permissions, onChange }) {
  // Build lookup: { "schedule.create": true, ... }
  const lookup = {};
  (permissions || []).forEach(p => {
    lookup[`${p.agent_name}.${p.operation}`] = Boolean(p.is_allowed);
  });

  const agents = Object.keys(agentOps || {});

  function toggle(agent, op) {
    const key = `${agent}.${op}`;
    const current = lookup[key] !== false; // default true
    // Build updated permissions list
    const updated = [];
    const seen = new Set();
    (permissions || []).forEach(p => {
      const k = `${p.agent_name}.${p.operation}`;
      if (k === key) {
        updated.push({ ...p, is_allowed: !current });
      } else {
        updated.push(p);
      }
      seen.add(k);
    });
    if (!seen.has(key)) {
      updated.push({ agent_name: agent, operation: op, is_allowed: !current });
    }
    onChange(updated);
  }

  return (
    <div className="admin-perm-matrix">
      <table className="admin-table">
        <thead>
          <tr>
            <th>Agent</th>
            {Array.from(new Set(Object.values(agentOps || {}).flat())).map(op => (
              <th key={op} style={{ textAlign: 'center', fontSize: '0.75rem' }}>
                {OP_LABELS[op] || op}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {agents.map(agent => {
            const ops = agentOps[agent] || [];
            const allOps = Array.from(new Set(Object.values(agentOps).flat()));
            return (
              <tr key={agent}>
                <td style={{ fontWeight: 500 }}>{AGENT_LABELS[agent] || agent}</td>
                {allOps.map(op => {
                  const applicable = ops.includes(op);
                  const key = `${agent}.${op}`;
                  const allowed = lookup[key] !== false;
                  return (
                    <td key={op} style={{ textAlign: 'center' }}>
                      {applicable ? (
                        <input
                          type="checkbox"
                          checked={allowed}
                          onChange={() => toggle(agent, op)}
                          style={{ cursor: 'pointer', width: 16, height: 16, accentColor: 'var(--primary)' }}
                        />
                      ) : (
                        <span style={{ color: 'var(--border)', fontSize: '0.7rem' }}>—</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ─── Roles Tab ────────────────────────────────────────────────────────────────

// Reserved-name check (mirrors backend):
// blocks "admin", "Admin", "ADMIN", "admin-2", "admin_3", "admin 4", etc.
// but allows "Site Admin", "Admin Assistant", "Operations Admin Lead".
const RESERVED_ADMIN_PATTERN = /^[\s_\-]*admin[\s_\-]*\d*$/i;
function isReservedRoleName(name) {
  return RESERVED_ADMIN_PATTERN.test((name || '').trim());
}

function RolesTab({ token, roles, agentOps, reloadRoles }) {
  const [error, setError] = useState('');
  const [editingRole, setEditingRole] = useState(null); // role object being edited
  const [showCreate, setShowCreate] = useState(false);
  const [newRoleName, setNewRoleName] = useState('');
  const [newRolePerms, setNewRolePerms] = useState([]);
  const [saving, setSaving] = useState(false);

  async function handleCreate(e) {
    e.preventDefault();
    if (!newRoleName.trim()) { setError('Role name is required'); return; }
    if (isReservedRoleName(newRoleName)) {
      setError("The name 'admin' is reserved. Choose a different role name.");
      return;
    }
    setSaving(true); setError('');
    try {
      await apiFetch('/org/roles', token, {
        method: 'POST',
        body: JSON.stringify({ name: newRoleName.trim(), permissions: newRolePerms }),
      });
      setNewRoleName(''); setNewRolePerms([]); setShowCreate(false);
      await reloadRoles();
    } catch (e) { setError(e.message); }
    finally { setSaving(false); }
  }

  async function handleSaveEdit() {
    if (!editingRole) return;
    if (isReservedRoleName(editingRole.name)) {
      setError("The name 'admin' is reserved. Choose a different role name.");
      return;
    }
    setSaving(true); setError('');
    try {
      await apiFetch(`/org/roles/${editingRole.id}`, token, {
        method: 'PUT',
        body: JSON.stringify({ name: editingRole.name, permissions: editingRole.permissions }),
      });
      setEditingRole(null);
      await reloadRoles();
    } catch (e) { setError(e.message); }
    finally { setSaving(false); }
  }

  async function handleDelete(roleId, roleName) {
    if (!confirm(`Delete role "${roleName}"? This cannot be undone.`)) return;
    setError('');
    try {
      await apiFetch(`/org/roles/${roleId}`, token, { method: 'DELETE' });
      await reloadRoles();
    } catch (e) { setError(e.message); }
  }

  if (!roles) return <div className="admin-loading">Loading roles…</div>;

  return (
    <div className="admin-tab-content">
      {error && <div className="admin-error">{error}</div>}

      <div className="admin-section-header">
        <span>Roles ({roles.length})</span>
        <button className="admin-btn-sm admin-btn-primary" onClick={() => { setShowCreate(v => !v); setEditingRole(null); }}>
          {showCreate ? 'Cancel' : '+ New Role'}
        </button>
      </div>

      {showCreate && (
        <form className="admin-role-form" onSubmit={handleCreate}>
          <input
            className="admin-input"
            placeholder="Role name (e.g. Supervisor)"
            value={newRoleName}
            onChange={e => setNewRoleName(e.target.value)}
            required
          />
          <div className="admin-perm-label">Permissions</div>
          <PermMatrix agentOps={agentOps} permissions={newRolePerms} onChange={setNewRolePerms} />
          <button className="admin-btn-sm admin-btn-primary" type="submit" disabled={saving}>
            {saving ? 'Creating…' : 'Create Role'}
          </button>
        </form>
      )}

      {roles.map(role => (
        <div key={role.id} className="admin-role-card">
          {editingRole?.id === role.id ? (
            <>
              <div className="admin-role-header">
                <input
                  className="admin-input"
                  value={editingRole.name}
                  onChange={e => setEditingRole(r => ({ ...r, name: e.target.value }))}
                  disabled={role.is_system}
                />
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="admin-btn-sm admin-btn-primary" onClick={handleSaveEdit} disabled={saving}>
                    {saving ? 'Saving…' : 'Save'}
                  </button>
                  <button className="admin-btn-sm admin-btn-outline" onClick={() => setEditingRole(null)}>Cancel</button>
                </div>
              </div>
              <PermMatrix
                agentOps={agentOps}
                permissions={editingRole.permissions || []}
                onChange={perms => setEditingRole(r => ({ ...r, permissions: perms }))}
              />
            </>
          ) : (
            <div className="admin-role-header">
              <div>
                <span className="admin-role-name">{role.name}</span>
                {role.is_system ? (
                  <span className="admin-badge admin-badge-indigo" style={{ marginLeft: 8 }}>System</span>
                ) : null}
                <span className="admin-badge admin-badge-gray" style={{ marginLeft: 6 }}>{role.user_count} user{role.user_count !== 1 ? 's' : ''}</span>
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                {role.is_system ? (
                  <span style={{ color: 'var(--text-secondary)', fontSize: '0.78rem' }}>
                    Built-in admin role — locked
                  </span>
                ) : (
                  <>
                    <button className="admin-btn-sm admin-btn-outline" onClick={() => {
                      setShowCreate(false);
                      setEditingRole({ ...role });
                    }}>
                      Edit
                    </button>
                    <button className="admin-btn-sm admin-btn-danger" onClick={() => handleDelete(role.id, role.name)}>
                      Delete
                    </button>
                  </>
                )}
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ─── Usage Tab ────────────────────────────────────────────────────────────────

function UsageTab({ token }) {
  const [usage, setUsage] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiFetch('/usage', token)
      .then(setUsage)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="admin-loading">Loading usage…</div>;
  if (!usage) return <div className="admin-error">Could not load usage data.</div>;

  const pct = usage.monthly_limit ? Math.round(((usage.total_input_tokens + usage.total_output_tokens) / usage.monthly_limit) * 100) : 0;

  return (
    <div className="admin-tab-content">
      <div className="admin-usage-summary">
        <div className="admin-usage-stat">
          <span className="admin-usage-label">Total used this month</span>
          <span className="admin-usage-value">{((usage.total_input_tokens + usage.total_output_tokens) || 0).toLocaleString()}</span>
        </div>
        <div className="admin-usage-stat">
          <span className="admin-usage-label">Monthly limit</span>
          <span className="admin-usage-value">{usage.monthly_limit ? usage.monthly_limit.toLocaleString() : '∞'}</span>
        </div>
        <div className="admin-usage-stat">
          <span className="admin-usage-label">Usage</span>
          <span className="admin-usage-value" style={{ color: pct >= 90 ? '#f87171' : pct >= 70 ? '#fbbf24' : 'var(--text-primary)' }}>
            {pct}%
          </span>
        </div>
      </div>

      {usage.agents && (
        <div className="admin-table-wrap" style={{ marginTop: 16 }}>
          <table className="admin-table">
            <thead>
              <tr><th>Agent</th><th>Tokens Used</th><th>Limit</th><th>Status</th></tr>
            </thead>
            <tbody>
              {Object.entries(usage.agents).map(([name, a]) => {
                const agentPct = a.limit ? Math.round((a.used / a.limit) * 100) : 0;
                return (
                  <tr key={name}>
                    <td style={{ fontWeight: 500 }}>{AGENT_LABELS[name] || name}</td>
                    <td>{(a.used || 0).toLocaleString()}</td>
                    <td>{a.limit ? a.limit.toLocaleString() : <span className="admin-badge admin-badge-indigo">No limit</span>}</td>
                    <td>
                      <span className={`admin-badge ${a.is_enabled ? 'admin-badge-green' : 'admin-badge-red'}`}>
                        {a.is_enabled ? 'Enabled' : 'Disabled'}
                      </span>
                      {a.limit > 0 && agentPct >= 100 && (
                        <span className="admin-badge admin-badge-red" style={{ marginLeft: 6 }}>Limit reached</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ─── SOPs Tab ─────────────────────────────────────────────────────────────────

const AGENT_NAMES = ['schedule', 'invoice', 'workorder', 'purchase_order'];

function SopsTab({ token }) {
  const [sops, setSops] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [uploading, setUploading] = useState(null); // agent_name being uploaded
  const [preview, setPreview] = useState(null); // { agent_name, text }

  const loadSops = () => {
    setLoading(true);
    apiFetch('/org/sops', token)
      .then(d => setSops(d.sops || []))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { loadSops(); }, []);

  async function handleUpload(agentName, file) {
    if (!file) return;
    setUploading(agentName);
    setError('');
    const form = new FormData();
    form.append('file', file);
    try {
      const r = await secureFetch(`${BACKEND_URL}/api/auth/org/sops/${agentName}`, {
        method: 'POST',
        body: form,
      });
      const data = await r.json().catch(() => ({}));
      loadSops();
    } catch (e) {
      setError(e.message);
    } finally {
      setUploading(null);
    }
  }

  async function handleReset(agentName) {
    if (!confirm(`Reset ${AGENT_LABELS[agentName]} SOP to default?`)) return;
    setError('');
    try {
      await apiFetch(`/org/sops/${agentName}`, token, { method: 'DELETE' });
      setPreview(null);
      loadSops();
    } catch (e) {
      setError(e.message);
    }
  }

  async function handlePreview(agentName) {
    if (preview?.agent_name === agentName) { setPreview(null); return; }
    try {
      const d = await apiFetch(`/org/sops/${agentName}/text`, token);
      setPreview({ agent_name: agentName, text: d.text || '(no custom SOP)' });
    } catch (e) {
      setError(e.message);
    }
  }

  if (loading) return <div className="admin-loading">Loading SOPs…</div>;

  return (
    <div>
      {error && <div className="admin-error">{error}<button onClick={() => setError('')} style={{marginLeft:8}}>✕</button></div>}
      <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', marginBottom: 16 }}>
        Upload a custom SOP file (.md, .txt, or .docx) to override an agent's default business rules for your organisation.
        Only admins can upload or reset SOPs.
      </p>
      <table className="admin-table">
        <thead>
          <tr>
            <th>Agent</th>
            <th>Status</th>
            <th>File</th>
            <th>Uploaded</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {AGENT_NAMES.map(name => {
            const row = sops.find(s => s.agent_name === name);
            const isCustom = row?.status === 'custom';
            return (
              <tr key={name}>
                <td>{AGENT_LABELS[name] || name}</td>
                <td>
                  <span className={`admin-badge-${isCustom ? 'success' : 'neutral'}`}>
                    {isCustom ? 'Custom' : 'Default'}
                  </span>
                </td>
                <td style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                  {isCustom ? row.original_filename || '—' : '—'}
                  {isCustom && row.char_count && (
                    <span style={{ marginLeft: 6, opacity: 0.6 }}>({row.char_count.toLocaleString()} chars)</span>
                  )}
                </td>
                <td style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                  {isCustom ? new Date(row.uploaded_at).toLocaleDateString() : '—'}
                </td>
                <td style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  <label className="admin-btn-primary" style={{ cursor: 'pointer', padding: '4px 10px', fontSize: '0.8rem' }}>
                    {uploading === name ? 'Uploading…' : 'Upload'}
                    <input
                      type="file"
                      accept=".md,.txt,.docx"
                      style={{ display: 'none' }}
                      disabled={!!uploading}
                      onChange={e => handleUpload(name, e.target.files[0])}
                    />
                  </label>
                  {isCustom && (
                    <>
                      <button
                        className="admin-btn-secondary"
                        style={{ padding: '4px 10px', fontSize: '0.8rem' }}
                        onClick={() => handlePreview(name)}
                      >
                        {preview?.agent_name === name ? 'Hide' : 'Preview'}
                      </button>
                      <button
                        className="admin-btn-danger"
                        style={{ padding: '4px 10px', fontSize: '0.8rem' }}
                        onClick={() => handleReset(name)}
                      >
                        Reset to Default
                      </button>
                    </>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {preview && (
        <div style={{ marginTop: 16, background: 'var(--bg-secondary)', borderRadius: 8, padding: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
            <strong style={{ fontSize: '0.9rem' }}>{AGENT_LABELS[preview.agent_name]} SOP Preview</strong>
            <button className="admin-btn-secondary" style={{ padding: '2px 8px', fontSize: '0.8rem' }} onClick={() => setPreview(null)}>Close</button>
          </div>
          <pre style={{
            fontSize: '0.78rem',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            maxHeight: 300,
            overflowY: 'auto',
            color: 'var(--text-primary)',
            margin: 0,
          }}>
            {preview.text?.slice(0, 2000)}{preview.text?.length > 2000 ? '\n\n…(truncated to 2000 chars for preview)' : ''}
          </pre>
        </div>
      )}
    </div>
  );
}

// ─── Departments Tab ──────────────────────────────────────────────────────────

function DepartmentsTab({ token }) {
  const [mapping, setMapping] = useState(null);      // {dept: [acct, ...]}
  const [warnings, setWarnings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [validating, setValidating] = useState(false);
  // local editable copy: [{dept: string, accounts: string}]
  const [rows, setRows] = useState([]);
  const [newDept, setNewDept] = useState('');
  const [newAccts, setNewAccts] = useState('');

  function mappingToRows(m) {
    return Object.entries(m || {}).map(([dept, accts]) => ({
      dept,
      accounts: Array.isArray(accts) ? accts.join(', ') : accts,
    }));
  }

  function rowsToMapping(r) {
    const m = {};
    r.forEach(({ dept, accounts }) => {
      if (!dept.trim()) return;
      m[dept.trim()] = accounts.split(',').map(s => s.trim()).filter(Boolean);
    });
    return m;
  }

  const load = () => {
    setLoading(true);
    apiFetch('/org/department-mapping', token)
      .then(d => {
        setMapping(d.mapping || {});
        setWarnings(d.drift_warnings || []);
        setRows(mappingToRows(d.mapping || {}));
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  async function handleSave() {
    setSaving(true);
    setError('');
    try {
      await apiFetch('/org/department-mapping', token, {
        method: 'PUT',
        body: JSON.stringify({ mapping: rowsToMapping(rows) }),
      });
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleValidate() {
    setValidating(true);
    setError('');
    try {
      const d = await apiFetch('/org/department-mapping/validate', token, { method: 'POST' });
      setWarnings(d.drift_warnings || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setValidating(false);
    }
  }

  async function handleDismissWarnings() {
    try {
      await apiFetch('/org/department-mapping/warnings', token, { method: 'DELETE' });
      setWarnings([]);
    } catch (e) {
      setError(e.message);
    }
  }

  function updateRow(idx, field, val) {
    setRows(r => r.map((row, i) => i === idx ? { ...row, [field]: val } : row));
  }

  function removeRow(idx) {
    setRows(r => r.filter((_, i) => i !== idx));
  }

  function addRow() {
    if (!newDept.trim()) return;
    setRows(r => [...r, { dept: newDept.trim(), accounts: newAccts.trim() }]);
    setNewDept('');
    setNewAccts('');
  }

  if (loading) return <div className="admin-loading">Loading departments…</div>;

  return (
    <div>
      {error && (
        <div className="admin-error">
          {error}
          <button onClick={() => setError('')} style={{ marginLeft: 8 }}>✕</button>
        </div>
      )}

      {warnings.length > 0 && (
        <div style={{ background: '#fef3c7', border: '1px solid #f59e0b', borderRadius: 8, padding: '12px 16px', marginBottom: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <strong style={{ color: '#92400e', fontSize: '0.88rem' }}>⚠ Department Mapping Warnings</strong>
            <button className="admin-btn-secondary" style={{ padding: '2px 8px', fontSize: '0.78rem' }} onClick={handleDismissWarnings}>Dismiss</button>
          </div>
          <ul style={{ margin: '8px 0 0 0', paddingLeft: 18 }}>
            {warnings.map((w, i) => (
              <li key={i} style={{ color: '#78350f', fontSize: '0.82rem', marginBottom: 4 }}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', marginBottom: 12 }}>
        Map department names to Simpro chart-of-accounts numbers. Used to filter schedules by department.
        Only admins can edit this mapping.
      </p>

      <table className="admin-table" style={{ marginBottom: 16 }}>
        <thead>
          <tr>
            <th>Department Name</th>
            <th>Account Numbers (comma-separated)</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => (
            <tr key={idx}>
              <td>
                <input
                  className="form-input"
                  style={{ width: '100%', fontSize: '0.85rem', padding: '4px 8px' }}
                  value={row.dept}
                  onChange={e => updateRow(idx, 'dept', e.target.value)}
                />
              </td>
              <td>
                <input
                  className="form-input"
                  style={{ width: '100%', fontSize: '0.85rem', padding: '4px 8px' }}
                  value={row.accounts}
                  placeholder="e.g. 4-1000, 4-1010"
                  onChange={e => updateRow(idx, 'accounts', e.target.value)}
                />
              </td>
              <td>
                <button className="admin-btn-danger" style={{ padding: '3px 10px', fontSize: '0.78rem' }} onClick={() => removeRow(idx)}>Remove</button>
              </td>
            </tr>
          ))}
          {/* Add new row */}
          <tr>
            <td>
              <input
                className="form-input"
                style={{ width: '100%', fontSize: '0.85rem', padding: '4px 8px' }}
                placeholder="New department name"
                value={newDept}
                onChange={e => setNewDept(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && addRow()}
              />
            </td>
            <td>
              <input
                className="form-input"
                style={{ width: '100%', fontSize: '0.85rem', padding: '4px 8px' }}
                placeholder="e.g. 4-2000"
                value={newAccts}
                onChange={e => setNewAccts(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && addRow()}
              />
            </td>
            <td>
              <button className="admin-btn-primary" style={{ padding: '3px 10px', fontSize: '0.78rem' }} onClick={addRow}>Add</button>
            </td>
          </tr>
        </tbody>
      </table>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <button className="admin-btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving…' : 'Save Mapping'}
        </button>
        <button className="admin-btn-secondary" onClick={handleValidate} disabled={validating}>
          {validating ? 'Validating…' : 'Validate Now'}
        </button>
        <span style={{ color: 'var(--text-secondary)', fontSize: '0.8rem' }}>
          {rows.length} department{rows.length !== 1 ? 's' : ''}
        </span>
      </div>
    </div>
  );
}


// ─── Main AdminPanel ──────────────────────────────────────────────────────────

export default function AdminPanel({ token, currentUser, onUserChange, onClose }) {
  const [activeTab, setActiveTab] = useState('members');
  const [roles, setRoles] = useState([]);
  const [agentOps, setAgentOps] = useState({});

  const reloadRoles = () => {
    return apiFetch('/org/roles', token)
      .then(d => {
        setRoles(d.roles || []);
        setAgentOps(d.agent_operations || {});
        return d;
      })
      .catch(() => {});
  };

  // Load roles once on mount so the Members tab's dropdown is populated immediately.
  useEffect(() => { reloadRoles(); }, []);

  const TABS = [
    { id: 'members', label: 'Members' },
    { id: 'roles', label: 'Roles & Permissions' },
    { id: 'usage', label: 'Usage' },
    { id: 'sops', label: 'SOPs' },
    { id: 'departments', label: 'Departments' },
  ];

  return (
    <div className="admin-panel-overlay" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="admin-panel">
        <div className="admin-panel-header">
          <span className="admin-panel-title">Team Management</span>
          <button className="admin-panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="admin-tabs">
          {TABS.map(t => (
            <button
              key={t.id}
              className={`admin-tab${activeTab === t.id ? ' active' : ''}`}
              onClick={() => setActiveTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="admin-panel-body">
          {activeTab === 'members' && (
            <MembersTab
              token={token}
              roles={roles}
              currentUser={currentUser}
              onUserChange={onUserChange}
            />
          )}
          {activeTab === 'roles' && <RolesTab token={token} roles={roles} agentOps={agentOps} reloadRoles={reloadRoles} />}
          {activeTab === 'usage' && <UsageTab token={token} />}
          {activeTab === 'sops' && <SopsTab token={token} />}
          {activeTab === 'departments' && <DepartmentsTab token={token} />}
        </div>
      </div>
    </div>
  );
}
