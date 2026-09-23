# 017 — UX & Accessibility

## Objective

Polish Universal Search into a desktop application that feels fast, coherent and usable daily, especially through keyboard interaction.

## UX principles

Primary flow:

1. launch
2. type
3. results appear
4. select
5. open

Do not turn the main window into a settings dashboard.

## Search UX

Improve:

- instant results
- selected-result state
- keyboard navigation
- Enter to open
- Escape to dismiss
- arrow navigation
- useful shortcuts
- query preservation
- loading state
- no-results state
- indexing/busy state
- error state

## Results

Show enough to distinguish documents:

- filename
- useful path
- type
- snippet
- optional metadata/source

Avoid visual noise.

## Accessibility

Support:

- keyboard-only use
- visible focus
- logical tab order
- scalable text
- high-contrast considerations
- accessible labels
- DPI scaling
- reduced-motion-friendly behaviour
- screen-reader-compatible controls where supported by the GUI framework

## Themes

Support light/dark modes where compatible. Centralise theme definitions; do not scatter colours/styles through code.

## Responsiveness

Typing/search must not freeze the UI. Long-running operations stay off the UI thread.

## Tests

Add keyboard/focus tests, empty/no-result/error states, scaling/layout tests where practical, theme tests, non-blocking search tests and single-instance window tests. Add a manual Windows UX/accessibility checklist.

## Acceptance

- main workflow is keyboard-first
- UI remains responsive
- scaling remains readable
- error states are understandable
- theme styling is centralised
- limitations are documented honestly

## Ready-to-copy implementation prompt

Implement Phase 017 — UX & Accessibility. Audit the real Windows GUI and improve the main search interaction, keyboard navigation, focus, results, loading/errors, DPI/scaling, themes and accessibility. Keep long operations off the UI thread. Add automated tests and a manual Windows checklist. Do not push.
