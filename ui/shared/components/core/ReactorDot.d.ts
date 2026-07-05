import * as React from 'react';

/**
 * ReactorDot — a small blinking "live" indicator, optionally with a status label.
 */
export interface ReactorDotProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** Dot color. @default "accent" */
  tone?: 'accent' | 'ok' | 'idle';
  /** Animate the 2.2s blink. @default true */
  live?: boolean;
  /** Optional uppercase mono status word beside the dot. */
  label?: React.ReactNode;
}

export declare function ReactorDot(props: ReactorDotProps): React.JSX.Element;
