import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CASE_TITLE_CHARS, normalizeCaseTitle } from '../cases';
import { Button, Icon } from './controls';

export function EditableTitle({ value, fallback, onSave, compact = false }: {
  value: string; fallback: string; onSave: (title: string) => void; compact?: boolean;
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(() => { if (editing) { input.current?.focus(); input.current?.select(); } }, [editing]);
  const close = () => { setEditing(false); requestAnimationFrame(() => trigger.current?.focus()); };

  return <div className={`editable-title ${compact ? 'editable-title--compact' : ''} ${editing ? 'editable-title--editing' : ''}`}>
    {editing ? <form className="title-edit-form" onSubmit={event => { event.preventDefault(); const title = normalizeCaseTitle(draft); if (title) { onSave(title); close(); } }} onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); close(); } }}>
      <label className="sr-only" htmlFor={id}>{t('case.titleLabel')}</label>
      <input id={id} ref={input} value={draft} maxLength={CASE_TITLE_CHARS} autoComplete="off" required onChange={event => setDraft(event.target.value)} placeholder={t('case.titlePlaceholder')}/>
      <Button type="submit" variant="ghost" disabled={!normalizeCaseTitle(draft)} aria-label={t('case.saveTitle')}><Icon name="check" size={16}/></Button>
      <Button variant="ghost" onClick={close} aria-label={t('common.cancelEdit')}><Icon name="close" size={16}/></Button>
    </form> : <button type="button" ref={trigger} className="title-edit-trigger" onClick={() => { setDraft(value); setEditing(true); }} aria-label={`${t('case.rename')}: ${value || fallback}`} title={t('case.rename')}>
      {!compact && <span className="case-title-text">{value || fallback}</span>}<Icon name="edit" size={compact ? 15 : 16}/>
    </button>}
  </div>;
}
