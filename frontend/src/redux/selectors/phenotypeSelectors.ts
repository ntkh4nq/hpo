import { createSelector } from '@reduxjs/toolkit';
import type { RootState } from '..';
import { selectAnalysis, selectBusy } from './analysisSelectors';
import { selectWorkspace } from './workspaceSelectors';
import { selectChat } from './chatSelectors';

export const selectFindings = (state: RootState) => state.phenotype.findings;
export const selectAnalysisRun = (state: RootState) => state.phenotype.analysisRun;
export const selectNotesArchive = (state: RootState) => state.phenotype.notesArchive;
/** "Tiếp tục" → POST /review, valid only while the server waits for review. */
export const selectCanRetrieve = createSelector([selectAnalysisRun, selectBusy, selectWorkspace, selectAnalysis], (run, busy, workspace, analysis) =>
  Boolean(!busy && run?.completed && run.description === workspace.description && analysis.status === 'review'));
/** Card edits and notes (§3.6): during review, or after a ranking while no turn runs and the server is idle. */
export const selectCanEdit = createSelector([selectBusy, selectChat, selectAnalysis, selectWorkspace], (busy, chat, analysis, workspace) =>
  !busy && !chat.pending && (analysis.status === 'review' || (analysis.status === 'complete' && !workspace.serverReview)));
export const selectEditsFor = (state: RootState, id: string) => state.phenotype.edits[id];
export const selectEdits = (state: RootState) => state.phenotype.edits;

export const selectHasAnalyzed = (state: RootState) => state.phenotype.hasAnalyzed;
