import React from 'react';

/**
 * StatCard — a KPI tile: a large mono value over an uppercase micro-label,
 * with an optional signed delta. `accent` paints the value terracotta.
 */
export function StatCard({ label, value, delta, deltaDir, accent = false, className = '', ...rest }) {
  return (
    <div className={['aia-stat', className].filter(Boolean).join(' ')} {...rest}>
      <span className={['aia-stat__value', accent ? 'aia-stat__value--accent' : ''].filter(Boolean).join(' ')}>{value}</span>
      <span className="aia-stat__label">{label}</span>
      {delta != null ? (
        <span className={['aia-stat__delta', deltaDir ? `aia-stat__delta--${deltaDir}` : ''].filter(Boolean).join(' ')}>{delta}</span>
      ) : null}
    </div>
  );
}
