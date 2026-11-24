# Gemini UI implementation handoff

Implement the remaining UI for Phenotype Studio. Codex owns Redux, service contracts, fixtures, build configuration, and deployment. The reference implementation is `src/features/phenotype/FindingCard.tsx`; use its controls, state access, and translation pattern consistently.

## Boundaries

Work only in `src/app`, `src/features`, `src/shared/ui`, `src/styles`, and `src/i18n/locales`. Read `src/redux`, `src/shared/types`, `src/shared/api`, and `src/mocks` to discover contracts. Preserve those contracts. Report any needed architecture change in your final response instead of editing outside the permitted locations. Leave package/configuration files unchanged. Use the approved installed packages and inline SVG icons; no additional dependencies, network fonts, remote assets, or image generation.

Do not read sensitive files such as `.env` or `.env.dev`. Do not modify unrelated files or create persistence for clinical content. Expose each folder's public interface through its `index.ts` (or `index.tsx` for the existing shared UI barrel). Cross-folder imports use that public entrypoint. Use relative sibling imports inside a folder.

## Visual direction

Create a polished, calm clinical research workspace, with warm ivory background, white panels, forest-teal accents, subtle borders, compact status badges, generous whitespace, and readable system typography. Extend the existing tokens and shell. Keep the title Phenotype Studio. Avoid oversized hero artwork; the workspace is the product. Use small meaningful inline SVG illustrations for empty states, and clear numbered workflow steps.

Desktop: header with brand, synthetic-demo indicator, EN/VI switch, and new workspace action; concise introduction; left column with clinical input and follow-up chat; larger right column with HPO review / evidence tabs. Include a compact pipeline indicator. Tablet and mobile: stack panels, preserve complete functionality, prevent horizontal overflow at 390px. Use semantic labels, accessible names, keyboard-visible focus, adequate contrast, and reduced-motion support.

## Build the interactions

1. Clinical input (`features/clinical-input`): scenario select with prenatal, ambiguous, empty, insufficient, failure options; explicit Load sample action; textarea bound to workspace description; locale-aware character count; Analyze sample action; custom-input limitation notice. Scenario selection can be component state until sample is loaded. Loading must use `sampleCases[scenario][locale]` and `workspaceActions.sampleLoaded`. Typing dispatches `descriptionChanged`. Analyze dispatches `analysisActions.requested` with crypto.randomUUID(), text, current scenario, and locale. Disable empty/whitespace input and analysis while busy. To cancel, use workspace reset and label clearly (it also clears the input).
2. HPO review (`features/phenotype`): retain and refine FindingCard; add a review panel with reviewed count, findings, helpful empty state, ontology-demo note, and Find related evidence action. Use `selectCanRetrieve`; dispatch `analysisActions.suggestionsRequested` with requestId, revision, findings, locale, scenario. Every finding can be reviewed, even an uncertain/unmapped observation: do not silently invent an HPO term or change assertion/assessment. Use a single open term editor if practical. Mapping confidence null means manually selected only if the finding has a term; otherwise display a dash.
3. Results (`features/results`): summary, insufficient-evidence feedback, stale-result warning, candidate relevance scores (Intl), matched finding labels, fictional-candidate markers, evidence buttons. Never describe scores as diagnostic probabilities. Evidence buttons dispatch workspaceActions.evidenceSelected(id). Show an appropriate empty state before results exist. If stale, keep old results visibly outdated and provide a way back to review.
4. Chat (`features/chat`): list messages with role and localized text, citations, pending/error feedback, local draft, send action. Enable only when current suggestions exist, not stale, no analysis busy, and not chat.pending. Dispatch chatActions.requested with requestId, revision, text, locale, and current result. Clear the local draft after sending. This is scripted mock conversation; keep that visible.
5. Evidence (`features/evidence`): accessible modal using native dialog.showModal(), titled source information, localized excerpt/location, and synthetic-source notice. Use selectSelectedEvidence. Close via button, Escape, or workspace evidenceSelected(null), restore focus to the triggering control, and avoid a trapped dialog after workspace reset or source changes. No invented external reference links.
6. Shell: integrate all features, pipeline steps/status, accessible tabs, alerts for analysis.error and analysis.notice, live processing feedback, private/session-only footer, synthetic-data disclaimer, and reset action. Wire language directly to i18n.changeLanguage; translations must update existing data displays without resetting clinical input.

## Contracts

Import typed hooks from `src/redux`, actions from `src/redux/slices`, and selectors from `src/redux/selectors`. Public selectors: selectWorkspace, selectAnalysis, selectPhenotype, selectChat, selectFindings, selectRevision, selectCanRetrieve, selectBusy, selectSelectedEvidence. Inspect the exported types/actions for signatures rather than duplicating them. i18n owns the locale; normalize it to 'en' or 'vi'. All interface text must use translation keys. Add matching en/vi keys as needed. HPO canonical labels, original source phrases, and source IDs remain unchanged. Use Intl for displayed numbers.

## Completion criteria

Run `npm run build` after the UI is implemented. Inspect your modified files for missing translations, deep imports, inert controls, and unsupported dependencies. Return the changed areas, check outcome, and any contract issues requiring Codex. Do not deploy or run another agent. Codex reviews and deploys after your work finishes.
