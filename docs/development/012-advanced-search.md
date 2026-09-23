# 012 — Advanced Search

## Objective

Build a useful, predictable advanced query language while keeping ordinary searches such as `BJT` simple and unchanged.

## Mandatory context

Read the current search parser/query model, ranking, database/FTS implementation, GUI and CLI flows, and all phase reports 001–011.

## Query features

Implement, where the current data model supports them:

- plain terms
- quoted phrases: `"Ebers Moll"`
- AND
- OR
- negation: `-CMOS`
- `name:BJT`
- `path:universidad`
- `type:pdf`
- `source:local`
- `after:2026-01-01`
- `before:2026-09-23`
- `size:>10MB`

Unknown or malformed syntax must never crash the application.

## Architecture

Separate:

1. lexical parsing
2. query AST
3. validation
4. FTS/database translation
5. ranking

Never construct SQL unsafely from raw user input. Prevent FTS5 operator injection.

Define exact semantics for precedence, quoting, escaping, Unicode, repeated terms, empty expressions, malformed expressions, negative-only queries and OR groups.

## UX

CLI and GUI must share semantics. Invalid queries should produce understandable feedback rather than tracebacks.

## Tests

Build a comprehensive parser suite covering valid/invalid expressions, quotes, Unicode, operators, precedence, escaping, SQL/FTS metacharacters and empty queries.

Add integration tests proving every supported filter changes the result set correctly.

## Acceptance

- simple searches remain compatible
- syntax is deterministic and documented
- invalid input never crashes CLI/GUI/background processes
- query semantics are shared
- no unsafe SQL interpolation
- parser and integration tests are comprehensive

## Constraints

Do not add a parser dependency without clear justification. Do not implement natural-language search or external AI.

## Ready-to-copy implementation prompt

Implement Phase 012 — Advanced Search. Inspect the existing search architecture, design a small explicit grammar and AST, implement safe parsing/translation, add supported filename/path/type/source/date/size filters, preserve simple searches, expose identical semantics in CLI and GUI, add extensive tests and documentation, and do not push.
