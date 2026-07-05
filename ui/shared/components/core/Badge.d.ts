import * as React from 'react';

/**
 * Badge — uppercase mono micro-cap for a node type or small status.
 *
 * @startingPoint section="Core" subtitle="Type / status micro-cap, 5 tones" viewport="700x110"
 */
export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** Color role. @default "neutral" */
  tone?: 'neutral' | 'accent' | 'ok' | 'warn' | 'danger' | 'info';
  /** Show a leading dot in the current color. @default false */
  dot?: boolean;
  /** Fill the badge instead of outlining it. @default false */
  solid?: boolean;
}

export declare function Badge(props: BadgeProps): React.JSX.Element;
