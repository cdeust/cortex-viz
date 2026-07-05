import * as React from 'react';

export interface SelectOption {
  value: string;
  label: React.ReactNode;
}

/**
 * Select — native dropdown skinned to match Input.
 */
export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  /** Options as strings or {value,label}. Omit to pass <option> children. */
  options?: (string | SelectOption)[];
}

export declare function Select(props: SelectProps): React.JSX.Element;
