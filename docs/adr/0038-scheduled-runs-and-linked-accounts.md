# 0038. Scheduled jobs take a `ScheduledRun`; `per_account` fans out over `linked_accounts`

Status: Accepted (2026-11-09)

## Context

A `Schedule` names a job type and a cron expression but carries no payload,
so the platform must build the payload of each scheduled job itself. With
`per_account` (the default), a tick creates one job per linked account, and
`linked_accounts`, until now only in the spec's data model, did not exist.
Dedupe keys appear on every log line a job writes, and an account's external
ID (a Dota account ID) is a personal identifier.

## Options considered

- **Payload:** an empty payload (the handler cannot tell which tick or which
  account it serves); a payload declared on the `Schedule` (static, so it
  cannot name the tick or the account); or a contract model the platform fills
  in.
- **Key per account:** the account's external ID (readable, but a personal
  identifier in every log line) or the linked account's row ID.

## Decision

- The contract gains `ScheduledRun(scheduled_for, account)` and
  `LinkedAccount(discord_user_id, external_id)`. A scheduled job type must take
  exactly `ScheduledRun` as its payload; `check_declarations` enforces it.
- `scheduled_for` is the tick, not when the job runs.
- `linked_accounts` is a platform table: `id`, `plugin`, `discord_user_id`,
  `external_id`, `linked_at`, with one account per Discord user per plug-in.
  `/link` will write it (M2 C2).
- A `per_account` tick creates one job per linked account of that plug-in,
  in `id` order, and none when there are none (the tick is still claimed).
- Keys are `<plug-in>:schedule:<job type>:<tick, UTC to the minute>`, plus
  `:<linked account id>` for `per_account`. The external ID travels in the
  payload, which the handler needs for its API calls, never in the key.
- The naming convention for unique constraints now joins every column
  (`column_0_N_name`): this table's is `uq_linked_accounts_plugin_discord_user_id`.
  Single-column names are unchanged, so no existing constraint is renamed.

## Consequences

- Handlers know which tick and which account they serve, even when they run
  late after a missed tick (ADR 0037).
- Log lines name a linked account by row ID; finding who that is takes a
  database lookup, which is the point.
- A schedule's job type cannot carry extra payload fields; a plug-in that
  needs per-schedule settings keeps them in its own configuration or tables.
- `contract_version` stays 1: the models are additions, and no existing
  plug-in declares schedules.
