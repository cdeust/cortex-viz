import * as React from 'react';

/**
 * Button — the primary action primitive for AI Architect interfaces.
 *
 * @startingPoint section="Core" subtitle="Terracotta primary + secondary, ghost, danger" viewport="700x150"
 */
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** Visual weight. @default "primary" */
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  /** Size / density. @default "md" */
  size?: 'sm' | 'md' | 'lg';
  /** Optional leading icon node (an inline SVG or glyph). */
  icon?: React.ReactNode;
  /** Disable interaction and dim the button. @default false */
  disabled?: boolean;
}

export declare function Button(props: ButtonProps): React.JSX.Element;
