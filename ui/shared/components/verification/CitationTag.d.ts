import * as React from 'react';

/**
 * CitationTag — a claim's source annotation, e.g. "(Nader et al. 2000)".
 */
export interface CitationTagProps extends React.HTMLAttributes<HTMLElement> {
  /** The citation text, e.g. "Nader et al. 2000". */
  source?: React.ReactNode;
  /** If set, render as a link to the paper / reference. */
  href?: string;
  /** Drop the dashed underline for mid-sentence use. @default false */
  inline?: boolean;
}

export declare function CitationTag(props: CitationTagProps): React.JSX.Element;
