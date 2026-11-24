import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAppSelector } from '../../redux';
import { selectAnalysis } from '../../redux/selectors';
import { Icon, Markdown } from '../../shared/ui';
import type { RenderLink } from '../../shared/ui/Markdown';
import { stepLabelKey, stepSummaryKey } from '../../shared/api/adapters';
import type { Step, TurnItem } from '../../shared/types';

type StepItem = Extract<TurnItem, { kind: 'step' }>;

/** The growing progress list of one turn: steps in start order, ✓ in place, children as sub-rows, text interleaved (T96, T97). */
export function TurnProgress({ items, renderLink }: { items: TurnItem[]; renderLink?: RenderLink }) {
  const ids = new Set(items.flatMap(item => (item.kind === 'step' ? [item.step.id] : [])));
  const top = items.filter(item => item.kind === 'text' || !item.step.parent_id || !ids.has(item.step.parent_id));
  if (!items.length) return null;
  // T115: a run of finished look-ups of the same kind (16 × "Xem gene …") reads as one row.
  const rows: (TurnItem | StepItem[])[] = [];
  for (const item of top) {
    const last = rows.at(-1);
    const run = Array.isArray(last) ? last : null;
    const groupable = item.kind === 'step' && item.state === 'done' && !items.some(c => c.kind === 'step' && c.step.parent_id === item.step.id);
    if (groupable && run && run[0].step.key === item.step.key) run.push(item);
    else rows.push(groupable ? [item] : item);
  }
  return (
    <ol className="turn-progress" aria-live="polite">
      {rows.map(row => Array.isArray(row)
        ? <StepRow key={row[0].step.id} item={row[0]} items={items} more={row.length - 1}/>
        : row.kind === 'text'
          ? <li key={`text-${row.messageId}`} className="turn-progress__item"><Markdown className="turn-progress__text" text={row.text} renderLink={renderLink}/></li>
          : <StepRow key={row.step.id} item={row} items={items}/>)}
    </ol>
  );
}

function StepRow({ item, items, more = 0 }: { item: StepItem; items: TurnItem[]; more?: number }) {
  const { t, i18n } = useTranslation();
  const candidates = useAppSelector(selectAnalysis).result?.candidates ?? [];
  const names = Object.fromEntries(candidates.map(candidate => [candidate.id, candidate.name]));
  const children = items.filter((child): child is StepItem => child.kind === 'step' && child.step.parent_id === item.step.id);
  // Sub-steps show while the step runs; once it finished they fold behind a toggle (the clinician needs the outcome, not the internals).
  const running = item.state === 'running' || children.some(child => child.state === 'running');
  const [expanded, setExpanded] = useState<boolean | null>(null);
  const open = expanded ?? running;
  const childrenId = useId();
  return (
    <li className="turn-progress__step" data-state={item.state}>
      <div className="turn-progress__row">
        <span className="turn-progress__marker" aria-hidden="true">
          {item.state === 'done' ? <Icon name="check" size={14}/> : item.state === 'running' ? <span className="button-spinner"/> : item.state === 'error' ? <Icon name="close" size={14}/> : '…'}
        </span>
        <span className="turn-progress__label">{more ? t('step.groupMore', { label: label(item.step, t, i18n.exists.bind(i18n), names), count: more }) : label(item.step, t, i18n.exists.bind(i18n), names)}</span>
        {item.state === 'waiting'
          ? <span className="turn-progress__summary small muted">{t('step.waitingReview')}</span>
          : summary(item.step, t) && <span className="turn-progress__summary small muted">{summary(item.step, t)}</span>}
        {item.state === 'running' && <span className="sr-only">{t('common.loading')}</span>}
        {item.state === 'error' && <span className="sr-only">{t('status.error')}</span>}
        {children.length > 0 && !running && (
          <button type="button" className="turn-progress__toggle" aria-expanded={open} aria-controls={childrenId} onClick={() => setExpanded(!open)}>
            <Icon name="chevron" size={12}/>{t(open ? 'step.hideDetails' : 'step.showDetails')}
          </button>
        )}
      </div>
      {children.length > 0 && (
        <ol id={childrenId} className="turn-progress turn-progress--children" hidden={!open}>
          {children.map(child => <StepRow key={child.step.id} item={child} items={items}/>)}
        </ol>
      )}
    </li>
  );
}

type T = (key: string, options?: Record<string, unknown>) => string;
/** A step's label; a disease code in its parameters shows as the disease name when the case lists it. */
function label(step: Step, t: T, exists: (key: string) => boolean, names: Record<string, string> = {}): string {
  if (step.kind === 'todo') return step.label ?? '';
  const key = stepLabelKey(step.key);
  const params: Record<string, unknown> = { ...step.params };
  if (typeof params.disease_id === 'string' && names[params.disease_id]) params.disease_id = names[params.disease_id];
  return exists(key) ? t(key, params) : t('step.unknown', { key: step.key });
}
function summary(step: Step, t: T): string | null {
  const key = stepSummaryKey(step.key);
  return key && step.summary ? t(key, step.summary) : null;
}
