/** Backend-shaped fixtures for tests only (docs/frontend-plan.md §7.1). Every shape follows §2.1 exactly. */
import type { ApiCandidate, ApiTerm, CaseView, Explanations, FindingView, Passage, Ranking, SseEvent, TurnRecordItem, TurnView } from '../shared/api';
import type { Citation, Step } from '../shared/types';

export const DESCRIPTION = 'Thai 22 tuần. Ghi nhận thiểu sản xương mũi thai. Không thấy thông liên thất sau khảo sát. Não thất khó đánh giá do tư thế thai.';

/** Vietnamese names exist only on search candidates (used for retrieval, never shown, T120). */
const VI_NAMES: Record<string, [string | null, boolean]> = {};
const term = (hpo_id: string, label_en: string, label_vi: string | null, label_vi_reviewed = false): ApiTerm => {
  VI_NAMES[hpo_id] = [label_vi, label_vi_reviewed];
  return { hpo_id, label_en, definition_en: `Definition of ${label_en}.` };
};
const candidate = (t: ApiTerm, matched_text: string, dense_score: number): ApiCandidate =>
  ({ ...t, label_vi: VI_NAMES[t.hpo_id]?.[0] ?? null, label_vi_reviewed: VI_NAMES[t.hpo_id]?.[1] ?? false, matched_text, dense_score });

export const NASAL = term('HP:0025707', 'Hypoplastic fetal nasal bone', 'Thiểu sản xương mũi thai', true);
export const VSD = term('HP:0001629', 'Ventricular septal defect', 'Thông liên thất');
export const VENTRICULOMEGALY = term('HP:0002119', 'Ventriculomegaly', 'Giãn não thất');
export const VENTRICLE_MORPHOLOGY = term('HP:0002118', 'Abnormal cerebral ventricle morphology', null);
export const HEART = term('HP:0001627', 'Abnormal heart morphology', 'Bất thường hình thái tim');
export const HEART_POSITION = term('HP:0001631', 'Atrial septal defect', 'Thông liên nhĩ', true);

const base = { temporality: '22 tuần', subject: 'FETUS' as const, excluded: false, note: null, machine: {} };

export const findingViews: FindingView[] = [
  { ...base, finding_id: 'f1', phrase: 'thiểu sản xương mũi thai', source_text: 'Ghi nhận thiểu sản xương mũi thai.', source_span: [14, 48],
    assertion: 'PRESENT', assessment: 'ASSESSED', mapping_status: 'MAPPED', term: NASAL, options: [], candidates: [candidate(NASAL, 'thiểu sản xương mũi thai', 0.91)] },
  { ...base, finding_id: 'f2', phrase: 'thông liên thất', source_text: 'Không thấy thông liên thất sau khảo sát.', source_span: [49, 89],
    assertion: 'ABSENT', assessment: 'ASSESSED', mapping_status: 'MAPPED', term: VSD, options: [], candidates: [candidate(VSD, 'thông liên thất', 0.88)] },
  { ...base, finding_id: 'f3', phrase: 'não thất khó đánh giá', source_text: 'Não thất khó đánh giá do tư thế thai.', source_span: [90, 127],
    assertion: 'UNCERTAIN', assessment: 'LIMITED', mapping_status: 'UNMAPPED', term: null, options: [],
    candidates: [candidate(VENTRICULOMEGALY, 'giãn não thất', 0.62), candidate(VENTRICLE_MORPHOLOGY, 'abnormal cerebral ventricle', 0.58)] },
];

export const ambiguousFindingViews: FindingView[] = [
  { ...base, finding_id: 'f1', phrase: 'nghi bất thường tim', source_text: 'Nghi bất thường tim, chưa xác định cấu trúc.', source_span: [14, 58],
    assertion: 'SUSPECTED', assessment: 'LIMITED', mapping_status: 'AMBIGUOUS', term: null, options: [HEART, HEART_POSITION],
    candidates: [candidate(HEART, 'bất thường tim', 0.7), candidate(HEART_POSITION, 'thông liên nhĩ', 0.55)] },
];

/** f3 after the clinician re-worded it as "giãn não thất" (remap). */
export const remappedFindingViews: FindingView[] = [
  findingViews[0], findingViews[1],
  { ...findingViews[2], phrase: 'giãn não thất', mapping_status: 'MAPPED', term: VENTRICULOMEGALY,
    candidates: [candidate(VENTRICULOMEGALY, 'giãn não thất', 0.95)],
    machine: { phrase: 'não thất khó đánh giá', term: null, mapping_status: 'UNMAPPED' } },
];

