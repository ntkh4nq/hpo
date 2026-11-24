import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAppDispatch, useAppSelector } from '../../redux';
import { historyActions } from '../../redux/slices';
import { selectHistory } from '../../redux/selectors';
import { EditableTitle, EmptyState, Icon } from '../../shared/ui';

/** Saved cases (server list, T116). Deleting asks once inside the row; a deleted case cannot be restored. */
export function HistoryPanel({ onOpenCase }: { onOpenCase: () => void }) {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const history = useAppSelector(selectHistory);
  const [confirming, setConfirming] = useState<string | null>(null);
  const entries = useMemo(() => [...history.entries].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)), [history.entries]);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const yesterday = new Date(today); yesterday.setDate(yesterday.getDate() - 1);
  const group = (date: string) => new Date(date) >= today ? 'today' : new Date(date) >= yesterday ? 'yesterday' : 'earlier';

  return <section className="history-panel history-panel--conversations" aria-labelledby="history-title">
    <div className="conversation-scroll"><h2 id="history-title">{t('history.conversations')}</h2>
    {entries.length === 0 ? <EmptyState title={t('history.empty')} hint={t('history.emptyHint')}/> : <>
      <nav className="conversation-list" aria-label={t('history.conversations')}>
        {entries.map((entry, index) => <div key={entry.id}>
          {(index === 0 || group(entries[index - 1].updatedAt) !== group(entry.updatedAt)) && <h3 className="history-date-group">{t(`history.${group(entry.updatedAt)}`)}</h3>}
          {confirming === entry.id
            ? <div className="conversation-row conversation-row--confirm" role="group" aria-label={t('history.confirmDelete')}>
                <span className="conversation-confirm__text">{t('history.confirmDelete')}</span>
                <button type="button" className="conversation-confirm__yes" autoFocus onClick={() => { setConfirming(null); dispatch(historyActions.removed(entry.id)); }}>{t('history.deleteYes')}</button>
                <button type="button" className="conversation-confirm__no" onClick={() => setConfirming(null)} onKeyDown={event => { if (event.key === 'Escape') setConfirming(null); }}>{t('common.cancelEdit')}</button>
              </div>
            : <div className={`conversation-row ${history.activeId === entry.id ? 'conversation-row--active' : ''}`}>
                <button type="button" className="conversation-open" aria-current={history.activeId === entry.id ? 'page' : undefined} title={entry.title || t('case.untitled')} onClick={() => { dispatch(historyActions.openRequested(entry.id)); onOpenCase(); }}>
                  <span>{entry.title || t('case.untitled')}</span>
                </button>
                <EditableTitle key={`${entry.id}-${history.activeId ?? 'new'}`} compact value={entry.title} fallback={t('case.untitled')} onSave={title => dispatch(historyActions.renamed({ id: entry.id, title }))}/>
                <button type="button" className="conversation-delete" aria-label={`${t('history.delete')}: ${entry.title || t('case.untitled')}`} title={t('history.delete')} onClick={() => setConfirming(entry.id)}><Icon name="trash" size={15}/></button>
              </div>}
        </div>)}
      </nav>
    </>}
    </div>
  </section>;
}
