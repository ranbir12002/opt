// src/components/ClarificationForm.jsx
import React, { useState } from 'react';

/**
 * ClarificationForm - Interactive form for resolving agent clarifications
 *
 * Displays dropdowns/inputs/checkboxes for missing or ambiguous fields.
 * Supports: ambiguous, missing, multi_select, free_text, confirmation, contradiction.
 * Submits fixes back to backend for reprocessing.
 */
export default function ClarificationForm({ clarificationData, onSubmit, onCancel }) {
  const {
    clarifications = [],
    clarification_count = 0,
    session_id,
    resolved_count = 0,
    total_count = 0,
    contradiction_type = false
  } = clarificationData;

  // State: { row_number: { field_name: selected_value_or_array } }
  const [selections, setSelections] = useState({});

  // Build an operation-aware label for dropdown/input prompts
  const getActionLabel = (clarification) => {
    const op = (clarification.operation || '').toUpperCase();
    const field = clarification.field || 'option';
    const opVerb = {
      CREATE: 'create',
      UPDATE: 'update',
      DELETE: 'delete',
      LOCK: 'lock',
      UNLOCK: 'unlock',
      COPY: 'copy',
    }[op];
    if (opVerb) return `Select the ${field} to ${opVerb}`;
    return `Select ${field}`;
  };

  const handleSelectionChange = (rowNum, field, value) => {
    setSelections(prev => ({
      ...prev,
      [rowNum]: {
        ...(prev[rowNum] || {}),
        [field]: value
      }
    }));
  };

  // Toggle an option in/out of a multi-select array
  const handleMultiSelectToggle = (rowNum, field, optionId) => {
    setSelections(prev => {
      const current = prev[rowNum]?.[field] || [];
      const arr = Array.isArray(current) ? current : [];
      const updated = arr.includes(optionId)
        ? arr.filter(v => v !== optionId)
        : [...arr, optionId];
      return {
        ...prev,
        [rowNum]: {
          ...(prev[rowNum] || {}),
          [field]: updated
        }
      };
    });
  };

  const handleSubmit = (e) => {
    e.preventDefault();

    // Block submission if any field is still empty
    if (!allFieldsFilled) return;

    // Build clarifications object: { row_num: { field: value } }
    const clarificationsPayload = {};
    const customEntries = [];

    clarifications.forEach(c => {
      const rowNum = c.row;
      const field = c.field;
      const selected = selections[rowNum]?.[field];
      const hasValue = Array.isArray(selected) ? selected.length > 0 : !!selected;

      if (hasValue) {
        // "Skip this row" — mark row for skipping
        if (selected === '__skip__') {
          clarificationsPayload[rowNum] = {
            ...(clarificationsPayload[rowNum] || {}),
            '__skip__': true,
          };
          return;
        }

        // "Other" custom input — send raw text for backend LLM to interpret
        if (selected === '__other__') {
          const customValue = (selections[rowNum]?.[`${field}__custom`] || '').trim();
          clarificationsPayload[rowNum] = {
            ...(clarificationsPayload[rowNum] || {}),
            [`${field}__custom`]: customValue,
          };
          customEntries.push({ row: String(rowNum), field, value: customValue });
          return;
        }

        // Contradiction type: use field name and chosen value directly
        // (no ID mapping needed — values are the user's chosen text)
        let targetField;
        if (c.type === 'contradiction') {
          targetField = field;
        } else if (c.type === 'free_text' || (!c.options || c.options.length === 0)) {
          targetField = field; // e.g. "Blocks", "StartTime", "Claim_123"
        } else if (field === 'JobName') targetField = 'JobID';
        else if (field === 'SiteName') targetField = 'JobID';
        else if (field === 'QuoteName') targetField = 'QuoteID';
        else if (field === 'SectionName') targetField = 'SectionID';
        else if (field === 'CostCentreName') targetField = 'CostCentreID';
        else if (field === 'StaffName') targetField = 'StaffID';
        else if (field === 'Schedule') targetField = 'ScheduleID';
        else targetField = field; // fallback

        clarificationsPayload[rowNum] = {
          ...(clarificationsPayload[rowNum] || {}),
          [targetField]: selected  // scalar for single-select, array for multi-select
        };
      }
    });

    // Build human-readable summary of selections for chat display
    const summaryParts = clarifications.map(c => {
      const selectedVal = selections[c.row]?.[c.field];
      if (!selectedVal) return null;

      // Skip entry
      if (selectedVal === '__skip__') {
        return `Row ${c.row}: skipped by user`;
      }

      // Custom "Other" entry
      if (selectedVal === '__other__') {
        const customText = selections[c.row]?.[`${c.field}__custom`] || '';
        const op = c.operation ? ` for ${c.operation.toLowerCase()}` : '';
        return `${c.field}: "${customText}" (manually entered)${op}`;
      }

      // Multi-select: list all selected names
      if (Array.isArray(selectedVal)) {
        const names = selectedVal.map(id => {
          const opt = c.options?.find(o => String(o.id) === String(id));
          return opt ? opt.name : id;
        });
        const op = c.operation ? ` for ${c.operation.toLowerCase()}` : '';
        return `${c.field}: selected ${names.length} (${names.join(', ')})${op}`;
      }

      // Single-select: find the display name from options if available
      const matchedOpt = c.options?.find(opt => String(opt.id) === String(selectedVal));
      const displayValue = matchedOpt ? matchedOpt.name : selectedVal;

      const op = c.operation ? ` for ${c.operation.toLowerCase()}` : '';
      return `${c.field}: "${displayValue}" selected${op}`;
    }).filter(Boolean);

    onSubmit({
      session_id,
      clarifications: clarificationsPayload,
      _summary: summaryParts.join(', '),
      _agent: clarificationData.agent || 'schedule',
      ...(contradiction_type ? { _contradiction: true } : {}),
      ...(customEntries.length > 0 ? { custom_entries: customEntries } : {}),
    });
  };

  // Render <option> elements, grouped by `group` field when present
  const renderOptions = (options) => {
    const hasGroups = options.some(opt => opt.group);
    if (!hasGroups) {
      return options.map((opt, i) => (
        <option key={i} value={opt.id}>
          {opt.name} (ID: {opt.id})
        </option>
      ));
    }
    // Group options by their group field
    const grouped = [];
    const groupMap = new Map();
    for (const opt of options) {
      const g = opt.group || 'Other';
      if (!groupMap.has(g)) {
        groupMap.set(g, []);
        grouped.push(g);
      }
      groupMap.get(g).push(opt);
    }
    return grouped.map(groupName => (
      <optgroup key={groupName} label={groupName}>
        {groupMap.get(groupName).map((opt, i) => (
          <option key={i} value={opt.id}>
            {opt.name} (ID: {opt.id})
          </option>
        ))}
      </optgroup>
    ));
  };

  // Render checkbox list for multi-select, with optional grouping
  const renderCheckboxOptions = (clarification) => {
    const options = clarification.options || [];
    const selected = selections[clarification.row]?.[clarification.field] || [];
    const selectedArr = Array.isArray(selected) ? selected : [];

    const hasGroups = options.some(opt => opt.group);

    if (!hasGroups) {
      return options.map((opt, i) => (
        <label
          key={i}
          className={`multi-select-item${selectedArr.includes(String(opt.id)) ? ' multi-select-item-selected' : ''}`}
        >
          <input
            type="checkbox"
            checked={selectedArr.includes(String(opt.id))}
            onChange={() => handleMultiSelectToggle(clarification.row, clarification.field, String(opt.id))}
          />
          <span>{opt.name} (ID: {opt.id})</span>
        </label>
      ));
    }

    // Grouped rendering
    const groupMap = new Map();
    const groupOrder = [];
    for (const opt of options) {
      const g = opt.group || 'Other';
      if (!groupMap.has(g)) { groupMap.set(g, []); groupOrder.push(g); }
      groupMap.get(g).push(opt);
    }

    return groupOrder.map(groupName => (
      <div key={groupName} className="multi-select-group">
        <div className="multi-select-group-label">{groupName}</div>
        {groupMap.get(groupName).map((opt, i) => (
          <label
            key={i}
            className={`multi-select-item${selectedArr.includes(String(opt.id)) ? ' multi-select-item-selected' : ''}`}
          >
            <input
              type="checkbox"
              checked={selectedArr.includes(String(opt.id))}
              onChange={() => handleMultiSelectToggle(clarification.row, clarification.field, String(opt.id))}
            />
            <span>{opt.name} (ID: {opt.id})</span>
          </label>
        ))}
      </div>
    ));
  };

  const allFieldsFilled = clarifications.every(c => {
    const val = selections[c.row]?.[c.field];
    if (c.type === 'multi_select') return Array.isArray(val) && val.length > 0;
    if (val === '__other__') {
      const customVal = selections[c.row]?.[`${c.field}__custom`];
      return !!customVal && customVal.trim().length > 0;
    }
    return !!val;
  });

  return (
    <div className="clarification-form-container">
      <div className="clarification-header">
        <div className="clarification-icon">{contradiction_type ? '⚠️' : '🔍'}</div>
        <div>
          <h3>
            {contradiction_type
              ? `${clarification_count} conflicting detail${clarification_count !== 1 ? 's' : ''} found`
              : `${clarification_count} row${clarification_count !== 1 ? 's' : ''} need clarification`
            }
          </h3>
          <p className="clarification-subtitle">
            {contradiction_type
              ? 'Please pick which value you intended'
              : `${resolved_count}/${total_count} rows processed successfully`
            }
          </p>
        </div>
      </div>

      {/* Multi-action progress indicator */}
      {clarificationData.multi_action_context && (
        <div className="multi-action-progress">
          <span className="multi-action-badge">
            {clarificationData.multi_action_context.progress
              ? `${clarificationData.multi_action_context.progress} actions done`
              : `Action ${(clarificationData.multi_action_context.sub_index ?? 0) + 1} of ${clarificationData.multi_action_context.total_sub_requests}`
            }
          </span>
          {clarificationData.multi_action_context.description && (
            <span className="multi-action-description">
              {clarificationData.multi_action_context.description}
            </span>
          )}
        </div>
      )}

      <form onSubmit={handleSubmit} className="clarification-form">
        {clarifications.map((clarification, idx) => (
          <div key={idx} className="clarification-item">
            <div className="clarification-item-header">
              {!contradiction_type && <span className="row-badge">Row {clarification.row}</span>}
              {clarification.operation && (
                <span className="operation-badge">{clarification.operation}</span>
              )}
              <span className="field-badge">{clarification.field}</span>
            </div>

            {clarification.row_context && Object.keys(clarification.row_context).length > 0 && (
              <p className="clarification-row-context">
                {Object.entries(clarification.row_context).map(([key, val], i) => (
                  <span key={key}>
                    {i > 0 && <span className="context-separator"> · </span>}
                    <span className="context-label">{key}:</span> {val}
                  </span>
                ))}
              </p>
            )}

            <p className="clarification-message">{clarification.message}</p>

            {clarification.type === 'ambiguous' && (
              <div className="clarification-input-group">
                <label>{getActionLabel(clarification)}:</label>
                <select
                  className="clarification-select"
                  value={selections[clarification.row]?.[clarification.field] || ''}
                  onChange={(e) => handleSelectionChange(
                    clarification.row,
                    clarification.field,
                    e.target.value
                  )}
                  required
                >
                  <option value="">-- Choose --</option>
                  {renderOptions(clarification.options)}
                  <option value="__skip__">Skip this row</option>
                  <option value="__other__">Other (specify)</option>
                </select>
                {selections[clarification.row]?.[clarification.field] === '__other__' && (
                  <input
                    type="text"
                    className="clarification-input"
                    placeholder={`Type the correct name, ID, or value`}
                    value={selections[clarification.row]?.[`${clarification.field}__custom`] || ''}
                    onChange={(e) => handleSelectionChange(
                      clarification.row,
                      `${clarification.field}__custom`,
                      e.target.value
                    )}
                    required
                    autoFocus
                  />
                )}
              </div>
            )}

            {clarification.type === 'missing' && clarification.options?.length > 0 && (
              <div className="clarification-input-group">
                <label>{getActionLabel(clarification)}:</label>
                <select
                  className="clarification-select"
                  value={selections[clarification.row]?.[clarification.field] || ''}
                  onChange={(e) => handleSelectionChange(
                    clarification.row,
                    clarification.field,
                    e.target.value
                  )}
                  required
                >
                  <option value="">-- Choose --</option>
                  {renderOptions(clarification.options)}
                  <option value="__skip__">Skip this row</option>
                  <option value="__other__">Other (specify)</option>
                </select>
                {selections[clarification.row]?.[clarification.field] === '__other__' && (
                  <input
                    type="text"
                    className="clarification-input"
                    placeholder={`Type the correct name, ID, or value`}
                    value={selections[clarification.row]?.[`${clarification.field}__custom`] || ''}
                    onChange={(e) => handleSelectionChange(
                      clarification.row,
                      `${clarification.field}__custom`,
                      e.target.value
                    )}
                    required
                    autoFocus
                  />
                )}
              </div>
            )}

            {clarification.type === 'multi_select' && clarification.options?.length > 0 && (
              <div className="clarification-input-group">
                <label>{getActionLabel(clarification)}:</label>
                <div className="multi-select-list">
                  {renderCheckboxOptions(clarification)}
                </div>
              </div>
            )}

            {clarification.type === 'confirmation' && (
              <div className="clarification-input-group">
                <div className="confirmation-buttons">
                  {clarification.options.map((opt, optIdx) => (
                    <button
                      key={optIdx}
                      type="button"
                      className={`btn-confirmation ${
                        selections[clarification.row]?.[clarification.field] === opt.id
                          ? 'btn-confirmation-selected'
                          : ''
                      }`}
                      onClick={() => handleSelectionChange(
                        clarification.row,
                        clarification.field,
                        opt.id
                      )}
                    >
                      {opt.name}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {clarification.type === 'contradiction' && (
              <div className="clarification-input-group">
                <div className="contradiction-options">
                  {clarification.options.map((opt, optIdx) => (
                    <label
                      key={optIdx}
                      className={`contradiction-option${
                        selections[clarification.row]?.[clarification.field] === opt.name
                          ? ' contradiction-option-selected'
                          : ''
                      }`}
                    >
                      <input
                        type="radio"
                        name={`contradiction-${clarification.row}-${clarification.field}`}
                        checked={selections[clarification.row]?.[clarification.field] === opt.name}
                        onChange={() => handleSelectionChange(
                          clarification.row,
                          clarification.field,
                          opt.name
                        )}
                      />
                      <span>{opt.name}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}

            {(clarification.type === 'free_text' || (clarification.type === 'missing' && (!clarification.options || clarification.options.length === 0))) && (
              <div className="clarification-input-group">
                <label>{clarification.placeholder || `Enter ${clarification.field}`}:</label>
                <input
                  type="text"
                  className="clarification-input"
                  placeholder={clarification.placeholder || clarification.field}
                  value={selections[clarification.row]?.[clarification.field] || ''}
                  onChange={(e) => handleSelectionChange(
                    clarification.row,
                    clarification.field,
                    e.target.value
                  )}
                  required
                />
              </div>
            )}
          </div>
        ))}

        <div className="clarification-actions">
          <button
            type="button"
            className="btn-secondary"
            onClick={onCancel}
          >
            Cancel & Re-upload
          </button>
          <button
            type="submit"
            className="btn-primary"
            disabled={!allFieldsFilled}
          >
            <span>Submit</span>
            <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path>
            </svg>
          </button>
        </div>
      </form>

      {!allFieldsFilled && (
        <p className="clarification-hint">
          Please select an option for each row before submitting.
        </p>
      )}
    </div>
  );
}
