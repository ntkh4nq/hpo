import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type { AnalysisRun, ArchivedAnalysisNotes, Assertion, Finding, FindingEdit } from '../../shared/types';
import { workspaceActions } from './workspaceSlice';
import { editsFromFindings } from '../../shared/api/caseFromView';

const initialState = {
  hasAnalyzed: false, findings: [] as Finding[], revision: 0, pendingRequestId: null as string | null,
  analysisRun: null as AnalysisRun | null, notesArchive: [] as ArchivedAnalysisNotes[],
  /** finding_id -> fields the clinician changed (T91); only values that differ from the machine reading. */
  edits: {} as Record<string, FindingEdit>,
};
type State = typeof initialState;

function archiveNotes(state: State) {
  if (!state.analysisRun) return;
  const findings = state.findings.filter(finding => finding.note?.text);
  if (!findings.length) return;
  const snapshot = { analysisId: state.analysisRun.id, description: state.analysisRun.description, findings };
  const index = state.notesArchive.findIndex(entry => entry.analysisId === snapshot.analysisId);
  if (index < 0) state.notesArchive.push(snapshot);
  else state.notesArchive[index] = snapshot;
}
function invalidateSource(state: State) {
  archiveNotes(state);
  state.findings = [];
  state.edits = {};
  state.pendingRequestId = null;
  if (state.analysisRun) state.analysisRun.completed = false;
  state.revision++;
}
type EditFields = Omit<FindingEdit, 'finding_id'>;
const SIMPLE_FIELDS = ['assertion', 'assessment', 'subject'] as const;

const slice = createSlice({ name: 'phenotype', initialState, reducers: {
  analysisStarted(state, action: PayloadAction<{ requestId: string; text: string }>) {
    archiveNotes(state);
    state.findings = [];
    state.edits = {};
    state.pendingRequestId = action.payload.requestId;
    state.analysisRun = { id: action.payload.requestId, description: action.payload.text, completed: false };
    state.revision++;
  },
  analysisCompleted(state, action: PayloadAction<{ requestId: string; findings: Finding[] }>) {
    if (state.analysisRun?.id !== action.payload.requestId || state.pendingRequestId !== action.payload.requestId) return;
    state.pendingRequestId = null;
    // The server re-applied the clinician's corrections that still match a finding of the new text (T132): keep them,
    // so "Tiếp tục" sends them back, and drop those notes from the local archive of the previous analysis.
    state.findings = action.payload.findings;
    state.edits = editsFromFindings(action.payload.findings);
    const carried = new Set(action.payload.findings.flatMap(f => (f.note?.text ? [`${f.sourceText}\n${f.note.text}`] : [])));
    if (carried.size) {
      state.notesArchive = state.notesArchive
        .map(entry => ({ ...entry, findings: entry.findings.filter(f => !carried.has(`${f.sourceText}\n${f.note?.text ?? ''}`)) }))
        .filter(entry => entry.findings.length);
    }
    state.analysisRun.completed = true;
    state.hasAnalyzed = true;
    state.revision++;
  },
  analysisFailed(state, action: PayloadAction<string>) {
    if (state.pendingRequestId === action.payload) state.pendingRequestId = null;
  },
  noteSaved(state, action: PayloadAction<{ analysisId: string; id: string; text: string }>) {
    if (!state.analysisRun?.completed || state.analysisRun.id !== action.payload.analysisId) return;
    const finding = state.findings.find(f => f.id === action.payload.id);
    const text = action.payload.text.trim();
    if (!finding || (finding.note?.text ?? '') === text) return;
    if (text) finding.note = { text };
    else delete finding.note;
    state.revision++;
  },
  /** Card edit (T91 b–d). Keeps only fields that differ from the machine value; hpo_id only within the candidates (#8). */
  editSaved(state, action: PayloadAction<{ analysisId: string; id: string; edit: EditFields }>) {
    if (!state.analysisRun?.completed || state.analysisRun.id !== action.payload.analysisId) return;
    const finding = state.findings.find(f => f.id === action.payload.id);
    if (!finding) return;
    const before = JSON.stringify(state.edits[finding.id] ?? null);
    const next: FindingEdit = { ...state.edits[finding.id], finding_id: finding.id };
    const { edit } = action.payload;
    for (const field of SIMPLE_FIELDS) {
      const value = edit[field];
      if (value === undefined) continue;
      const machine = (finding.machine[field] as string | undefined) ?? finding[field];
      if (value === machine) delete next[field];
      else Object.assign(next, { [field]: value });
    }
    if (edit.excluded !== undefined) {
      if (edit.excluded) next.excluded = true;
      else delete next.excluded;
    }
    if (edit.hpo_id !== undefined && finding.candidates.some(c => c.id === edit.hpo_id)) {
      const shownTerm = next.hpo_id ?? finding.term?.id;
      if (edit.hpo_id !== shownTerm) next.hpo_id = edit.hpo_id;
    }
    if (Object.keys(next).length > 1) state.edits[finding.id] = next;
    else delete state.edits[finding.id];
    if (JSON.stringify(state.edits[finding.id] ?? null) !== before) state.revision++;
  },
  /** Findings from the server (artifact, done.view, POST /findings, review after remap). */
  findingsReplaced(state, action: PayloadAction<{ findings: Finding[]; keepLocalNotes?: boolean }>) {
    if (!state.analysisRun) return;
    // Server notes win, unless a note typed meanwhile is newer than the response.
    const local = new Map(state.findings.map(f => [f.id, f.note]));
    state.findings = action.payload.keepLocalNotes
      ? action.payload.findings.map(({ note: _server, ...finding }) => {
        const localNote = local.get(finding.id);
        return localNote ? { ...finding, note: localNote } : finding;
      })
      : action.payload.findings;
    state.revision++;
  },
  /** `assertion`: the clinician's conclusion sent with the new wording (a measurement read as abnormal, T118). */
  remapRequested(state, action: PayloadAction<{ analysisId: string; requestId: string; id: string; phrase: string; assertion?: Assertion }>) {
    const edit = state.edits[action.payload.id];
    if (!edit?.hpo_id) return;
    delete edit.hpo_id;
    if (Object.keys(edit).length === 1) delete state.edits[action.payload.id];
  },
}, extraReducers: builder => {
  builder.addCase(workspaceActions.reset, state => ({ ...initialState, revision: state.revision + 1 }));
  builder.addCase(workspaceActions.descriptionChanged, invalidateSource);
  builder.addCase(workspaceActions.sampleLoaded, invalidateSource);
  builder.addCase(workspaceActions.caseOpened, (state, action) => ({
    ...initialState, findings: action.payload.findings, revision: state.revision + 1,
    analysisRun: action.payload.analysisRun ?? (action.payload.findings.length || action.payload.result ? { id: `legacy-${action.payload.id}`, description: action.payload.description, completed: true } : null),
    notesArchive: action.payload.notesArchive,
    hasAnalyzed: action.payload.hasAnalyzed,
    edits: action.payload.edits,
  }));
} });
export const phenotypeActions = slice.actions;
export const phenotypeReducer = slice.reducer;
