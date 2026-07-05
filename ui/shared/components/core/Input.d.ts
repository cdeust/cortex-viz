import * as React from 'react';

/**
 * Input — text / search field in the cmdk aesthetic with terracotta focus.
 *
 * @startingPoint section="Core" subtitle="Mono field, optional leading icon" viewport="700x110"
 */
export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  /** Optional leading icon node, inset into the field. */
  icon?: React.ReactNode;
}

export declare function Input(props: InputProps): React.JSX.Element;
