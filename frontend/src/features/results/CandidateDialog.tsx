import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAppDispatch } from '../../redux';
import { workspaceActions } from '../../redux/slices';
import { Dialog, DialogSection, Icon, SourceId } from '../../shared/ui';
import { frequencyKey } from '../../shared/api/adapters';
import type { Candidate, DiseaseTrait, ExplanationPair, Finding, MatchView, SuggestionResult } from '../../shared/types';

type T = (key: string, options?: Record<string, unknown>) => string;

export const isMatched = (match: MatchView) => match.kind !== 'none' && match.kind !== 'excluded';
export const findingLabel = (finding: Finding | undefined, fallback: string) =>
  (finding?.term ? finding.term.label : null) ?? finding?.sourceText ?? fallback;

/** A raw hpoa frequency in words: an HPO frequency term, "n/m" patients, or the value as written. */
function frequencyText(raw: (string | null)[], t: T): string | null {
  const values = [...new Set(raw.filter((v): v is string => Boolean(v)))].map(v => {
    const key = frequencyKey(v);
    if (key) return t(key);
    return /^\d+\/\d+$/.test(v) ? t('results.frequencyPatients', { value: v }) : v;
  });
  return values.length ? values.join(', ') : null;
}

/** One suggestion in front of the thread: overview, which findings the disease records, what else is frequent, sources. */
export function CandidateDialog({ candidate, result, onClose }: { candidate: Candidate | null; result: SuggestionResult; onClose: () => void }) {
  const { t } = useTranslation();
  return (
    <Dialog open={Boolean(candidate)} onClose={onClose}
      title={candidate?.name ?? ''}
      subtitle={candidate ? <>
        {candidate.genes.length > 0 && <span className="detail-dialog__genes">{t('results.geneLabel')}{' '}
          {candidate.genes.map(gene => <SourceId key={gene.ncbiGeneId} id={gene.ncbiGeneId} label={gene.symbol} className="detail-dialog__gene"/>)}
        </span>}
        <SourceId id={candidate.id} className="detail-dialog__id"/>
      </> : undefined}>
      {candidate && <Body key={candidate.id} candidate={candidate} result={result}/>}
    </Dialog>
  );
}

function Body({ candidate, result }: { candidate: Candidate; result: SuggestionResult }) {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const [showSources, setShowSources] = useState(false);
  const sourcesId = useId();
  const open = (id: string) => dispatch(workspaceActions.evidenceSelected(id));
  const names = (ids: string[], fallback: string) => ids.map(id => findingLabel(result.profile.find(f => f.id === id), id)).join(', ') || fallback;
  const evidence = (id: string) => result.evidence.find(e => e.id === id);
  const overview = candidate.overviewId ? evidence(candidate.overviewId) : undefined;
  const matched = candidate.matches.filter(isMatched);
  const missing = candidate.matches.filter(m => m.kind === 'none');
  const loading = result.explanations === 'loading';
  const literatureFailed = result.explanations === 'error' || Boolean(candidate.explanation?.pairs.some(p => p.status === 'failed'));
  const annotationRefs = [...new Set(matched.flatMap(m => m.references.flat()))];
  const literature = candidate.evidenceIds.map(evidence).filter(e => e !== undefined);

  const traits: [string, DiseaseTrait[]][] = [['results.inheritance', candidate.inheritance], ['results.clinicalCourse', candidate.clinicalCourse]];

  return (
    <>
      {traits.some(([, items]) => items.length > 0) && (
        <DialogSection title={t('results.diseaseInfo')}>
          <dl className="candidate-traits">
            {traits.filter(([, items]) => items.length > 0).map(([label, items]) => (
              <div key={label} className="candidate-traits__row">
                <dt>{t(label)}</dt>
                <dd>{items.map(item => (
                  <span key={item.hpoId} className="candidate-traits__item">
                    {item.labelEn}
                    {frequencyText(item.frequencyRaw, t) && <span className="muted"> ({frequencyText(item.frequencyRaw, t)})</span>}
                    <Refs refs={item.references.flat()}/>
                  </span>
                ))}</dd>
              </div>
            ))}
          </dl>
        </DialogSection>
      )}

      {overview && (
        <DialogSection title={t('results.overview')}>
          <p className="candidate-overview" lang="en">{overview.excerpt}</p>
          <p className="candidate-overview__source"><button type="button" className="candidate-link" onClick={() => open(overview.id)}>{t('results.viewSource')}</button></p>
        </DialogSection>
      )}

      {matched.length > 0 && (
        <DialogSection title={t('results.recorded')}>
          <ul className="candidate-matches">
            {matched.map(match => {
              const pair = candidate.explanation?.pairs.find(p => p.queryHpoId === match.queryHpoId && p.diseaseHpoId === match.diseaseHpoId);
              const frequency = frequencyText(match.frequencyRaw, t);
              const notes = [match.kind !== 'exact' ? t(`match.${match.kind}`) : null, match.assertion === 'SUSPECTED' ? t('results.suspectedInFetus') : null].filter(Boolean);
              return (
                <li key={`${match.queryHpoId}-${match.diseaseHpoId}`} className="candidate-match">
                  <div className="candidate-match__head">
                    <span className="candidate-match__name">{names(match.findingIds, match.queryHpoId)}</span>
                    <span className="candidate-match__frequency">{frequency ?? t('results.frequencyUnknown')}</span>
                  </div>
                  {notes.length > 0 && <p className="candidate-match__note">{notes.join(' · ')}</p>}
                  <Basis frequencyRaw={match.frequencyRaw} references={match.references}/>
                  <Literature pair={pair} onOpen={open}/>
                </li>
              );
            })}
          </ul>
          {literatureFailed && <p className="candidate-matches__hint">{t('results.literatureFailed')}</p>}
          {loading && <p className="results-loading candidate-match__loading"><span className="button-spinner" aria-hidden="true"/>{t('results.literatureLoading')}</p>}
          <p className="candidate-matches__hint">{t('results.frequencyHint')}</p>
        </DialogSection>
      )}

      {candidate.conflicts.length > 0 && (
        <DialogSection title={t('results.conflicts')}>
          <ul className="candidate-matches">
            {candidate.conflicts.map(conflict => (
              <li key={conflict.diseaseHpoId} className="candidate-match">
                <div className="candidate-match__head">
                  <span className="candidate-match__name">{names(conflict.findingIds, conflict.queryHpoIds.join(', '))}</span>
                  <span className="candidate-match__frequency">{frequencyText(conflict.frequencyRaw, t) ?? t('results.frequencyUnknown')}</span>
                </div>
                {conflict.diseaseLabel !== names(conflict.findingIds, conflict.queryHpoIds.join(', ')) && <p className="candidate-match__note">{t('results.conflictRecorded', { term: conflict.diseaseLabel })}</p>}
                <Basis frequencyRaw={conflict.frequencyRaw} references={conflict.references}/>
              </li>
            ))}
          </ul>
          <p className="candidate-matches__hint">{t('results.conflictsHint')}</p>
        </DialogSection>
      )}

      {missing.length > 0 && (
        <DialogSection title={t('results.notRecorded')}>
          <p className="candidate-plain">{missing.map(m => names(m.findingIds, m.queryHpoId)).join(', ')}</p>
        </DialogSection>
      )}

      {candidate.unmentioned.length > 0 && (
        <DialogSection title={t('results.unmentioned')}>
          <ul className="candidate-matches">
            {candidate.unmentioned.map(term => (
              <li key={term.hpoId} className="candidate-match">
                <div className="candidate-match__head">
                  <span className="candidate-match__name">{term.labelEn}</span>
                  <span className="candidate-match__frequency">{frequencyText(term.frequencyRaw, t) ?? t('results.frequencyUnknown')}</span>
                </div>
                <Basis frequencyRaw={term.frequencyRaw} references={term.references}/>
              </li>
            ))}
          </ul>
        </DialogSection>
      )}

      {result.explanations === 'ready' && literature.length === 0 && !overview && (
        <p className="candidate-matches__hint">{t('results.noLiterature')}</p>
      )}

      {(annotationRefs.length > 0 || literature.length > 0) && (
        <section className="candidate-sources">
          <button type="button" className="candidate-sources__toggle" aria-expanded={showSources} aria-controls={sourcesId} onClick={() => setShowSources(value => !value)}>
            <Icon name="chevron" size={14}/>{t('results.sources')}
          </button>
          <div id={sourcesId} className="candidate-sources__body" hidden={!showSources}>
            {literature.length > 0 && (
              <ul className="candidate-sources__docs">
                {literature.map(e => (
                  <li key={e.id}><button type="button" className="candidate-link" onClick={() => open(e.id)}>{e.title}</button></li>
                ))}
              </ul>
            )}
            {annotationRefs.length > 0 && <p className="candidate-sources__refs">{t('results.annotationSources')}: <Refs refs={annotationRefs} bare/></p>}
          </div>
        </section>
      )}
    </>
  );
}

