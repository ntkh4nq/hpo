import { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAppDispatch, useAppSelector } from '../../redux';
import { analysisActions } from '../../redux/slices';
import {
  selectAnalysis,
  selectAnalysisRun,
  selectBusy,
  selectCanRetrieve,
  selectEdits,
  selectFindings,
} from '../../redux/selectors';
import { Button, EmptyState, Icon, Panel, Tooltip } from '../../shared/ui';
import { awaitsDecision, applyEdit, plainTitle } from '../../shared/api/adapters';
import { FindingDialog } from './FindingDialog';
import { FindingList } from './FindingList';
import { PreviousAnalysisNotes } from './PreviousAnalysisNotes';
import { TurnProgress } from '../progress';

export function PhenotypeReview({ onRetrieveRequested, onEditDescription }: { onRetrieveRequested?: () => void; onEditDescription: () => void }) {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();

  const findings = useAppSelector(selectFindings);
  const run = useAppSelector(selectAnalysisRun);
  const canRetrieve = useAppSelector(selectCanRetrieve);
  const busy = useAppSelector(selectBusy);
  const analysis = useAppSelector(selectAnalysis);

  // Once suggestions exist, the findings they used fold into a summary; they reopen on demand.
  const resultCurrent = Boolean(analysis.result);
  const [showFindings, setShowFindings] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const opened = findings.find(finding => finding.id === openId) ?? null;
  const listId = useId();
  // Fold only when a supervisor-written ranking arrives, not on each card re-rank (P7).
  useEffect(() => { setShowFindings(false); }, [analysis.result?.summaryFor]);
  const folded = resultCurrent && !showFindings && findings.length > 0;
  const notes = findings.filter(finding => finding.note?.text).length;
  // Undecided measurements do not block "Tiếp tục"; the clinician is told they will not be used (T134 (7)).
  const edits = useAppSelector(selectEdits);
  const undecided = findings.filter(finding => { const shown = applyEdit(finding, edits[finding.id]); return awaitsDecision(shown) && !shown.excluded; }).length;
  // Findings behind the terms the current ranking used, counted by code (T129).
  const usedCount = new Set(analysis.result?.terms.flatMap(term => term.findingIds) ?? []).size;
  // Progress of the description turn; it stays above the empty state when the first message was a question.
  const showProgress = analysis.reviewProgress.length > 0 && !folded;

  const handleRetrieve = () => {
    dispatch(analysisActions.suggestionsRequested({ requestId: crypto.randomUUID() }));
    onRetrieveRequested?.();
  };

  return (
    <Panel className="phenotype-review-panel">
      {showProgress && <TurnProgress items={analysis.reviewProgress}/>}
      {findings.length === 0 ? (
        busy ? (showProgress ? null : <div className="review-loading" aria-busy="true"><span className="sr-only">{t('common.processing')}</span><div className="loading-line"/><div className="loading-line loading-line--short"/><div className="loading-card"/></div>) : <EmptyState title={t('phenotype.empty')} hint={t('phenotype.emptyHint')}/>
      ) : (
        folded ? (
          <div className="review-summary">
            <div className="review-summary__head">
              <p className="review-summary__title">{t('phenotype.usedForResults', { count: findings.length, used: usedCount })}</p>
              {notes > 0 && <span className="review-summary__notes">{t('phenotype.notesCount', { count: notes })}</span>}
            </div>
            <ul className="review-summary__terms" aria-label={t('workflow.step2')}>
              {findings.map(finding => (
                <li key={finding.id} className="term-chip" data-assertion={finding.assertion}>
                  <span className="term-chip__label">{finding.term ? finding.term.label : (({ name, value }) => (value ? `${name} · ${value}` : name))(plainTitle(finding))}</span>
                  <span className="sr-only">: {t(`assertion.${finding.assertion}`)}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : (
        <>
          <p className="findings-count">{t('phenotype.foundCount', { count: findings.length })}</p>
          {analysis.carryOver && analysis.status === 'review' && <p className="findings-carry">{t(analysis.carryOver.not_applied ? 'phenotype.carryOverPartial' : 'phenotype.carryOver', { applied: analysis.carryOver.applied, missing: analysis.carryOver.not_applied })}</p>}
          <FindingList id={listId} findings={findings} onOpen={setOpenId}/>
        </>
        )
      )}
      <FindingDialog finding={opened} analysisId={run?.id ?? ''} onClose={() => setOpenId(null)}/>
      <PreviousAnalysisNotes/>
      <div className="phenotype-footer">
        {canRetrieve && undecided > 0 && <p className="review-footer-note">{t('phenotype.undecidedMeasurements', { count: undecided })}</p>}
        <div className="review-footer-actions">
          <Tooltip content={t('help.editDescription')}><Button variant="secondary" disabled={busy} onClick={onEditDescription}>{t('input.editDescription')}</Button></Tooltip>
          {resultCurrent && findings.length > 0 && <Button variant="ghost" onClick={() => setShowFindings(value => !value)} aria-expanded={showFindings} aria-controls={showFindings ? listId : undefined}>
            <Icon name="chevron" size={14}/>{t(showFindings ? 'phenotype.hideFindings' : 'phenotype.showFindings')}
          </Button>}
          {canRetrieve && <Button variant="primary" onClick={handleRetrieve} className="retrieve-btn">{t('phenotype.retrieve')}</Button>}
        </div>
      </div>
    </Panel>
  );
}
