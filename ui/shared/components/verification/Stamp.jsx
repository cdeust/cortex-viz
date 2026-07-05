import React from 'react';

/**
 * Stamp — the verdict made physical: a rubber-stamp certification mark with
 * inked texture and a slight tilt. The paper-surface counterpart of ProofBadge.
 * `label` is the verdict word; `sub` the evidence line (date, n, benchmark).
 */
export function Stamp({ label = 'Proven', sub, tone = 'accent', tilt = -3, clean = false, className = '', ...rest }) {
  const cls = [
    'aia-stamp',
    tone !== 'accent' ? `aia-stamp--${tone}` : '',
    clean ? 'aia-stamp--clean' : '',
    className,
  ].filter(Boolean).join(' ');
  return (
    <span className={cls} style={{ '--stamp-tilt': `${tilt}deg` }} {...rest}>
      {label}
      {sub ? <span className="aia-stamp__sub">{sub}</span> : null}
    </span>
  );
}
