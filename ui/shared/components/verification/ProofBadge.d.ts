import * as React from 'react';

/**
 * ProofBadge — the verdict of a zetetic gate (proven / unsourced / blocked /
 * pending). The signature verification primitive.
 *
 * @startingPoint section="Verification" subtitle="Gate verdict — proven / unsourced / blocked" viewport="700x120"
 */
export interface ProofBadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** The gate verdict. @default "proven" */
  status?: 'proven' | 'unsourced' | 'blocked' | 'pending';
  /** Override the default label text (e.g. "2 violations"). */
  children?: React.ReactNode;
}

export declare function ProofBadge(props: ProofBadgeProps): React.JSX.Element;