/** Identifiers as links to their source pages (PubMed, OMIM, Orphanet), deduplicated. */
function Refs({ refs, bare = false }: { refs: string[]; bare?: boolean }) {
  const unique = [...new Set(refs)];
  if (!unique.length) return null;
  return <span className={bare ? 'candidate-refs candidate-refs--bare' : 'candidate-refs'}>{unique.map(ref => <SourceId key={ref} id={ref}/>)}</span>;
}

/** What each frequency rests on: the source rows behind it, each with its references (principle #6). */
function Basis({ frequencyRaw, references }: { frequencyRaw: (string | null)[]; references: string[][] }) {
  const { t } = useTranslation();
  if (!references.some(refs => refs.length)) return null;
  const distinct = new Set(frequencyRaw.map(v => v ?? '')).size;
  return (
    <p className="candidate-match__basis">
      <span className="muted">{t('results.sourceOf')}</span>
      {distinct <= 1
        ? <Refs refs={references.flat()}/>
        : references.map((refs, i) => (
          <span key={i} className="candidate-match__basis-row">
            <span className="muted">{frequencyText([frequencyRaw[i]], t) ?? t('results.frequencyUnknown')}</span><Refs refs={refs}/>
          </span>
        ))}
    </p>
  );
}

/** "What the literature says" for one match: only claims grounded in a passage; nothing when there is none. */
function Literature({ pair, onOpen }: { pair: ExplanationPair | undefined; onOpen: (id: string) => void }) {
  const { t } = useTranslation();
  if (!pair || pair.status !== 'cited') return null;
  return (
    <div className="candidate-literature" aria-label={t('results.literature')}>
      {pair.claims.map(claim => (
        <p key={claim.text} className="candidate-literature__claim">
          {claim.text}{' '}
          {claim.refs.length === 1
            ? <button type="button" className="candidate-link" onClick={() => onOpen(claim.refs[0])}>{t('results.openPassage')}</button>
            : <span className="candidate-literature__refs">{t('results.openPassages')}{' '}
                {claim.refs.map((ref, i) => <button key={ref} type="button" className="candidate-link" onClick={() => onOpen(ref)}>{i + 1}</button>)}
              </span>}
        </p>
      ))}
    </div>
  );
}
