import React from 'react';

/**
 * Card — a surface with a hairline border. `title` renders a header row with
 * an optional `aside` (right-aligned action/badge). `elevated` lifts it with
 * shadow; `flush` removes padding for edge-to-edge content (tables, media).
 */
export function Card({ title, aside, elevated = false, flush = false, className = '', children, ...rest }) {
  const cls = [
    'aia-card',
    elevated ? 'aia-card--elevated' : '',
    flush ? 'aia-card--flush' : '',
    className,
  ].filter(Boolean).join(' ');
  return (
    <div className={cls} {...rest}>
      {title ? (
        <div className="aia-card__head">
          <span className="aia-card__title">{title}</span>
          {aside ? <span style={{ marginLeft: 'auto' }}>{aside}</span> : null}
        </div>
      ) : null}
      {children}
    </div>
  );
}
