import { parseSse } from './sse';
import type { Assertion, Assessment, Citation, FindingEdit, MappingStatus, MatchKind, PairStatus, Step, Subject } from '../types';

/* Backend shapes, exactly as the API sends them (docs/frontend-plan.md §2.1; backend/api, agent/state.py, ranking/schema.py). */
export interface ApiTerm { hpo_id: string; label_en: string; definition_en: string | null }
/** A search candidate also carries the Vietnamese name used for retrieval; it is never shown (T120). */
export interface ApiCandidate extends ApiTerm { label_vi: string | null; label_vi_reviewed: boolean; matched_text: string; dense_score: number | null }
export interface FindingView {
  finding_id: string; phrase: string; source_text: string; source_span: [number, number] | null;
  assertion: Assertion; assessment: Assessment; temporality: string | null; subject: Subject;
  mapping_status: MappingStatus; term: ApiTerm | null; options: ApiTerm[]; candidates: ApiCandidate[];
  measurement?: { quantity: string; value: string } | null;
  excluded: boolean; note: string | null; machine: Record<string, unknown>;
}
export interface QueryTerm { hpo_id: string; assertion: 'PRESENT' | 'SUSPECTED'; weight: number; label_en?: string | null; from_options: string[]; finding_ids: string[] }
/** `reason` is a key (ranking/schema.py SKIP_REASONS, T129); cases saved before T129 carry Vietnamese text instead. */
export interface ApiSkipped { phrase: string | null; hpo_id: string | null; reason: string; finding_id: string | null; assertion?: string | null }
export interface ApiMatch {
  query_hpo_id: string; assertion: 'PRESENT' | 'SUSPECTED'; weight: number; kind: MatchKind;
  disease_hpo_id: string | null; common_ancestor_id: string | null;
  frequency_raw: (string | null)[]; evidence: (string | null)[]; references: string[][];
}
export interface ApiUnmentioned { hpo_id: string; label_en: string; frequency_raw: (string | null)[]; references?: string[][] }
export interface ApiTrait { hpo_id: string; label_en: string; frequency_raw: (string | null)[]; references: string[][] }
/** An absent fetal finding the disease records frequently or without frequency (T130); never changes rank. */
export interface ApiConflict { query_hpo_id: string; finding_ids: string[]; disease_hpo_id: string; disease_label_en?: string | null; frequency_raw: (string | null)[]; evidence: (string | null)[]; references: string[][] }
export interface ApiGene { ncbi_gene_id: string; symbol: string; association_type: string | null; source: string | null }
export interface Suggestion {
  rank: number; disease_id: string; disease_name: string | null; source_db: string; score: number;
  matches: ApiMatch[]; unmentioned: ApiUnmentioned[]; genes: ApiGene[];
  inheritance?: ApiTrait[]; clinical_course?: ApiTrait[]; conflicts?: ApiConflict[];
}
export interface Ranking {
  hpo_release: string; method: string; terms: QueryTerm[]; skipped: ApiSkipped[]; suggestions: Suggestion[];
  absent?: { hpo_id: string; label_en: string | null; finding_ids: string[] }[];
  genes: { ncbi_gene_id: string; symbol: string; best_rank: number; disease_ids: string[] }[];
}
/** Corrections of the previous analysis re-applied to the new findings (T132). */
export interface CarryOver { applied: number; not_applied: number }
export interface MessageView { role: 'user' | 'assistant'; text: string; citations: Citation[] }
export type CaseStatus = 'idle' | 'running' | 'review';
/** One row of the case list (GET /cases, T116). */
export interface CaseSummary { case_id: string; title: string; created_at: string; updated_at: string; status: CaseStatus }
/** What one turn showed the doctor (case_turn, T116). `findings`: a card edit after the suggestions (no model). */
export type TurnRecordItem = { kind: 'step'; step: Step } | { kind: 'text'; message_id: string; text: string };
export interface TurnView {
  turn_no: number; route: 'message' | 'review' | 'findings';
  input: { text?: string; action?: 'continue' | 'remap' | 'cancel'; ranking_changed?: boolean } & Record<string, unknown>;
  items: TurnRecordItem[]; outcome: 'review' | 'done' | 'error' | null; citations: Citation[];
}
export interface ArchivedNotesView { description: string; findings: FindingView[] }
export interface CaseView {
  case_id: string; status: CaseStatus; title?: string; updated_at?: string | null; description: string | null; hpo_release: string | null;
  analysis_flags: { split_failed?: boolean; choose_failed?: boolean } | null;
  findings: FindingView[]; ranking: Ranking | null; messages: MessageView[];
  turns?: TurnView[]; notes_archive?: ArchivedNotesView[];
  /** The last turn paused for review but the server's checkpoint expired: the text must be sent again (T139). */
  review_expired?: boolean;
}
export interface Passage {
  chunk_id: number; ref: string; kind: string; display_text: string; figure_path: string | null;
  section_path: string[] | string; doc_id: number; source: string; title: string; citation: string | null;
  url: string | null; license: string; disease_ids: string[]; active: boolean;
}
export interface Explanations {
  explanation_key: string; cached: boolean; created_at: string; model: string; prompt_version: string;
  usage: Record<string, number>;
  items: { disease_id: string; overview?: string | null; pairs: { query_hpo_id: string; disease_hpo_id: string; status: PairStatus; claims: { text: string; refs: string[] }[] }[] }[];
  passages: Record<string, Passage>;
}
export type SseEvent =
  | { event: 'case'; data: { case_id: string } }
  | { event: 'step'; data: Step }
  | { event: 'text'; data: { message_id: string; delta: string } }
  | { event: 'answer_reset'; data: { message_id: string } }
  | { event: 'artifact'; data: { kind: 'findings'; data: FindingView[] } | { kind: 'ranking'; data: Ranking } }
  | { event: 'review'; data: { findings: FindingView[]; carry_over?: CarryOver | null; description?: string | null } }
  | { event: 'done'; data: { citations: Citation[]; view: CaseView } }
  | { event: 'error'; data: { code: 'model_unavailable' | 'internal_error'; message: string } };
