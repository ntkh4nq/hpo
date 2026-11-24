import { useTranslation } from 'react-i18next';
import { useAppDispatch, useAppSelector } from '../../redux';
import { workspaceActions } from '../../redux/slices';
import { selectSelectedEvidence } from '../../redux/selectors';
import { Button, Dialog, Feedback } from '../../shared/ui';

export function EvidenceModal() {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const selected = useAppSelector(selectSelectedEvidence);
  const evidence = selected?.evidence ?? null;
  const handleClose = () => dispatch(workspaceActions.evidenceSelected(null));

  return (
    <Dialog open={Boolean(selected)} onClose={handleClose}
      title={evidence ? evidence.title : t('evidence.title')}
      footer={<Button variant="secondary" onClick={handleClose}>{t('evidence.close')}</Button>}>
      {selected && !evidence && <>
        {selected.loading && <p className="evidence-location-text" role="status">{t('evidence.loading')}</p>}
        {selected.gone && <Feedback>{t('evidence.gone')}</Feedback>}
        {selected.failed && <Feedback error>{t('common.error')}</Feedback>}
      </>}
      {evidence && <>
        <div className="evidence-meta-row">
          <span className="field-tag small muted">{t('evidence.location')}</span>
          <p className="evidence-location-text">{evidence.location}</p>
        </div>

        <div className="evidence-excerpt-section">
          <span className="field-tag small muted">{t('evidence.excerpt')}</span>
          {evidence.active === false && <p className="small muted">{t('evidence.inactive')}</p>}
          <p className="evidence-excerpt-quote" lang="en">{evidence.excerpt}</p>
        </div>

        {evidence.citation && (
          <div className="evidence-meta-row">
            <span className="field-tag small muted">{t('evidence.source')}</span>
            <p className="evidence-location-text" lang="en">{evidence.citation}</p>
          </div>
        )}

        {evidence.url && <a className="source-origin" href={evidence.url} target="_blank" rel="noopener noreferrer">{t('evidence.openSource')} ↗</a>}
        {evidence.license && <p className="small muted" lang="en">{t('evidence.license')}: {evidence.license}</p>}
      </>}
    </Dialog>
  );
}
