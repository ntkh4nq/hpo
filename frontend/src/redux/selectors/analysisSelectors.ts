import { createSelector } from '@reduxjs/toolkit';
import type { RootState } from '..';

export const selectAnalysis = (state: RootState) => state.analysis;
export const selectBusy = createSelector([selectAnalysis], analysis => ['extracting', 'mapping', 'retrieving', 'generating'].includes(analysis.status));
