import { useEffect, useId, useRef, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Icon } from './controls';

/** Detail dialog over the thread (native `showModal`): title row, scrolling body, optional footer.
 *  Focus returns to the element that opened it. Escape and a backdrop click close it. */
export function Dialog({ open, onClose, title, subtitle, status, footer, children }: {
  open: boolean; onClose: () => void; title: ReactNode; subtitle?: ReactNode; status?: ReactNode; footer?: ReactNode; children: ReactNode;
}) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      trigger.current = document.activeElement as HTMLElement | null;
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
      if (trigger.current && document.contains(trigger.current)) trigger.current.focus();
    }
  }, [open]);

  return (
    <dialog ref={ref} className="detail-dialog" aria-labelledby={titleId}
      onCancel={event => { event.preventDefault(); onClose(); }}
      onClick={event => { if (event.target === ref.current) onClose(); }}>
      {open && (
        <div className="detail-dialog__container">
          <header className="detail-dialog__header">
            <div className="detail-dialog__heading">
              <h2 id={titleId} className="detail-dialog__title">{title}</h2>
              {subtitle && <div className="detail-dialog__subtitle">{subtitle}</div>}
            </div>
            {status && <div className="detail-dialog__status">{status}</div>}
            <Button variant="ghost" onClick={onClose} aria-label={t('evidence.close')} className="detail-dialog__close">
              <Icon name="close" size={18}/>
            </Button>
          </header>
          <div className="detail-dialog__body">{children}</div>
          {footer && <footer className="detail-dialog__footer">{footer}</footer>}
        </div>
      )}
    </dialog>
  );
}

/** One titled block inside a detail dialog. */
export function DialogSection({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="detail-dialog__section">
      <div className="detail-dialog__section-head">
        <h3 className="detail-dialog__section-title">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}
