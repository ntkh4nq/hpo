import { createSelector } from '@reduxjs/toolkit';
import type { RootState } from '..';
import type { Evidence } from '../../shared/types';
import { selectAnalysis } from './analysisSelectors';

export const selectWorkspace = (state: RootState) => state.workspace;
/** The evidence dialog's content: from the current result, or a passage fetched for a chat citation. */
export const selectSelectedEvidence = createSelector([selectWorkspace, selectAnalysis], (workspace, analysis): { evidence: Evidence | null; loading: boolean; gone: boolean; failed: boolean } | null => {
  const id = workspace.selectedEvidenceId;
  if (!id) return null;
  const fromResult = analysis.result?.evidence.find(e => e.id === id);
  if (fromResult) return { evidence: fromResult, loading: false, gone: false, failed: false };
  const cached = analysis.evidenceCache[id];
  if (cached && 'gone' in cached) return { evidence: null, loading: false, gone: true, failed: false };
  if (cached) return { evidence: cached, loading: false, gone: false, failed: false };
  const failed = analysis.evidenceFailedId === id || !id.startsWith('CHUNK:');
  return { evidence: null, loading: !failed, gone: false, failed };
});
