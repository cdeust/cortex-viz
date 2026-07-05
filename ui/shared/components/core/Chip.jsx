import React from 'react';

/**
 * Chip — a filter pill. Toggles pressed/unpressed; optional trailing count.
 * Used in the graph filter-bar (All / Episodic / Semantic / Entity …).
 */
export function Chip({ active = false, count, onClick, className = '', children, ...rest }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={['aia-chip', className].filter(Boolean).join(' ')}
      {...rest}
    >
      {children}
      {count != null ? <span className="aia-chip__count">{count}</span> : null}
    </button>
  );
}
