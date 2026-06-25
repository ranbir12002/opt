// src/components/RenderBlock.jsx
import React, { useState, useRef, useEffect } from 'react';

export default function RenderBlock({ envelope }) {
  if (!envelope) return null;
  const { title, summary, blocks = [] } = envelope;

  return (
    <div className="render-envelope">
      {title && <h3>{title}</h3>}
      {summary && <div className="summary" dangerouslySetInnerHTML={{ __html: summary }} />}

      {blocks.map((b, i) => (
        <Block key={i} block={b} />
      ))}
    </div>
  );
}

function Block({ block }) {
  const { type, title, spec = {} } = block;
  return (
    <div className={`block block-${type}`}>
      {title && <h4>{title}</h4>}
      {type === 'text'   && <p>{spec.markdown}</p>}
      {type === 'json'   && <pre className="code">{JSON.stringify(spec.json ?? spec, null, 2)}</pre>}
      {type === 'cards'  && <Cards spec={spec} />}
      {type === 'table'  && <Table spec={spec} />}
      {Array.isArray(spec.downloads) && spec.downloads.length > 0 && (
        <DownloadDropdown downloads={spec.downloads} />
      )}
      {spec.note && <div className="muted">{spec.note}</div>}
    </div>
  );
}

function DownloadDropdown({ downloads }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    function handleClickOutside(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    if (open) document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [open]);

  return (
    <div className="download-dropdown" ref={ref}>
      <button className="download-trigger" onClick={() => setOpen(o => !o)}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
          <polyline points="7 10 12 15 17 10" />
          <line x1="12" y1="15" x2="12" y2="3" />
        </svg>
        Download
        <svg className={`chevron ${open ? 'open' : ''}`} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      {open && (
        <div className="download-menu">
          {downloads.map((d, idx) => {
            const href = d.url || d.blob || '#';
            const label = d.type === 'xlsx' ? 'EXCEL' : String(d.type).toUpperCase();
            return (
              <a key={idx} href={href} download={`export.${d.type}`} onClick={() => setOpen(false)}>
                {label}
              </a>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Cards({ spec }) {
  const items = spec.items || [];
  return (
    <div className="cards-grid">
      {items.map((m, i) => (
        <div key={i} className="card">
          <div className="label">{m.label}</div>
          <div className="value">{String(m.value)}{m.unit ? ` ${m.unit}` : ''}</div>
        </div>
      ))}
    </div>
  );
}

function Table({ spec }) {
  const cols = spec.columns || [];
  const rows = spec.rows || [];
  return (
    <div className="table-scroll">
      <table className="table">
        <thead>
          <tr>{cols.map(c => <th key={c.key}>{c.label || c.key}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {cols.map(c => (
                <td key={c.key}>{renderCell(r[c.key], c.type)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {spec.preview && spec.preview.total > spec.preview.shown && (
        <div className="muted">
          Showing {spec.preview.shown} of {spec.preview.total}. Use download for full data.
        </div>
      )}
    </div>
  );
}

function renderCell(value, type) {
  if (value == null) return '';
  if (typeof value === 'object') return <code>{JSON.stringify(value)}</code>;
  const str = String(value);
  // Preserve multi-line content (newlines from Excel cells, combined descriptions, etc.)
  if (str.includes('\n')) {
    return <span style={{ whiteSpace: 'pre-line' }}>{str}</span>;
  }
  return str;
}
