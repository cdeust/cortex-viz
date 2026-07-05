import * as React from 'react';

/**
 * Chip — a toggleable filter pill with an optional trailing count.
 *
 * @startingPoint section="Core" subtitle="Filter pill, pressed state + count" viewport="700x110"
 */
export interface ChipProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'onClick'> {
  /** Pressed / selected state. @default false */
  active?: boolean;
  /** Optional count shown as a mono suffix. */
  count?: number;
  onClick?: React.MouseEventHandler<HTMLButtonElement>;
}

export declare function Chip(props: ChipProps): React.JSX.Element;
