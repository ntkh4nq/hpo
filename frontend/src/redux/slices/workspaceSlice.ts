import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import { normalizeCaseTitle } from '../../shared/cases';
import type { CaseSnapshot } from '../../shared/types';
import { turnActions } from './turnActions';

const initialState = {
  description: '', title: '', titleCustomized: false, caseKey: 0,
  selectedEvidenceId: null as string | null,
  /** Suggestion whose details dialog is open (T114): opened from the list, the summary or a chat answer. */
  openCandidateId: null as string | null,
  /** Server case (T116); set by the SSE `case` event or by opening a saved case. */
  caseId: null as string | null,
  /** The server waits at the review step (POST /messages and /findings answer 409 meanwhile). Typing does not change it. */
  serverReview: false,
};
const workspaceSlice = createSlice({ name: 'workspace', initialState, reducers: {
  reset: state => ({ ...initialState, caseKey: state.caseKey + 1 }),
  descriptionChanged(state, action: PayloadAction<string>) {
    state.description = action.payload;
    state.selectedEvidenceId = null;
  },
  /** A chat message read as a new description: show it without cancelling the running turn. */
  descriptionSynced(state, action: PayloadAction<string>) {
    state.description = action.payload;
  },
  sampleLoaded(state, action: PayloadAction<{ text: string }>) {
    state.description = action.payload.text;
    state.selectedEvidenceId = null;
  },
  titleChanged(state, action: PayloadAction<string>) {
    const title = normalizeCaseTitle(action.payload);
    if (title) { state.title = title; state.titleCustomized = true; }
  },
  /** The server's title of the open case (default title after the first message, or a rename elsewhere). */
  titleSynced(state, action: PayloadAction<string>) { state.title = action.payload; },
  caseOpened(state, action: PayloadAction<CaseSnapshot>) {
    const entry = action.payload;
    return { ...initialState, caseKey: state.caseKey + 1, description: entry.description, title: entry.title, titleCustomized: true, caseId: entry.id, serverReview: entry.reviewPending };
  },
  caseCreated(state, action: PayloadAction<string>) { state.caseId = action.payload; },
  caseExpired(state) { state.caseId = null; state.serverReview = false; },
  evidenceSelected(state, action: PayloadAction<string | null>) { state.selectedEvidenceId = action.payload; },
  candidateOpened(state, action: PayloadAction<string | null>) { state.openCandidateId = action.payload; },
}, extraReducers: builder => {
  builder.addCase(turnActions.turnEnded, (state, action) => {
    if (action.payload.how === 'review') state.serverReview = true;
    if (action.payload.how === 'done') state.serverReview = false;
  });
  builder.addCase(turnActions.serverSynced, (state, action) => {
    if (action.payload.status !== 'running') state.serverReview = action.payload.status === 'review';
  });
} });
export const workspaceActions = workspaceSlice.actions;
export const workspaceReducer = workspaceSlice.reducer;
