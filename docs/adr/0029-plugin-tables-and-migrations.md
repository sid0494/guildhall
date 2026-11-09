# 0029. Plug-in tables: declared on the plug-in, migrated in the one history

Status: Accepted (2026-11-09)

## Context

Plug-ins now write their own tables through declared statements (ADR 0028).
The platform needs to know which tables each plug-in owns, to check its
writes, and the tables need migrations. The platform's tables use a naming
convention for constraints (ADR 0013) and one Alembic history, run by the
`migrate` service (ADR 0026). The spec's goal is that a new plug-in is a
self-contained piece of work.

## Options considered

Migrations:

- **One history for everything:** `env.py` gathers the platform's metadata and
  every catalogue plug-in's; a plug-in's changes are ordinary revisions in
  `migrations/versions/`. Linear, one command, autogenerate unchanged; the
  migration files live outside the plug-in's folder.
- **One Alembic branch per plug-in:** migrations inside each plug-in's folder,
  a branch label per plug-in, `alembic upgrade heads`. Fully self-contained,
  but every revision needs branch options, and autogenerate must be filtered
  per branch; mistakes put revisions on the wrong branch.
- **Separate histories:** independent environments and version tables; many
  commands and no ordering against the platform's tables.
- **`create_all()` at start-up:** no way to change an existing table.

## Decision

- A plug-in declares its tables in a `MetaData` from
  `guildhall.contract.plugin_metadata()`, which applies the platform's naming
  convention, and exposes it as `SourcePlugin.tables` (empty by default). The
  naming convention moves into the contract; the platform imports it.
- The start-up check rejects a table not named `<plugin>_...`; the write check
  (ADR 0028) requires each statement to target one of the plug-in's declared
  tables.
- Migrations stay in the one history. `env.py` includes the metadata of every
  plug-in in the catalogue, enabled or not, so the schema never depends on
  configuration. Revision files that touch a plug-in's tables name it in their
  slug and touch only its tables.
- A test checks that a fully migrated database has exactly the tables the
  platform and plug-ins declare.

## Consequences

- Table code is self-contained in the plug-in; its migration files are shared,
  and reviewed for touching only that plug-in's tables.
- Disabled plug-ins' tables exist and stay empty.
- If per-plug-in histories are needed later, a plug-in's revisions can be
  moved onto an Alembic branch from an existing revision, as a deliberate
  one-time change.
