import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { useAppDispatch, useAppSelector } from '../../redux';
import { chatActions, workspaceActions } from '../../redux/slices';
import { selectAnalysis, selectCanChat, selectChat, selectWorkspace } from '../../redux/selectors';
import { Feedback, Markdown, sourceUrl } from '../../shared/ui';
import type { RenderLink } from '../../shared/ui/Markdown';
import type { Citation } from '../../shared/types';
import { TurnProgress } from '../progress';
const typing = (label: string) => (
  <div className="chat-typing" role="status" aria-label={label}>
    <span className="typing-dot"/><span className="typing-dot"/><span className="typing-dot"/>
  </div>
);

/** Follow-up messages rendered inside the case thread, after the results turn. */
export function ChatThread() {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const chat = useAppSelector(selectChat);
  const candidates = useAppSelector(selectAnalysis).result?.candidates ?? [];
  // Like Claude/ChatGPT: a new question scrolls into view and the streaming answer stays pinned to the bottom,
  // unless the doctor scrolled up to read something else.
  const pinned = useRef(true);
  const last = chat.messages.at(-1);
  const growth = last ? last.progress.reduce((n, item) => n + (item.kind === 'text' ? item.text.length : 1), last.text.length) : 0;
  useEffect(() => {
    const scroller = document.getElementById('main');
    if (!scroller) return;
    const onScroll = () => { pinned.current = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 160; };
    scroller.addEventListener('scroll', onScroll, { passive: true });
    return () => scroller.removeEventListener('scroll', onScroll);
  }, []);
  useEffect(() => { if (chat.pending) pinned.current = true; }, [chat.requestId, chat.pending]);
  useEffect(() => {
    const scroller = document.getElementById('main');
    if (scroller && pinned.current && chat.pending) scroller.scrollTo({ top: scroller.scrollHeight });
  }, [growth, chat.messages.length, chat.pending]);

  if (chat.messages.length === 0 && !chat.pending && !chat.error) return null;

  return (
    <div className="chat-thread" aria-live="polite" aria-label={t('chat.title')}>
      {chat.messages.map(message => {
        // T114: passages cited in the text become numbered marks [1] [2] that match the numbered pills below.
        const passages = message.citations.filter(citation => citation.kind === 'chunk').map(citation => citation.id);
        const number = (id: string) => passages.indexOf(id) + 1;
        const renderLink: RenderLink = (label, target, key) => {
          if (target.startsWith('CHUNK:')) {
            const n = number(target);
            return <button key={key} type="button" className="cite-mark" onClick={() => dispatch(workspaceActions.evidenceSelected(target))}
              aria-label={`${t('results.evidence')} ${n || ''}`}>{n || '•'}</button>;
          }
          // a disease of the current list opens its details, as in the summary; other codes open their source page
          if (candidates.some(candidate => candidate.id === target)) {
            return <a key={key} href={`#${target}`} className="summary-ref" onClick={event => { event.preventDefault(); dispatch(workspaceActions.candidateOpened(target)); }}>{label}</a>;
          }
          const url = sourceUrl(target);
          return url ? <a key={key} className="summary-source" href={url} target="_blank" rel="noreferrer noopener">{label}</a> : label;
        };
        // Codes the text already links inline need no second pill; passages keep their numbered pills.
        const pills = message.citations.filter(citation => citation.kind === 'chunk' || !message.text.includes(citation.id));
        return (
        <div key={message.id} className={`chat-message chat-message--${message.role}`}>
          <span className="sr-only">{message.role === 'user' ? t('chat.you') : t('chat.assistant')}: </span>
          <div className="chat-message__body">
            {message.role === 'user'
              ? <p className="chat-message__text">{message.text}</p>
              : message.progress.length > 0
                ? <TurnProgress items={message.progress} renderLink={renderLink}/>
                : chat.pending && message.id === `${chat.requestId}-a` ? typing(t('common.loading')) : <Markdown className="chat-message__text" text={message.text} renderLink={renderLink}/>}
            {pills.length > 0 && (
              <div className="chat-citations">
                {pills.map(citation => citation.kind === 'chunk' ? (
                  <button key={citation.id} type="button" className="citation-pill" onClick={() => dispatch(workspaceActions.evidenceSelected(citation.id))} aria-label={`${t('results.evidence')}: ${citation.label ?? citation.id}`}>
                    <span className="citation-pill__number">{number(citation.id)}</span>{pillLabel(citation, t('results.evidence'))}
                  </button>
                ) : (
                  sourceUrl(citation.id)
                    ? <a key={citation.id} className="citation-pill" href={sourceUrl(citation.id) ?? undefined} target="_blank" rel="noreferrer noopener">{citation.label ? `${citation.label} · ${citation.id}` : citation.id} ↗</a>
                    : <span key={citation.id} className="citation-pill citation-pill--static">{citation.label ? `${citation.label} · ${citation.id}` : citation.id}</span>
                ))}
              </div>
            )}
          </div>
        </div>
        );
      })}
      {chat.error && <Feedback error>{t(chat.error, { message: chat.errorMessage ?? '' })}</Feedback>}
    </div>
  );
}

const pillLabel = (citation: Citation, fallback: string) => citation.label ?? fallback;

/** Composer docked at the bottom of the workspace once suggestions exist. */
export function ChatComposer() {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const chat = useAppSelector(selectChat);
  const workspace = useAppSelector(selectWorkspace);
  const enabled = useAppSelector(selectCanChat);
  const [draft, setDraft] = useState('');
  const field = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const element = field.current;
    if (!element) return;
    element.style.height = 'auto';
    element.style.height = `${Math.min(element.scrollHeight, 180)}px`;
  }, [draft]);

  const send = (event?: FormEvent) => {
    event?.preventDefault();
    const text = draft.trim();
    if (!enabled || !text) return;
    dispatch(chatActions.requested({ requestId: crypto.randomUUID(), text }));
    setDraft('');
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) send(event);
  };

  return (
    <form className={`chat-composer ${enabled ? '' : 'chat-composer--locked'}`} onSubmit={send}>
      <label htmlFor="chat-composer-input" className="sr-only">{t('chat.placeholder')}</label>
      <textarea
        id="chat-composer-input"
        ref={field}
        rows={1}
        value={draft}
        onChange={event => setDraft(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder={enabled ? t('chat.placeholder') : workspace.serverReview ? t('chat.lockedReview') : t('chat.lockedBusy')}
        disabled={!enabled}
        className="chat-composer__input"
      />
      <button type="submit" className="chat-composer__send" disabled={!enabled || !draft.trim()} aria-label={t('chat.send')} title={t('chat.send')}>
        {chat.pending ? <span className="button-spinner" aria-hidden="true"/> : <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7"/></svg>}
      </button>
    </form>
  );
}
