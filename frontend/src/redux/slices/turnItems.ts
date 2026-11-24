/** The growing progress list of one turn (docs/frontend-plan.md §3.3; T96, T97). Pure functions. */
import type { Step, TurnItem } from '../../shared/types';

/** A new step id appends a row; a known id updates that row in place (✓ at its own row). Planning calls are not rows. */
export function applyStep(items: TurnItem[], step: Step): TurnItem[] {
  if (step.key === 'tool:write_todos') return items;
  const index = items.findIndex(item => item.kind === 'step' && item.step.id === step.id);
  if (index < 0) return [...items, { kind: 'step', step, state: step.status }];
  return items.map((item, i) => {
    if (i !== index || item.kind !== 'step') return item;
    return { kind: 'step', step: { ...item.step, status: step.status, summary: step.summary ?? item.step.summary, params: step.params ?? item.step.params }, state: step.status };
  });
}

export function applyText(items: TurnItem[], messageId: string, delta: string): TurnItem[] {
  const index = items.findIndex(item => item.kind === 'text' && item.messageId === messageId);
  if (index < 0) return [...items, { kind: 'text', messageId, text: delta }];
  return items.map((item, i) => (i === index && item.kind === 'text' ? { ...item, text: item.text + delta } : item));
}

/** `answer_reset`: the message's text is removed; a replacement `text` with the same id re-creates it at the end. */
export const resetText = (items: TurnItem[], messageId: string): TurnItem[] =>
  items.filter(item => !(item.kind === 'text' && item.messageId === messageId));

/** Rows still running when the turn ends: waiting for review, failed, or done. */
export function endTurn(items: TurnItem[], how: 'done' | 'review' | 'error'): TurnItem[] {
  const state = how === 'review' ? 'waiting' : how;
  return items.map(item => (item.kind === 'step' && item.state === 'running' ? { ...item, state } : item));
}

/** The review the turn waited for was resolved: its waiting rows are done (T134 (5)). */
export const reviewResolved = (items: TurnItem[]): TurnItem[] =>
  items.map(item => (item.kind === 'step' && item.state === 'waiting' ? { ...item, state: 'done' } : item));

export const turnText = (items: TurnItem[]) =>
  items.flatMap(item => (item.kind === 'text' ? [item.text] : [])).join('\n\n');