export type ReviewDecision =
  | { action: 'continue'; edits: FindingEdit[]; notes: Record<string, string> }
  | { action: 'remap'; finding_id: string; phrase: string; assertion?: Assertion }
  | { action: 'cancel' };

/** Non-2xx answer. `code`: the router's detail.code; `invalid_request` for FastAPI 422; `not_deployed` for an unknown route. */
export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const API_BASE: string = import.meta.env?.VITE_API_BASE ?? '/api';

async function toApiError(response: Response): Promise<ApiError> {
  let body: { detail?: unknown } = {};
  try { body = await response.json(); } catch { /* not JSON */ }
  const detail = body.detail;
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const { code, message } = detail as { code?: string; message?: string };
    return new ApiError(response.status, code ?? 'unknown', message ?? '');
  }
  if (Array.isArray(detail)) return new ApiError(response.status, 'invalid_request', String((detail[0] as { msg?: string })?.msg ?? ''));
  if (response.status === 404) return new ApiError(404, 'not_deployed', String(detail ?? ''));
  return new ApiError(response.status, 'unknown', typeof detail === 'string' ? detail : response.statusText);
}

type Method = 'GET' | 'POST' | 'PATCH' | 'DELETE';
async function request(path: string, method: Method, body: unknown, signal: AbortSignal | undefined, accept: string): Promise<Response> {
  const response = await fetch(API_BASE + path, {
    method, signal,
    headers: body === undefined ? { Accept: accept } : { 'Content-Type': 'application/json', Accept: accept },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw await toApiError(response);
  return response;
}

async function json<T>(path: string, method: Method, body: unknown, signal?: AbortSignal): Promise<T> {
  return (await request(path, method, body, signal, 'application/json')).json() as Promise<T>;
}

async function* stream(path: string, body: unknown, signal?: AbortSignal): AsyncGenerator<SseEvent> {
  const response = await request(path, 'POST', body, signal, 'text/event-stream');
  if (!response.body) return;
  for await (const event of parseSse(response.body)) yield event as SseEvent;
}

const casePath = (caseId: string) => `/cases/${encodeURIComponent(caseId)}`;

/** The project backend (docs/frontend-plan.md §2.3). Methods are plain properties so tests can replace them. */
export const caseApi = {
  createCase: (text: string, signal?: AbortSignal): AsyncIterable<SseEvent> => stream('/cases', { text }, signal),
  // wholeDescription: the text is the whole description (the description box) and replaces it; a chat message is added to it (T137).
  sendMessage: (caseId: string, text: string, signal?: AbortSignal, wholeDescription = false): AsyncIterable<SseEvent> =>
    stream(`${casePath(caseId)}/messages`, wholeDescription ? { text, whole_description: true } : { text }, signal),
  review: (caseId: string, decision: ReviewDecision, signal?: AbortSignal): AsyncIterable<SseEvent> => stream(`${casePath(caseId)}/review`, decision, signal),
  updateFindings: (caseId: string, edits: FindingEdit[], notes: Record<string, string>, signal?: AbortSignal): Promise<CaseView> =>
    json<CaseView>(`${casePath(caseId)}/findings`, 'POST', { edits, notes }, signal),
  getCase: (caseId: string, signal?: AbortSignal): Promise<CaseView> => json<CaseView>(casePath(caseId), 'GET', undefined, signal),
  listCases: (signal?: AbortSignal): Promise<CaseSummary[]> => json<CaseSummary[]>('/cases', 'GET', undefined, signal),
  renameCase: (caseId: string, title: string, signal?: AbortSignal): Promise<CaseView> => json<CaseView>(casePath(caseId), 'PATCH', { title }, signal),
  deleteCase: async (caseId: string, signal?: AbortSignal): Promise<void> => { await request(casePath(caseId), 'DELETE', undefined, signal, 'application/json'); },
  explanations: (caseId: string, signal?: AbortSignal): Promise<Explanations> => json<Explanations>(`${casePath(caseId)}/explanations`, 'POST', undefined, signal),
  passage: (chunkId: string, signal?: AbortSignal): Promise<Passage> => json<Passage>(`/passages/${encodeURIComponent(chunkId)}`, 'GET', undefined, signal),
};
