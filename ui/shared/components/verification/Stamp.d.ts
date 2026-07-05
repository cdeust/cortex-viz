import * as React from 'react';

/**
 * Stamp — a rubber-stamp certification verdict with inked texture and tilt.
 * The paper-surface counterpart of ProofBadge.
 */
export interface StampProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** The verdict word. @default "Proven" */
  label?: React.ReactNode;
  /** Evidence line under the verdict (date · n · benchmark). */
  sub?: React.ReactNode;
  /** Ink colour role. @default "accent" */
  tone?: 'accent' | 'ok' | 'danger' | 'neutral';
  /** Tilt in degrees. @default -3 */
  tilt?: number;
  /** Disable the inked-texture mask (solid print). @default false */
  clean?: boolean;
}

export declare function Stamp(props: StampProps): React.JSX.Element;
