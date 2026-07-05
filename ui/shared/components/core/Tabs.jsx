import React from 'react';

/**
 * Tabs — the product's primary view switcher (the "view-toggle").
 * Underline marks the active tab; color is the only other signal.
 * Controlled: pass `value` and `onChange`.
 */
export function Tabs({ items = [], value, onChange, className = '', ...rest }) {
  return (
    <div className={['aia-tabs', className].filter(Boolean).join(' ')} role="tablist" {...rest}>
      {items.map((it) => {
        const id = typeof it === 'string' ? it : it.id;
        const label = typeof it === 'string' ? it : it.label;
        const selected = id === value;
        return (
          <button
            key={id}
            role="tab"
            aria-selected={selected}
            className="aia-tab"
            onClick={() => onChange && onChange(id)}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
