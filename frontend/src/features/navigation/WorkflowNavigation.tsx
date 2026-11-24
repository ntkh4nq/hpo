import { useTranslation } from 'react-i18next';
import { Tooltip } from '../../shared/ui';

export const workspaceSections = ['clinical-input', 'phenotype-review', 'case-results'] as const;
export type WorkspaceSection = typeof workspaceSections[number];

/** Compact horizontal stepper shown in the workspace header. */
export function WorkflowNavigation({
  resultsAvailable = false,
  activeSection,
  onNavigate,
}: {
  resultsAvailable?: boolean;
  activeSection: WorkspaceSection;
  onNavigate: (section: WorkspaceSection) => void;
}) {
  const { t } = useTranslation();
  return (
    <nav className="workflow-stepper" aria-label={t('app.pipeline')}>
      <ol>
        {workspaceSections.map((id, index) => {
          const available = id !== 'case-results' || resultsAvailable;
          const current = activeSection === id;
          return (
            <li key={id} className="workflow-stepper__item">
              {index > 0 && <span className="workflow-stepper__line" aria-hidden="true"/>}
              <Tooltip content={t(`workflow.help${index + 1}`)}>
                <button
                  type="button"
                  className="workflow-stepper__step"
                  aria-current={current ? 'step' : undefined}
                  aria-controls={id}
                  disabled={!available}
                  onClick={() => onNavigate(id)}
                >
                  <span className="workflow-stepper__number" aria-hidden="true">{index + 1}</span>
                  <span className="workflow-stepper__label">{t(`workflow.step${index + 1}`)}</span>
                </button>
              </Tooltip>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
