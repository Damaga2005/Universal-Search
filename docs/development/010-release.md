# Development Prompt 010 — Windows Release

## Objective

Turn Universal Search into an installable Windows product.

## Deliverables

- versioned executable;
- installer;
- application icon;
- Start Menu entry;
- optional startup/indexer registration;
- clean uninstall;
- upgrade behavior;
- application data directory;
- logs;
- database migration strategy.

## Requirements

- User data must not be stored inside the installation directory.
- Upgrades must preserve the search index where possible.
- Uninstall must clearly distinguish application files from user index/data.
- No network account is required.
- No paid API is required.

## Acceptance criteria

A fresh Windows installation can:

1. install Universal Search;
2. configure indexed folders;
3. build an index;
4. close the GUI;
5. keep indexing in the background;
6. reopen search through Windows/shortcut;
7. search local and OneDrive content;
8. uninstall cleanly.

## Prompt

Prepare Universal Search for a production-style Windows release. Review application data paths, migrations, logging, installer behavior, startup registration, upgrades and uninstall semantics. Build a reproducible release process and test installation/reinstallation behavior before declaring the milestone complete.