const match = (kind: Ranking['suggestions'][number]['matches'][number]['kind'], disease_hpo_id: string | null, extra: Partial<Ranking['suggestions'][number]['matches'][number]> = {}) => ({
  query_hpo_id: 'HP:0025707', assertion: 'PRESENT' as const, weight: 1, kind, disease_hpo_id, common_ancestor_id: null,
  frequency_raw: kind === 'none' ? [] : ['HP:0040282'], evidence: kind === 'none' ? [] : ['PCS'], references: kind === 'none' ? [] : [['PMID:1234567']], ...extra,
});

export const ranking: Ranking = {
  hpo_release: 'v2026-10-01', method: 'ppo',
  terms: [{ hpo_id: 'HP:0025707', assertion: 'PRESENT', weight: 1, label_en: 'Hypoplastic fetal nasal bone', from_options: [], finding_ids: ['f1'] }],
  skipped: [
    { phrase: 'thông liên thất', hpo_id: 'HP:0001629', reason: 'absent_checked', finding_id: 'f2', assertion: 'ABSENT' },
    { phrase: 'não thất khó đánh giá', hpo_id: null, reason: 'unmapped', finding_id: 'f3', assertion: 'UNCERTAIN' },
  ],
  suggestions: [
    { rank: 1, disease_id: 'OMIM:190685', disease_name: 'Down syndrome', source_db: 'OMIM', score: 3.2,
      matches: [match('exact', 'HP:0025707')],
      unmentioned: [{ hpo_id: 'HP:0001631', label_en: 'Atrial septal defect', frequency_raw: ['HP:0040283'] }],
      genes: [] },
    { rank: 2.5, disease_id: 'ORPHA:2000', disease_name: 'Tie disease A', source_db: 'ORPHA', score: 1.1,
      matches: [match('related', 'HP:0000430', { common_ancestor_id: 'HP:0005105', assertion: 'PRESENT' })],
      unmentioned: [],
      genes: [
        { ncbi_gene_id: '2263', symbol: 'FGFR2', association_type: 'MENDELIAN', source: 'orphanet' },
        { ncbi_gene_id: '2261', symbol: 'FGFR3', association_type: null, source: 'omim' },
      ] },
    { rank: 2.5, disease_id: 'OMIM:300000', disease_name: null, source_db: 'OMIM', score: 1.1,
      matches: [match('excluded', 'HP:0025707')],
      unmentioned: [],
      genes: [{ ncbi_gene_id: '9999', symbol: 'GENEX', association_type: 'MENDELIAN', source: 'omim' }] },
  ],
  genes: [{ ncbi_gene_id: '2263', symbol: 'FGFR2', best_rank: 2.5, disease_ids: ['ORPHA:2000'] }],
};

/** The same ranking after f1 was edited to SUSPECTED on its card: different suggestions. */
export const rerankedRanking: Ranking = {
  ...ranking,
  terms: [{ ...ranking.terms[0], assertion: 'SUSPECTED', weight: 0.5 }],
  suggestions: [ranking.suggestions[1], { ...ranking.suggestions[0], rank: 2 }, { ...ranking.suggestions[2], rank: 3 }].map((s, i) => ({ ...s, rank: i + 1 })),
};

export const caseView = (overrides: Partial<CaseView> = {}): CaseView => ({
  case_id: 'case-1', status: 'idle', description: DESCRIPTION, hpo_release: 'v2026-10-01',
  analysis_flags: { split_failed: false, choose_failed: false }, findings: findingViews, ranking, messages: [], ...overrides,
});

export const passage: Passage = {
  chunk_id: 101, ref: 'CHUNK:101', kind: 'text', display_text: 'Absent or hypoplastic nasal bone is a marker of trisomy 21.',
  figure_path: null, section_path: ['Prenatal diagnosis', 'Ultrasound markers'], doc_id: 7, source: 'pmc', title: 'Prenatal markers review',
  citation: 'Doe J. 2020', url: 'https://pmc.ncbi.nlm.nih.gov/articles/PMC0000001/', license: 'CC BY', disease_ids: ['OMIM:190685'], active: true,
};

export const explanations = (overrides: Partial<Explanations> = {}): Explanations => ({
  explanation_key: 'k1', cached: false, created_at: '2026-11-23T00:00:00Z', model: 'deepseek-v4-flash', prompt_version: 'v1',
  usage: { input_tokens: 10, output_tokens: 5, requests: 1 },
  items: [
    { disease_id: 'OMIM:190685', pairs: [{ query_hpo_id: 'HP:0025707', disease_hpo_id: 'HP:0025707', status: 'cited',
      claims: [{ text: 'Hypoplastic nasal bone is a known marker.', refs: ['CHUNK:101'] }] }] },
    { disease_id: 'ORPHA:2000', pairs: [{ query_hpo_id: 'HP:0025707', disease_hpo_id: 'HP:0000430', status: 'no_passage', claims: [] }] },
    { disease_id: 'OMIM:300000', pairs: [{ query_hpo_id: 'HP:0025707', disease_hpo_id: 'HP:0025707', status: 'failed', claims: [] }] },
  ],
  passages: { 'CHUNK:101': passage },
  ...overrides,
});

