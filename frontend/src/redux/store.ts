import { configureStore } from '@reduxjs/toolkit';
import createSagaMiddleware from 'redux-saga';
import { analysisReducer, chatReducer, phenotypeReducer, workspaceReducer, historyReducer } from './slices';
import { rootSaga } from './sagas';
export function createAppStore() {
  const sagaMiddleware = createSagaMiddleware();
  const store = configureStore({ reducer: {analysis:analysisReducer, phenotype:phenotypeReducer, chat:chatReducer, workspace:workspaceReducer, history:historyReducer}, middleware: getDefault => getDefault({thunk:false}).concat(sagaMiddleware), devTools: import.meta.env?.DEV ?? false });
  const sagaTask = sagaMiddleware.run(rootSaga);
  return { store, sagaTask };
}
const app = createAppStore();
export const store = app.store;
export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
