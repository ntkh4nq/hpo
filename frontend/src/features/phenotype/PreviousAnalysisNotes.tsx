import { useTranslation } from 'react-i18next';
import { useAppSelector } from '../../redux';
import { selectNotesArchive } from '../../redux/selectors';

export function PreviousAnalysisNotes() {
  const { t } = useTranslation();
  const archive = useAppSelector(selectNotesArchive);
  if (!archive.length) return null;
  return <details className="previous-analysis-notes">
    <summary>{t('notes.previous', { count: archive.length })}</summary>
    {[...archive].reverse().map((run, index) => <section key={run.analysisId} className="archived-note-run" aria-labelledby={`notes-run-${run.analysisId}`}>
      <h3 id={`notes-run-${run.analysisId}`}>{t('notes.run', { number: archive.length - index })}</h3>
      <p className="archived-description">{run.description}</p>
      {run.findings.map(finding => <article key={finding.id} className="archived-finding-note">
        <h4>{finding.term ? finding.term.label : t('finding.unmapped')} {finding.term && <span className="term-id">{finding.term.id}</span>}</h4>
        <p className="small muted">{t(`assertion.${finding.assertion}`)} · {t(`assessment.${finding.assessment}`)}</p>
        <blockquote className="source-quote">{finding.sourceText}</blockquote>
        <div className="clinician-note"><strong>{t('finding.note')}</strong><p>{finding.note?.text}</p></div>
      </article>)}
    </section>)}
  </details>;
}
