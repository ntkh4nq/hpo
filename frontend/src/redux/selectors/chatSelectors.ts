import { createSelector } from '@reduxjs/toolkit';
import type { RootState } from '..';
import { selectBusy } from './analysisSelectors';

export const selectChat = (state: RootState) => state.chat;
/** Follow-up composer (P2): a server case exists, no turn runs, the server does not wait for review. */
export const selectCanChat = createSelector([(state: RootState) => state.workspace, selectBusy, selectChat], (workspace, busy, chat) =>
  Boolean(workspace.caseId) && !busy && !chat.pending && !workspace.serverReview);
