# Development Prompt 008 — Personal Context and University Search

## Objective

Make Universal Search progressively more useful for a user's own document collection, especially university material, without requiring an external AI service.

## Context model

Allow the user to define logical contexts such as:

    University
    Personal
    Projects
    Work

A context may contain one or more indexed roots.

## University features

Support:

- university workspace;
- subjects/categories;
- preferred sources;
- document types;
- related terms;
- optional recency preferences.

The system may infer lightweight relationships from document co-occurrence and repeated terms, but these relationships must remain explainable.

## Local usage learning

Optional local signals may include:

- result opened;
- result selected;
- query/result association.

Privacy requirements:

- disabled by default unless explicitly enabled;
- local-only;
- inspectable;
- deletable by the user;
- never uploaded.

## Acceptance criteria

- A user can define a University context.
- University results can receive contextual ranking.
- Related terms can improve recall without hiding exact matches.
- Personalization is explainable.
- Tests verify that personalization never overwhelms exact filename/content matches.

## Prompt

Implement the personal-context layer on top of deterministic search and ranking. Start with explicit user-defined contexts and explainable term/document relationships. Add optional local usage signals behind a clear setting. Keep all data local and provide tests for privacy and ranking behavior. Do not introduce external AI.
