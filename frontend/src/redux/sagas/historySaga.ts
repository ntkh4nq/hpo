/** Saved cases (docs/followup-plan.md §A.4, T116): the list, opening, renaming and deleting live on the server. */
import { call, put, select, takeEvery, takeLatest } from 'redux-saga/effects';
import type { SagaIterator } from 'redux-saga';
import { ApiError, caseApi, type CaseSummary, type CaseView } from '../../shared/api';
import { snapshotFromView } from '../../shared/api/caseFromView';
import { analysisActions, historyActions, turnActions, workspaceActions } from '../slices';
import { isCaseGone } from './turnSaga';
import { normalizeCaseTitle } from '../../shared/cases';
import type { RootState } from '..';

function* loadCases(): SagaIterator {
  try {
    const list: CaseSummary[] = yield call(caseApi.listCases);
    yield put(historyActions.loaded(list.map(c => ({ id: c.case_id, title: c.title, updatedAt: c.updated_at }))));
    const state: RootState = yield select();
    const active = list.find(c => c.case_id === state.workspace.caseId);
    // A new case gets its default title from the server; a title the clinician typed is never overwritten.
    if (active && !state.workspace.titleCustomized && active.title !== state.workspace.title) yield put(workspaceActions.titleSynced(active.title));
  } catch { /* list unavailable: keep the last one */ }
}

function* openCase(action: ReturnType<typeof historyActions.openRequested>): SagaIterator {
  const state: RootState = yield select();
  if (state.history.activeId === action.payload) return;
  try {
    const view: CaseView = yield call(caseApi.getCase, action.payload);
    yield put(workspaceActions.caseOpened(snapshotFromView(view)));
    if (view.status === 'running') yield put(analysisActions.errorShown({ error: 'error.caseBusy' }));
    // Served from the case's explanations cache when unchanged: no model request.
    if (view.ranking) yield put(analysisActions.explanationsRequested());
  } catch (error) {
    yield put(analysisActions.errorShown({ error: isCaseGone(error) ? 'notice.caseExpired' : 'common.error' }));
    yield* loadCases();
  }
}

function* renameCase(action: ReturnType<typeof historyActions.renamed>): SagaIterator {
  const title = normalizeCaseTitle(action.payload.title);
  if (!title) return;
  const state: RootState = yield select();
  if (state.workspace.caseId === action.payload.id) yield put(workspaceActions.titleChanged(title));
  else yield* sendTitle(action.payload.id, title);
}

function* sendTitle(caseId: string, title: string): SagaIterator {
  try { yield call(caseApi.renameCase, caseId, title); } catch { /* the reload shows the server's title */ }
  yield* loadCases();
}

/** The header's title field: sent at once for a saved case, after the first message for a new one. */
function* titleChanged(): SagaIterator {
  const { caseId, title }: RootState['workspace'] = yield select((s: RootState) => s.workspace);
  if (caseId && title) yield* sendTitle(caseId, title);
}

function* caseCreated(): SagaIterator {
  const { caseId, title, titleCustomized }: RootState['workspace'] = yield select((s: RootState) => s.workspace);
  if (caseId && titleCustomized && title) yield* sendTitle(caseId, title);
  else yield* loadCases();
}

function* removeCase(action: ReturnType<typeof historyActions.removed>): SagaIterator {
  const caseId: string | null = yield select((s: RootState) => s.workspace.caseId);
  if (caseId === action.payload) yield put(workspaceActions.reset());
  try {
    yield call(caseApi.deleteCase, action.payload);
  } catch (error) {
    if (error instanceof ApiError && error.code === 'case_busy') yield put(analysisActions.errorShown({ error: 'error.caseBusy' }));
  }
  yield* loadCases();
}

export function* watchHistory(): SagaIterator {
  yield takeLatest([historyActions.loadRequested.type, turnActions.turnEnded.type], loadCases);
  yield takeLatest(historyActions.openRequested.type, openCase);
  yield takeEvery(historyActions.renamed.type, renameCase);
  yield takeLatest(workspaceActions.titleChanged.type, titleChanged);
  yield takeEvery(workspaceActions.caseCreated.type, caseCreated);
  yield takeEvery(historyActions.removed.type, removeCase);
  yield* loadCases();
}
