/** Streamed turns of a case (docs/frontend-plan.md §2.5, §3.4): analyze, continue, remap, chat. */
import { call, cancelled, put, select } from 'redux-saga/effects';
import type { SagaIterator } from 'redux-saga';
import { ApiError, caseApi, type CaseView, type Ranking, type SseEvent } from '../../shared/api';
import { emptyResult, toFinding, toResult } from '../../shared/api/adapters';
import { analysisActions, chatActions, phenotypeActions, turnActions, workspaceActions, type TurnOrigin } from '../slices';
import type { RootState } from '..';

type StartedBy = 'analyze' | 'continue' | 'remap' | 'chat';
interface Turn { requestId: string; origin: TurnOrigin; startedBy: StartedBy; text?: string }
interface Opened { it: AsyncIterator<SseEvent>; first: IteratorResult<SseEvent> }
const TERMINAL = new Set(['review', 'done', 'error']);

/** i18n key (and backend message) for a failed request (§3.4 "HTTP error before the stream"). */
export function errorKey(error: unknown): { error: string; errorMessage: string | null } {
  if (error instanceof ApiError) {
    if (error.code === 'case_busy') return { error: 'error.caseBusy', errorMessage: null };
    if (error.code === 'case_waiting_review') return { error: 'error.waitingReview', errorMessage: null };
    if (error.status === 422) return { error: 'error.invalid', errorMessage: error.message };
  }
  return { error: 'common.error', errorMessage: null };
}
export const isCaseGone = (error: unknown) => error instanceof ApiError && error.status === 404 && error.code === 'case_not_found';

/** Start reading a stream; HTTP errors (ApiError) surface here, before any event is handled. */
function* open(stream: AsyncIterable<SseEvent>): SagaIterator<Opened> {
  const it = stream[Symbol.asyncIterator]();
  const first: IteratorResult<SseEvent> = yield call([it, it.next]);
  return { it, first };
}

/** GET /cases/{id}: the server status decides what is allowed (§3.6). */
export function* syncStatus(caseId: string): SagaIterator {
  try {
    const view: CaseView = yield call(caseApi.getCase, caseId);
    const state: RootState = yield select();
    if (state.workspace.caseId !== caseId) return;
    if (view.status === 'running') yield put(analysisActions.errorShown({ error: 'error.caseBusy' }));
    yield put(turnActions.serverSynced({ status: view.status, hasFindings: Boolean(state.phenotype.analysisRun?.completed) }));
  } catch (error) {
    if (isCaseGone(error)) { yield put(workspaceActions.caseExpired()); return; }
    // Server unreachable: keep the last known server state, so a review that never reached the server can be retried.
    const state: RootState = yield select();
    if (state.workspace.caseId === caseId) yield put(turnActions.serverSynced({ status: state.workspace.serverReview ? 'review' : 'idle', hasFindings: Boolean(state.phenotype.analysisRun?.completed) }));
  }
}

function* resultMeta(requestId: string): SagaIterator<{ analysisId: string; description: string }> {
  const state: RootState = yield select();
  return { analysisId: state.phenotype.analysisRun?.id ?? requestId, description: state.phenotype.analysisRun?.description ?? state.workspace.description };
}

function* applyRanking(turn: Turn, ranking: Ranking): SagaIterator {
  const state: RootState = yield select();
  const meta = yield* resultMeta(turn.requestId);
  const result = toResult(ranking, state.phenotype.findings, meta);
  if (turn.origin === 'results') {
    yield put(analysisActions.suggestionsSucceeded({ requestId: turn.requestId, result }));
    yield put(analysisActions.stageChanged({ requestId: turn.requestId, stage: 'generating' }));
  } else {
    yield put(analysisActions.rankingReplaced({ result }));
  }
}

/** Dispatch every event of a turn; the turn is finished when the stream closes, not when `done` arrives (§3.4). */
function* runTurn(turn: Turn, opened: Opened): SagaIterator {
  const ref = { requestId: turn.requestId, origin: turn.origin };
  let ranked = false;
  let terminal: SseEvent | null = null;
  let next = opened.first;
  while (!next.done) {
    const event = next.value;
    switch (event.event) {
      case 'case': yield put(workspaceActions.caseCreated(event.data.case_id)); break;
      case 'step':
        yield put(turnActions.turnStep({ ...ref, step: event.data }));
        if (turn.origin === 'analysis' && event.data.key === 'choose_terms' && event.data.status === 'running') yield put(analysisActions.stageChanged({ requestId: turn.requestId, stage: 'mapping' }));
        break;
      case 'text': yield put(turnActions.turnText({ ...ref, messageId: event.data.message_id, delta: event.data.delta })); break;
      case 'answer_reset': yield put(turnActions.turnTextReset({ ...ref, messageId: event.data.message_id })); break;
      case 'artifact':
        if (event.data.kind === 'findings') yield put(phenotypeActions.findingsReplaced({ findings: event.data.data.map(toFinding) }));
        else { ranked = true; yield* applyRanking(turn, event.data.data); }
        break;
      default: if (TERMINAL.has(event.event)) terminal = event;
    }
    next = yield call([opened.it, opened.it.next]);
  }
  yield* finishTurn(turn, terminal, ranked);
}

