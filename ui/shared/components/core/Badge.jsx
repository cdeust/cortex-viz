import React from 'react';

/**
 * Badge — an uppercase mono micro-cap for a node's type or a small status.
 * `tone` maps to the data/status palette; `dot` shows a leading indicator;
 * `solid` fills the badge (pass a background via style for data colors).
 */
export function Badge({ tone = 'neutral', dot = false, solid = false, className = '', children, ...rest }) {
  const cls = [
    'aia-badge',
    tone !== 'neutral' ? `aia-badge--${tone}` : '',
    solid ? 'aia-badge--solid' : '',
    className,
  ].filter(Boolean).join(' ');
  return (
    <span className={cls} {...rest}>
      {dot ? <span className="aia-badge__dot" aria-hidden="true" /> : null}
      {children}
    </span>
  );
}
