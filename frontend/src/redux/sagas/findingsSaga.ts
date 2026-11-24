/** Card edits and notes after a ranking: POST /findings at once with the full set, re-ranked in code (P4, T91). */
import { actionChannel, call, cancelled, put, select, take } from 'redux-saga/effects';
import { buffers } from 'redux-saga';
import type { SagaIterator } from 'redux-saga';
import { caseApi, type CaseView } from '../../shared/api';
import { toFinding, toResult } from '../../shared/api/adapters';
import { analysisActions, phenotypeActions, workspaceActions } from '../slices';
import type { RootState } from '..';
import { errorKey, isCaseGone } from './turnSaga';

function* sendFindings(): SagaIterator {
  const state: RootState = yield select();
  const { analysis, chat, workspace, phenotype } = state;
  // Any running turn would answer 409 case_busy; the controls are disabled then anyway (§3.6).
  if (!analysis.result || analysis.status !== 'complete' || chat.pending || workspace.serverReview || !workspace.caseId) return;
  const notes = Object.fromEntries(phenotype.findings.flatMap(f => (f.note?.text ? [[f.id, f.note.text]] : [])));
  const controller = new AbortController();
  try {
    const view: CaseView = yield call(caseApi.updateFindings, workspace.caseId, Object.values(phenotype.edits), notes, controller.signal);
    // A note typed while this request ran is newer than the response; the queued request will send it.
    const revisionNow: number = yield select((s: RootState) => s.phenotype.revision);
    yield put(phenotypeActions.findingsReplaced({ findings: view.findings.map(toFinding), keepLocalNotes: revisionNow !== phenotype.revision }));
    if (!view.ranking) return;
    const after: RootState = yield select();
    const before = after.analysis.result?.rankingVersion;
    const run = after.phenotype.analysisRun;
    yield put(analysisActions.rankingReplaced({ result: toResult(view.ranking, after.phenotype.findings, { analysisId: run?.id ?? '', description: run?.description ?? '' }) }));
    const version: number | undefined = yield select((s: RootState) => s.analysis.result?.rankingVersion);
    // Unchanged suggestions keep their explanations (a note-only update does not re-rank, graph.py: apply_findings_action).
    if (version !== before) yield put(analysisActions.explanationsRequested());
  } catch (failure) {
    if (isCaseGone(failure)) yield put(workspaceActions.caseExpired());
    else yield put(analysisActions.errorShown(errorKey(failure)));
  } finally {
    if (yield cancelled()) controller.abort();
  }
}

/** One request at a time; a sliding buffer of 1 always sends the latest full set. */
export function* watchFindings(): SagaIterator {
  const channel = yield actionChannel([phenotypeActions.editSaved.type, phenotypeActions.noteSaved.type], buffers.sliding(1));
  while (true) {
    yield take(channel);
    yield call(sendFindings);
  }
}
