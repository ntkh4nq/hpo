import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type { CaseEntry } from '../../shared/types';
import { workspaceActions } from './workspaceSlice';
import { normalizeCaseTitle } from '../../shared/cases';

/** The saved cases (server list, T116) and the one open in the workspace. */
const initialState = { entries: [] as CaseEntry[], activeId: null as string | null };
const slice = createSlice({
  name: 'history',
  initialState,
  reducers: {
    loadRequested() {},
    loaded(state, action: PayloadAction<CaseEntry[]>) { state.entries = action.payload; },
    openRequested(_state, _action: PayloadAction<string>) {},
    /** Optimistic; the saga sends PATCH /cases/{id} and reloads the list. */
    renamed(state, action: PayloadAction<{ id: string; title: string }>) {
      const entry = state.entries.find(entry => entry.id === action.payload.id);
      const title = normalizeCaseTitle(action.payload.title);
      if (entry && title) entry.title = title;
    },
    /** Optimistic; the saga sends DELETE /cases/{id}. */
    removed(state, action: PayloadAction<string>) {
      state.entries = state.entries.filter(entry => entry.id !== action.payload);
      if (state.activeId === action.payload) state.activeId = null;
    },
  },
  extraReducers: builder => {
    builder.addCase(workspaceActions.reset, state => { state.activeId = null; });
    builder.addCase(workspaceActions.caseOpened, (state, action) => { state.activeId = action.payload.id; });
    builder.addCase(workspaceActions.caseCreated, (state, action) => { state.activeId = action.payload; });
  },
});

export const historyActions = slice.actions;
export const historyReducer = slice.reducer;
