import React from 'react';

/**
 * Button — the primary action primitive.
 * Terracotta primary, quiet secondary/ghost, and a danger variant for
 * destructive or "blocked" actions. Icon is an optional leading glyph node.
 */
export function Button({
  variant = 'primary',
  size = 'md',
  icon = null,
  disabled = false,
  type = 'button',
  className = '',
  children,
  ...rest
}) {
  const cls = [
    'aia-btn',
    `aia-btn--${variant}`,
    `aia-btn--${size}`,
    className,
  ].filter(Boolean).join(' ');

  return (
    <button type={type} className={cls} disabled={disabled} {...rest}>
      {icon ? <span className="aia-btn__icon" aria-hidden="true">{icon}</span> : null}
      {children}
    </button>
  );
}
