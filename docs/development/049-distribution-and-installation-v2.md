# 049 — Windows Distribution & Installation

## Objective
Turn the validated build and existing portable/single-file work from phases 020 and 037 into a professional Windows installation and lifecycle experience.

This phase does not replace the release-engineering foundations already in place.

## Scope
Evaluate and implement, where evidence supports it:

- reproducible Windows installer;
- per-user installation;
- Start Menu integration;
- upgrade;
- uninstall;
- repair;
- explicit data-preservation choices;
- file associations/context-menu integration where justified;
- tray/startup integration;
- version metadata;
- artifact hashes.

If Inno Setup is retained, make its build reproducible and test it. If another installer is selected, document the decision and migration path before changing strategy.

## Security
Evaluate:

- code signing;
- artifact hashes;
- installer integrity;
- authenticity of future update packages.

Never claim signed or verified status without evidence.

Do not implement an auto-updater unless authenticity and rollback semantics can be guaranteed.

## Clean-machine validation
Test:

- first install;
- upgrade;
- repair;
- uninstall;
- reinstall;
- preserved data;
- removed data;
- failure during installation;
- Start Menu/Explorer integration;
- packaged startup.

## Acceptance
A new Windows user can install, launch, configure, upgrade, repair and uninstall Universal Search without developer tooling, with explicit and predictable treatment of user data.

## Ready-to-copy implementation prompt
Implement Phase 049 — Windows Distribution & Installation. Audit the existing install/uninstall scripts, PyInstaller outputs and distribution strategy. Build a reproducible professional Windows installation lifecycle with upgrade, repair, uninstall, Start Menu/tray integration and explicit data-preservation semantics. Validate on a clean Windows environment, generate artifact hashes and document signing status. Do not implement an auto-updater unless authenticity and rollback can be guaranteed. Do not push unless explicitly instructed.
