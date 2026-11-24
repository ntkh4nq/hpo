/** Passages cited in chat answers (CHUNK:n), fetched when the doctor opens one (evidence-plan §5). */
import { call, cancelled, put, select, takeLatest } from 'redux-saga/effects';
import type { SagaIterator } from 'redux-saga';
import { ApiError, caseApi, type Passage } from '../../shared/api';
import { toEvidence } from '../../shared/api/adapters';
import { analysisActions, workspaceActions } from '../slices';
import type { RootState } from '..';

function* fetchPassage(action: ReturnType<typeof workspaceActions.evidenceSelected>): SagaIterator {
  const id = action.payload;
  if (!id?.startsWith('CHUNK:')) return;
  const state: RootState = yield select();
  if (state.analysis.result?.evidence.some(e => e.id === id) || id in state.analysis.evidenceCache) return;
  yield put(analysisActions.evidenceFailed(null));
  const controller = new AbortController();
  try {
    const passage: Passage = yield call(caseApi.passage, id.slice('CHUNK:'.length), controller.signal);
    yield put(analysisActions.evidenceCached({ id, evidence: toEvidence(passage) }));
  } catch (failure) {
    if (failure instanceof ApiError && failure.code === 'passage_not_found') yield put(analysisActions.evidenceCached({ id, evidence: { gone: true } }));
    else yield put(analysisActions.evidenceFailed(id));
  } finally {
    if (yield cancelled()) controller.abort();
  }
}

export function* watchEvidence(): SagaIterator {
  yield takeLatest(workspaceActions.evidenceSelected.type, fetchPassage);
}
