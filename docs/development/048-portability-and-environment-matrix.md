# 048 — Portability & Environment Matrix

## Objective
Expand confidence beyond the single currently validated Python/Windows environment.

## Matrix
Evaluate supported combinations of:
- Windows versions;
- Python versions;
- Tk availability;
- CPU architectures where relevant;
- locale/encoding;
- DPI settings.

Do not claim support for environments that are not tested.

## Core portability
Keep platform-independent modules testable outside Windows.

Where practical, run Linux/macOS tests for the core without pretending the application itself is cross-platform.

## Compatibility
Test:
- database migrations;
- package installation;
- extraction;
- search;
- ranking;
- provider boundaries;
- configuration.

## Acceptance
Supported environments are explicitly documented and CI evidence matches the support statement.

## Ready-to-copy implementation prompt
Implement Phase 048 — Portability & Environment Matrix. Audit the current environment assumptions and expand automated validation for supported Python/Windows combinations, locale/encoding, DPI and Tk availability. Keep the core platform-independent and test it separately where useful. Update CI and support documentation only for environments actually verified. Do not claim unsupported platforms. Do not push unless explicitly instructed.
