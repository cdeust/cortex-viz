import React from 'react';

/**
 * Select — a native dropdown skinned to match Input (cmdk aesthetic).
 * Pass `options` as strings or {value,label}, or provide <option> children.
 */
export function Select({ options, className = '', children, ...rest }) {
  return (
    <select className={['aia-field', 'aia-field--select', className].filter(Boolean).join(' ')} {...rest}>
      {options
        ? options.map((o) => {
            const value = typeof o === 'string' ? o : o.value;
            const label = typeof o === 'string' ? o : o.label;
            return <option key={value} value={value}>{label}</option>;
          })
        : children}
    </select>
  );
}
