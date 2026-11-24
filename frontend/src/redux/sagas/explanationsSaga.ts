/** "What the literature says" for the current ranking (evidence-plan §4; evidence-impl-plan §Audit). */
import { call, cancelled, delay, put, select, takeLatest } from 'redux-saga/effects';
import type { SagaIterator } from 'redux-saga';
import { ApiError, caseApi, type Explanations } from '../../shared/api';
import { analysisActions } from '../slices';
import type { RootState } from '..';

const RETRIES_ON_BUSY = 3;

function* fetchExplanations(): SagaIterator {
  const state: RootState = yield select();
  const version = state.analysis.result?.rankingVersion;
  const caseId = state.workspace.caseId;
  if (!version || !caseId) return;
  yield put(analysisActions.explanationsLoading());
  const controller = new AbortController();
  try {
    for (let attempt = 0; ; attempt++) {
      try {
        const explanations: Explanations = yield call(caseApi.explanations, caseId, controller.signal);
        const now: number | undefined = yield select((s: RootState) => s.analysis.result?.rankingVersion);
        // Items follow the ranking the server held; a re-rank while this was in flight makes it outdated.
        if (now === version) yield put(analysisActions.explanationsReceived({ rankingVersion: version, explanations }));
        return;
      } catch (failure) {
        if (failure instanceof ApiError && failure.code === 'not_deployed') { yield put(analysisActions.explanationsUnavailable()); return; }
        if (failure instanceof ApiError && failure.status === 409) {
          if (attempt < RETRIES_ON_BUSY) { yield delay(1000); continue; }
          yield put(analysisActions.explanationsIdle());
          return;
        }
        yield put(analysisActions.explanationsFailed());
        return;
      }
    }
  } finally {
    if (yield cancelled()) controller.abort();
  }
}

/** Only the request sent last counts: takeLatest cancels and aborts the older one. */
export function* watchExplanations(): SagaIterator {
  yield takeLatest(analysisActions.explanationsRequested.type, fetchExplanations);
}
