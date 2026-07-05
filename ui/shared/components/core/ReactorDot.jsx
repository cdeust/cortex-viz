import React from 'react';

/**
 * ReactorDot — the system's one living element: a small "live" indicator that
 * blinks to show a process is running / synchronized. `tone` sets the color;
 * `live` toggles the 2.2s blink; `label` renders an adjacent status word.
 */
export function ReactorDot({ tone = 'accent', live = true, label, className = '', ...rest }) {
  const dot = (
    <span
      className={['aia-dot', tone !== 'accent' ? `aia-dot--${tone}` : '', live ? 'aia-dot--live' : ''].filter(Boolean).join(' ')}
      aria-hidden="true"
    />
  );
  if (!label) return React.cloneElement(dot, { className: dot.props.className + (className ? ' ' + className : ''), ...rest });
  return (
    <span
      className={className}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontFamily: 'var(--font-mono)', fontSize: 10.5, letterSpacing: '.04em', textTransform: 'uppercase', color: 'var(--fg-2)' }}
      {...rest}
    >
      {dot}
      {label}
    </span>
  );
}
