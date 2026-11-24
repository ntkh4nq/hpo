import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAppDispatch, useAppSelector } from '../redux';
import { workspaceActions } from '../redux/slices';
import {
  selectHasAnalyzed,
  selectAnalysisRun,
  selectAnalysis,
  selectChat,
  selectHistoryEntries,
  selectWorkspace,
} from '../redux/selectors';
import { Feedback } from '../shared/ui';
import { WorkspaceHeader } from './WorkspaceHeader';
import { WorkflowHeading } from './WorkflowHeading';
import { ClinicalInput } from '../features/clinical-input';
import { PhenotypeReview } from '../features/phenotype';
import { EvidenceModal } from '../features/evidence';
import { ResultsPanel } from '../features/results';
import { ChatComposer, ChatThread } from '../features/chat';
import { CaseOutline, NavSidebar, WorkflowNavigation, workspaceSections, type WorkspaceSection } from '../features/navigation';
import type { Locale } from '../shared/types';

const NARROW = '(max-width: 1024px)';
const prefersReducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

export function App() {
  const { t, i18n } = useTranslation();
  const locale: Locale = i18n.resolvedLanguage?.startsWith('vi') ? 'vi' : 'en';
  const dispatch = useAppDispatch();

  const workspace = useAppSelector(selectWorkspace);
  const analysis = useAppSelector(selectAnalysis);
  const history = useAppSelector(selectHistoryEntries);
  const analyzed = useAppSelector(selectHasAnalyzed);
  // The first turn's progress lives in the review section (T96): reveal the thread as soon as it has a row.
  const hasAnalyzed = analyzed || analysis.reviewProgress.length > 0;
  const run = useAppSelector(selectAnalysisRun);
  const hasResults = Boolean(analysis.result);
  const chat = useAppSelector(selectChat);

  const [isNarrow, setIsNarrow] = useState(() => window.matchMedia(NARROW).matches);
  const [isSidebarOpen, setIsSidebarOpen] = useState(() => !window.matchMedia(NARROW).matches);
  const [activeSection, setActiveSection] = useState<WorkspaceSection>('clinical-input');
  const [showResults, setShowResults] = useState(false);
  const [inputExpandRequest, setInputExpandRequest] = useState(0);
  const [scrolled, setScrolled] = useState(false);
  const canvas = useRef<HTMLElement>(null);
  const previouslyOpen = useRef(isSidebarOpen);
  const previousLocale = useRef(locale);
  const previousCompletedRun = useRef<string | null>(run?.completed ? run.id : null);

  const resultsVisible = hasResults || showResults || ['retrieving', 'generating'].includes(analysis.status);

  const navigateSection = useCallback((id: WorkspaceSection) => {
    if (isNarrow) setIsSidebarOpen(false);
    setActiveSection(id);
    if (id === 'clinical-input') setInputExpandRequest(value => value + 1);
    requestAnimationFrame(() => {
      const section = document.getElementById(id) ?? document.getElementById('clinical-input');
      const scroller = canvas.current;
      if (section && scroller) {
        // scrollIntoView would also scroll the page and shift the whole shell; move only the thread.
        const top = section.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop - 16;
        scroller.scrollTo({ top: Math.max(0, top), behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
      }
      const focusTarget = id === 'clinical-input' ? section?.querySelector<HTMLTextAreaElement>('textarea') ?? section : section;
      focusTarget?.focus({ preventScroll: true });
    });
  }, [isNarrow]);

  // Case switches and new input reset the visible flow.
  useEffect(() => { setShowResults(false); setActiveSection('clinical-input'); }, [workspace.caseKey]);
  useEffect(() => { setShowResults(false); }, [workspace.description, run?.id]);

  // A new supervisor-written ranking folds the findings above it; bring the results (and the streaming summary) into view.
  const summaryFor = analysis.result?.summaryFor ?? null;
  const previousSummaryFor = useRef<number | null>(summaryFor);
  const summaryCase = useRef(workspace.caseKey);
  useEffect(() => {
    // Opening a saved case brings its summary along: that is not a new summary to scroll to.
    const sameCase = summaryCase.current === workspace.caseKey;
    if (sameCase && summaryFor !== null && summaryFor !== previousSummaryFor.current) navigateSection('case-results');
    previousSummaryFor.current = summaryFor;
    summaryCase.current = workspace.caseKey;
  }, [summaryFor, workspace.caseKey, navigateSection]);

  // A completed extraction moves the clinician to the review step.
  const runCase = useRef(workspace.caseKey);
  useEffect(() => {
    const sameCase = runCase.current === workspace.caseKey;
    runCase.current = workspace.caseKey;
    if (run?.completed && previousCompletedRun.current !== run.id) {
      previousCompletedRun.current = run.id;
      if (sameCase) navigateSection('phenotype-review');   // not when a saved case is opened
    }
  }, [run?.completed, run?.id, workspace.caseKey, navigateSection]);

  // Scroll spy on the thread scroll container: highlights the current step in the header stepper.
  useEffect(() => {
    const scroller = canvas.current;
    if (!scroller) return;
    let frame = 0;
    const update = () => {
      frame = 0;
      const box = scroller.getBoundingClientRect();
      setScrolled(scroller.scrollTop > 4);
      let current: WorkspaceSection = 'clinical-input';
      const visible = workspaceSections.map(id => document.getElementById(id)).filter((section): section is HTMLElement => Boolean(section && !section.hidden));
      for (const section of visible) if (section.getBoundingClientRect().top <= box.top + box.height * 0.35) current = section.id as WorkspaceSection;
      const atBottom = scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 8;
      const last = visible.at(-1);
      if (atBottom && last && last.getBoundingClientRect().top < box.bottom - 100) current = last.id as WorkspaceSection;
      setActiveSection(current);
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
    scroller.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    return () => { cancelAnimationFrame(frame); scroller.removeEventListener('scroll', schedule); window.removeEventListener('resize', schedule); };
  }, []);

  // Gentle fade when the language changes (data stays in place).
  useEffect(() => {
    if (previousLocale.current === locale) return;
    previousLocale.current = locale;
    if (prefersReducedMotion()) return;
    const animation = canvas.current?.animate([{ opacity: 0.72 }, { opacity: 1 }], { duration: 180, easing: 'ease-out' });
    return () => animation?.cancel();
  }, [locale]);

  useEffect(() => {
    const media = window.matchMedia(NARROW);
    const update = () => { setIsNarrow(media.matches); setIsSidebarOpen(!media.matches); };
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);

  // Move focus into the drawer when it opens on narrow screens, and back to its trigger when it closes.
  useEffect(() => {
    if (isSidebarOpen && isNarrow) document.getElementById('sidebar-close-trigger')?.focus();
    else if (!isSidebarOpen && previouslyOpen.current) document.getElementById(isNarrow ? 'header-menu-trigger' : 'sidebar-open-trigger')?.focus();
    previouslyOpen.current = isSidebarOpen;
  }, [isSidebarOpen, isNarrow]);

  const handleNewCase = () => {
    dispatch(workspaceActions.reset());
    setShowResults(false);
    navigateSection('clinical-input');
  };

  const drawerOpen = isNarrow && isSidebarOpen;

  return (
    <div className={`app-shell ${isSidebarOpen ? 'app-shell--sidebar-open' : 'app-shell--sidebar-collapsed'} ${isNarrow ? 'app-shell--narrow' : ''}`}>
      <a href="#main" className="skip-link">{t('app.skip')}</a>

      <NavSidebar
        isModal={isNarrow}
        isOpen={isSidebarOpen}
        onToggleOpen={() => setIsSidebarOpen(open => !open)}
        onNewCase={handleNewCase}
        historyCount={history.length}
        onOpenCase={() => {
          // Opening a saved case shows it as it was (description read-only), from the top; it does not open the editor.
          if (isNarrow) setIsSidebarOpen(false);
          canvas.current?.scrollTo({ top: 0 });
        }}
      />
      <button
        type="button"
        className={`sidebar-scrim ${drawerOpen ? 'sidebar-scrim--visible' : ''}`}
        onClick={() => setIsSidebarOpen(false)}
        aria-label={t('nav.collapse')}
        tabIndex={drawerOpen ? 0 : -1}
        aria-hidden={!drawerOpen}
      />

      <div className={`workspace-main-area ${scrolled ? 'workspace-main-area--scrolled' : ''}`} inert={drawerOpen}>
        <h1 className="sr-only">PhenoGuide</h1>
        <WorkspaceHeader
          showMenuButton={isNarrow}
          onOpenSidebar={() => setIsSidebarOpen(true)}
          stepper={hasAnalyzed ? <WorkflowNavigation resultsAvailable={resultsVisible} activeSection={activeSection} onNavigate={navigateSection}/> : null}
        />

        <div className="workspace-body">
        <div className="workspace-column">
        <main id="main" ref={canvas} tabIndex={-1} className={`clinical-canvas ${hasAnalyzed ? '' : 'clinical-canvas--welcome'}`}>
          <div className="thread">
            <div className="workspace-alerts" aria-live="polite">
              {analysis.error && (
                <Feedback error><strong>{t('status.error')}</strong><p>{t(analysis.error, { message: analysis.errorMessage ?? '' })}</p></Feedback>
              )}
              {analysis.notice && <Feedback><p>{t(`notice.${analysis.notice}`)}</p></Feedback>}
            </div>

            {/* Turn 1 — the clinician's description (welcome composer before the first analysis). */}
            <section id="clinical-input" tabIndex={-1} className={`turn ${hasAnalyzed ? 'turn--user' : 'welcome'}`} aria-labelledby="workflow-heading-1">
              {hasAnalyzed
                ? <WorkflowHeading step={1}/>
                : <div className="welcome-heading">
                    <img src="/favicon.svg" width={40} height={40} alt=""/>
                    <h2 id="workflow-heading-1">{t('welcome.title')}</h2>
                    <p>{t('welcome.hint')}</p>
                  </div>}
              <ClinicalInput key={workspace.caseKey} introductory={!hasAnalyzed} expandRequest={inputExpandRequest}/>
            </section>

            {/* Turn 2 — HPO findings for review. */}
            <section hidden={!hasAnalyzed} id="phenotype-review" tabIndex={-1} className="turn turn--assistant" aria-labelledby="workflow-heading-2">
              <WorkflowHeading step={2}/>
              <div className="turn-body">
                <PhenotypeReview key={workspace.caseKey} onRetrieveRequested={() => { setShowResults(true); navigateSection('case-results'); }} onEditDescription={() => navigateSection('clinical-input')}/>
              </div>
            </section>

            {/* Turn 3 — related diseases and genes. */}
            <section hidden={!resultsVisible} id="case-results" tabIndex={-1} className="turn turn--assistant" aria-labelledby="workflow-heading-3">
              <WorkflowHeading step={3}/>
              <div className="turn-body"><ResultsPanel/></div>
            </section>

            {/* Follow-up questions about the current result. */}
            {chat.messages.length > 0 && <section id="follow-up" className="turn turn--follow-up" aria-label={t('chat.title')}><ChatThread/></section>}
          </div>
        </main>

        <footer className="composer-dock">
          {hasAnalyzed && workspace.caseId && <ChatComposer/>}
          <p className="composer-dock__note">{t('app.disclaimer')}</p>
        </footer>
        </div>
        {hasAnalyzed && <CaseOutline activeSection={activeSection} onNavigate={navigateSection}/>}
        </div>
      </div>

      <EvidenceModal/>
    </div>
  );
}