function* finishTurn(turn: Turn, terminal: SseEvent | null, ranked: boolean): SagaIterator {
  const { requestId, origin, startedBy } = turn;
  const ref = { requestId, origin };
  if (terminal?.event === 'review') {
    const findings = terminal.data.findings.map(toFinding);
    if (startedBy === 'analyze') {
      yield put(phenotypeActions.analysisCompleted({ requestId, findings }));
    } else if (startedBy === 'chat') {
      // The server added the message to the description (T137): show the description it analysed.
      const text = terminal.data.description ?? turn.text ?? '';
      yield put(analysisActions.restartedForReview({ requestId }));
      yield put(workspaceActions.descriptionSynced(text));
      yield put(phenotypeActions.analysisStarted({ requestId, text }));
      yield put(phenotypeActions.analysisCompleted({ requestId, findings }));
    } else {
      yield put(phenotypeActions.findingsReplaced({ findings, keepLocalNotes: true }));
    }
    // A remap keeps the counts of its analysis; a new analysis brings its own (T132).
    const carryOver = startedBy === 'remap' ? undefined : terminal.data.carry_over ?? null;
    yield put(analysisActions.succeeded({ requestId, notice: findings.length ? null : 'noFindings', carryOver }));
    yield put(turnActions.turnEnded({ ...ref, how: 'review' }));
    return;
  }
  if (terminal?.event === 'done') {
    const { view, citations } = terminal.data;
    const findings = view.findings.map(toFinding);
    if (origin === 'results' && !ranked) {
      const meta = yield* resultMeta(requestId);
      const result = view.ranking ? toResult(view.ranking, findings, meta) : emptyResult(findings, meta, view.hpo_release);
      yield put(analysisActions.suggestionsSucceeded({ requestId, result }));
    }
    if (origin === 'chat') yield put(chatActions.succeeded({ requestId, citations }));
    if (origin === 'analysis' && startedBy === 'analyze') yield put(phenotypeActions.analysisCompleted({ requestId, findings }));
    yield put(turnActions.turnEnded({ ...ref, how: 'done' }));
    yield put(phenotypeActions.findingsReplaced({ findings }));
    if (view.analysis_flags?.split_failed) yield put(analysisActions.noticeSet({ requestId, notice: 'splitFailed' }));
    else if (origin === 'analysis' && startedBy === 'analyze') yield put(analysisActions.noticeSet({ requestId, notice: 'notAnalyzed' }));
    else if (findings.length) yield put(analysisActions.noticeCleared('noFindings'));
    if (ranked || (origin === 'results' && view.ranking)) yield put(analysisActions.explanationsRequested());
    return;
  }
  // `error` event, or a stream that closed without a final event.
  const code = terminal?.event === 'error' ? terminal.data.code : 'internal_error';
  const error = code === 'model_unavailable' ? 'error.modelUnavailable' : 'common.error';
  yield put(turnActions.turnEnded({ ...ref, how: 'error' }));
  yield* failTurn(turn, error, null);
}

/** Show a failed turn's error, then ask the server what the case allows now. */
function* failTurn({ requestId, origin }: Turn, error: string, errorMessage: string | null): SagaIterator {
  if (origin === 'chat') yield put(chatActions.failed({ requestId, error, errorMessage }));
  else {
    yield put(analysisActions.failed({ requestId, error, errorMessage }));
    yield put(phenotypeActions.analysisFailed(requestId));
  }
  const caseId: string | null = yield select((state: RootState) => state.workspace.caseId);
  if (caseId) yield* syncStatus(caseId);
}

/** A request that failed before or while streaming (HTTP status, network). */
function* turnFailed(turn: Turn, failure: unknown): SagaIterator {
  const { requestId, origin } = turn;
  yield put(turnActions.turnEnded({ requestId, origin, how: 'error' }));
  if (isCaseGone(failure)) {
    yield put(workspaceActions.caseExpired());
    if (origin === 'chat') yield put(chatActions.failed({ requestId, error: 'notice.caseExpired' }));
    else yield put(phenotypeActions.analysisFailed(requestId));
    return;
  }
  const { error, errorMessage } = errorKey(failure);
  yield* failTurn(turn, error, errorMessage);
}

