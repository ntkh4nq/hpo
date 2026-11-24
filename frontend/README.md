# PhenoGuide

Bilingual prenatal phenotype review template. React + TypeScript + Vite, Redux Toolkit + Redux Saga, i18next, and plain CSS. The UI was extended by Gemini 3.8 Flash through Antigravity from the reference UI in `UI_SPEC.md`, then reviewed and integrated by Codex.

## Run locally

From the repository root (pnpm, not npm): `make up` (Postgres + Redis), `make api` (FastAPI on :8000), then `make web` (Vite on http://127.0.0.1:5173; `/api` is proxied to :8000). Inside `frontend/`: `pnpm install`, then `pnpm run typecheck`, `pnpm run build` and `pnpm test` validate types, production assets and the behavioral workflow. Tests use Node's built-in TypeScript stripping and module hooks with a fake backend; no extra test dependency is required.

## Try the demo

1. Load a synthetic case from **Use an example** and select **Analyze**.
2. Inspect the read-only HPO terms, original excerpts, assertions, assessment statuses, confidence and provenance. HPO cannot be added, replaced or deleted directly.
3. Use **Add note / Edit note** for context tied to the current analysis. Save or clear a note, or cancel an unsaved draft. Notes never change the HPO mapping or clinical assertion.
4. Select **Continue** to retrieve disease/gene suggestions and source evidence. Notes are included as a separate field on each finding in the request; the mock retains but does not interpret them or recalculate rankings from their content.
5. Select **Edit case description** to return to the existing description. Edit that text and select **Analyze again** to regenerate the full HPO list. Retrieval is available only after successful extraction of that exact description, including supported cases with no findings.
6. Saved notes are archived before an existing analysis is cleared. **Notes from previous analyses** groups them with their original description, HPO and clinical statuses. They stay read-only and are never transferred to new findings or automatically sent as current context.

Use EN/VI at any time. UI text and mock summaries translate while original clinical text, notes, evidence excerpts and canonical HPO labels remain unchanged. Clinical data lives only in session memory.

Conversation history restores the description, current analysis, HPO notes, prior-note archive and result snapshot. Switching cases cancels active tasks and preserves the current draft; renaming changes metadata only. A new case clears the active workspace while retaining previous session cases. Reloading clears all clinical content.

The UI is connected to the project backend (cases, review, findings, explanations, passages over JSON and Server-Sent Events). What changed from this prototype and why is recorded in `docs/frontend-plan.md` at the repository root.

## Architecture

`redux/sagas` handles side effects and dispatches slice actions. `redux/slices` holds state and pure reducers. `redux/selectors` exposes state and derived values. Each folder exports through its `index.ts`. Components dispatch requests and read state through typed hooks and selectors. Thunk middleware is disabled.

Sagas cancel superseded requests, workspace resets, and conversation switches. Profile revision and request identity protect against stale results. Retrieval results include the profile snapshot used to create them. Editing a profile invalidates results and conversation.

Each extraction has an analysis ID, its exact source description and a completion flag. Source edits cancel pending work and invalidate the active profile. Profile revisions and request IDs prevent obsolete responses from replacing current data. Saved-note archives belong to the case and survive reanalysis, failure and cancellation; they are recorded in history snapshots.

Suggestion requests carry the analysis ID, analyzed description and canonical extracted profile with current notes. Results snapshot that same context. A real backend may interpret notes as clinician context, but must distinguish them from extracted observations.

`shared/api` exports the `ClinicalService` contract and current mock adapter. Replace the adapter to connect a backend; keep extraction, ontology validation, RAG orchestration, retrieval, and medical ranking there. The contract carries the requested response language and preserves assertion, assessment, provenance, and separate confidence values.

The layout is a conversation-style workspace (see [LAYOUT_SPEC.md](LAYOUT_SPEC.md)): a sidebar with session cases, a single centered thread where the case description is the user turn and HPO findings and disease/gene suggestions are assistant turns, and a docked composer for follow-up questions once suggestions exist. Follow-ups are scoped to the current result and reset when the profile changes. `app/App.tsx` owns the shell and the thread scroll container; `app/WorkspaceHeader` holds the case title and the step navigator (`features/navigation/WorkflowNavigation`); `features/navigation/NavSidebar` holds new case, history and the profile menu (language and theme). Styles load from `styles/index.css` in a fixed order: `tokens.css` (all colours, light and dark), `base.css` (shared primitives), the shell, then one stylesheet per feature. Translation dictionaries are in `i18n/locales` and are checked for key parity.

## Deployment

`.openai/hosting.json` identifies the private ChatGPT Site and the static `dist` directory. Build and package the exact source revision using the Sites source workflow, then deploy that saved revision. Keep the existing Site identity for subsequent updates. Never commit credentials or `.env` files.

The local publishing mirror in `.sites-runtime/site-checkout` retains the independent Sites Git history; the main frontend folder remains part of the parent project. For updates, copy the frontend source and configuration (excluding `.git`, `.sites-runtime`, `node_modules`, and sensitive files) into that mirror, then run the Sites workflow there. Dependencies are linked locally for builds; `.sites-runtime` is ignored.
