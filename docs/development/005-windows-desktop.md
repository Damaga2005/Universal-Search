# Development Prompt 005 — Windows Desktop Application

## Objective

Create the actual Windows application users will launch from Windows Search/Start.

## Product behavior

The application should:

- launch quickly;
- present a focused search box;
- show results as the user types;
- display filename, location, source and useful context;
- open files with the default Windows application;
- reveal a file in Explorer;
- support keyboard navigation;
- remain usable with thousands or millions of indexed documents.

## UX principles

The UI should be minimal and search-first.

Do not build a large settings-heavy application.

The primary workflow is:

    Windows Search / shortcut
        -> Universal Search
        -> type query
        -> select result
        -> open

## Requirements

- Separate GUI from the search core.
- Do not put database logic inside UI components.
- Provide a testable application service layer.
- Support packaging as a Windows executable.
- Establish an application icon and product identity.
- Prepare for a tray/background indexer.

## Acceptance criteria

- Universal Search can be launched as a Windows application.
- Search results appear quickly.
- Opening a result works.
- Keyboard-only navigation works.
- The core search tests remain independent of the GUI.

## Prompt

Implement the Windows desktop application layer around the existing search engine. First identify the most appropriate native/local UI technology for the repository and justify it briefly. Keep the core independent of the GUI, add UI tests where practical, implement keyboard-first search, result opening and Explorer reveal, then package a development Windows executable.
