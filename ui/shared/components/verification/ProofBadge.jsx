import React from 'react';

const LABELS = { proven: 'Proven', unsourced: 'Unsourced', blocked: 'Blocked', pending: 'Pending' };

/**
 * ProofBadge — the verdict of a zetetic gate. Carries the exact lexicon:
 * proven · unsourced · blocked · pending. Dot + uppercase mono word.
 * Pass children to override the default label (e.g. "2 violations").
 */
export function ProofBadge({ status = 'proven', className = '', children, ...rest }) {
  return (
    <span className={['aia-proof', `aia-proof--${status}`, className].filter(Boolean).join(' ')} {...rest}>
      <span className="aia-proof__dot" aria-hidden="true" />
      {children || LABELS[status]}
    </span>
  );
}
