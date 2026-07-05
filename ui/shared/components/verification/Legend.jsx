import React from 'react';

/**
 * Legend — a data key for a graph or chart. `items` are {label, color, line?}
 * where a `line` swatch is a thin rule (edges) and the default is a dot (nodes).
 */
export function Legend({ items = [], className = '', ...rest }) {
  return (
    <div className={['aia-legend', className].filter(Boolean).join(' ')} {...rest}>
      {items.map((it, i) => (
        <span className="aia-legend__item" key={it.label || i}>
          <span
            className={['aia-legend__swatch', it.line ? 'aia-legend__swatch--line' : ''].filter(Boolean).join(' ')}
            style={{ background: it.color }}
            aria-hidden="true"
          />
          {it.label}
        </span>
      ))}
    </div>
  );
}
