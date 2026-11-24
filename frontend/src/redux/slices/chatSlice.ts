import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type { ChatMessage, ChatRequest, Citation } from '../../shared/types';
import { workspaceActions } from './workspaceSlice';
import { turnActions } from './turnActions';
import { applyStep, applyText, endTurn, resetText, turnText } from './turnItems';

const initialState = { messages: [] as ChatMessage[], pending: false, requestId: null as string | null, error: null as string | null, errorMessage: null as string | null };
type State = typeof initialState;
const answerOf = (state: State, requestId: string | null) =>
  requestId && state.requestId === requestId ? state.messages.find(m => m.id === `${requestId}-a`) : undefined;

const slice = createSlice({ name: 'chat', initialState, reducers: {
  requested(state, action: PayloadAction<ChatRequest>) {
    if (state.pending) return;
    const { requestId, text } = action.payload;
    Object.assign(state, { pending: true, error: null, errorMessage: null, requestId });
    state.messages.push({ id: requestId, role: 'user', text, citations: [], progress: [] });
    state.messages.push({ id: `${requestId}-a`, role: 'assistant', text: '', citations: [], progress: [] });
  },
  succeeded(state, action: PayloadAction<{ requestId: string; citations: Citation[] }>) {
    const answer = answerOf(state, action.payload.requestId);
    if (!answer) return;
    answer.text = turnText(answer.progress);
    answer.citations = action.payload.citations;
  },
  failed(state, action: PayloadAction<{ requestId: string; error: string; errorMessage?: string | null }>) {
    if (state.requestId !== action.payload.requestId) return;
    const answer = answerOf(state, action.payload.requestId);
    if (answer && answer.progress.length === 0) state.messages = state.messages.filter(m => m !== answer);
    Object.assign(state, { pending: false, requestId: null, error: action.payload.error, errorMessage: action.payload.errorMessage ?? null });
  },
}, extraReducers: builder => {
  builder.addCase(workspaceActions.reset, () => initialState);
  builder.addCase(workspaceActions.caseOpened, (_state, action) => ({ ...initialState, messages: action.payload.chat }));
  builder.addCase(turnActions.turnStep, (state, action) => {
    const answer = action.payload.origin === 'chat' ? answerOf(state, action.payload.requestId) : undefined;
    if (answer) answer.progress = applyStep(answer.progress, action.payload.step);
  });
  builder.addCase(turnActions.turnText, (state, action) => {
    const answer = action.payload.origin === 'chat' ? answerOf(state, action.payload.requestId) : undefined;
    if (answer) answer.progress = applyText(answer.progress, action.payload.messageId, action.payload.delta);
  });
  builder.addCase(turnActions.turnTextReset, (state, action) => {
    const answer = action.payload.origin === 'chat' ? answerOf(state, action.payload.requestId) : undefined;
    if (answer) answer.progress = resetText(answer.progress, action.payload.messageId);
  });
  builder.addCase(turnActions.turnEnded, (state, action) => {
    if (action.payload.origin !== 'chat') return;
    const answer = answerOf(state, action.payload.requestId);
    if (answer) answer.progress = endTurn(answer.progress, action.payload.how);
    if (state.requestId === action.payload.requestId) state.pending = false;
  });
} });
export const chatActions = slice.actions;
export const chatReducer = slice.reducer;
