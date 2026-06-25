// src/components/ClarificationFileDownload.jsx
import React from 'react';

/**
 * ClarificationFileDownload - UI for downloading corrected Excel template
 *
 * Shown when there are >5 clarifications (too many for interactive UI).
 * User downloads pre-filled Excel, fixes issues locally, and re-uploads.
 */
export default function ClarificationFileDownload({ clarificationData, backendUrl }) {
  const {
    clarification_count = 0,
    resolved_count = 0,
    total_count = 0,
    errors_summary = {},
    session_id,
    message
  } = clarificationData;

  const downloadUrl = `${backendUrl}/api/schedule/download-corrected/${session_id}`;

  return (
    <div className="clarification-download-container">
      <div className="clarification-download-header">
        <div className="warning-icon">⚠️</div>
        <div>
          <h3>{clarification_count} issues found (too many to fix here)</h3>
          <p className="clarification-subtitle">
            {resolved_count}/{total_count} rows processed successfully
          </p>
        </div>
      </div>

      <div className="clarification-download-content">
        <p className="clarification-message">{message}</p>

        <div className="errors-breakdown">
          <h4>Issues breakdown:</h4>
          <ul>
            {errors_summary.missing_fields > 0 && (
              <li>
                <span className="error-badge">Missing Fields</span>
                {errors_summary.missing_fields} row{errors_summary.missing_fields !== 1 ? 's' : ''}
              </li>
            )}
            {errors_summary.ambiguous_matches > 0 && (
              <li>
                <span className="error-badge">Ambiguous Matches</span>
                {errors_summary.ambiguous_matches} row{errors_summary.ambiguous_matches !== 1 ? 's' : ''}
              </li>
            )}
          </ul>
        </div>

        <div className="download-instructions">
          <h4>How to fix:</h4>
          <ol>
            <li>Download the corrected template below</li>
            <li>Open it in Excel and fill in the highlighted issues</li>
            <li>Re-upload the completed file</li>
          </ol>
        </div>

        <a
          href={downloadUrl}
          download
          className="btn-download"
        >
          <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
          </svg>
          Download Corrected Template
        </a>

        <p className="clarification-hint">
          The template contains your original data with issues marked for correction.
        </p>
      </div>
    </div>
  );
}
