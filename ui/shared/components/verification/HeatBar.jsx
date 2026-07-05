import React from 'react';

/**
 * HeatBar — memory temperature as a gradient fill (cold → hot). `value` is
 * 0..1. Shows an optional label row with the numeric readout.
 */
export function HeatBar({ value = 0, label = 'Heat', showValue = true, className = '', ...rest }) {
  const v = Math.max(0, Math.min(1, value));
  return (
    <div className={['aia-heat', className].filter(Boolean).join(' ')} {...rest}>
      {(label || showValue) ? (
        <div className="aia-heat__meta">
          <span>{label}</span>
          {showValue ? <span className="aia-heat__val">{v.toFixed(3)}</span> : null}
        </div>
      ) : null}
      <div className="aia-heat__track">
        <div className="aia-heat__fill" style={{ '--heat-scale': Math.max(v, 0.001), width: `${v * 100}%` }} />
      </div>
    </div>
  );
}
