# 0030. Plug-in reads go through a checked, read-only fetch

Status: Accepted (2026-11-09)

## Context

Handlers need to read their plug-in's tables, for example a player's last 20
games on a hero. ADR 0028 settled that writes are declared in the outcome and
reads use a read-only connection, and ADR 0016 keeps transactions and pooled
connections out of the time a handler waits on upstream APIs. The spec says
each plug-in owns its tables and never reads another plug-in's.

## Options considered

- **A raw read-only connection held for the handler:** full power, including
  raw SQL, but a pooled connection and an open transaction last through the
  handler's HTTP calls, and nothing stops reading other tables.
- **A narrow fetch with an ownership check:** `ctx.db.fetch(select(...))`
  checks the statement, borrows a connection for one read-only transaction and
  returns every row. Nothing is held between calls, ownership covers reads as
  well as writes; no raw SQL, and each fetch is its own snapshot.
- **Isolation in Postgres:** a schema and role per plug-in, switched per job.
  Enforced even against raw SQL, but role and grant management in migrations
  and role switching on pooled connections are easy to get wrong.

## Decision

The narrow fetch:

- `Context.db` is a `Database` with one method, `fetch`, implemented by the
  platform's `PluginDatabase`. Each call runs one `select` in its own
  `SET TRANSACTION READ ONLY` transaction and returns the rows as a list.
- One check, `check_statement`, walks every element of a read or a write with
  SQLAlchemy's documented traversal API (`visitors.iterate`). Every table it
  meets, including in subqueries, aliases and CTEs, must be one the plug-in
  declares, and raw SQL text is refused because it cannot be inspected; the
  `*` of `count(*)` is the one literal allowed.
- A violation raises `ContractViolationError`, which the runner dead-letters
  with the reason instead of retrying.
- The check guards against mistakes in our own plug-ins; it is not a sandbox.
  Postgres enforces read-only independently, which tests show by calling
  `nextval()`, a write the static check cannot see.

## Consequences

- No connection or transaction is held while a handler waits on anything
  else, and table ownership is checked for reads and writes alike.
- Two fetches can see different moments; if a plug-in needs one consistent
  view, a multi-statement fetch can be added later as a new method.
- Platform data a plug-in needs, such as linked accounts, reaches it through
  the job's payload or a deliberate context method, never by reading platform
  tables.
- Unit tests pin the check's behaviour on joins, subqueries, CTEs, aliases,
  lightweight tables and raw text, so a SQLAlchemy upgrade that changes the
  traversal fails CI.
