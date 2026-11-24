import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type { AnalysisRequest, Evidence, Notice, ReviewContinueRequest, SuggestionResult, TurnItem } from '../../shared/types';
import type { CarryOver, Explanations } from '../../shared/api';
import { applyExplanations, suggestionsKey } from '../../shared/api/adapters';
import { workspaceActions } from './workspaceSlice';
import { turnActions } from './turnActions';
import { applyStep, applyText, endTurn, resetText, reviewResolved } from './turnItems';

type Status = 'idle' | 'extracting' | 'mapping' | 'review' | 'retrieving' | 'generating' | 'complete' | 'error';
export type CachedEvidence = Evidence | { gone: true };
const initialState = {
  status: 'idle' as Status, requestId: null as string | null,
  result: null as SuggestionResult | null,
  /** i18n key of the current error, e.g. `common.error`, `error.caseBusy`. */
  error: null as string | null, errorMessage: null as string | null,
  notice: null as Notice,
  /** Corrections of the previous analysis re-applied by the server to this review (T132); null when none existed. */
  carryOver: null as CarryOver | null,
  reviewProgress: [] as TurnItem[], resultsProgress: [] as TurnItem[],
  /** Supervisor text of the ranking turn while it streams, one entry per message (P7, T97). */
  summaryDraft: [] as { messageId: string; text: string }[],
  /** Passages fetched for chat CHUNK citations; `{gone: true}` when the passage no longer exists. */
  evidenceCache: {} as Record<string, CachedEvidence>,
  evidenceFailedId: null as string | null,
};
type State = typeof initialState;
const startTurn = (state: State, requestId: string): State => ({ ...initialState, status: 'extracting', requestId, evidenceCache: state.evidenceCache });

