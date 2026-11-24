import links from '../config/sourceLinks.json';

const templates = links as Record<string, string>;

/** Public page of an identifier such as OMIM:618505 or PMID:37196654 (shared/config/sourceLinks.json); null when unknown. */
export function sourceUrl(id: string): string | null {
  const at = id.indexOf(':');
  const template = at > 0 ? templates[id.slice(0, at)] : undefined;
  return template && !id.startsWith('_') ? template.replace('{id}', encodeURIComponent(id.slice(at + 1))) : null;
}

/** An identifier in monospace; a link to its source page in a new tab when the prefix is known. */
export function SourceId({ id, label, className = '' }: { id: string; label?: string; className?: string }) {
  const url = sourceUrl(id);
  const text = label ?? id;
  return url
    ? <a className={`source-id source-id--link ${className}`} href={url} target="_blank" rel="noreferrer noopener">{text}</a>
    : <span className={`source-id ${className}`}>{text}</span>;
}
