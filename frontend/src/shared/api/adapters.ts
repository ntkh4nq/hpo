/** Backend shapes -> the prototype's types, in one place (docs/frontend-plan.md §2.2–§2.3). Pure functions. */
import type { ApiConflict, ApiTerm, ApiTrait, Explanations, FindingView, Passage, Ranking } from '.';
import type { Candidate, ConflictView, DiseaseTrait, Evidence, Finding, FindingEdit, HpoTerm, SuggestionResult } from '../types';

export function toTerm(term: ApiTerm): HpoTerm {
  return { id: term.hpo_id, label: term.label_en, definition: term.definition_en ?? '' };
}

export function toFinding(view: FindingView): Finding {
  const finding: Finding = {
    id: view.finding_id, phrase: view.phrase, sourceText: view.source_text, sourceSpan: view.source_span, subject: view.subject,
    measurement: view.measurement ?? null,
    term: view.term ? toTerm(view.term) : null, options: view.options.map(toTerm), candidates: view.candidates.map(toTerm),
    assertion: view.assertion, assessment: view.assessment, mappingStatus: view.mapping_status, temporality: view.temporality,
    excluded: view.excluded, machine: view.machine,
  };
  if (view.note) finding.note = { text: view.note };
  return finding;
}

/** Display of a finding with the clinician's unsent edit applied (same rule as agent/state.py: effective_findings). */
export function applyEdit(finding: Finding, edit: FindingEdit | undefined): Finding {
  if (!edit) return finding;
  const shown: Finding = { ...finding };
  if (edit.assertion) shown.assertion = edit.assertion;
  if (edit.assessment) shown.assessment = edit.assessment;
  if (edit.subject) shown.subject = edit.subject;
  if (edit.excluded !== undefined) shown.excluded = edit.excluded;
  const candidate = edit.hpo_id ? finding.candidates.find(c => c.id === edit.hpo_id) : undefined;
  if (candidate) Object.assign(shown, { term: candidate, mappingStatus: 'MAPPED', options: [] });
  return shown;
}

/** Fields of an edit that differ from what the server holds; `true` when the finding shows a clinician change. */
// `excluded` is set only by the clinician and is not tracked in `machine` (agent/state.py: TRACKED).
export const isEdited = (finding: Finding, edit: FindingEdit | undefined) =>
  Object.keys(finding.machine).length > 0 || finding.excluded || Boolean(edit && Object.keys(edit).some(key => key !== 'finding_id'));

/** A measurement with a number but no conclusion yet: the clinician decides normal or abnormal (T117). */
export const awaitsDecision = (finding: Finding) => Boolean(finding.measurement) && finding.assertion === 'UNCERTAIN';

/** Still waiting for the clinician (T134 (6)): no term yet (unmapped or ambiguous) or an undecided measurement, and
 *  not changed by the clinician. A measurement read as normal needs no term. */
export function needsDoctor(finding: Finding, edit: FindingEdit | undefined): boolean {
  if (isEdited(finding, edit)) return false;
  const shown = applyEdit(finding, edit);
  if (awaitsDecision(shown)) return true;
  if (shown.measurement && shown.assertion === 'ABSENT') return false;
  return shown.mappingStatus !== 'MAPPED';
}

/** The row title of a finding without a mapped term: an undecided measurement reads as quantity + value; one with a
 *  conclusion reads as the clinician's words + value ("ối nhiều · 27 cm", T134 (1)); anything else as its phrase. */
export function plainTitle(finding: Finding): { name: string; value: string | null } {
  if (!finding.measurement) return { name: finding.phrase, value: null };
  return { name: awaitsDecision(finding) ? finding.measurement.quantity : finding.phrase, value: finding.measurement.value };
}

const toTrait = (t: ApiTrait): DiseaseTrait => ({ hpoId: t.hpo_id, labelEn: t.label_en, frequencyRaw: t.frequency_raw, references: t.references });

/** One row per disease term: two absent findings that hit the same recorded phenotype are one contradiction, not two. */
function conflictsByDiseaseTerm(conflicts: ApiConflict[]): ConflictView[] {
  const rows = new Map<string, ConflictView>();
  for (const c of conflicts) {
    const row = rows.get(c.disease_hpo_id);
    if (row) {
      row.queryHpoIds.push(c.query_hpo_id);
      row.findingIds = [...new Set([...row.findingIds, ...c.finding_ids])];
    } else {
      rows.set(c.disease_hpo_id, { queryHpoIds: [c.query_hpo_id], findingIds: [...c.finding_ids], diseaseHpoId: c.disease_hpo_id,
        diseaseLabel: c.disease_label_en ?? c.disease_hpo_id, frequencyRaw: c.frequency_raw, references: c.references });
    }
  }
  return [...rows.values()];
}

interface ResultMeta { analysisId: string; description: string }

