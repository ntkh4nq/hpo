import { useTranslation } from 'react-i18next';
import { useAppDispatch, useAppSelector } from '../../redux';
import { chatActions, workspaceActions } from '../../redux/slices';
import { selectAnalysis, selectBusy, selectCanChat, selectWorkspace } from '../../redux/selectors';
import { Button, EmptyState, Feedback, Icon, Markdown, Panel, sourceUrl } from '../../shared/ui';
import type { RenderLink } from '../../shared/ui/Markdown';
import { TurnProgress } from '../progress';
import { CandidateDialog, findingLabel, isMatched } from './CandidateDialog';
import type { SuggestionResult } from '../../shared/types';
import { skipLine } from './skipLine';

/** Related diseases and genes: a short summary, one row per suggestion (details open in a dialog), findings not used. */
export function ResultsPanel() {
  const { t } = useTranslation();
  const analysis = useAppSelector(selectAnalysis);
  const busy = useAppSelector(selectBusy);
  const result = analysis.result;
  const dispatch = useAppDispatch();
  const openId = useAppSelector(selectWorkspace).openCandidateId;
  const canChat = useAppSelector(selectCanChat);
  const setOpenId = (id: string | null) => dispatch(workspaceActions.candidateOpened(id));

  if (!result && busy) {
    return (
      <Panel className="results-panel">
        <TurnProgress items={analysis.resultsProgress}/>
        <div className="results-loading" role="status"><span className="button-spinner" aria-hidden="true"/>{t(`status.${analysis.status}`)}</div>
        <div className="review-loading" aria-hidden="true"><div className="loading-line"/><div className="loading-line loading-line--short"/><div className="loading-card"/></div>
      </Panel>
    );
  }

  if (!result) {
    return <Panel className="results-panel"><TurnProgress items={analysis.resultsProgress}/><EmptyState title={t('results.empty')} hint={t('results.emptyHint')}/></Panel>;
  }

  // A suggestion sharing no finding carries no basis the clinician can check; it is counted, not listed.
  const listed = result.candidates.filter(candidate => candidate.matches.some(isMatched));
  const hidden = result.candidates.length - listed.length;
  const streaming = analysis.status === 'generating';
  // The list follows the summary that introduces it: shown once the summary is written, not under the streaming text.
  const showCandidates = !streaming && !result.insufficient && listed.length > 0;
  const summary = streaming
    ? analysis.summaryDraft.map(entry => entry.text).join('\n\n')
    : result.summaryFor === result.rankingVersion ? result.summary : '';
  const opened = result.candidates.find(candidate => candidate.id === openId) ?? null;
  // A card edit changed the list after the summary was written: say so and offer a new one (T134 (4)).
  const summaryOutdated = !streaming && Boolean(result.summary) && result.summaryFor !== result.rankingVersion;
  // Every finding the ranking did not use is listed with its state and reason, written by code (T129).
  const skipped = result.skipped;
  // Summary links (T114): a listed disease opens its details; a source code (PMID…) opens its page; anything else is text.
  const renderLink: RenderLink = (label, target, key) => {
    if (label === target && !/^\d+$/.test(label) && !target.startsWith('PMID:')) return label;   // a bare code: as written
    if (listed.some(candidate => candidate.id === target)) {
      // An <a>, not a <button>: a button's text never wraps, so long disease names jumped whole to the next line.
      return <a key={key} href={`#${target}`} className="summary-ref" onClick={event => { event.preventDefault(); setOpenId(target); }}>{label}</a>;
    }
    const url = sourceUrl(target);
    return url ? <a key={key} className={/^\d+$/.test(label) ? 'summary-source summary-source--cite' : 'summary-source'} title={target} href={url} target="_blank" rel="noreferrer noopener">{label}</a> : label;
  };

  return (
    <Panel className="results-panel">
      {!streaming && result.terms.length > 0 && (
        <p className="results-facts">
          <span className="results-facts__title">{t('results.usedTerms', { count: result.terms.length })}</span>{' '}
          {result.terms.map((term, index) => (
            <span key={term.hpoId}>{index > 0 && ' · '}{term.label}{term.assertion === 'SUSPECTED' && <span className="muted"> ({t('results.suspectedShort')})</span>}</span>
          ))}
        </p>
      )}
      {summary && <Markdown className="results-summary" text={summary} renderLink={renderLink}/>}
      {summaryOutdated && (
        <div className="results-outdated">
          <p className="muted">{t('results.summaryOutdated')}</p>
          <Button variant="secondary" disabled={!canChat} onClick={() => dispatch(chatActions.requested({ requestId: crypto.randomUUID(), text: t('results.rewriteSummaryRequest') }))}>{t('results.rewriteSummary')}</Button>
        </div>
      )}
      {streaming && !summary && <div className="results-loading" role="status"><span className="button-spinner" aria-hidden="true"/>{t('status.generating')}</div>}

      {result.insufficient && <Feedback><strong>{t('results.insufficient')}</strong></Feedback>}

      {showCandidates && (
        <section className="results-section" aria-labelledby="results-candidates-title">
          <div className="results-section__head">
            <h3 id="results-candidates-title" className="section-title">{t('results.candidates')} <span className="section-title__count">{listed.length}</span></h3>
            <p className="results-section__hint">{t('results.scoreHint')}</p>
          </div>
          <ul className="candidate-list">
            {listed.map(candidate => {
              const shared = candidate.matches.filter(isMatched).length;
              // Two sources can list the same disease (OMIM and Orphanet); only then does the row name its source.
              const twin = listed.some(other => other !== candidate && other.name === candidate.name);
              const meta = [candidate.genes.map(gene => gene.symbol).join(', '), twin ? t('results.fromSource', { source: t(`source.${candidate.sourceDb}`, { defaultValue: candidate.sourceDb }) }) : '']
                .filter(Boolean).join(' · ');
              return (
                <li key={candidate.id}>
                  <button type="button" className="candidate-row" onClick={() => setOpenId(candidate.id)}>
                    <span className="candidate-row__main">
                      <span className="candidate-row__name">{candidate.name}</span>
                      {meta && <span className="candidate-row__genes">{meta}</span>}
                    </span>
                    {candidate.matches.length > 0 && <span className="candidate-row__match">{t('results.sharesOf', { shared, total: candidate.matches.length })}
                      {candidate.conflicts.length > 0 && <span className="candidate-row__conflict">{t('results.conflictCount', { count: candidate.conflicts.length })}</span>}</span>}
                    <span className="candidate-row__chevron"><Icon name="chevron" size={16}/></span>
                  </button>
                </li>
              );
            })}
          </ul>
          {hidden > 0 && <p className="results-section__hint">{t('results.hiddenNoMatch', { count: hidden })}</p>}
        </section>
      )}

      {!streaming && skipped.length > 0 && (
        <details className="result-explanation">
          <summary>{t('results.skipped', { count: skipped.length })}</summary>
          <ul className="skipped-list">
            {skipped.map(item => {
              const finding = result.profile.find(f => f.id === item.findingId);
              return <li key={`${item.findingId ?? item.hpoId}-${item.phrase}`}><strong>{finding ? findingLabel(finding, item.phrase ?? '') : item.phrase ?? item.hpoId}</strong> <span className="muted">— {skipLine(item, finding, t)}</span></li>;
            })}
          </ul>
        </details>
      )}

      <CandidateDialog candidate={opened} result={result as SuggestionResult} onClose={() => setOpenId(null)}/>
    </Panel>
  );
}