const step = (id: string, key: string, status: Step['status'], extra: Partial<Step> = {}): SseEvent =>
  ({ event: 'step', data: { id, parent_id: null, kind: key === 'todo' ? 'todo' : key.startsWith('tool:') ? 'tool' : 'node', key, status, label: null, params: null, summary: null, ...extra } });

/** First message: description -> findings for review. */
export const descriptionTurn: SseEvent[] = [
  { event: 'case', data: { case_id: 'case-1' } },
  step('call-1', 'tool:analyze_findings', 'running', { params: {} }),
  step('n-extract', 'extract', 'running', { parent_id: 'call-1' }),
  step('n-split', 'split', 'running', { parent_id: 'call-1' }),
  step('n-split', 'split', 'done', { parent_id: 'call-1', summary: { items: 3 } }),
  step('n-choose', 'choose_terms', 'running', { parent_id: 'call-1' }),
  step('n-choose', 'choose_terms', 'done', { parent_id: 'call-1' }),
  step('n-check', 'check_choices', 'running', { parent_id: 'call-1' }),
  step('n-check', 'check_choices', 'done', { parent_id: 'call-1', summary: { mapped: 2, ambiguous: 0, unmapped: 1 } }),
  step('n-extract', 'extract', 'done', { parent_id: 'call-1' }),
  { event: 'review', data: { findings: findingViews } },
];

/** A later description message on an existing case (no `case` event). */
export const reanalysisTurn: SseEvent[] = descriptionTurn.slice(1);

export const ambiguousTurn: SseEvent[] = [
  { event: 'case', data: { case_id: 'case-1' } },
  step('call-1', 'tool:analyze_findings', 'running'),
  { event: 'review', data: { findings: ambiguousFindingViews } },
];

export const emptyReviewTurn: SseEvent[] = [
  step('call-1', 'tool:analyze_findings', 'running'),
  { event: 'review', data: { findings: [] } },
];

export const SUMMARY_PARTS = ['Có ba gợi ý. ', 'Hạng 1 là ', 'OMIM:190685.'];

/** "Tiếp tục": edits applied, ranking, supervisor summary. */
export const continueTurn = (view: CaseView = caseView()): SseEvent[] => [
  step('call-1', 'tool:analyze_findings', 'done', { summary: { findings: 3, suggestions: 3 } }),
  { event: 'artifact', data: { kind: 'findings', data: view.findings } },
  { event: 'artifact', data: { kind: 'ranking', data: view.ranking ?? ranking } },
  ...SUMMARY_PARTS.map((delta): SseEvent => ({ event: 'text', data: { message_id: 'm-sum', delta } })),
  { event: 'done', data: { citations: [{ id: 'OMIM:190685', kind: 'disease', label: 'Down syndrome' }], view } },
];

export const emptyContinueTurn: SseEvent[] = [
  { event: 'text', data: { message_id: 'm-empty', delta: 'Chưa có biểu hiện để xếp hạng.' } },
  { event: 'done', data: { citations: [], view: caseView({ findings: [], ranking: null }) } },
];

/** Follow-up question; the first answer is withdrawn by the guard and replaced. */
export const chatTurn: SseEvent[] = [
  step('todo:Giải thích hạng 1', 'todo', 'running', { label: 'Giải thích hạng 1' }),
  step('call-todos', 'tool:write_todos', 'running', { parent_id: 'todo:Giải thích hạng 1' }),
  step('call-todos', 'tool:write_todos', 'done', { parent_id: 'todo:Giải thích hạng 1' }),
  step('call-ex', 'tool:explain_disease', 'running', { parent_id: 'todo:Giải thích hạng 1', params: { disease_id: 'OMIM:190685' } }),
  step('call-ex', 'tool:explain_disease', 'done', { parent_id: 'todo:Giải thích hạng 1', params: { disease_id: 'OMIM:190685' }, summary: { rank: 1 } }),
  { event: 'text', data: { message_id: 'm-chat', delta: 'Câu trả lời ' } },
  { event: 'text', data: { message_id: 'm-chat', delta: 'bị rút lại' } },
  { event: 'answer_reset', data: { message_id: 'm-chat' } },
  { event: 'text', data: { message_id: 'm-chat', delta: 'Bệnh hạng 1 khớp HP:0025707 [CHUNK:101].' } },
  step('todo:Giải thích hạng 1', 'todo', 'done', { label: 'Giải thích hạng 1' }),
  { event: 'done', data: { citations: [
    { id: 'HP:0025707', kind: 'hpo', label: 'Thiểu sản xương mũi thai' },
    { id: 'CHUNK:101', kind: 'chunk', label: 'Prenatal markers review' },
  ], view: caseView() } },
];