/** Run a turn; a cancelled turn aborts its stream (the server frees the case) and leaves no running row behind. */
function* guarded(turn: Turn, body: (signal: AbortSignal) => SagaIterator<Opened>): SagaIterator {
  const controller = new AbortController();
  try {
    const opened: Opened = yield* body(controller.signal);
    yield* runTurn(turn, opened);
  } catch (failure) {
    yield* turnFailed(turn, failure);
  } finally {
    controller.abort();
    if (yield cancelled()) yield put(turnActions.turnEnded({ requestId: turn.requestId, origin: turn.origin, how: 'error' }));
  }
}

/** P3: leave a pending review (POST /review cancel, events read to the end and not shown). */
function* cancelReview(caseId: string, signal: AbortSignal): SagaIterator {
  for (let attempt = 0; attempt < 2; attempt++) {
    const it = caseApi.review(caseId, { action: 'cancel' }, signal)[Symbol.asyncIterator]();
    let last: SseEvent | null = null;
    while (true) {
      const next: IteratorResult<SseEvent> = yield call([it, it.next]);
      if (next.done) break;
      if (TERMINAL.has(next.value.event)) last = next.value;
    }
    if (last?.event === 'done') return;
    if (last?.event !== 'review') break;
  }
  throw new Error('review cancel failed');
}

export function* analyze(action: ReturnType<typeof analysisActions.requested>): SagaIterator {
  const { requestId, text } = action.payload;
  yield put(phenotypeActions.analysisStarted({ requestId, text }));
  const caseId: string | null = yield select((state: RootState) => state.workspace.caseId);
  yield* guarded({ requestId, origin: 'analysis', startedBy: 'analyze', text }, function* (signal): SagaIterator<Opened> {
    if (!caseId) return yield* open(caseApi.createCase(text, signal));
    try {
      return yield* open(caseApi.sendMessage(caseId, text, signal, true));
    } catch (failure) {
      if (!(failure instanceof ApiError && failure.code === 'case_waiting_review')) throw failure;
      yield* cancelReview(caseId, signal);
      return yield* open(caseApi.sendMessage(caseId, text, signal, true));
    }
  });
}

export function* continueReview(action: ReturnType<typeof analysisActions.suggestionsRequested>): SagaIterator {
  const { requestId } = action.payload;
  const state: RootState = yield select();
  const run = state.phenotype.analysisRun;
  const caseId = state.workspace.caseId;
  if (state.analysis.requestId !== requestId || state.analysis.status !== 'retrieving' || !run?.completed || run.description !== state.workspace.description || !caseId) {
    yield put(analysisActions.suggestionsDiscarded(requestId));
    return;
  }
  // Edits and notes are read from the store, never from the action (canonical data).
  const edits = Object.values(state.phenotype.edits);
  const notes = Object.fromEntries(state.phenotype.findings.flatMap(f => (f.note?.text ? [[f.id, f.note.text]] : [])));
  yield* guarded({ requestId, origin: 'results', startedBy: 'continue' }, function* (signal): SagaIterator<Opened> {
    return yield* open(caseApi.review(caseId, { action: 'continue', edits, notes }, signal));
  });
}

export function* remap(action: ReturnType<typeof phenotypeActions.remapRequested>): SagaIterator {
  const { requestId, id, phrase, assertion } = action.payload;
  yield put(analysisActions.remapStarted({ requestId }));
  const state: RootState = yield select();
  const caseId = state.workspace.caseId;
  if (state.analysis.requestId !== requestId || state.analysis.status !== 'mapping' || !caseId) return;
  yield* guarded({ requestId, origin: 'analysis', startedBy: 'remap' }, function* (signal): SagaIterator<Opened> {
    return yield* open(caseApi.review(caseId, { action: 'remap', finding_id: id, phrase: phrase.trim(), ...(assertion ? { assertion } : {}) }, signal));
  });
}

export function* chat(action: ReturnType<typeof chatActions.requested>): SagaIterator {
  const { requestId, text } = action.payload;
  const caseId: string | null = yield select((state: RootState) => state.workspace.caseId);
  if (!caseId) { yield put(chatActions.failed({ requestId, error: 'common.error' })); return; }
  yield* guarded({ requestId, origin: 'chat', startedBy: 'chat', text }, function* (signal): SagaIterator<Opened> {
    return yield* open(caseApi.sendMessage(caseId, text, signal));
  });
}