export function toResult(ranking: Ranking, profile: Finding[], meta: ResultMeta): SuggestionResult {
  const findingsOf = new Map(ranking.terms.map(term => [term.hpo_id, term.finding_ids]));
  const candidates: Candidate[] = ranking.suggestions.map(s => {
    const matches = s.matches.map(m => ({
      queryHpoId: m.query_hpo_id, findingIds: findingsOf.get(m.query_hpo_id) ?? [], assertion: m.assertion, kind: m.kind,
      diseaseHpoId: m.disease_hpo_id, commonAncestorId: m.common_ancestor_id, frequencyRaw: m.frequency_raw, references: m.references,
    }));
    const matched = matches.filter(m => m.kind !== 'none' && m.kind !== 'excluded').flatMap(m => m.findingIds);
    return {
      id: s.disease_id, name: s.disease_name ?? s.disease_id, sourceDb: s.source_db, rank: s.rank,
      genes: s.genes.map(g => ({ symbol: g.symbol, ncbiGeneId: g.ncbi_gene_id, associationType: g.association_type })),
      matchedFindingIds: [...new Set(matched)], matches,
      unmentioned: s.unmentioned.map(u => ({ hpoId: u.hpo_id, labelEn: u.label_en, frequencyRaw: u.frequency_raw, references: u.references ?? [] })),
      inheritance: (s.inheritance ?? []).map(toTrait), clinicalCourse: (s.clinical_course ?? []).map(toTrait),
      conflicts: conflictsByDiseaseTerm(s.conflicts ?? []),
      evidenceIds: [], explanation: null, overviewId: null,
    };
  });
  return {
    analysisId: meta.analysisId, description: meta.description, profile, summary: '', candidates, evidence: [],
    insufficient: candidates.length === 0,
    skipped: ranking.skipped.map(k => ({ phrase: k.phrase, hpoId: k.hpo_id, reason: k.reason, findingId: k.finding_id, assertion: k.assertion ?? null })),
    terms: ranking.terms.map(term => ({ hpoId: term.hpo_id, label: term.label_en ?? term.hpo_id, findingIds: term.finding_ids, fromOptions: term.from_options, assertion: term.assertion })),
    hpoRelease: ranking.hpo_release, rankingVersion: 0, summaryFor: null, explanations: 'idle',
  };
}

/** Result of a ranking turn whose profile was empty (`done.view.ranking === null`). */
export function emptyResult(profile: Finding[], meta: ResultMeta, hpoRelease: string | null): SuggestionResult {
  return toResult({ hpo_release: hpoRelease ?? '', method: '', terms: [], skipped: [], suggestions: [], genes: [] }, profile, meta);
}

/** Code-built part of a result, used to tell whether a re-rank changed the suggestions. */
export const suggestionsKey = (result: SuggestionResult) =>
  JSON.stringify(result.candidates.map(({ explanation: _e, evidenceIds: _i, overviewId: _o, ...candidate }) => candidate));

export function toEvidence(passage: Passage): Evidence {
  const location = Array.isArray(passage.section_path) ? passage.section_path.join(' › ') : passage.section_path;
  const evidence: Evidence = {
    id: passage.ref, title: passage.title, excerpt: passage.display_text, location, active: passage.active,
  };
  if (passage.url) evidence.url = passage.url;
  if (passage.citation) evidence.citation = passage.citation;
  if (passage.license) evidence.license = passage.license;
  return evidence;
}

/** Attach the literature to the current result; items follow the current ranking, one per suggestion. */
export function applyExplanations(result: SuggestionResult, explanations: Explanations): SuggestionResult {
  const byDisease = new Map(explanations.items.map(item => [item.disease_id, item]));
  const evidence = Object.values(explanations.passages).map(toEvidence);
  const candidates = result.candidates.map(candidate => {
    const item = byDisease.get(candidate.id);
    if (!item) return { ...candidate, explanation: null, evidenceIds: [], overviewId: null };
    const pairs = item.pairs.map(p => ({ queryHpoId: p.query_hpo_id, diseaseHpoId: p.disease_hpo_id, status: p.status, claims: p.claims }));
    const refs = [...new Set(pairs.flatMap(p => p.claims.flatMap(c => c.refs)))].filter(ref => ref in explanations.passages);
    const overviewId = item.overview && item.overview in explanations.passages ? item.overview : null;
    return { ...candidate, explanation: { pairs }, evidenceIds: refs, overviewId };
  });
  return { ...result, candidates, evidence, explanations: 'ready' };
}

/** i18n key of a step's label (colons are namespace separators in i18next, so `tool:x` becomes `step.tool.x`). */
export function stepLabelKey(key: string): string {
  if (key.startsWith('tool:')) return `step.tool.${key.slice(5)}`;
  return `step.node.${key}`;
}

const SUMMARY_KEYS: Record<string, string> = {
  split: 'step.summary.split', check_choices: 'step.summary.check_choices', 'tool:analyze_findings': 'step.summary.analyze_findings',
  'tool:search_disease': 'step.summary.results', 'tool:search_hpo': 'step.summary.results', 'tool:find_passages': 'step.summary.results',
  // no rank summary for explain_disease: ranks mean nothing to the clinician (user, 2026-11-23)
};
export const stepSummaryKey = (key: string): string | null => SUMMARY_KEYS[key] ?? null;

const FREQUENCY_CODES = new Set(['HP:0040280', 'HP:0040281', 'HP:0040282', 'HP:0040283', 'HP:0040284', 'HP:0040285']);
/** i18n key for an HPO frequency term, or null for other raw values (e.g. "3/7", "45%"). */
export const frequencyKey = (raw: string) => (FREQUENCY_CODES.has(raw) ? `frequency.${raw.replace(':', '_')}` : null);
