import { useState, type KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { useAppDispatch, useAppSelector } from '../../redux';
import { phenotypeActions } from '../../redux/slices';
import { selectAnalysis, selectCanEdit, selectEditsFor } from '../../redux/selectors';
import { Badge, Button, Dialog, DialogSection, SourceId } from '../../shared/ui';
import { applyEdit, awaitsDecision, isEdited, plainTitle } from '../../shared/api/adapters';
import type { Assertion, Assessment, Finding, Subject } from '../../shared/types';

const ASSERTIONS: Assertion[] = ['PRESENT', 'ABSENT', 'SUSPECTED', 'UNCERTAIN'];
const ASSESSMENTS: Assessment[] = ['ASSESSED', 'LIMITED', 'NOT_ASSESSABLE', 'NOT_ASSESSED'];
const SUBJECTS: Subject[] = ['FETUS', 'MOTHER', 'FAMILY'];
export const assertionTone = (assertion: Assertion) => (assertion === 'PRESENT' ? 'teal' : assertion === 'ABSENT' ? 'neutral' : 'amber');

type Mode = 'view' | 'note' | 'edit' | 'abnormal';

/** One finding in front of the thread: what the description said, the HPO term and its definition, the clinician's
 *  note, and the edit form (T91). Opened from a row of FindingList. */
export function FindingDialog({ finding: serverFinding, analysisId, onClose }: { finding: Finding | null; analysisId: string; onClose: () => void }) {
  return (
    <Dialog open={Boolean(serverFinding)} onClose={onClose}
      title={serverFinding ? <Title finding={serverFinding}/> : ''}
      subtitle={serverFinding ? <Subtitle finding={serverFinding}/> : undefined}
      status={serverFinding ? <Status finding={serverFinding}/> : undefined}>
      {serverFinding && <Body key={serverFinding.id} serverFinding={serverFinding} analysisId={analysisId} onClose={onClose}/>}
    </Dialog>
  );
}

function useShown(serverFinding: Finding) {
  const edit = useAppSelector(state => selectEditsFor(state, serverFinding.id));
  return { finding: applyEdit(serverFinding, edit), edited: isEdited(serverFinding, edit) };
}

function Title({ finding: serverFinding }: { finding: Finding }) {
  const { finding } = useShown(serverFinding);
  if (finding.term && finding.mappingStatus === 'MAPPED') return <>{finding.term.label}</>;
  const plain = plainTitle(finding);
  return <>{plain.value ? `${plain.name} · ${plain.value}` : plain.name}</>;
}

function Subtitle({ finding: serverFinding }: { finding: Finding }) {
  const { finding } = useShown(serverFinding);
  if (!finding.term || finding.mappingStatus !== 'MAPPED') return null;
  return <SourceId id={finding.term.id} className="detail-dialog__id"/>;
}

function Status({ finding: serverFinding }: { finding: Finding }) {
  const { t } = useTranslation();
  const { finding, edited } = useShown(serverFinding);
  return <>
    <Badge tone={assertionTone(finding.assertion)}>{t(`assertion.${finding.assertion}`)}</Badge>
    {edited && <Badge>{t('finding.edited')}</Badge>}
  </>;
}

function Body({ serverFinding, analysisId, onClose }: { serverFinding: Finding; analysisId: string; onClose: () => void }) {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const canEdit = useAppSelector(selectCanEdit);
  const inReview = useAppSelector(selectAnalysis).status === 'review';
  const { finding } = useShown(serverFinding);
  const [mode, setMode] = useState<Mode>('view');
  const [noteDraft, setNoteDraft] = useState(finding.note?.text ?? '');
  const [form, setForm] = useState({ excluded: finding.excluded, assertion: finding.assertion, assessment: finding.assessment, subject: finding.subject, hpoId: '', phrase: finding.phrase });
  const remapPhrase = form.phrase.trim();
  const mapped = Boolean(finding.term) && finding.mappingStatus === 'MAPPED';
  const facts = [
    t(`subject.${finding.subject}`),
    finding.temporality,
    finding.assessment !== 'ASSESSED' ? t(`assessment.${finding.assessment}`) : null,
  ].filter(Boolean);

  // Escape inside a form returns to the view instead of closing the dialog.
  const backToView = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.stopPropagation(); event.preventDefault(); setMode('view'); } };

  const startEdit = () => {
    setForm({ excluded: finding.excluded, assertion: finding.assertion, assessment: finding.assessment, subject: finding.subject, hpoId: '', phrase: finding.phrase });
    setMode('edit');
  };

  if (mode === 'note') {
    return (
      <form className="finding-editor" onKeyDown={backToView}
        onSubmit={event => {
          event.preventDefault();
          if (noteDraft.trim() !== (finding.note?.text ?? '')) dispatch(phenotypeActions.noteSaved({ analysisId, id: finding.id, text: noteDraft }));
          setMode('view');
        }}>
        <label className="finding-editor__label" htmlFor={`finding-note-${finding.id}`}>{t('finding.note')}</label>
        <textarea autoFocus id={`finding-note-${finding.id}`} maxLength={2000} rows={4} value={noteDraft} onChange={event => setNoteDraft(event.target.value)} placeholder={t('finding.notePlaceholder')}/>
        <p className="small muted">{t('help.note')}</p>
        <div className="editor-actions">
          <Button variant="ghost" onClick={() => setMode('view')}>{t('common.cancelEdit')}</Button>
          <Button type="submit" disabled={!canEdit}>{t('finding.saveNote')}</Button>
        </div>
      </form>
    );
  }

  if (mode === 'abnormal') {
    // A measurement read as abnormal (T118): a concept among the candidates, or the clinician's own words (remap, review only).
    return (
      <form className="finding-editor" onKeyDown={backToView}
        onSubmit={event => {
          event.preventDefault();
          dispatch(phenotypeActions.editSaved({ analysisId, id: finding.id, edit: { assertion: 'PRESENT', ...(form.hpoId ? { hpo_id: form.hpoId } : {}) } }));
          setMode('view');
        }}>
        <p className="small muted">{t('finding.measureAbnormalHint')}</p>
        {finding.candidates.length > 0 && <div className="finding-editor__grid">
          <label htmlFor={`finding-measure-term-${finding.id}`}>{t('finding.termLabel')}</label>
          <select autoFocus id={`finding-measure-term-${finding.id}`} value={form.hpoId} onChange={event => setForm({ ...form, hpoId: event.target.value })}>
            <option value="">{t('finding.measureNoTerm')}</option>
            {finding.candidates.map(candidate => <option key={candidate.id} value={candidate.id}>{candidate.label}</option>)}
          </select>
        </div>}
        <div className="editor-actions">
          <Button variant="ghost" onClick={() => setMode('view')}>{t('common.cancelEdit')}</Button>
          <Button type="submit" disabled={!canEdit || !form.hpoId}>{t('finding.saveEdit')}</Button>
        </div>
        {inReview && (
          <fieldset className="finding-editor__remap">
            <legend>{t('finding.measureWrite')}</legend>
            <input type="text" aria-label={t('finding.measureWrite')} maxLength={4000} value={form.phrase === finding.phrase ? '' : form.phrase} placeholder={t('finding.measureWritePlaceholder')} onChange={event => setForm({ ...form, phrase: event.target.value })}/>
            <div className="editor-actions">
              <Button variant="secondary" disabled={!canEdit || !remapPhrase || remapPhrase === finding.phrase}
                onClick={() => { dispatch(phenotypeActions.remapRequested({ analysisId, requestId: crypto.randomUUID(), id: finding.id, phrase: remapPhrase, assertion: 'PRESENT' })); onClose(); }}>
                {t('finding.remap')}
              </Button>
            </div>
          </fieldset>
        )}
      </form>
    );
  }

  if (mode === 'edit') {
    return (
      <form className="finding-editor" onKeyDown={backToView}
        onSubmit={event => {
          event.preventDefault();
          dispatch(phenotypeActions.editSaved({ analysisId, id: finding.id, edit: {
            excluded: form.excluded, assertion: form.assertion, assessment: form.assessment, subject: form.subject,
            ...(form.hpoId ? { hpo_id: form.hpoId } : {}),
          } }));
          setMode('view');
        }}>
        <div className="finding-editor__grid">
          <label htmlFor={`finding-assertion-${finding.id}`}>{t('finding.assertionLabel')}</label>
          <select autoFocus id={`finding-assertion-${finding.id}`} value={form.assertion} onChange={event => setForm({ ...form, assertion: event.target.value as Assertion })}>
            {ASSERTIONS.map(value => <option key={value} value={value}>{t(`assertion.${value}`)}</option>)}
          </select>
          <label htmlFor={`finding-assessment-${finding.id}`}>{t('finding.assessmentLabel')}</label>
          <select id={`finding-assessment-${finding.id}`} value={form.assessment} onChange={event => setForm({ ...form, assessment: event.target.value as Assessment })}>
            {ASSESSMENTS.map(value => <option key={value} value={value}>{t(`assessment.${value}`)}</option>)}
          </select>
          <label htmlFor={`finding-subject-${finding.id}`}>{t('finding.subjectLabel')}</label>
          <select id={`finding-subject-${finding.id}`} value={form.subject} onChange={event => setForm({ ...form, subject: event.target.value as Subject })}>
            {SUBJECTS.map(value => <option key={value} value={value}>{t(`subject.${value}`)}</option>)}
          </select>
          {finding.candidates.length > 0 && <>
            <label htmlFor={`finding-term-${finding.id}`}>{t('finding.termLabel')}</label>
            <select id={`finding-term-${finding.id}`} value={form.hpoId} onChange={event => setForm({ ...form, hpoId: event.target.value })}>
              <option value="">{t('finding.keepTerm')}</option>
              {finding.candidates.map(candidate => <option key={candidate.id} value={candidate.id}>{candidate.label}</option>)}
            </select>
          </>}
        </div>
        <label className="finding-editor__check">
          <input type="checkbox" checked={form.excluded} onChange={event => setForm({ ...form, excluded: event.target.checked })}/>
          {t('finding.excludeLabel')}
        </label>
        {inReview && (
          <fieldset className="finding-editor__remap">
            <legend>{t('finding.remapLabel')}</legend>
            <input type="text" aria-label={t('finding.remapLabel')} maxLength={4000} value={form.phrase} onChange={event => setForm({ ...form, phrase: event.target.value })}/>
            <p className="small muted">{t('finding.remapHint')}</p>
            <div className="editor-actions">
              <Button variant="secondary" disabled={!canEdit || !remapPhrase || remapPhrase === finding.phrase}
                onClick={() => { dispatch(phenotypeActions.remapRequested({ analysisId, requestId: crypto.randomUUID(), id: finding.id, phrase: remapPhrase })); onClose(); }}>
                {t('finding.remap')}
              </Button>
            </div>
          </fieldset>
        )}
        <div className="editor-actions">
          <Button variant="ghost" onClick={() => setMode('view')}>{t('common.cancelEdit')}</Button>
          <Button type="submit" disabled={!canEdit}>{t('finding.saveEdit')}</Button>
        </div>
      </form>
    );
  }

  return (
    <>
      <DialogSection title={t('finding.sourceLabel')}>
        <blockquote className="source-quote">{finding.sourceText}</blockquote>
        <p className="finding-facts">{facts.join(' · ')}{finding.excluded && <> · <span className="finding-facts__warn">{t('finding.excluded')}</span></>}</p>
      </DialogSection>

      {awaitsDecision(finding) && (
        <DialogSection title={t('finding.measurement')}>
          <p className="finding-attention">{t('finding.measureHint')}</p>
          <div className="editor-actions finding-measure__actions">
            <Button variant="secondary" disabled={!canEdit} onClick={() => dispatch(phenotypeActions.editSaved({ analysisId, id: finding.id, edit: { assertion: 'ABSENT' } }))}>{t('finding.measureNormal')}</Button>
            <Button variant="secondary" disabled={!canEdit} onClick={() => { setForm({ ...form, hpoId: '', phrase: finding.phrase }); setMode('abnormal'); }}>{t('finding.measureAbnormal')}</Button>
          </div>
        </DialogSection>
      )}

      {!mapped && !awaitsDecision(finding) && !(finding.measurement && finding.assertion === 'ABSENT') && (
        <p className="finding-attention">
          {t(`mapping.${finding.mappingStatus}`)}
          {finding.mappingStatus === 'AMBIGUOUS' && finding.options.length > 0 && <> — {t('finding.options', { labels: finding.options.map(option => option.label).join(', ') })}</>}
        </p>
      )}

      {mapped && finding.term?.definition && (
        <DialogSection title={t('finding.definition')}>
          <p className="finding-definition" lang="en">{finding.term.definition}</p>
        </DialogSection>
      )}

      <DialogSection title={t('finding.note')} aside={finding.note?.text && canEdit ? <Button variant="ghost" className="finding-inline-btn" onClick={() => setMode('note')}>{t('finding.editNote')}</Button> : undefined}>
        {finding.note?.text
          ? <p className="clinician-note">{finding.note.text}</p>
          : <p className="small muted">{t('finding.noNote')}</p>}
      </DialogSection>

      <div className="editor-actions finding-dialog__actions">
        {!finding.note?.text && <Button variant="secondary" disabled={!canEdit} onClick={() => setMode('note')}>{t('finding.addNote')}</Button>}
        <Button variant="secondary" disabled={!canEdit} onClick={startEdit}>{t('finding.edit')}</Button>
      </div>
    </>
  );
}
