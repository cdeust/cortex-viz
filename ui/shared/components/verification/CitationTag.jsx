import React from 'react';

/**
 * CitationTag — a claim's source annotation, e.g. "(Nader et al. 2000)".
 * Renders a dashed-underline mono tag; `href` makes it a link, `title` the
 * hover reference. `inline` drops the underline for use mid-sentence.
 */
export function CitationTag({ source, href, inline = false, className = '', title, children, ...rest }) {
  const cls = ['aia-cite', inline ? 'aia-cite--inline' : '', className].filter(Boolean).join(' ');
  const body = (
    <>
      <span className="aia-cite__mark" aria-hidden="true">·</span>
      {children || source}
    </>
  );
  const ref = title || (typeof source === 'string' ? source : undefined);
  if (href) {
    return <a className={cls} href={href} title={ref} {...rest}>{body}</a>;
  }
  return <span className={cls} title={ref} {...rest}>{body}</span>;
}
