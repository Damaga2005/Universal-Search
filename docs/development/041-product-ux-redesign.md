# 041 — Product Experience & UX

## Objective
Evolve Universal Search from a technically complete Windows utility into a coherent, modern Windows desktop product.

This is a product-experience phase, not a second implementation of the accessibility, theme, DPI or state foundations already delivered in phases 017 and 039.

## Scope
Audit the complete current GUI and redesign the primary experience around:

- launch -> search -> results -> selection -> open/reveal;
- clear visual hierarchy for filename, path, source, type and snippet;
- keyboard-first navigation;
- compact, information-dense result presentation;
- responsive loading, indexing, empty and error states;
- coherent light/dark appearance;
- consistent spacing, typography and controls;
- settings and diagnostics surfaces that feel part of the same product;
- Windows-native interaction conventions where they improve usability.

Reuse and extend the existing accessibility, focus, DPI and state infrastructure. Do not recreate phase 039 from scratch.

Do not turn the application into a web dashboard.

## Non-goals
- no new search backend;
- no new ranking model;
- no new provider architecture;
- no cloud AI;
- no unrelated backend features.

## Interaction
Primary flow:

launch -> type -> results -> keyboard selection -> open/reveal.

Search must never block the UI thread.

## Validation
Create a reproducible Windows UX checklist and test:

- keyboard-only use;
- mouse use;
- high DPI;
- light/dark;
- empty and zero-result states;
- long filenames and paths;
- large snippets;
- indexing in progress;
- provider/index errors;
- focus order and existing accessibility contracts.

Measure perceived and actual interaction latency where practical.

## Acceptance
The application has one coherent primary experience that builds on, rather than duplicates, the phase 039 accessibility/interface foundation.

## Ready-to-copy implementation prompt
Implement Phase 041 — Product Experience & UX. Audit the existing Windows GUI and redesign the primary product experience around fast search, hierarchy, keyboard interaction, clear system states and modern Windows conventions. Reuse the existing accessibility, DPI, theme and state infrastructure from phases 017/039 instead of recreating it. Preserve the search/indexing core and platform boundaries. Do not add unrelated backend features. Add automated coverage plus a documented Windows manual UX checklist, build the packaged application, perform real smoke testing and document measurable UX effects. Do not push unless explicitly instructed.
