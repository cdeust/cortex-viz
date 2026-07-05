import * as React from 'react';

/**
 * Card — a bordered surface with an optional titled header row.
 *
 * @startingPoint section="Core" subtitle="Hairline surface, titled header + aside" viewport="700x180"
 */
export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Header title; omit for a plain surface. */
  title?: React.ReactNode;
  /** Right-aligned header slot (badge, menu, action). */
  aside?: React.ReactNode;
  /** Lift with shadow on an elevated surface. @default false */
  elevated?: boolean;
  /** Remove padding for edge-to-edge content. @default false */
  flush?: boolean;
}

export declare function Card(props: CardProps): React.JSX.Element;
