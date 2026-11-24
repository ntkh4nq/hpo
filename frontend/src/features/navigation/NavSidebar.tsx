import type { KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon, Tooltip } from '../../shared/ui';
import { HistoryPanel } from '../history';
import { ProfileMenu } from '../profile';

interface NavSidebarProps {
  isOpen: boolean;
  isModal: boolean;
  onToggleOpen: () => void;
  onOpenCase: () => void;
  onNewCase: () => void;
  historyCount: number;
}

export function NavSidebar({ isOpen, isModal, onToggleOpen, onOpenCase, onNewCase, historyCount }: NavSidebarProps) {
  const { t } = useTranslation();
  const handlePanelKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Escape' && isOpen && isModal) { event.stopPropagation(); onToggleOpen(); return; }
    if (!isOpen || !isModal || event.key !== 'Tab') return;
    const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), a[href], [tabindex="0"]')).filter(element => element.tabIndex >= 0 && !element.closest('[inert]') && element.getClientRects().length > 0);
    const first = controls[0], last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  };

  return <aside
    className={`sidebar-panel ${isOpen ? 'sidebar-panel--expanded' : 'sidebar-panel--collapsed'}`}
    aria-label={t('nav.history')}
    role={isModal && isOpen ? 'dialog' : undefined}
    aria-modal={isModal && isOpen ? true : undefined}
    onKeyDown={handlePanelKeyDown}
  >
    {/* Expanded: brand, new case, history, profile */}
    <div className="sidebar-expanded-layer" inert={!isOpen} aria-hidden={!isOpen}>
      <div className="sidebar-brand-row">
        <div className="sidebar-brand">
          <img className="brand-logo" src="/favicon.svg" alt="" width={28} height={28}/>
          <span className="brand-title">PhenoGuide</span>
        </div>
        <Tooltip content={t('help.collapse')}>
          <button id="sidebar-close-trigger" type="button" onClick={onToggleOpen} className="icon-button sidebar-collapse-btn" aria-label={t('nav.collapse')}>
            <SidebarGlyph/>
          </button>
        </Tooltip>
      </div>
      <button type="button" className="sidebar-new-case" onClick={onNewCase}>
        <Icon name="plus" size={18}/>
        <span>{t('app.reset')}</span>
      </button>
      <div className="sidebar-history-body"><HistoryPanel onOpenCase={onOpenCase}/></div>
      <div className="sidebar-footer">{isOpen && <ProfileMenu/>}</div>
    </div>

    {/* Collapsed rail (desktop only) */}
    <div className="sidebar-collapsed-layer" inert={isOpen} aria-hidden={isOpen}>
      <Tooltip content={t('help.expand')}>
        <button id="sidebar-open-trigger" type="button" onClick={onToggleOpen} className="rail-button rail-button--brand" aria-label={t('nav.expand')}>
          <img className="brand-logo" src="/favicon.svg" alt="" width={28} height={28}/>
        </button>
      </Tooltip>
      <Tooltip content={t('help.newCase')}>
        <button type="button" onClick={onNewCase} className="rail-button rail-button--new" aria-label={t('app.reset')}>
          <Icon name="plus" size={18}/>
        </button>
      </Tooltip>
      <Tooltip content={t('help.history')}>
        <button type="button" onClick={onToggleOpen} className="rail-button" aria-label={t('nav.history')}>
          <Icon name="history" size={19}/>
          {historyCount > 0 && <span className="rail-badge">{historyCount}</span>}
        </button>
      </Tooltip>
      <div className="rail-spacer"/>
      {!isOpen && <ProfileMenu compact/>}
    </div>
  </aside>;
}

export function SidebarGlyph() {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/></svg>;
}
