import React from 'react';

/**
 * Input — a text / search field in the command-palette (cmdk) aesthetic:
 * mono type, elevated surface, terracotta focus ring. Optional leading icon.
 */
export function Input({ icon = null, className = '', type = 'text', ...rest }) {
  const field = (
    <input type={type} className={['aia-field', !icon && className].filter(Boolean).join(' ')} {...rest} />
  );
  if (!icon) return field;
  return (
    <span className={['aia-inputwrap', className].filter(Boolean).join(' ')}>
      <span className="aia-input__icon" aria-hidden="true">{icon}</span>
      {field}
    </span>
  );
}
