import * as React from 'react';

export interface LegendItem {
  label: React.ReactNode;
  /** Any CSS color — use the data tokens, e.g. "var(--stage-late)". */
  color: string;
  /** Render a line swatch (edge) instead of a dot (node). */
  line?: boolean;
}

/**
 * Legend — a data key of colored dots / lines for a graph or chart.
 */
export interface LegendProps extends React.HTMLAttributes<HTMLDivElement> {
  items: LegendItem[];
}

export declare function Legend(props: LegendProps): React.JSX.Element;
