import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon, Tooltip } from '../../shared/ui';
import { useAppSelector } from '../../redux';
import { selectAnalysis, selectAnalysisRun, selectBusy, selectChat, selectEdits, selectFindings, selectWorkspace } from '../../redux/selectors';
import { needsDoctor } from '../../shared/api/adapters';
import type { WorkspaceSection } from './WorkflowNavigation';

const COLLAPSED_KEY = 'case-outline-collapsed';
const readCollapsed = () => { try { return localStorage.getItem(COLLAPSED_KEY) === '1'; } catch { return false; } };

type StepState = 'done' | 'current' | 'warning' | 'todo';
interface Step { id: WorkspaceSection; state: StepState; status: string; stats: Array<[string, number]>; available: boolean }

/** Right-hand case progress panel (wide screens): each step with its status and counts. */
export function CaseOutline({ activeSection, onNavigate }: { activeSection: WorkspaceSection; onNavigate: (section: WorkspaceSection) => void }) {
  const { t, i18n } = useTranslation();
  const workspace = useAppSelector(selectWorkspace);
  const run = useAppSelector(selectAnalysisRun);
  const findings = useAppSelector(selectFindings);
  const analysis = useAppSelector(selectAnalysis);
  const chat = useAppSelector(selectChat);
  const busy = useAppSelector(selectBusy);
  const count = new Intl.NumberFormat(i18n.resolvedLanguage);
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const toggleCollapsed = () => setCollapsed(value => {
    try { localStorage.setItem(COLLAPSED_KEY, value ? '0' : '1'); } catch { /* preference stays in memory */ }
    return !value;
  });

  const edited = Boolean(run && run.description !== workspace.description);
  const edits = useAppSelector(selectEdits);
  const needsReview = findings.filter(f => needsDoctor(f, edits[f.id])).length;
  const notes = findings.filter(f => f.note?.text).length;
  const result = analysis.result;
  const retrieving = ['retrieving', 'generating'].includes(analysis.status);

  const steps: Step[] = [
    {
      id: 'clinical-input',
      state: edited ? 'warning' : run?.completed ? 'done' : 'current',
      status: t(edited ? 'outline.edited' : run?.completed ? 'outline.analyzed' : busy ? `status.${analysis.status}` : 'outline.notStarted'),
      stats: [],
      available: true,
    },
    {
      id: 'phenotype-review',
      state: !run?.completed ? 'todo' : result ? 'done' : 'current',
      status: t(!run?.completed ? 'outline.notStarted' : result ? 'outline.analyzed' : 'outline.ready'),
      stats: run?.completed ? [['outline.findings', findings.length], ['outline.needsReview', needsReview], ['outline.notes', notes]] : [],
      available: Boolean(run?.completed),
    },
    {
      id: 'case-results',
      state: retrieving ? 'current' : !result ? 'todo' : 'done',
      status: retrieving ? t(`status.${analysis.status}`) : t(!result ? 'outline.notStarted' : 'outline.current'),
      stats: result ? [['outline.suggestions', result.candidates.length], ['outline.followUps', chat.messages.filter(m => m.role === 'user').length]] : [],
      available: Boolean(result) || retrieving,
    },
  ];

  const marker = (step: Step, index: number) => (
    <span className="outline-step__marker" aria-hidden="true">
      {step.state === 'done'
        ? <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="m5 12 4 4L19 6"/></svg>
        : index + 1}
    </span>
  );

  // One panel whose width animates; the inner layer keeps a fixed width so text never reflows mid-slide.
  return (
    <aside className={`case-outline ${collapsed ? 'case-outline--collapsed' : ''}`} aria-label={t('outline.title')}>
      {collapsed ? (
        <div className="case-outline__inner case-outline__inner--mini" key="mini">
        <Tooltip content={t('outline.expand')}>
          <button type="button" className="icon-button case-outline__toggle" onClick={toggleCollapsed} aria-expanded={false} aria-label={t('outline.expand')}>
            <span className="case-outline__toggle-icon case-outline__toggle-icon--expand"><Icon name="chevron" size={16}/></span>
          </button>
        </Tooltip>
        <ol className="case-outline__mini">
          {steps.map((step, index) => (
            <li key={step.id} className={`outline-step--${step.state}`}>
              <Tooltip content={`${t(`workflow.step${index + 1}`)} · ${step.status}`}>
                <button
                  type="button"
                  className="outline-mini-step"
                  aria-current={activeSection === step.id ? 'step' : undefined}
                  aria-label={`${t(`workflow.step${index + 1}`)}: ${step.status}`}
                  disabled={!step.available}
                  onClick={() => onNavigate(step.id)}
                >
                  {marker(step, index)}
                </button>
              </Tooltip>
            </li>
          ))}
        </ol>
        </div>
      ) : (
        <div className="case-outline__inner" key="full">
      <div className="case-outline__header">
        <h2 id="case-outline-title" className="case-outline__title">{t('outline.title')}</h2>
        <Tooltip content={t('outline.collapse')}>
          <button type="button" className="icon-button case-outline__toggle" onClick={toggleCollapsed} aria-expanded={true} aria-label={t('outline.collapse')}>
            <span className="case-outline__toggle-icon"><Icon name="chevron" size={16}/></span>
          </button>
        </Tooltip>
      </div>
      <ol className="case-outline__steps">
        {steps.map((step, index) => (
          <li key={step.id} className={`outline-step outline-step--${step.state}`}>
            {marker(step, index)}
            <button
              type="button"
              className="outline-step__button"
              aria-current={activeSection === step.id ? 'step' : undefined}
              aria-controls={step.id}
              disabled={!step.available}
              onClick={() => onNavigate(step.id)}
            >
              <span className="outline-step__name">{t(`workflow.step${index + 1}`)}</span>
              <span className="outline-step__status">{step.status}</span>
            </button>
            {step.stats.length > 0 && (
              <dl className="outline-step__stats">
                {step.stats.map(([label, value]) => (
                  <div key={label} className={label === 'outline.needsReview' && value > 0 ? 'outline-stat--attention' : undefined}>
                    <dt>{t(label)}</dt>
                    <dd>{count.format(value)}</dd>
                  </div>
                ))}
              </dl>
            )}
          </li>
        ))}
      </ol>
        </div>
      )}
    </aside>
  );
}
