# PhenoGuide layout spec — conversation-style workspace

Owner: Claude (planner). Implementers: Claude (shell, tokens, chat), Gemini (feature styling).
This spec replaces the *visual/layout* parts of `UI_SPEC.md`. Behavioural and contract rules in `UI_SPEC.md` and `README.md` still apply (Redux contracts, i18n keys, accessibility, no new dependencies, no network assets).

## 1. Idea

A case is a conversation. The clinician writes a description (user turn); the system answers with HPO findings, then disease/gene suggestions (assistant turns); follow-up questions continue the same thread. One centered reading column, a quiet sidebar of past cases, a composer docked at the bottom.

It is a clinical tool, not a chatbot: assistant turns are structured panels (cards, lists, badges), not prose bubbles.

## 2. Wireframes

Desktop (≥ 1025px)

```
┌──────────────┬──────────────────────────────────────────────────────────┐
│ ◉ PhenoGuide «│  Case title ✎            ①─②─③ stepper          DEMO   │ ← app-header 56px, sticky
│              ├──────────────────────────────────────────────────────────┤
│ [+ New case] │            ┌──────────── 768px column ────────────┐      │
│              │            │                    ┌───────────────┐ │      │
│ Today        │            │                    │ Case descr.   │ │ user turn (right, tinted)
│  · Case A    │            │                    │ … ✎ Edit      │ │      │
│  · Case B    │            │                    └───────────────┘ │      │
│ Earlier      │            │ ◈ HPO findings                       │ assistant turn
│  · Case C    │            │ ┌──────────────────────────────────┐ │      │
│              │            │ │ finding cards …                  │ │      │
│              │            │ └──────────────────────────────────┘ │      │
│              │            │           [Edit description] [Continue →]   │
│              │            │ ◈ Related diseases and genes         │      │
│              │            │   summary / candidate cards …        │      │
│              │            │ You: why FGFR3?            (user)    │      │
│              │            │ ◈ answer + citation pills            │      │
│              │            └──────────────────────────────────────┘      │
│ ──────────── │      ┌──────────────────────────────────────────┐        │
│ ◯ Guest  ⋯   │      │ Ask a follow-up…                    [↑]  │        │ ← composer dock (only once results exist)
└──────────────┘      └──────────────────────────────────────────┘        │
                      synthetic data · session only (12px muted)
```