export const NEW_DESCRIPTION = 'Thai 22 tuần. Nghi bất thường tim, chưa xác định cấu trúc.';
/** The description the server analysed after that chat message: the case's description plus the message (T137). */
export const MERGED_DESCRIPTION = `${DESCRIPTION}\n${NEW_DESCRIPTION}`;
/** A chat message the supervisor reads as a new description: ends in review. */
export const chatReviewTurn: SseEvent[] = [
  step('call-2', 'tool:analyze_findings', 'running'),
  step('n-split-2', 'split', 'running', { parent_id: 'call-2' }),
  { event: 'review', data: { findings: ambiguousFindingViews, description: MERGED_DESCRIPTION } },
];

/** A message answered without reading findings (a question as the first message). */
export const answerOnlyTurn: SseEvent[] = [
  { event: 'case', data: { case_id: 'case-q' } },
  { event: 'text', data: { message_id: 'm-q', delta: 'HPO là bộ thuật ngữ ' } },
  { event: 'text', data: { message_id: 'm-q', delta: 'kiểu hình chuẩn.' } },
  { event: 'done', data: { citations: [], view: caseView({ case_id: 'case-q', description: null, findings: [], ranking: null }) } },
];

export const remapTurn: SseEvent[] = [
  step('n-remap', 'remap_one', 'running'),
  step('n-remap', 'remap_one', 'done'),
  { event: 'review', data: { findings: remappedFindingViews } },
];

export const cancelTurn: SseEvent[] = [
  { event: 'text', data: { message_id: 'm-cancel', delta: 'Đã huỷ bước xem biểu hiện.' } },
  { event: 'done', data: { citations: [], view: caseView({ ranking: null }) } },
];

export const errorTurn: SseEvent[] = [
  step('call-e', 'tool:analyze_findings', 'running'),
  step('call-e', 'tool:analyze_findings', 'error'),
  { event: 'error', data: { code: 'model_unavailable', message: 'gateway timeout' } },
];

/** A saved turn (case_turn, T116) from the SSE events it streamed: final steps in first-seen order, texts as shown. */
export function savedTurn(turn_no: number, route: TurnView['route'], input: TurnView['input'], events: SseEvent[], outcome: TurnView['outcome'], citations: Citation[] = []): TurnView {
  const order: string[] = [];
  const steps: Record<string, Step> = {};
  const texts: Record<string, string> = {};
  for (const e of events) {
    if (e.event === 'step') { if (!(('s:' + e.data.id) in steps)) order.push('s:' + e.data.id); steps['s:' + e.data.id] = e.data; }
    if (e.event === 'text') { if (!(('t:' + e.data.message_id) in texts)) order.push('t:' + e.data.message_id); texts['t:' + e.data.message_id] = (texts['t:' + e.data.message_id] ?? '') + e.data.delta; }
    if (e.event === 'answer_reset') texts['t:' + e.data.message_id] = '';
  }
  const items: TurnRecordItem[] = order.flatMap((key): TurnRecordItem[] => key.startsWith('s:') ? [{ kind: 'step', step: steps[key] }]
    : texts[key] ? [{ kind: 'text', message_id: key.slice(2), text: texts[key] }] : []);
  return { turn_no, route, input, items, outcome, citations };
}

/** A saved case: description turn, "Tiếp tục" with its summary, one follow-up question, a card edit, archived notes. */
export const savedCaseView = (overrides: Partial<CaseView> = {}, rankingChanged = false): CaseView => caseView({
  case_id: 'case-9', title: 'Ca đã lưu', description: 'Mô tả gốc của bác sĩ.',
  findings: [{ ...findingViews[0], note: 'Ghi chú đã lưu.' }, { ...findingViews[1], assertion: 'SUSPECTED', machine: { assertion: 'ABSENT' } }, { ...findingViews[2], excluded: true }],
  turns: [
    savedTurn(1, 'message', { text: 'Mô tả gốc của bác sĩ.' }, descriptionTurn, 'review'),
    savedTurn(2, 'review', { action: 'continue', edits: [], notes: {} }, continueTurn(), 'done', [{ id: 'OMIM:190685', kind: 'disease', label: 'Down syndrome' }]),
    savedTurn(3, 'message', { text: 'Giải thích thêm' }, chatTurn, 'done', [{ id: 'CHUNK:101', kind: 'chunk', label: 'Prenatal markers review' }]),
    savedTurn(4, 'findings', { edits: [], notes: {}, ranking_changed: rankingChanged }, [], 'done'),
  ],
  notes_archive: [{ description: 'Mô tả cũ.', findings: [{ ...findingViews[1], note: 'Ghi chú cũ.' }] }],
  ...overrides,
});
