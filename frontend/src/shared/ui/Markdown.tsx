import type { ReactNode } from 'react';

/** `[label](target)` in supervisor text; the caller decides what a target becomes (T114). */
export type RenderLink = (label: string, target: string, key: number) => ReactNode;

/** Minimal Markdown for supervisor text: # headings, paragraphs, line breaks, - / 1. lists, **bold**, *italic*, `code`,
 * [label](target) links, [CODE:123] citations and bare source codes rendered by `renderLink` (plain text without it).
 * | tables |.
 * Builds React elements only (no HTML injection); unclosed markers while streaming stay as typed. */
export function Markdown({ text, className = '', renderLink }: { text: string; className?: string; renderLink?: RenderLink }) {
  const inline = (value: string) => inlineNodes(value, renderLink);
  const blocks: ReactNode[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let table: string[][] = [];
  const cells = (line: string) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim());
  const flushTable = () => {
    // GFM table: header row, separator row (---), body rows; anything shorter stays as paragraph text
    if (table.length >= 2 && table[1].every(cell => /^:?-{2,}:?$/.test(cell))) {
      const [head, , ...body] = table;
      blocks.push(
        <div key={blocks.length} className="markdown__table-wrap">
          <table className="markdown__table">
            <thead><tr>{head.map((cell, i) => <th key={i}>{inline(cell)}</th>)}</tr></thead>
            <tbody>{body.map((row, r) => <tr key={r}>{head.map((_, i) => <td key={i}>{inline(row[i] ?? '')}</td>)}</tr>)}</tbody>
          </table>
        </div>);
    } else {
      table.forEach(row => paragraph.push(`| ${row.join(' | ')} |`));
    }
    table = [];
  };
  const flushParagraph = () => {
    if (paragraph.length) blocks.push(<p key={blocks.length}>{paragraph.flatMap((line, i) => (i ? [<br key={i}/>, ...inline(line)] : inline(line)))}</p>);
    paragraph = [];
  };
  const flushList = () => {
    if (list) {
      const items = list.items.map((item, i) => <li key={i}>{inline(item)}</li>);
      blocks.push(list.ordered ? <ol key={blocks.length}>{items}</ol> : <ul key={blocks.length}>{items}</ul>);
    }
    list = null;
  };
  for (const raw of text.split('\n')) {
    const line = raw.trimEnd();
    if (/^\s*\|.*\|\s*$/.test(line)) { flushParagraph(); flushList(); table.push(cells(line)); continue; }
    if (table.length) { flushTable(); flushParagraph(); }
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    const heading = /^\s*#{1,6}\s+(.*)$/.exec(line);
    if (!line.trim()) { flushParagraph(); flushList(); continue; }
    if (bullet || numbered) {
      flushParagraph();
      const ordered = Boolean(numbered);
      if (list && list.ordered !== ordered) flushList();
      list ??= { ordered, items: [] };
      list.items.push((bullet ?? numbered)?.[1] ?? '');
      continue;
    }
    flushList();
    if (heading) { flushParagraph(); blocks.push(<h4 key={blocks.length} className="markdown__heading">{inline(heading[1])}</h4>); continue; }
    paragraph.push(line);
  }
  flushTable();
  flushParagraph();
  flushList();
  return <div className={`markdown ${className}`}>{blocks}</div>;
}

const TOKEN = /(\[[^\]\n]+\]\([^)\s]+\)|\[[A-Z]+:\d+\](?!\()|\b(?:HP|OMIM|ORPHA|PMID|CHUNK):\d+\b|\*\*[^*\n]+\*\*|`[^`\n]+`|\*[^*\s][^*\n]*\*)/g;
const BARE = /^(?:HP|OMIM|ORPHA|PMID|CHUNK):\d+$/;
const CITE = /^\[([A-Z]+:\d+)\]$/;
const LINK = /^\[([^\]\n]+)\]\(([^)\s]+)\)$/;
function inlineNodes(text: string, renderLink?: RenderLink): ReactNode[] {
  return text.split(TOKEN).filter(Boolean).map((part, i) => {
    const link = LINK.exec(part);
    if (link) return renderLink ? renderLink(link[1], link[2], i) : link[1];
    const cite = CITE.exec(part);
    if (cite) return renderLink ? renderLink(cite[1], cite[1], i) : part;
    if (renderLink && BARE.test(part)) return renderLink(part, part, i);
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) return <strong key={i}>{inlineNodes(part.slice(2, -2), renderLink)}</strong>;
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) return <code key={i}>{part.slice(1, -1)}</code>;
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2) return <em key={i}>{part.slice(1, -1)}</em>;
    return part;
  });
}