Empty / welcome state (no analysis yet): no stepper, no dock. The column is vertically centered: small logo, greeting `welcome.title`, hint, then the clinical-input composer (large textarea card, "Use an example" ghost button and Analyze primary button inside the card's bottom bar).

Collapsed sidebar (desktop): 56px rail with logo button (expands), `+` new case, history icon with count badge, profile avatar at bottom.

Tablet/mobile (≤ 1024px): sidebar becomes an off-canvas drawer (272px, scrim, focus trap — existing behaviour). Header shows a menu button at left. Column becomes full width with 16px gutters. Stepper shows numbers only ≤ 640px. No horizontal overflow at 390px.

## 3. Shell structure (Claude implements)

```
.app-shell                       grid: [sidebar auto] [main 1fr]; height 100dvh
  aside.sidebar-panel            272px expanded / 56px rail; bg --surface-sidebar; right border
    brand row + collapse btn
    New case button (full width, secondary style w/ plus icon)
    HistoryPanel                 flex 1, scrolls
    sidebar footer               ProfileMenu (trigger = avatar + "Guest clinician"), popover opens upward
  .workspace-main-area           flex column, min-height 0
    header.app-header            56px, sticky, bg --background @ 85% + blur, bottom hairline only when scrolled
    main#main.clinical-canvas    flex 1, overflow-y auto (the only scroll container)
      .thread                    max-width 768px, margin auto, padding 24px 24px 48px, gap 28px
        .workspace-alerts
        section#clinical-input   .turn--user  (welcome: .welcome)
        section#phenotype-review .turn--assistant
        section#case-results     .turn--assistant
        section#follow-up        chat messages (ChatThread)
    footer.composer-dock         sticky bottom; same 768px column; ChatComposer + disclaimer line
```

Turns:
- `.turn` — no outer card. Heading row = 28px round mark (teal soft bg, WorkflowIcon) + h2 (`WorkflowHeading`, 16px/600).
- `.turn--user` — the description card is right-aligned, max-width 85%, bg `--surface-user`, radius 18px, padding 14px 18px. When expanded for editing it becomes full width.
- `.turn--assistant` — content flows at full column width directly below the heading, indented 40px on desktop (align with heading text), 0 on mobile.

The left workflow side panel is removed; its job moves to the header stepper (`WorkflowNavigation`, horizontal, 3 pills, current = teal soft bg, click scrolls to section). `RightNavRail` is dead code and is deleted.

## 4. Design tokens (Claude implements in `src/styles/tokens.css`)

Light blue/teal clinical palette. Colour only through tokens; no hex values in feature CSS.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--background` | `#f5f8fa` | `#0e171b` | page / thread |
| `--surface` | `#ffffff` | `#15222a` | cards |
| `--surface-sidebar` | `#edf3f6` | `#111d23` | sidebar |
| `--surface-secondary` | `#f2f6f8` | `#1a2a32` | subtle wells, inputs bars |
| `--surface-hover` | `#e6eef2` | `#1f323b` | hover |
| `--surface-user` | `#e4f1f4` | `#173039` | user turn bubble |
| `--ink` / `--text-primary` | `#0f2733` | `#e5eff2` | primary text |
| `--text-secondary` | `#3f5865` | `#a9bec7` | secondary text |
| `--muted` / `--text-muted` | `#647d89` | `#7f98a3` | captions |
| `--line` | `#dce6eb` | `#23363f` | hairlines |
| `--border-default` | `#c9d7de` | `#2f4651` | inputs, cards |
| `--primary` | `#0b7285` | `#3bb3c3` | teal: actions, focus |
| `--primary-dark` | `#095c6b` | `#6fcdd9` | hover/pressed |
| `--primary-soft` | `#e0f2f5` | `rgba(59,179,195,.14)` | selected, marks |
| `--info` / `--info-soft` | `#2563a6` / `#e7f0fa` | `#7cb3ea` / `rgba(124,179,234,.14)` | links, evidence |
| `--amber` / `--amber-soft` | `#8a5a00` / `#fbf1d9` | `#f2c25b` / `rgba(242,194,91,.15)` | stale, uncertain |
| `--rose` / `--rose-soft` | `#b23a3a` / `#fbe9e9` | `#f08a8a` / `rgba(240,138,138,.15)` | errors, excluded |
| `--green` / `--green-soft` | `#2c7a4b` / `#e5f4ea` | `#7ccf9b` / `rgba(124,207,155,.14)` | present/confirmed |

Legacy names (`--teal*`, `--surface-card`, `--surface-input`, `--border-subtle`, `--border-hover`, `--surface-selected`) stay as aliases.

Type: system stack, base 15px / 1.6, h2 16px/600, h3 15px/600, captions 13px, badges 12px/500. Radius: `--radius` 14px (cards), `--radius-sm` 10px (buttons, inputs), `--radius-xs` 6px (badges), `--radius-pill` 999px. Shadows minimal: cards use a 1px border and at most `--shadow-sm`; the composer card uses `--shadow-md`.

Focus: `outline: 2px solid var(--primary); outline-offset: 2px` on `:focus-visible` everywhere. Motion: ≤ 200ms, disabled under `prefers-reduced-motion`.

## 5. Feature styling (Gemini implements)

Rewrite each feature stylesheet from scratch so that it fully styles the class names used by that feature's TSX, using only tokens from §4. Remove every `!important`. Do not depend on `src/styles/index.css` or `src/styles/polish.css` — both will be deleted. Global primitives (`.button*`, `.panel`, `.badge*`, `.feedback*`, `.empty-state*`, `.tooltip-bubble`, `.sr-only`, `.skip-link`, `.editable-title*`, form controls) live in `src/styles/base.css` (Claude) — reuse, don't redefine.

- **clinical-input**: welcome composer = one white card (radius 18px, border, `--shadow-md`), borderless auto-height textarea (min 4 rows), bottom bar with ghost "Use an example" left and primary Analyze right. Sample picker = inline row inside the card. Compact (analyzed) state = user bubble per §3 with excerpt clamped to 4 lines and a ghost Edit button.
- **phenotype**: finding cards = white, 1px border, radius 14px, 16px padding, 10px gap between cards. Header row: HPO label (600) + HPO id (mono, muted, 12px) + status badges (present=green, uncertain=amber, excluded=rose, unmapped=neutral). Source quote = left 3px `--primary-soft` border, italic secondary text. Notes area in a `--surface-secondary` well. Footer actions right-aligned, sticky-feeling but not sticky.
- **results**: summary as plain prose (no card). Candidate cards like finding cards; gene tag = mono pill on `--info-soft`. Matched findings = small check pills. Evidence buttons = secondary small. Stale state = amber banner on top + candidate cards at 60% opacity.
- **history** (in sidebar): rows 34px, radius 8px, title truncates, hover shows rename/delete icons only on hover/focus-within, active row `--surface-hover` + 600 weight. Date group labels 12px muted uppercase-free.
- **profile**: trigger row in sidebar footer (avatar 28px circle + name + chevron); popover 260px, opens upward, radius 14px, `--shadow-lg`.
- **evidence**: native `<dialog>` 560px max, radius 16px, backdrop `rgb(15 39 51 / .35)`.
- **chat** (Claude): messages in thread — user message = right bubble like `.turn--user`; assistant = plain text with mark. Composer = card with textarea + round 36px send button.

## 6. Acceptance

`npm run build` and `npm test` pass; no TSX class name without a CSS rule (script check); no `!important`; no hex colours outside tokens.css; 390px width has no horizontal scroll; light and dark both legible; keyboard path: skip link → sidebar → header stepper → thread → composer.

## 7. Decisions after Gemini's spec review

- Feature TSX changes (ClinicalInput composer bar, ProfileMenu upward popover, chat split into `ChatThread` + `ChatComposer`, scroll spy on `main#main`) are done by Claude; Gemini edits CSS only.
- Stale results use an amber outline, not reduced opacity (contrast). Dialog backdrop uses `--scrim`.
- History row actions stay keyboard-reachable (opacity, not display) and are always visible under `(hover: none)`.
- The skip link keeps targeting `#main`: skipping past navigation is its purpose.
- `!important` is allowed only in `base.css` for `[hidden]` and the reduced-motion reset.

## 8. Revision 2

- **New case** is the only filled (teal) control in the sidebar; the collapsed rail shows a filled circle; on narrow screens a round `+` sits in the header too.
- **HPO card**: left edge colour = assertion (teal present, grey absent, amber uncertain/suspected). Header shows the assessment only when it is not "Assessed". The source quote sits in a tinted well. "Details & source" is a disclosure button (`aria-expanded`) that opens a panel with definition, two confidence meters, and a source & context grid (assessment, observed, location in description, origin).
- **Case progress panel** (`features/navigation/CaseOutline`): at ≥ 1280px a 264px right panel lists the three steps with state (done / current / warning / not started) and counts (findings, need review, notes, suggestions, follow-ups). It replaces the header stepper at that width; below 1280px the header stepper returns (numbers only below 1180px).
