# 048 — Windows & Environment Matrix

## Objective
Expand confidence in the environments Universal Search actually intends to support, without turning the Windows application into a cross-platform product.

## Support boundary
Universal Search remains a Windows-first desktop application.

Validate and document only combinations that are actually tested, including where relevant:

- supported Windows versions;
- supported Python versions for development/CI;
- CPU architecture;
- Tk availability;
- locale/encoding;
- DPI/scaling;
- SQLite behavior;
- PyInstaller/package behavior.

The current CI/runtime mismatch must be made explicit and resolved or documented rather than silently treated as support.

## Core portability
Keep platform-independent modules testable outside Windows.

Linux/macOS testing may be used as a core portability probe, but must never imply that the GUI, installer, shell integration or full application is supported there.

## Compatibility
Test where applicable:

- database migrations;
- package installation;
- extraction;
- search;
- ranking;
- provider boundaries;
- configuration;
- packaged startup.

## Acceptance
The support matrix, CI matrix and release documentation agree on exactly what is supported and what is merely probed.

No unsupported platform is advertised as supported.

## Ready-to-copy implementation prompt
Implement Phase 048 — Windows & Environment Matrix. Audit environment assumptions and establish an evidence-backed support matrix for Windows, Python/CI, architecture, locale/encoding, DPI, Tk, SQLite and packaging. Keep the core platform-independent and test it separately where useful, but do not claim cross-platform application support. Align CI and documentation with verified evidence. Do not push unless explicitly instructed.
