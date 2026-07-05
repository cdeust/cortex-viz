import * as React from 'react';

/**
 * StatCard — a KPI tile: big mono value, uppercase label, optional delta.
 *
 * @startingPoint section="Core" subtitle="KPI tile — mono value + micro-label" viewport="700x150"
 */
export interface StatCardProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Uppercase micro-label under the value. */
  label: React.ReactNode;
  /** The metric — a number or preformatted string. */
  value: React.ReactNode;
  /** Optional signed delta / secondary line. */
  delta?: React.ReactNode;
  /** Colors the delta. */
  deltaDir?: 'up' | 'down';
  /** Paint the value terracotta. @default false */
  accent?: boolean;
}

export declare function StatCard(props: StatCardProps): React.JSX.Element;
