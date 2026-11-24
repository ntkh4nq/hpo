import { all, cancel, fork, take, takeLatest, takeLeading } from 'redux-saga/effects';
import type { SagaIterator, Task } from 'redux-saga';
import { analysisActions, chatActions, phenotypeActions, workspaceActions } from '../slices';
import { analyze, chat, continueReview, remap } from './turnSaga';
import { watchFindings } from './findingsSaga';
import { watchExplanations } from './explanationsSaga';
import { watchEvidence } from './evidenceSaga';
import { watchHistory } from './historySaga';

/** Turns tied to the description: a source edit, a new case or a case switch cancels them. */
function* watchWorkspace(): SagaIterator {
  yield all([
    takeLatest(analysisActions.requested.type, analyze),
    // takeLeading: a double click must not abort a valid turn.
    takeLeading(analysisActions.suggestionsRequested.type, continueReview),
    takeLeading(phenotypeActions.remapRequested.type, remap),
  ]);
}
/** Work tied to the server case: typing in the description (still enabled during a chat turn) must not cancel it. */
function* watchCase(): SagaIterator {
  yield all([
    takeLeading(chatActions.requested.type, chat),
    fork(watchFindings),
    fork(watchExplanations),
    fork(watchEvidence),
  ]);
}
function* restartOn(watcher: () => SagaIterator, actions: string[]): SagaIterator {
  while (true) {
    const task: Task = yield fork(watcher);
    yield take(actions);
    yield cancel(task);
  }
}
export function* rootSaga(): SagaIterator {
  yield fork(watchHistory);
  yield fork(restartOn, watchWorkspace, [workspaceActions.reset.type, workspaceActions.descriptionChanged.type, workspaceActions.sampleLoaded.type, workspaceActions.caseOpened.type]);
  yield fork(restartOn, watchCase, [workspaceActions.reset.type, workspaceActions.caseOpened.type]);
}
