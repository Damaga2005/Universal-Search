# 031 — Product UX Redesign

## Objective
Evolve Universal Search from a technically complete Windows utility into a modern 2026 desktop product.

The goal is not cosmetic restyling. Rework the primary user experience around fast search, clear hierarchy, keyboard-first interaction, modern Windows conventions and understandable system state.

## Scope
Audit the complete current GUI before changing it.

Design and implement:
- modern search launcher;
- prominent search field;
- keyboard-first navigation;
- compact result cards/rows;
- clear filename, path, source, type and snippet hierarchy;
- loading/indexing states;
- empty/no-results states;
- error states;
- light/dark themes;
- DPI/scaling behavior;
- consistent spacing, typography and controls;
- accessible focus states;
- settings and diagnostics surfaces consistent with the main product.

Do not turn the application into a web dashboard.

## Interaction
Primary flow:
launch -> type -> results -> keyboard selection -> open/reveal.

Target fast perceived response. Search must never block the UI thread.

## Windows
Use native Windows conventions where they improve usability, while preserving the existing platform boundary.

## Validation
Create a manual UX checklist with screenshots or reproducible steps where appropriate.

Test:
- keyboard-only use;
- mouse use;
- high DPI;
- light/dark;
- empty results;
- long filenames;
- long paths;
- large snippets;
- indexing in progress;
- errors;
- accessibility focus order.

## Acceptance
The application should look and behave like a contemporary Windows desktop product rather than a legacy Tk/Windows-95-style utility.

## Ready-to-copy implementation prompt
Implement Phase 031 — Product UX Redesign. Audit the existing GUI and redesign the complete primary experience for a modern 2026 Windows desktop application. Prioritize search speed, hierarchy, keyboard interaction, accessibility, DPI/scaling, light/dark themes and clear system states. Preserve the search/indexing core and platform boundaries. Do not add unrelated backend features. Add automated UI/service coverage plus a documented Windows manual UX checklist, build the packaged application, perform real smoke testing and document measurable UX/performance effects. Do not push unless explicitly instructed.
