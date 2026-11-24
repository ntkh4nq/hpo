import { useTranslation } from 'react-i18next';
import { Tooltip } from '../shared/ui';

/** Heading for an assistant turn. */
export function WorkflowHeading({ step }: { step: 1 | 2 | 3 }) {
  const { t } = useTranslation();
  return (
    <div className="workflow-heading">
      <Tooltip content={t(`workflow.help${step}`)}>
        <h2 id={`workflow-heading-${step}`} tabIndex={0}>
          {t(`workflow.step${step}`)}
        </h2>
      </Tooltip>
    </div>
  );
}