const slice = createSlice({ name: 'analysis', initialState, reducers: {
  requested: (state, action: PayloadAction<AnalysisRequest>) => startTurn(state, action.payload.requestId),
  /** A chat turn that ended in review: the new description replaces the case's findings (no saga listens). */
  restartedForReview: (state, action: PayloadAction<{ requestId: string }>) => startTurn(state, action.payload.requestId),
  stageChanged(state, action: PayloadAction<{ requestId: string; stage: 'mapping' | 'generating' }>) { if (state.requestId === action.payload.requestId) state.status = action.payload.stage; },
  succeeded(state, action: PayloadAction<{ requestId: string; notice: Notice; carryOver?: CarryOver | null }>) {
    if (state.requestId !== action.payload.requestId) return;
    state.status = 'review'; state.notice = action.payload.notice;
    const counts = action.payload.carryOver;
    if (counts !== undefined) state.carryOver = counts && counts.applied + counts.not_applied > 0 ? counts : null;
  },
  failed(state, action: PayloadAction<{ requestId: string; error: string; errorMessage?: string | null }>) {
    if (state.requestId !== action.payload.requestId) return;
    state.status = 'error'; state.error = action.payload.error; state.errorMessage = action.payload.errorMessage ?? null;
  },
  /** An error of a request that is not a turn (POST /findings, GET /cases): shown without changing the status. */
  errorShown(state, action: PayloadAction<{ error: string; errorMessage?: string | null }>) {
    state.error = action.payload.error; state.errorMessage = action.payload.errorMessage ?? null;
  },
  noticeSet(state, action: PayloadAction<{ requestId: string; notice: Notice }>) { if (state.requestId === action.payload.requestId) state.notice = action.payload.notice; },
  /** Clear a notice that no longer holds, whichever turn set it (T127: findings arrived after "no findings"). */
  noticeCleared(state, action: PayloadAction<Notice>) { if (state.notice === action.payload) state.notice = null; },
  /** "Tiếp tục" → POST /review continue; accepted only while the server waits for review. */
  suggestionsRequested(state, action: PayloadAction<ReviewContinueRequest>) {
    if (state.status !== 'review') return;
    Object.assign(state, { status: 'retrieving', requestId: action.payload.requestId, error: null, errorMessage: null, summaryDraft: [], resultsProgress: [] });
  },
  suggestionsDiscarded(state, action: PayloadAction<string>) { if (state.requestId === action.payload) { state.requestId = null; state.status = state.result ? 'complete' : 'idle'; } },
  /** A supervisor-written ranking (artifact ranking): a new version that the turn's summary describes. */
  suggestionsSucceeded(state, action: PayloadAction<{ requestId: string; result: SuggestionResult }>) {
    if (state.requestId !== action.payload.requestId) return;
    const version = (state.result?.rankingVersion ?? 0) + 1;
    state.status = 'complete';
    state.result = { ...action.payload.result, rankingVersion: version, summaryFor: version };
  },
  /** Re-rank in code (POST /findings, T91): a new version only when the suggestions changed; the summary is kept but hidden then (P7). */
  rankingReplaced(state, action: PayloadAction<{ result: SuggestionResult }>) {
    const old = state.result;
    const incoming = action.payload.result;
    if (!old) { state.result = { ...incoming, rankingVersion: 1, summaryFor: null }; return; }
    if (suggestionsKey(old) === suggestionsKey(incoming)) { state.result = { ...old, profile: incoming.profile, skipped: incoming.skipped, terms: incoming.terms }; return; }
    state.result = { ...incoming, summary: old.summary, summaryFor: old.summaryFor, rankingVersion: old.rankingVersion + 1 };
  },
  remapStarted(state, action: PayloadAction<{ requestId: string }>) {
    if (state.status !== 'review') return;
    Object.assign(state, { requestId: action.payload.requestId, status: 'mapping', error: null, errorMessage: null, reviewProgress: [] });
  },
  explanationsRequested() {},
  explanationsLoading(state) { if (state.result) state.result.explanations = 'loading'; },
  explanationsReceived(state, action: PayloadAction<{ rankingVersion: number; explanations: Explanations }>) {
    if (state.result?.rankingVersion === action.payload.rankingVersion) state.result = applyExplanations(state.result, action.payload.explanations);
  },
  explanationsUnavailable(state) { if (state.result) state.result.explanations = 'unavailable'; },
  explanationsFailed(state) { if (state.result) state.result.explanations = 'error'; },
  explanationsIdle(state) { if (state.result) state.result.explanations = 'idle'; },
  evidenceCached(state, action: PayloadAction<{ id: string; evidence: CachedEvidence }>) {
    state.evidenceCache[action.payload.id] = action.payload.evidence;
    if (state.evidenceFailedId === action.payload.id) state.evidenceFailedId = null;
  },
  evidenceFailed(state, action: PayloadAction<string | null>) { state.evidenceFailedId = action.payload; },
}, extraReducers: builder => {
  for (const action of [workspaceActions.descriptionChanged, workspaceActions.sampleLoaded]) builder.addCase(action, state => ({ ...initialState, evidenceCache: state.evidenceCache }));
  builder.addCase(workspaceActions.reset, () => initialState);
  builder.addCase(workspaceActions.caseOpened, (_state, action) => ({
    ...initialState,
    result: action.payload.result, notice: action.payload.notice, reviewProgress: action.payload.reviewProgress,
    status: action.payload.reviewPending ? 'review' : action.payload.result ? 'complete' : 'idle',
  }));
  builder.addCase(workspaceActions.caseExpired, state => {
    Object.assign(state, { status: 'idle', notice: 'caseExpired', requestId: null, error: null, errorMessage: null });
  });
  builder.addCase(turnActions.turnStep, (state, action) => {
    const { requestId, origin, step } = action.payload;
    if (requestId !== state.requestId) return;
    if (origin === 'analysis') state.reviewProgress = applyStep(state.reviewProgress, step);
    if (origin === 'results') state.resultsProgress = applyStep(state.resultsProgress, step);
  });
  builder.addCase(turnActions.turnText, (state, action) => {
    const { requestId, origin, messageId, delta } = action.payload;
    if (requestId !== state.requestId) return;
    if (origin === 'analysis') state.reviewProgress = applyText(state.reviewProgress, messageId, delta);
    if (origin === 'results') {
      const entry = state.summaryDraft.find(e => e.messageId === messageId);
      if (entry) entry.text += delta;
      else state.summaryDraft.push({ messageId, text: delta });
    }
  });
  builder.addCase(turnActions.turnTextReset, (state, action) => {
    const { requestId, origin, messageId } = action.payload;
    if (requestId !== state.requestId) return;
    if (origin === 'analysis') state.reviewProgress = resetText(state.reviewProgress, messageId);
    if (origin === 'results') state.summaryDraft = state.summaryDraft.filter(e => e.messageId !== messageId);
  });
  builder.addCase(turnActions.turnEnded, (state, action) => {
    const { requestId, origin, how } = action.payload;
    if (requestId !== state.requestId || origin === 'chat') return;
    if (origin === 'analysis') state.reviewProgress = endTurn(state.reviewProgress, how);
    if (origin === 'results') {
      state.resultsProgress = endTurn(state.resultsProgress, how);
      if (how === 'done') state.reviewProgress = reviewResolved(state.reviewProgress);
      if (how === 'done' && state.result && state.result.summaryFor === state.result.rankingVersion) {
        state.result.summary = state.summaryDraft.map(e => e.text).join('\n\n');
      }
    }
    if (how === 'done') state.status = state.result ? 'complete' : 'idle';
  });
  builder.addCase(turnActions.serverSynced, (state, action) => {
    const { status, hasFindings } = action.payload;
    if (status === 'review' && hasFindings && ['complete', 'error', 'idle'].includes(state.status)) state.status = 'review';
    if (status === 'idle' && state.status === 'review') state.status = state.result ? 'complete' : 'idle';
  });
} });
export const analysisActions = slice.actions;
export const analysisReducer = slice.reducer;
