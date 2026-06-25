import React, { useState, useEffect } from 'react';

/**
 * ThinkingPanel — vertical stepper showing the AI's execution plan with live progress.
 *
 * Props:
 *   plan      – Array of { id, label, status, detail }
 *   collapsed – Boolean: auto-collapse when response tokens start streaming
 */
export default function ThinkingPanel({ plan, collapsed = false }) {
  const [isCollapsed, setIsCollapsed] = useState(collapsed);

  useEffect(() => {
    if (collapsed) setIsCollapsed(true);
  }, [collapsed]);

  if (!plan || plan.length === 0) return null;

  const doneCount = plan.filter(s => s.status === 'done').length;
  const failedCount = plan.filter(s => s.status === 'failed').length;
  const allDone = plan.every(s => s.status === 'done' || s.status === 'failed');

  const headerText = allDone
    ? `Completed ${doneCount}/${plan.length} steps${failedCount > 0 ? ` (${failedCount} issue${failedCount > 1 ? 's' : ''})` : ''}`
    : `Working... (${doneCount}/${plan.length})`;

  return (
    <div className={`thinking-panel ${isCollapsed ? 'collapsed' : ''}`}>
      <button
        className="thinking-header"
        onClick={() => setIsCollapsed(c => !c)}
        type="button"
      >
        <span className="thinking-icon">
          {allDone && failedCount === 0 ? (
            <CheckCircleIcon />
          ) : allDone && failedCount > 0 ? (
            <WarningIcon />
          ) : (
            <SpinnerIcon />
          )}
        </span>
        <span className="thinking-title">{headerText}</span>
        <span className={`thinking-chevron ${isCollapsed ? '' : 'open'}`}>
          <ChevronIcon />
        </span>
      </button>

      {!isCollapsed && (
        <div className="thinking-steps">
          {plan.map(step => (
            <div key={step.id} className={`thinking-step status-${step.status}`}>
              <div className="step-indicator">
                {step.status === 'done' && <CheckIcon />}
                {step.status === 'in_progress' && <SpinnerSmallIcon />}
                {step.status === 'failed' && <XIcon />}
                {step.status === 'pending' && <CircleIcon />}
              </div>
              <div className="step-content">
                <span className="step-label">{step.label}</span>
                {step.detail && <span className="step-detail">{step.detail}</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ─── Inline SVG Icons (no external deps) ──────────────────────────── */

function CheckCircleIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="7" stroke="#22c55e" strokeWidth="1.5" fill="rgba(34,197,94,0.1)" />
      <path d="M5 8l2 2 4-4" stroke="#22c55e" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <path d="M3.5 7l2.5 2.5 4.5-5" stroke="#22c55e" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function SpinnerIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" className="thinking-spinner">
      <circle cx="8" cy="8" r="6" stroke="rgba(129,140,248,0.3)" strokeWidth="2" />
      <path d="M8 2a6 6 0 0 1 6 6" stroke="#818cf8" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function SpinnerSmallIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" className="thinking-spinner">
      <circle cx="7" cy="7" r="5" stroke="rgba(129,140,248,0.3)" strokeWidth="1.5" />
      <path d="M7 2a5 5 0 0 1 5 5" stroke="#818cf8" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function XIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <path d="M4 4l6 6M10 4l-6 6" stroke="#ef4444" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function CircleIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <circle cx="7" cy="7" r="4" stroke="#475569" strokeWidth="1.5" />
    </svg>
  );
}

function WarningIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path d="M8 1.5l6.93 12H1.07L8 1.5z" stroke="#f59e0b" strokeWidth="1.2" fill="rgba(245,158,11,0.1)" strokeLinejoin="round" />
      <path d="M8 6v3" stroke="#f59e0b" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="8" cy="11.5" r="0.75" fill="#f59e0b" />
    </svg>
  );
}

function ChevronIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
      <path d="M3 4.5l3 3 3-3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
