import type { RootState } from '..';

export const selectHistory = (state: RootState) => state.history;
export const selectHistoryEntries = (state: RootState) => state.history.entries;
