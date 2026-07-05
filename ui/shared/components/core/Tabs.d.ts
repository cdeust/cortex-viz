import * as React from 'react';

export interface TabItem {
  id: string;
  label: React.ReactNode;
}

/**
 * Tabs — the product's primary view switcher (underline-active "view-toggle").
 *
 * @startingPoint section="Core" subtitle="Underline-active view switcher" viewport="700x120"
 */
export interface TabsProps {
  /** Tabs to render — plain strings or {id,label} objects. */
  items: (string | TabItem)[];
  /** The id of the currently selected tab. */
  value: string;
  /** Called with the id of a newly selected tab. */
  onChange?: (id: string) => void;
  className?: string;
}

export declare function Tabs(props: TabsProps): React.JSX.Element;
