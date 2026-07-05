import * as React from 'react';

/**
 * HeatBar — memory temperature as a cold→hot gradient fill (value 0..1).
 *
 * @startingPoint section="Verification" subtitle="Memory-temperature gradient bar" viewport="700x110"
 */
export interface HeatBarProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Heat, 0..1. @default 0 */
  value: number;
  /** Left label. @default "Heat" */
  label?: React.ReactNode;
  /** Show the numeric readout (3 dp). @default true */
  showValue?: boolean;
}

export declare function HeatBar(props: HeatBarProps): React.JSX.Element;
