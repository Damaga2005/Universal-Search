# 039 — Distribution & Installation v2

## Objective
Turn the technically validated build into a professional Windows distribution experience.

## Scope
Evaluate and implement:
- robust installer;
- per-user installation;
- Start Menu integration;
- upgrade;
- uninstall;
- optional data preservation;
- file associations/context menu;
- tray startup;
- version metadata;
- repair behavior.

If Inno Setup is retained, make its build reproducible and test it.

If another installer is technically better, document the decision before changing it.

## Security
Evaluate:
- code signing;
- artifact hashes;
- update authenticity;
- installer integrity.

Never claim signed/verified status without evidence.

## Clean-machine testing
Test:
- first install;
- upgrade;
- repair;
- uninstall;
- reinstall;
- preserved data;
- removed data;
- failure during install.

## Acceptance
A new Windows user can install, launch, configure and uninstall Universal Search without manual developer tooling.

## Ready-to-copy implementation prompt
Implement Phase 039 — Distribution & Installation v2. Audit the current install.ps1, uninstall.ps1, PyInstaller output and installer strategy. Build a professional reproducible Windows distribution path with upgrade/uninstall/repair semantics, clean Start Menu/tray integration and explicit data-preservation behavior. Validate on a clean Windows environment, generate hashes and document signing status. Do not implement an auto-updater unless authenticity can be guaranteed. Do not push unless explicitly instructed.
