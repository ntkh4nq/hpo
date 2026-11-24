/** Actions of a streamed turn, shared by several slices (kept apart to avoid import cycles between slices). */
import { createAction } from '@reduxjs/toolkit';
import type { Step } from '../../shared/types';

/** Which UI area a turn writes to (docs/frontend-plan.md §3.1). */
export type TurnOrigin = 'analysis' | 'results' | 'chat';
export type TurnEnd = 'done' | 'review' | 'error';
interface TurnRef { requestId: string; origin: TurnOrigin }

export const turnActions = {
  turnStep: createAction<TurnRef & { step: Step }>('turn/step'),
  turnText: createAction<TurnRef & { messageId: string; delta: string }>('turn/text'),
  turnTextReset: createAction<TurnRef & { messageId: string }>('turn/textReset'),
  turnEnded: createAction<TurnRef & { how: TurnEnd }>('turn/ended'),
  /** Result of GET /cases/{id} (§3.6); `hasFindings`: the UI holds the findings of a pending review. */
  serverSynced: createAction<{ status: 'idle' | 'running' | 'review'; hasFindings: boolean }>('turn/serverSynced'),
};
